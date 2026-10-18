"""Factory selecting a sandbox runner from Settings."""

from typing import Optional

from src.config import Settings, get_settings
from src.sandbox.base import SandboxRunner


def create_runner(settings: Optional[Settings] = None) -> SandboxRunner:
    """Create the runner configured by ``settings.sandbox_type``."""
    settings = settings or get_settings()
    if settings.sandbox_type == "docker":
        from src.sandbox.docker_runner import DockerRunner

        return DockerRunner(
            image=settings.sandbox_docker_image,
            timeout=settings.sandbox_timeout,
            memory_limit=settings.sandbox_memory_limit,
            cpu_quota=settings.sandbox_cpu_quota,
        )
    if settings.sandbox_type == "subprocess":
        from src.sandbox.subprocess_runner import SubprocessRunner

        return SubprocessRunner(timeout=settings.sandbox_timeout)
    raise ValueError(f"Unknown sandbox_type: {settings.sandbox_type!r}")
