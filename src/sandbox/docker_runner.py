"""
Docker-based test runner.

Runs pytest in a throwaway container: no network, read-only root filesystem,
memory / CPU / pids limits, all capabilities dropped, non-root user, and only
the test file (and implementation) bind-mounted read-only. Images are never
pulled automatically; the configured image must already exist locally and
contain pytest.
"""

import logging
import time
from pathlib import Path
from typing import Any, Optional

from src.core.schemas import TestExecutionResult
from src.sandbox.base import INFRA_MARKER, build_result, error_result, pytest_args

logger = logging.getLogger(__name__)

CONTAINER_TESTS_DIR = "/work/tests"
CONTAINER_IMPL_DIR = "/work/impl"
CPU_PERIOD = 100_000


class DockerUnavailableError(RuntimeError):
    """Raised when the Docker daemon / SDK cannot be used."""


def _host_path(path: Path) -> str:
    """Host path in a form the Docker daemon accepts (forward slashes)."""
    return str(path.resolve()).replace("\\", "/")


def _decode(data: Any) -> str:
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


class DockerRunner:
    """Execute pytest files inside an isolated Docker container."""

    def __init__(
        self,
        image: str = "python:3.11-slim",
        timeout: int = 300,
        memory_limit: str = "512m",
        cpu_quota: int = 50000,
        pids_limit: int = 128,
        user: str = "65534:65534",
        client: Any = None,
    ):
        self.image = image
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.cpu_quota = cpu_quota
        self.pids_limit = pids_limit
        self.user = user
        self._client = client

    # -- docker access -----------------------------------------------------
    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import docker  # type: ignore
            except ImportError as e:
                raise DockerUnavailableError(
                    "The 'docker' Python package is not installed (pip install docker)"
                ) from e
            try:
                client = docker.from_env()
                client.ping()
            except Exception as e:
                raise DockerUnavailableError(f"Docker daemon unreachable: {e}") from e
            self._client = client
        return self._client

    def is_available(self) -> bool:
        """True if the Docker daemon is reachable."""
        try:
            self._get_client().ping()
            return True
        except Exception:
            return False

    # -- command construction ---------------------------------------------
    def build_command(self, test_name: str) -> list[str]:
        return ["python", "-m", "pytest", *pytest_args(f"{CONTAINER_TESTS_DIR}/{test_name}")]

    def build_run_kwargs(
        self, test_path: Path, implementation_path: Optional[Path]
    ) -> dict:
        """Keyword arguments for ``client.containers.run``."""
        volumes = {
            _host_path(test_path): {
                "bind": f"{CONTAINER_TESTS_DIR}/{test_path.name}",
                "mode": "ro",
            }
        }
        if implementation_path is not None:
            volumes[_host_path(implementation_path)] = {
                "bind": f"{CONTAINER_IMPL_DIR}/{implementation_path.name}",
                "mode": "ro",
            }
        return {
            "image": self.image,
            "command": self.build_command(test_path.name),
            "detach": True,
            "network_mode": "none",
            "read_only": True,
            "mem_limit": self.memory_limit,
            "memswap_limit": self.memory_limit,
            "cpu_period": CPU_PERIOD,
            "cpu_quota": self.cpu_quota,
            "pids_limit": self.pids_limit,
            "user": self.user,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
            "volumes": volumes,
            "working_dir": "/work",
            "environment": {
                "PYTHONPATH": CONTAINER_IMPL_DIR,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            },
        }

    # -- execution ---------------------------------------------------------
    def run_test(
        self,
        test_file_path: str,
        task_id: str,
        implementation_path: Optional[str] = None,
    ) -> TestExecutionResult:
        """Run a pytest file in a container and return the result."""
        test_path = Path(test_file_path).resolve()
        if not test_path.is_file():
            return error_result(task_id, f"Test file not found: {test_file_path}")
        impl: Optional[Path] = None
        if implementation_path:
            impl = Path(implementation_path).resolve()
            if not impl.is_file():
                return error_result(
                    task_id, f"Implementation file not found: {implementation_path}"
                )

        try:
            client = self._get_client()
        except DockerUnavailableError as e:
            return error_result(task_id, f"{INFRA_MARKER}: {e}")

        start = time.time()
        container = None
        try:
            container = client.containers.run(**self.build_run_kwargs(test_path, impl))
            try:
                wait = container.wait(timeout=self.timeout)
            except Exception as e:
                try:
                    container.kill()
                except Exception:
                    pass
                logger.error(f"Container run timed out / failed: {e}")
                return error_result(
                    task_id,
                    f"Test execution timed out after {self.timeout}s",
                    time.time() - start,
                )
            rc = int(wait.get("StatusCode", -1)) if isinstance(wait, dict) else int(wait)
            stdout = _decode(container.logs(stdout=True, stderr=False))
            stderr = _decode(container.logs(stdout=False, stderr=True))
            return build_result(task_id, rc, stdout, stderr, time.time() - start)
        except Exception as e:
            hint = ""
            if type(e).__name__ == "ImageNotFound":
                hint = (
                    f" (image '{self.image}' not present locally; build or pull it "
                    "yourself - it must contain pytest)"
                )
            logger.error(f"Docker execution failed: {e}")
            return error_result(
                task_id,
                f"{INFRA_MARKER}: docker run failed: {e}{hint}",
                time.time() - start,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
