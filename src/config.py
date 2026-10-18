"""
Central configuration for Weave.

Settings are loaded from environment variables and an optional ``.env`` file
in the project root. Importing this module has no side effects and does not
require an API key.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_PATH_FIELDS = ("vector_db_path", "tool_directory", "tests_directory", "log_file")


class Settings(BaseSettings):
    """Application settings (environment variables are the uppercase field names)."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-5-20250929"

    # Vector database / embeddings
    vector_db_path: Path = Path("data/vector_db")
    embedding_model: str = "all-MiniLM-L6-v2"

    # Sandbox
    sandbox_type: Literal["subprocess", "docker"] = "subprocess"
    sandbox_timeout: int = Field(default=300, gt=0)
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_quota: int = 50000
    sandbox_docker_image: str = "python:3.11-slim"

    # Tool registry
    tool_directory: Path = Path("tools")
    tests_directory: Path = Path("tests/generated")
    auto_approve_tools: bool = False
    hot_reload: bool = True
    regression_interval_seconds: int = Field(default=0, ge=0)  # 0 = off

    # Reuse thresholds
    reuse_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    reuse_high_confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    # Logging
    log_level: str = "INFO"
    log_file: Path = Path("data/logs/orchestrator.log")

    @field_validator("sandbox_type", mode="before")
    @classmethod
    def _normalize_sandbox_type(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator(*_PATH_FIELDS, mode="after")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        """Resolve relative paths against the project root."""
        return v if v.is_absolute() else (PROJECT_ROOT / v).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (for tests)."""
    get_settings.cache_clear()
