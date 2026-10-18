"""
Pipeline: the end-to-end autopoietic loop.

    plan -> (reuse | tests -> critic -> red check -> synthesis -> verify)
         -> library registration -> approval gate -> hot-load into the registry

The Pipeline owns scheduling and policy only; every step is delegated to an
agent (Planner, Librarian, Auditor, Architect, Warden), the ToolRegistry and
the ApprovalGate, all of which are injected so they can be replaced in tests.
"""

import ast
import importlib.util
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.approval import ApprovalGate, ApprovalStatus, ToolProposal
from src.core.schemas import (
    DecompositionRequest,
    TaskGraph,
    TaskNode,
    TaskState,
)
from src.utils.naming import derive_function_name

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 3
MAX_TASKS_LIMIT = 50


def bind_test_imports(test_path: str, module_stem: str, function_name: str) -> bool:
    """
    Point a generated test file at the real implementation module.

    Tests are written before the implementation exists, so they import the
    function from a guessed module (``from add_numbers import add_numbers`` or
    ``from tools.add_numbers import add_numbers``). Implementations get unique
    file names (task id + hash), so the guessed import is rewritten to the
    actual module stem.

    Returns:
        True if the file was modified.
    """
    path = Path(test_path)
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    lines = source.split("\n")
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        if node.module == module_stem:
            continue
        imported = {a.name for a in node.names}
        guessed = node.module.split(".")[-1] == function_name
        if not guessed and function_name in imported:
            try:
                guessed = importlib.util.find_spec(node.module.split(".")[0]) is None
            except (ImportError, ValueError):
                guessed = True
        if not guessed:
            continue
        indent = lines[node.lineno - 1][: node.col_offset]
        new_node = ast.ImportFrom(module=module_stem, names=node.names, level=0)
        edits.append((node.lineno, node.end_lineno or node.lineno, indent + ast.unparse(new_node)))

    if not edits:
        return False
    for start, end, text in sorted(edits, reverse=True):
        lines[start - 1:end] = [text]
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


