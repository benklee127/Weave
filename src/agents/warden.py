"""
Warden Agent: Runtime Execution and Security

This agent manages sandboxed execution of tests and validates results.
Phase 1 uses subprocess, Phase 4 will add Docker/Firecracker.
"""

import logging
from typing import Optional

from src.core.schemas import TaskNode, TestExecutionResult
from src.sandbox.subprocess_runner import SubprocessRunner

logger = logging.getLogger(__name__)


class WardenAgent:
    """
    Agent responsible for executing tests in a secure sandbox.

    The Warden ensures that code execution is isolated and resource-limited.
    """

    def __init__(self, timeout: int = 300):
        """
        Initialize the Warden Agent.

        Args:
            timeout: Maximum execution time in seconds
        """
        self.agent_id = "warden"
        self.runner = SubprocessRunner(timeout=timeout)

    def execute_tests(self, task: TaskNode) -> TestExecutionResult:
        """
        Execute tests for a task.

        Args:
            task: TaskNode with test_suite_path and implementation_path

        Returns:
            TestExecutionResult with execution details

        Raises:
            ValueError: If task doesn't have required paths
        """
        if not task.test_suite_path:
            raise ValueError(f"Task {task.id} has no test suite")

        logger.info(f"Executing tests for task: {task.id}")

        # Run the tests
        result = self.runner.run_test(
            test_file_path=task.test_suite_path,
            task_id=task.id,
            implementation_path=task.implementation_path,
        )

        # Log result
        if result.success:
            logger.info(
                f"✓ Tests PASSED for {task.id}: "
                f"{result.tests_passed} tests in {result.execution_time:.2f}s"
            )
        else:
            logger.warning(
                f"✗ Tests FAILED for {task.id}: "
                f"{result.tests_failed} failed, {result.tests_passed} passed"
            )
            logger.debug(f"Error output:\n{result.stderr}")

        return result

    def validate_test_failure(self, result: TestExecutionResult) -> bool:
        """
        Validate that tests actually fail (Red state in TDD).

        This ensures tests aren't vacuous (always passing).

        Args:
            result: TestExecutionResult from running tests

        Returns:
            True if tests failed as expected, False otherwise
        """
        # Tests should fail when run against empty/stub implementation
        if result.success:
            logger.warning(
                "Tests passed without implementation - possible vacuous tests"
            )
            return False

        if result.tests_failed == 0:
            logger.warning("No tests failed - possible vacuous tests")
            return False

        logger.debug(f"Tests correctly failed: {result.tests_failed} failures")
        return True
