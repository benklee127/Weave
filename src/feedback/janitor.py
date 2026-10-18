"""
Janitor: continuous regression testing and self-healing of library tools.

The Janitor re-runs the stored tests of every active tool in the sandbox,
marks tools that fail as ``broken`` (and unloads them from the live
registry), and can heal a broken tool by asking the Architect to refine it
against the failing output, re-verifying, re-indexing (version bump) and
reloading it.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.schemas import TaskNode, TestExecutionResult
from src.sandbox.base import is_infra_error

logger = logging.getLogger(__name__)


class Janitor:
    """Re-tests, heals and prunes tools in the library."""

    def __init__(
        self,
        runner: Any,
        librarian: Any,
        registry: Any,
        architect: Any,
        llm: Optional[Any] = None,
        settings: Optional[Any] = None,
        feedback: Optional[Any] = None,
    ):
        self.runner = runner
        self.librarian = librarian
        self.registry = registry
        self.architect = architect
        self.llm = llm
        self.settings = settings
        self.feedback = feedback
        self._test_paths: Dict[str, str] = {}
        self._run_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_report: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Test discovery
    # ------------------------------------------------------------------

    def register_tests(self, tool_id: str, test_path: str) -> None:
        """Remember (and persist in the library) the stored tests of a tool."""
        self._test_paths[tool_id] = str(test_path)
        try:
            self.librarian.vector_store.update_metadata(tool_id, {"test_path": str(test_path)})
        except Exception as e:
            logger.debug(f"Could not persist test_path for {tool_id}: {e}")

    def find_tests(self, tool: Dict[str, Any]) -> Optional[str]:
        """Locate the stored test file for a tool dict, or None."""
        candidates: List[str] = []
        if tool.get("id") in self._test_paths:
            candidates.append(self._test_paths[tool["id"]])
        for key in ("test_path", "test_suite_path"):
            if tool.get(key):
                candidates.append(str(tool[key]))
        for c in candidates:
            if Path(c).is_file():
                return c
        tests_dir = getattr(self.settings, "tests_directory", None)
        fn = tool.get("function_name") or tool.get("name")
        if tests_dir and fn and Path(tests_dir).is_dir():
            found = sorted(
                Path(tests_dir).glob(f"test_{fn}_*.py"), key=lambda p: p.stat().st_mtime
            )
            if found:
                return str(found[-1])
        return None

    # ------------------------------------------------------------------
    # Regression
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_bytecode(source_path: str) -> None:
        """Drop cached bytecode so a same-size, same-second rewrite is never masked."""
        src = Path(source_path)
        for pyc in (src.parent / "__pycache__").glob(f"{src.stem}.*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass

    def _run(self, tool_id: str, source_path: str, test_path: str) -> TestExecutionResult:
        self._clear_bytecode(source_path)
        try:
            return self.runner.run_test(
                test_file_path=test_path,
                task_id=f"janitor_{tool_id}",
                implementation_path=source_path,
            )
        except Exception as e:
            return TestExecutionResult(
                task_id=f"janitor_{tool_id}", success=False, exit_code=-1,
                stderr=f"[sandbox] INFRA ERROR: runner raised {e!r}",
            )

    def run_regression(self, tool_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Re-run stored tests for every active tool (or the given ids).

        Passing tools get a success outcome; failing tools get a failure
        outcome, are marked ``broken`` and unloaded from the registry.
        Infrastructure errors and tools without tests are skipped, never
        marked broken.
        """
        with self._run_lock:
            report: Dict[str, Any] = {
                "checked": 0, "passed": [], "broken": [], "skipped": [], "details": {},
            }
            tools = self.librarian.list_tools(status="active")
            if tool_ids is not None:
                wanted = set(tool_ids)
                tools = [t for t in tools if t["id"] in wanted]
            for tool in tools:
                tid = tool["id"]
                test_path = self.find_tests(tool)
                source = tool.get("source_path")
                if not test_path or not source or not Path(source).is_file():
                    report["skipped"].append(tid)
                    report["details"][tid] = "no tests or source available"
                    continue
                result = self._run(tid, source, test_path)
                if is_infra_error(result):
                    report["skipped"].append(tid)
                    report["details"][tid] = "sandbox infrastructure error"
                    continue
                report["checked"] += 1
                self.librarian.record_outcome(tid, result.success)
                if result.success:
                    report["passed"].append(tid)
                else:
                    self.librarian.set_status(tid, "broken")
                    self.registry.unload_tool(tid)
                    report["broken"].append(tid)
                    report["details"][tid] = (result.stderr or result.stdout)[-2000:]
                    logger.warning(f"Regression: {tid} is broken")
            self.last_report = report
            return report

    # ------------------------------------------------------------------
    # Healing
    # ------------------------------------------------------------------

    def heal(self, tool_id: str, max_attempts: int = 3) -> Dict[str, Any]:
        """
        Try to repair a tool: refine against the failing output, re-verify,
        re-index (bumping the version) and reload it in the registry.
        On failure the original source is restored and the tool stays broken.
        """
        tool = self.librarian.get_tool(tool_id)
        if tool is None:
            return {"tool_id": tool_id, "healed": False, "attempts": 0, "error": "unknown tool"}
        source = tool.get("source_path")
        test_path = self.find_tests(tool)
        if not source or not Path(source).is_file():
            return {"tool_id": tool_id, "healed": False, "attempts": 0,
                    "error": "source file missing"}
        if not test_path:
            return {"tool_id": tool_id, "healed": False, "attempts": 0,
                    "error": "no stored tests"}

        src_path = Path(source)
        original = src_path.read_text(encoding="utf-8")
        old_version = tool.get("version")
        test_code = Path(test_path).read_text(encoding="utf-8")
        task = TaskNode(
            id=f"heal_{tool_id}",
            description=tool.get("description") or tool.get("name") or tool_id,
            input_schema=tool.get("input_schema") or {},
            output_schema=tool.get("output_schema") or {},
            test_suite_path=test_path,
            implementation_path=str(src_path),
        )

        result = self._run(tool_id, str(src_path), test_path)
        attempts = 0
        error = ""
        while not result.success and attempts < max_attempts:
            if is_infra_error(result):
                error = "sandbox infrastructure error"
                break
            attempts += 1
            try:
                self.architect.refine_implementation(task, test_code, result, iteration=attempts)
            except Exception as e:
                error = f"refinement failed: {e}"
                logger.warning(f"Heal {tool_id} attempt {attempts}: {error}")
                break
            result = self._run(tool_id, str(src_path), test_path)

        if not result.success:
            src_path.write_text(original, encoding="utf-8")
            self.librarian.set_status(tool_id, "broken")
            return {
                "tool_id": tool_id, "healed": False, "attempts": attempts,
                "error": error or (result.stderr or result.stdout)[-2000:],
            }

        self.librarian.register_tool(tool_id, str(src_path), test_result=result)
        self.librarian.set_status(tool_id, "active")
        self.librarian.record_outcome(tool_id, True)
        reloaded = False
        try:
            self.registry.load_tool(Path(src_path), tool_id=tool_id)
            reloaded = True
        except Exception as e:
            logger.warning(f"Healed {tool_id} but reload failed: {e}")
        new = self.librarian.get_tool(tool_id) or {}
        logger.info(f"Healed {tool_id} in {attempts} attempt(s)")
        return {
            "tool_id": tool_id, "healed": True, "attempts": attempts,
            "old_version": old_version, "new_version": new.get("version"),
            "reloaded": reloaded,
        }

    def heal_broken(self, max_attempts: int = 3) -> Dict[str, Dict[str, Any]]:
        """Attempt to heal every broken tool."""
        return {
            t["id"]: self.heal(t["id"], max_attempts)
            for t in self.librarian.list_tools(status="broken")
        }

    def deprecate_duplicates(self, threshold: float = 0.97) -> List[str]:
        """Deprecate near-duplicates and unload them from the registry."""
        ids = self.librarian.deprecate_duplicates(threshold)
        for tid in ids:
            self.registry.unload_tool(tid)
        return ids

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def start_background(self, interval: float) -> bool:
        """Start the periodic regression thread. Returns False if not started."""
        if interval <= 0:
            return False
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.run_regression()
                except Exception:
                    logger.exception("Janitor regression run failed")

        self._thread = threading.Thread(target=loop, name="weave-janitor", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background thread (safe to call when not running)."""
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
