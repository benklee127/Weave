"""
Subprocess test runner.

Runs pytest in a child process of the current interpreter with a scrubbed
environment (no secrets), a timeout that kills the whole process tree and a
cap on captured output. This is host-level execution, not strong isolation;
use the Docker runner for untrusted code.
"""

import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from src.core.schemas import TestExecutionResult
from src.sandbox.base import (
    MAX_OUTPUT_CHARS,
    build_result,
    error_result,
    parse_pytest_output,
    pytest_args,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Only these variables are passed through to the child process.
ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL",
    "TZ", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "OS",
)


def build_env(extra_pythonpath: Optional[list[str]] = None) -> dict[str, str]:
    """Build a scrubbed environment for the child process (never contains secrets)."""
    allow = {k.upper() for k in ENV_ALLOWLIST}
    env = {k: v for k, v in os.environ.items() if k.upper() in allow}
    env["PYTHONPATH"] = os.pathsep.join(extra_pythonpath or [])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and all its descendants (best effort)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Process tree kill failed: {e}")
    try:
        proc.kill()
    except Exception:
        pass


def _read_capped(handle, limit: int) -> str:
    """Read a temp file, keeping only head and tail if it is huge."""
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    handle.seek(0)
    if size <= limit * 4:
        data = handle.read()
    else:
        head = handle.read(limit * 2)
        handle.seek(-limit * 2, os.SEEK_END)
        data = head + b"\n...[output truncated]...\n" + handle.read()
    return data.decode("utf-8", errors="replace")


class SubprocessRunner:
    """Execute pytest files in a child process with timeout and output caps."""

    def __init__(self, timeout: int = 300, max_output_chars: int = MAX_OUTPUT_CHARS):
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def run_test(
        self,
        test_file_path: str,
        task_id: str,
        implementation_path: Optional[str] = None,
    ) -> TestExecutionResult:
        """
        Execute a pytest file.

        Args:
            test_file_path: Path to the pytest file.
            task_id: Associated task ID.
            implementation_path: Implementation module; its directory is put on
                PYTHONPATH so the tests can import it by module name.
        """
        logger.info(f"Running tests: {test_file_path}")
        test_path = Path(test_file_path).resolve()
        if not test_path.is_file():
            return error_result(task_id, f"Test file not found: {test_file_path}")

        pythonpath: list[str] = []
        if implementation_path:
            impl = Path(implementation_path).resolve()
            if not impl.is_file():
                return error_result(
                    task_id, f"Implementation file not found: {implementation_path}"
                )
            pythonpath.append(str(impl.parent))
        pythonpath.append(str(PROJECT_ROOT))

        cmd = [sys.executable, "-m", "pytest", *pytest_args(str(test_path))]
        env = build_env(pythonpath)
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        start = time.time()
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    **popen_kwargs,
                )
                try:
                    proc.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    kill_process_tree(proc)
                    try:
                        proc.wait(timeout=10)
                    except Exception:  # pragma: no cover
                        pass
                    elapsed = time.time() - start
                    logger.error(f"Test execution timed out after {self.timeout}s")
                    return error_result(
                        task_id,
                        f"Test execution timed out after {self.timeout}s",
                        elapsed,
                    )
                stdout = _read_capped(out, self.max_output_chars)
                stderr = _read_capped(err, self.max_output_chars)
                rc = proc.returncode
        except Exception as e:
            logger.error(f"Test execution failed: {e}")
            return error_result(task_id, str(e), time.time() - start)

        elapsed = time.time() - start
        result = build_result(task_id, rc, stdout, stderr, elapsed)
        logger.info(
            f"Test execution complete: {'PASSED' if result.success else 'FAILED'} "
            f"({result.tests_passed} passed, {result.tests_failed} failed, {elapsed:.2f}s)"
        )
        return result

    def _parse_pytest_output(self, output: str) -> tuple[int, int]:
        """Backward-compatible wrapper around parse_pytest_output."""
        return parse_pytest_output(output)
