"""
Sandbox runner contract and helpers shared by all runner implementations.
"""

import re
from typing import Optional, Protocol, runtime_checkable

from src.core.schemas import TestExecutionResult

# Maximum number of characters of stdout/stderr kept in a result.
MAX_OUTPUT_CHARS = 100_000

# pytest exit codes that indicate a problem with the run itself rather than
# genuine test failures: 2 = interrupted / collection error, 3 = internal
# error, 4 = usage error, 5 = no tests collected.
INFRA_EXIT_CODES = {
    2: "interrupted or collection error (e.g. import failure)",
    3: "pytest internal error",
    4: "pytest usage error",
    5: "no tests collected",
}

INFRA_MARKER = "[sandbox] INFRA ERROR"


@runtime_checkable
class SandboxRunner(Protocol):
    """Anything able to execute a pytest file in isolation."""

    def run_test(
        self,
        test_file_path: str,
        task_id: str,
        implementation_path: Optional[str] = None,
    ) -> TestExecutionResult:
        ...


def parse_pytest_output(output: str) -> tuple[int, int]:
    """Extract (passed, failed) counts from pytest output."""
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    return (
        int(passed.group(1)) if passed else 0,
        int(failed.group(1)) if failed else 0,
    )


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cap output, keeping head and tail (pytest summaries live at the end)."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n...[{omitted} characters truncated]...\n{text[-half:]}"


def infra_note(exit_code: int) -> str:
    """Return a stderr note when ``exit_code`` signals an infrastructure problem."""
    reason = INFRA_EXIT_CODES.get(exit_code)
    if reason is None:
        return ""
    return f"{INFRA_MARKER}: pytest exit code {exit_code} - {reason}\n"


def is_infra_error(result: TestExecutionResult) -> bool:
    """True if the result reflects a sandbox/infra problem, not real test failures."""
    return INFRA_MARKER in (result.stderr or "")


def error_result(
    task_id: str, message: str, execution_time: float = 0.0, exit_code: int = -1
) -> TestExecutionResult:
    """Build a failed result for errors that happened outside pytest."""
    return TestExecutionResult(
        task_id=task_id,
        success=False,
        exit_code=exit_code,
        stdout="",
        stderr=message,
        tests_passed=0,
        tests_failed=0,
        execution_time=execution_time,
    )


def build_result(
    task_id: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    execution_time: float,
) -> TestExecutionResult:
    """Turn raw pytest process output into a TestExecutionResult."""
    stdout = truncate_output(stdout)
    stderr = truncate_output(stderr)
    note = infra_note(exit_code)
    if note:
        stderr = note + stderr
    passed, failed = parse_pytest_output(stdout)
    return TestExecutionResult(
        task_id=task_id,
        success=exit_code == 0,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        tests_passed=passed,
        tests_failed=failed,
        execution_time=execution_time,
    )


def pytest_args(test_path: str) -> list[str]:
    """Common pytest arguments (ignore project addopts such as coverage)."""
    return [
        test_path,
        "-p", "no:cacheprovider",
        "-o", "addopts=",
        "-v",
        "--tb=short",
        "--color=no",
    ]
