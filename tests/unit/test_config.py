"""Tests for src.config."""

import os

import pytest
from pydantic import ValidationError

from src.config import PROJECT_ROOT, Settings, get_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    names = {f.upper() for f in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in names:
            monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def make(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_defaults_need_no_api_key():
    s = make()
    assert s.anthropic_api_key is None
    assert s.sandbox_type == "subprocess"
    assert s.hot_reload is True
    assert s.regression_interval_seconds == 0
    assert s.reuse_similarity_threshold == 0.75
    assert s.reuse_high_confidence == 0.85
    assert s.auto_approve_tools is False


def test_relative_paths_resolve_to_project_root():
    s = make()
    for p in (s.vector_db_path, s.tool_directory, s.tests_directory, s.log_file):
        assert p.is_absolute()
    assert s.tool_directory == (PROJECT_ROOT / "tools").resolve()


def test_absolute_paths_untouched(tmp_path):
    s = make(tool_directory=tmp_path / "t")
    assert s.tool_directory == tmp_path / "t"


def test_env_override(monkeypatch):
    monkeypatch.setenv("SANDBOX_TYPE", "Docker")
    monkeypatch.setenv("SANDBOX_TIMEOUT", "42")
    monkeypatch.setenv("AUTO_APPROVE_TOOLS", "true")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = make()
    assert s.sandbox_type == "docker"
    assert s.sandbox_timeout == 42
    assert s.auto_approve_tools is True
    assert s.log_level == "DEBUG"


def test_invalid_sandbox_type_rejected():
    with pytest.raises(ValidationError):
        make(sandbox_type="firecracker")


def test_env_file_supported(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SANDBOX_MEMORY_LIMIT=1g\nANTHROPIC_API_KEY=abc\n", encoding="utf-8")
    s = Settings(_env_file=str(env))
    assert s.sandbox_memory_limit == "1g"
    assert s.anthropic_api_key == "abc"


def test_get_settings_cached_and_resettable(monkeypatch):
    a = get_settings()
    assert get_settings() is a
    monkeypatch.setenv("SANDBOX_TIMEOUT", "7")
    reset_settings_cache()
    assert get_settings() is not a
    assert get_settings().sandbox_timeout == 7


def test_env_example_matches_settings():
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert keys == {name.upper() for name in Settings.model_fields}
