"""
Subprocess test runner for Phase 1.

This module executes pytest tests in a subprocess with timeout enforcement.
For production (Phase 4), this will be replaced with Docker/Firecracker.
"""

import subprocess
import logging
import time
import re
from pathlib import Path
from typing import Optional

from src.core.schemas import TestExecutionResult

logger = logging.getLogger(__name__)


class SubprocessRunner:
    """
    Execute tests in a subprocess with resource limits.

    This is the Phase 1 implementation. Phase 4 will add Docker isolation.
    """

    def __init__(self, timeout: int = 300):
        """
        Initialize the subprocess runner.

        Args:
            timeout: Maximum execution time in seconds
        """
        self.timeout = timeout

    def run_test(
        self,
        test_file_path: str,
        task_id: str,
        implementation_path: Optional[str] = None,
    ) -> TestExecutionResult:
        """
        Execute a pytest test file.

        Args:
            test_file_path: Path to pytest file
            task_id: Associated task ID
            implementation_path: Path to implementation (if available)

        Returns:
            TestExecutionResult with execution details
        """
        logger.info(f"Running tests: {test_file_path}")

        test_path = Path(test_file_path)
        if not test_path.exists():
            return TestExecutionResult(
                task_id=task_id,
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Test file not found: {test_file_path}",
                tests_passed=0,
                tests_failed=0,
                execution_time=0.0,
            )

        start_time = time.time()

        try:
            # If implementation exists, temporarily inject it into sys.path
            # This allows the tests to import the function
            cmd = ["pytest", str(test_path), "-v", "--tb=short"]

            logger.debug(f"Executing: {' '.join(cmd)}")

            # Run pytest
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(test_path.parent.parent),  # Run from project root
            )

            execution_time = time.time() - start_time

            # Parse pytest output
            tests_passed, tests_failed = self._parse_pytest_output(result.stdout)

            # Determine success
            success = result.returncode == 0

            logger.info(
                f"Test execution complete: "
                f"{'PASSED' if success else 'FAILED'} "
                f"({tests_passed} passed, {tests_failed} failed, "
                f"{execution_time:.2f}s)"
            )

            return TestExecutionResult(
                task_id=task_id,
                success=success,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
                execution_time=execution_time,
            )

        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            logger.error(f"Test execution timed out after {self.timeout}s")

            return TestExecutionResult(
                task_id=task_id,
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Test execution timed out after {self.timeout}s",
                tests_passed=0,
                tests_failed=0,
                execution_time=execution_time,
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Test execution failed: {e}")

            return TestExecutionResult(
                task_id=task_id,
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                tests_passed=0,
                tests_failed=0,
                execution_time=execution_time,
            )

    def _parse_pytest_output(self, output: str) -> tuple[int, int]:
        """
        Parse pytest output to extract test counts.

        Args:
            output: pytest stdout

        Returns:
            Tuple of (passed_count, failed_count)
        """
        # Look for patterns like "5 passed", "2 failed"
        passed_match = re.search(r'(\d+) passed', output)
        failed_match = re.search(r'(\d+) failed', output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0

        return passed, failed