class Pipeline:
    """
    Run a natural-language request through the full tool lifecycle.

    Args:
        settings: Settings (uses ``auto_approve_tools``).
        llm: LLM client (used for token accounting; agents hold their own ref).
        planner, librarian, auditor, architect, warden: the agents.
        registry: ToolRegistry hot-loading tools into the live MCP server.
        approval: ApprovalGate deciding whether verified tools go live.
        janitor: Optional Janitor (kept for the orchestrator's management tools).
        max_iterations: Shared per-task budget for synthesis, refinement and
            exceptions (default: ``architect.max_refinement_iterations`` or 3).
        reserved_names: Tool names a generated tool may never take (e.g. the
            server's own management tools).
    """

    def __init__(
        self,
        settings: Any,
        llm: Any,
        planner: Any,
        librarian: Any,
        auditor: Any,
        architect: Any,
        warden: Any,
        registry: Any,
        approval: ApprovalGate,
        janitor: Any = None,
        max_iterations: Optional[int] = None,
        reserved_names: Optional[Set[str]] = None,
    ):
        self.settings = settings
        self.llm = llm
        self.planner = planner
        self.librarian = librarian
        self.auditor = auditor
        self.architect = architect
        self.warden = warden
        self.registry = registry
        self.approval = approval
        self.janitor = janitor
        self.max_iterations = max(
            1, max_iterations or getattr(architect, "max_refinement_iterations", DEFAULT_MAX_ITERATIONS)
        )
        self.reserved_names = set(reserved_names or ())
        self._run_lock = threading.Lock()

        # The gate activates / retires tools through the pipeline.
        if approval.on_approve is None:
            approval.on_approve = self.activate_proposal
        if approval.on_reject is None:
            approval.on_reject = self.retire_proposal

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, description: str, max_tasks: int = 10) -> Dict[str, Any]:
        """
        Execute the full pipeline for ``description``.

        Returns:
            A report with ``status`` (completed | partial | failed | error),
            counts (``verified``, ``reused``, ``failed``), per-task
            ``results`` and token usage.
        """
        with self._run_lock:
            return self._run(description, max_tasks)

    def activate_proposal(self, proposal: ToolProposal) -> Dict[str, Any]:
        """Load an approved tool into the registry and mark it approved."""
        name = self._load(proposal.tool_id, proposal.path)
        self._set_metadata(proposal.tool_id, {"approved": True, "loaded_name": name})
        return {"loaded": True, "name": name}

    def retire_proposal(self, proposal: ToolProposal, reason: str = "") -> None:
        """A rejected tool is unloaded and deprecated so it is never reused."""
        try:
            self.registry.unload_tool(proposal.tool_id)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"unload during rejection failed: {e}")
        try:
            self.librarian.set_status(proposal.tool_id, "deprecated")
        except Exception as e:
            logger.warning(f"Could not deprecate rejected tool {proposal.tool_id}: {e}")

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def _run(self, description: str, max_tasks: int) -> Dict[str, Any]:
        correlation_id = f"gen_{uuid.uuid4().hex[:8]}"
        logger.info(f"=== Starting pipeline {correlation_id}: {description[:100]}")
        max_tasks = max(1, min(int(max_tasks), MAX_TASKS_LIMIT))

        try:
            decomposition = self.planner.decompose(
                DecompositionRequest(
                    user_request=description, max_tasks=max_tasks, correlation_id=correlation_id
                )
            )
            graph: TaskGraph = decomposition.task_graph
            graph.validate_acyclic()
        except Exception as e:
            logger.error(f"Planning failed: {e}", exc_info=True)
            return {
                "status": "error",
                "correlation_id": correlation_id,
                "description": description,
                "error": str(e),
                "token_usage": self._token_usage(),
            }

        outcomes: Dict[str, Dict[str, Any]] = {}
        while True:
            ready = graph.get_ready_tasks()
            if not ready:
                break
            for task in ready:
                outcome = self._process_task(task, graph)
                outcomes[task.id] = outcome
                if task.state == TaskState.FAILED:
                    for dep_id in graph.propagate_failure(task.id):
                        dependent = graph.get_node(dep_id)
                        outcomes[dep_id] = self._outcome(dependent, "failed", error=dependent.error_trace)

        # Anything still pending has an unsatisfiable dependency.
        for task in graph.nodes:
            if task.id not in outcomes:
                task.update_state(TaskState.FAILED, error="Task could not be scheduled")
                outcomes[task.id] = self._outcome(task, "failed", error=task.error_trace)

        results = [outcomes[t.id] for t in graph.nodes]
        verified = sum(1 for r in results if r["status"] == "verified")
        reused = sum(1 for r in results if r["status"] == "reused")
        failed = sum(1 for r in results if r["status"] == "failed")
        if failed == 0:
            status = "completed"
        elif verified + reused == 0:
            status = "failed"
        else:
            status = "partial"

        logger.info(
            f"=== Pipeline {correlation_id} {status}: {verified} verified, "
            f"{reused} reused, {failed} failed of {len(graph.nodes)}"
        )
        return {
            "status": status,
            "correlation_id": correlation_id,
            "description": description,
            "total_tasks": len(graph.nodes),
            "verified": verified,
            "reused": reused,
            "failed": failed,
            "results": results,
            "pending_approvals": self.approval.list_pending(),
            "library_stats": self._library_stats(),
            "token_usage": self._token_usage(),
        }

    def _process_task(self, task: TaskNode, graph: TaskGraph) -> Dict[str, Any]:
        """Take one ready task to VERIFIED or FAILED; never raises."""
        logger.info(f"--- Task {task.id}: {task.description}")
        task.update_state(TaskState.IN_PROGRESS)
        try:
            dependency_context = self._dependency_context(task, graph)

            reused = self._try_reuse(task)
            if reused is not None:
                return reused

            test_code, problem = self._prepare_tests(task, dependency_context)
            if problem:
                return self._fail(task, problem)

            if not self.warden.run_red_check(task):
                return self._fail(
                    task, "Red check failed: tests do not fail against a stub implementation"
                )

            test_result, problem = self._synthesize_and_verify(task, test_code, dependency_context)
            if test_result is None:
                return self._fail(task, problem)

            task.update_state(TaskState.VERIFIED)
            return self._finalize(task, test_result)
        except Exception as e:
            logger.error(f"Task {task.id} failed unexpectedly: {e}", exc_info=True)
            return self._fail(task, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _try_reuse(self, task: TaskNode) -> Optional[Dict[str, Any]]:
        """Reuse a library tool when it is verifiably equivalent; else None."""
        try:
            match = self.librarian.get_best_match(task)
            if match is None:
                return None
            if match.similarity < self.librarian.high_confidence_threshold:
                logger.info(
                    f"Match {match.tool_id} below high-confidence threshold "
                    f"({match.similarity:.3f}); generating instead"
                )
                return None
            if not self.librarian.verify_match(match, task):
                return None

            tool = self.librarian.get_tool(match.tool_id) or {}
            test_path, source_path = tool.get("test_path"), tool.get("source_path")
            if not test_path or not Path(test_path).is_file() or not source_path:
                logger.info(f"No stored tests for {match.tool_id}; cannot verify reuse")
                return None

            probe = TaskNode(
                id=task.id,
                description=task.description,
                test_suite_path=test_path,
                implementation_path=source_path,
            )
            result = self.warden.execute_tests(probe)
            if not result.success:
                logger.warning(f"Stored tests failed for {match.tool_id}; marking broken")
                self.librarian.record_outcome(match.tool_id, False)
                self.librarian.set_status(match.tool_id, "broken")
                self.registry.unload_tool(match.tool_id)
                return None

            self.librarian.record_outcome(match.tool_id, True)
            task.reused_tool_id = match.tool_id
            task.implementation_path = source_path
            task.test_suite_path = test_path
            task.update_state(TaskState.VERIFIED)
            logger.info(f"Reusing {match.tool_id} for task {task.id} (similarity {match.similarity:.3f})")
            activation = self._activate(task, match.tool_id, source_path, test_path, result)
            return self._outcome(
                task,
                "reused",
                reused_tool_id=match.tool_id,
                reused_tool_name=match.metadata.get("name"),
                similarity=match.similarity,
                source_path=source_path,
                implementation_path=source_path,
                test_path=test_path,
                tests_passed=result.tests_passed,
                execution_time=result.execution_time,
                **activation,
            )
        except Exception as e:
            logger.warning(f"Reuse check failed for task {task.id}, generating instead: {e}")
            return None

    def _prepare_tests(self, task: TaskNode, dependency_context: Optional[str]):
        """Generate tests and have the Critic approve them (one regeneration allowed)."""
        reason = ""
        for attempt in (1, 2):
            self.auditor.generate_test_suite(task, dependency_context=dependency_context)
            test_code = Path(task.test_suite_path).read_text(encoding="utf-8")
            try:
                verdict = self.auditor.validate_test_quality(test_code)
            except ValueError as e:
                reason = f"critic failed: {e}"
                continue
            if str(verdict.recommendation).upper() == "APPROVE":
                return test_code, None
            reason = f"{verdict.recommendation}: {verdict.reason}"
            if verdict.issues:
                reason += f" (issues: {'; '.join(map(str, verdict.issues))})"
            logger.warning(f"Critic rejected tests for {task.id} (attempt {attempt}): {reason}")
        return None, f"Test suite rejected by critic after regeneration: {reason}"

    def _synthesize_and_verify(
        self, task: TaskNode, test_code: str, dependency_context: Optional[str]
    ):
        """
        Synthesize, test and refine under ONE shared iteration budget.

        Every pass (first synthesis, each refinement, and any pass that
        raised) consumes one iteration.
        """
        last_result = None
        last_error = "no attempt was made"
        function_name = derive_function_name(task.description)
        refine_cap = getattr(self.architect, "max_refinement_iterations", self.max_iterations)

        for attempt in range(1, self.max_iterations + 1):
            task.attempts = attempt
            logger.info(f"Task {task.id}: iteration {attempt}/{self.max_iterations}")
            try:
                if not task.implementation_path:
                    self.architect.synthesize_implementation(
                        task, test_code, dependency_context=dependency_context
                    )
                    bind_test_imports(
                        task.test_suite_path, Path(task.implementation_path).stem, function_name
                    )
                    test_code = Path(task.test_suite_path).read_text(encoding="utf-8")
                elif last_result is not None:
                    self.architect.refine_implementation(
                        task, test_code, last_result, min(attempt - 1, refine_cap)
                    )
                result = self.warden.execute_tests(task)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"Task {task.id} iteration {attempt} raised: {last_error}")
                continue

            if result.success:
                return result, None
            last_result = result
            last_error = (result.stderr or result.stdout or "tests failed")[-2000:]
            logger.warning(f"Task {task.id}: {result.tests_failed} test(s) failed")

        return None, (
            f"Tests did not pass within the iteration budget ({self.max_iterations}): {last_error}"
        )

    def _finalize(self, task: TaskNode, test_result: Any) -> Dict[str, Any]:
        """Register a verified tool in the library and run the approval gate."""
        registered, reg_error = True, None
        try:
            self.librarian.register_tool(
                tool_id=task.id, source_path=task.implementation_path, test_result=test_result
            )
            self._set_metadata(
                task.id, {"test_path": str(task.test_suite_path), "task_description": task.description}
            )
        except Exception as e:
            registered, reg_error = False, str(e)
            logger.warning(f"Library registration failed for {task.id}: {e}")

        activation = self._activate(
            task, task.id, task.implementation_path, task.test_suite_path, test_result
        )
        if activation["approval"] == ApprovalStatus.REJECTED.value:
            return self._fail(task, "Tool rejected by approver", registered_in_library=registered)

        extra: Dict[str, Any] = dict(activation)
        if reg_error:
            extra["registration_error"] = reg_error
        return self._outcome(
            task,
            "verified",
            implementation_path=task.implementation_path,
            test_path=task.test_suite_path,
            tests_passed=test_result.tests_passed,
            execution_time=test_result.execution_time,
            registered_in_library=registered,
            **extra,
        )

    def _activate(
        self, task: TaskNode, tool_id: str, path: str, test_path: Optional[str], test_result: Any
    ) -> Dict[str, Any]:
        """Run the approval gate and, if approved, hot-load the tool."""
        info: Dict[str, Any] = {
            "approval": ApprovalStatus.PENDING.value,
            "loaded": False,
            "tool_name": None,
        }
        try:
            for entry in self.registry.list_loaded():
                if entry.get("tool_id") == tool_id:
                    return {**info, "approval": ApprovalStatus.APPROVED.value, "loaded": True,
                            "tool_name": entry.get("name")}

            meta = self.librarian.get_tool(tool_id) or {}
            if meta.get("approved") is True:
                status = ApprovalStatus.APPROVED
            else:
                proposal = ToolProposal(
                    tool_id=tool_id,
                    name=meta.get("function_name") or meta.get("name") or Path(path).stem,
                    path=str(path),
                    description=meta.get("description") or task.description,
                    signature=meta.get("signature", ""),
                    test_path=test_path,
                    task_id=task.id,
                    tests_passed=getattr(test_result, "tests_passed", 0),
                )
                status = self.approval.request(proposal)
            info["approval"] = status.value

            if status == ApprovalStatus.APPROVED:
                name = self._load(tool_id, path)
                self._set_metadata(tool_id, {"approved": True, "loaded_name": name})
                info.update(loaded=True, tool_name=name)
            elif status == ApprovalStatus.REJECTED:
                self.retire_proposal(ToolProposal(tool_id=tool_id, name="", path=str(path)), "rejected")
        except Exception as e:
            info["load_error"] = str(e)
            logger.warning(f"Could not activate tool {tool_id}: {e}")
        return info

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load(self, tool_id: str, path: str) -> str:
        meta = self.librarian.get_tool(tool_id) or {}
        wanted = meta.get("function_name") or meta.get("name")
        if wanted and wanted in self.reserved_names:
            raise ValueError(f"Tool name '{wanted}' is reserved by the server")
        return self.registry.load_tool(Path(path), tool_id=tool_id)

    def _set_metadata(self, tool_id: str, fields: Dict[str, Any]) -> None:
        try:
            self.librarian.vector_store.update_metadata(tool_id, fields)
        except Exception as e:
            logger.debug(f"Could not update metadata for {tool_id}: {e}")

    def _dependency_context(self, task: TaskNode, graph: TaskGraph) -> Optional[str]:
        """Signatures of verified upstream tools, for the downstream prompts."""
        lines: List[str] = []
        for dep_id in task.dependencies:
            dep = graph.get_node(dep_id)
            if dep is None or dep.state != TaskState.VERIFIED:
                continue
            tool = self.librarian.get_tool(dep.reused_tool_id or dep.id) or {}
            signature = tool.get("signature") or ""
            summary = tool.get("description") or dep.description
            if signature:
                lines.append(f"- {signature}: {summary}")
            else:
                lines.append(f"- {derive_function_name(dep.description)}(...): {summary}")
        return "\n".join(lines) or None

    def _fail(self, task: TaskNode, error: str, **extra: Any) -> Dict[str, Any]:
        task.update_state(TaskState.FAILED, error=error)
        logger.error(f"Task {task.id} FAILED: {error}")
        return self._outcome(task, "failed", error=error, **extra)

    @staticmethod
    def _outcome(task: TaskNode, status: str, **fields: Any) -> Dict[str, Any]:
        out = {
            "task_id": task.id,
            "description": task.description,
            "status": status,
            "attempts": task.attempts,
        }
        out.update(fields)
        return out

    def _library_stats(self) -> Dict[str, Any]:
        try:
            return self.librarian.get_library_stats()
        except Exception:
            return {}

    def _token_usage(self) -> Dict[str, Any]:
        getter = getattr(self.llm, "get_token_usage", None)
        try:
            return getter() if getter else {}
        except Exception:
            return {}
