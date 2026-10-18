"""
Warden Agent: Runtime Execution and Security

This agent manages sandboxed execution of tests and validates results.
The actual isolation is delegated to a SandboxRunner (subprocess or Docker).
"""

import ast
import importlib.util
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

from src.config import get_settings
from src.core.schemas import TaskNode, TestExecutionResult
from src.sandbox.base import SandboxRunner, is_infra_error
from src.sandbox.factory import create_runner

logger = logging.getLogger(__name__)

_IGNORED_ROOTS = {"pytest", "unittest", "src"} | set(sys.stdlib_module_names)


class WardenAgent:
    """
    Agent responsible for executing tests in a secure sandbox.

    The Warden ensures that code execution is isolated and resource-limited.
    """

    def __init__(self, runner: Optional[SandboxRunner] = None, timeout: Optional[int] = None):
        """
        Initialize the Warden Agent.

        Args:
            runner: Sandbox runner; defaults to ``create_runner(get_settings())``.
            timeout: Deprecated convenience; when given (and no runner), a
                SubprocessRunner with this timeout is used.
        """
        self.agent_id = "warden"
        if runner is None:
            if timeout is not None:
                from src.sandbox.subprocess_runner import SubprocessRunner

                runner = SubprocessRunner(timeout=timeout)
            else:
                runner = create_runner(get_settings())
        self.runner = runner

    def execute_tests(self, task: TaskNode) -> TestExecutionResult:
        """
        Execute tests for a task.

        Raises:
            ValueError: If task doesn't have a test suite.
        """
        if not task.test_suite_path:
            raise ValueError(f"Task {task.id} has no test suite")

        logger.info(f"Executing tests for task: {task.id}")

        result = self.runner.run_test(
            test_file_path=task.test_suite_path,
            task_id=task.id,
            implementation_path=task.implementation_path,
        )

        if result.success:
            logger.info(
                f"Tests PASSED for {task.id}: "
                f"{result.tests_passed} tests in {result.execution_time:.2f}s"
            )
        else:
            logger.warning(
                f"Tests FAILED for {task.id}: "
                f"{result.tests_failed} failed, {result.tests_passed} passed"
            )
            logger.debug(f"Error output:\n{result.stderr}")

        return result

    def validate_test_failure(self, result: TestExecutionResult) -> bool:
        """
        Validate that tests actually fail (Red state in TDD).

        Returns:
            True if tests failed as expected, False otherwise.
        """
        if result.success:
            logger.warning(
                "Tests passed without implementation - possible vacuous tests"
            )
            return False

        if is_infra_error(result):
            logger.warning("Sandbox infrastructure error, cannot validate red state")
            return False

        if result.tests_failed == 0:
            logger.warning("No tests failed - possible vacuous tests")
            return False

        logger.debug(f"Tests correctly failed: {result.tests_failed} failures")
        return True

    def run_red_check(self, task: TaskNode) -> bool:
        """
        Run the task's tests against a stub implementation (every imported
        name raises NotImplementedError) to prove the tests are not vacuous.

        Returns:
            True if the tests genuinely fail against the stub.
        """
        if not task.test_suite_path or not Path(task.test_suite_path).is_file():
            logger.warning(f"Task {task.id} has no readable test suite for red check")
            return False

        modules = self._stub_modules(task)
        if not modules:
            logger.warning(f"Task {task.id}: could not determine names to stub")
            return False

        tmp = Path(tempfile.mkdtemp(prefix="weave_red_"))
        try:
            stub_paths = []
            for module, names in modules.items():
                stub = tmp / f"{module}.py"
                stub.write_text(self._render_stub(names), encoding="utf-8")
                stub_paths.append(stub)
            # Runners take a single implementation file; additional stubs
            # are only meaningful for the primary module.
            result = self.runner.run_test(
                test_file_path=task.test_suite_path,
                task_id=task.id,
                implementation_path=str(stub_paths[0]),
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        ok = self.validate_test_failure(result)
        logger.info(f"Red check for {task.id}: {'OK (tests fail on stub)' if ok else 'NOT OK'}")
        return ok

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _render_stub(names: list[str]) -> str:
        lines = ['"""Auto-generated stub for the red check."""', ""]
        for name in names:
            lines += [
                f"def {name}(*args, **kwargs):",
                '    raise NotImplementedError("stub implementation")',
                "",
            ]
        return "\n".join(lines)

    def _stub_modules(self, task: TaskNode) -> dict[str, list[str]]:
        """Map module name -> imported names to stub, ordered primary-first."""
        try:
            tree = ast.parse(Path(task.test_suite_path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as e:
            logger.warning(f"Cannot parse tests for red check: {e}")
            return {}

        primary = Path(task.implementation_path).stem if task.implementation_path else None
        found: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            root = node.module.split(".")[0]
            if "." in node.module:
                continue
            if node.module != primary:
                if root in _IGNORED_ROOTS:
                    continue
                try:
                    if importlib.util.find_spec(root) is not None:
                        continue
                except (ImportError, ValueError):
                    pass
            names = [a.name for a in node.names if a.name != "*"]
            found.setdefault(node.module, [])
            found[node.module] += [n for n in names if n not in found[node.module]]

        if primary and primary in found:
            found = {primary: found.pop(primary), **found}
        return found
