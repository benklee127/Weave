"""
Shared pytest fixtures for Weave.

Fixture names are a stable contract used by every test module:
``fake_llm``, ``tmp_settings``, ``tmp_tools_dir``, ``tmp_tests_dir``,
``tmp_vector_store``.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fakes import FakeLLMClient  # noqa: E402


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    """A scripted LLM client (no network, no API key)."""
    return FakeLLMClient()


@pytest.fixture
def tmp_tools_dir(tmp_path: Path) -> Path:
    """Empty directory for generated tool implementations."""
    d = tmp_path / "tools"
    d.mkdir()
    return d


@pytest.fixture
def tmp_tests_dir(tmp_path: Path) -> Path:
    """Empty directory for generated tests."""
    d = tmp_path / "generated_tests"
    d.mkdir()
    return d


@pytest.fixture
def tmp_settings(tmp_path: Path, tmp_tools_dir: Path, tmp_tests_dir: Path):
    """
    Settings with every path inside ``tmp_path``, no API key, subprocess
    sandbox, auto-approval on, hot reload off, and no .env influence.
    """
    overrides = dict(
        anthropic_api_key=None,
        vector_db_path=tmp_path / "vector_db",
        tool_directory=tmp_tools_dir,
        tests_directory=tmp_tests_dir,
        log_file=tmp_path / "logs" / "test.log",
        sandbox_type="subprocess",
        auto_approve_tools=True,
        hot_reload=False,
        regression_interval_seconds=0,
    )
    try:
        from src.config import Settings, reset_settings_cache
    except ImportError:  # config module not available yet
        yield SimpleNamespace(
            sandbox_timeout=60,
            reuse_similarity_threshold=0.75,
            reuse_high_confidence=0.85,
            anthropic_model="test-model",
            embedding_model="hash",
            **overrides,
        )
        return
    reset_settings_cache()
    yield Settings(_env_file=None, **overrides)
    reset_settings_cache()


@pytest.fixture
def tmp_vector_store(tmp_settings):
    """A VectorStore persisted under tmp_path."""
    from src.memory.vector_store import VectorStore

    return VectorStore(persist_directory=str(tmp_settings.vector_db_path))
