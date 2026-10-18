"""Tests for the Janitor (regression, healing, dedupe, scheduler)."""

import time

import pytest
from fastmcp import FastMCP

from src.agents.architect import ArchitectAgent
from src.agents.librarian import LibrarianAgent
from src.feedback.janitor import Janitor
from src.memory.embeddings import HashEmbeddingGenerator
from src.registry.tool_registry import ToolRegistry
from src.sandbox.subprocess_runner import SubprocessRunner

GOOD = "def add_two(a: int, b: int) -> int:\n    \"\"\"Add two integers.\"\"\"\n    return a + b\n"
BAD = "def add_two(a: int, b: int) -> int:\n    \"\"\"Add two integers.\"\"\"\n    return a - b\n"
TESTS = (
    "from add_two import add_two\n\n"
    "def test_add():\n    assert add_two(1, 2) == 3\n\n"
    "def test_add_zero():\n    assert add_two(0, 5) == 5\n"
)


@pytest.fixture
def env(tmp_settings, tmp_tools_dir, tmp_tests_dir, tmp_vector_store, fake_llm):
    librarian = LibrarianAgent(
        vector_store=tmp_vector_store,
        settings=tmp_settings,
        embedding_generator=HashEmbeddingGenerator(),
    )
    registry = ToolRegistry(FastMCP("t"), tmp_tools_dir)
    architect = ArchitectAgent(fake_llm, tool_output_dir=tmp_tools_dir)
    janitor = Janitor(
        SubprocessRunner(timeout=60), librarian, registry, architect,
        llm=fake_llm, settings=tmp_settings,
    )
    impl = tmp_tools_dir / "add_two.py"
    impl.write_text(GOOD, encoding="utf-8")
    test = tmp_tests_dir / "test_add_two_t1_abcd.py"
    test.write_text(TESTS, encoding="utf-8")
    librarian.register_tool("add_two", str(impl))
    registry.load_tool(impl, tool_id="add_two")
    janitor.register_tests("add_two", str(test))
    return janitor, librarian, registry, impl, fake_llm


def test_regression_passes_healthy_tool(env):
    janitor, librarian, registry, *_ = env
    report = janitor.run_regression()
    assert report["passed"] == ["add_two"] and not report["broken"]
    assert librarian.get_tool("add_two")["success_count"] == 1
    assert registry.list_loaded()


def test_regression_marks_broken_and_unloads(env):
    janitor, librarian, registry, impl, _ = env
    impl.write_text(BAD, encoding="utf-8")
    report = janitor.run_regression()
    assert report["broken"] == ["add_two"]
    assert librarian.get_tool("add_two")["status"] == "broken"
    assert librarian.get_tool("add_two")["fail_count"] == 1
    assert registry.list_loaded() == []
    # broken tools are no longer regression-tested
    assert janitor.run_regression()["checked"] == 0


def test_regression_skips_tool_without_tests(env, tmp_settings):
    janitor, librarian, *_ = env
    janitor._test_paths.clear()
    librarian.vector_store.update_metadata("add_two", {"test_path": ""})
    report = janitor.run_regression()
    # Falls back to tests_directory glob (test_add_two_*.py) so it is found.
    assert report["passed"] == ["add_two"]


def test_regression_skips_when_no_tests_anywhere(env):
    janitor, librarian, *_ = env
    janitor._test_paths.clear()
    janitor.settings = None
    librarian.vector_store.update_metadata("add_two", {"test_path": ""})
    report = janitor.run_regression()
    assert report["skipped"] == ["add_two"]
    assert librarian.get_tool("add_two")["status"] == "active"


def test_heal_restores_tool_with_bumped_version(env):
    janitor, librarian, registry, impl, llm = env
    impl.write_text(BAD, encoding="utf-8")
    janitor.run_regression()
    llm.add_response("debugging code", GOOD, agent="architect")
    result = janitor.heal("add_two")
    assert result["healed"] is True and result["attempts"] == 1
    assert result["old_version"] == "1.0.0" and result["new_version"] == "1.0.1"
    assert librarian.get_tool("add_two")["status"] == "active"
    assert impl.read_text(encoding="utf-8").strip() == GOOD.strip()
    assert registry.call("add_two", a=2, b=3) == 5


def test_heal_failure_restores_source_and_stays_broken(env):
    janitor, librarian, registry, impl, llm = env
    impl.write_text(BAD, encoding="utf-8")
    janitor.run_regression()
    llm.add_response("debugging code", BAD, agent="architect")
    result = janitor.heal("add_two", max_attempts=2)
    assert result["healed"] is False and result["attempts"] == 2
    assert librarian.get_tool("add_two")["status"] == "broken"
    assert impl.read_text(encoding="utf-8") == BAD
    assert registry.list_loaded() == []


def test_heal_unknown_tool(env):
    janitor, *_ = env
    assert janitor.heal("nope")["healed"] is False


def test_heal_broken_all(env):
    janitor, _, _, impl, llm = env
    impl.write_text(BAD, encoding="utf-8")
    janitor.run_regression()
    llm.add_response("debugging code", GOOD, agent="architect")
    assert janitor.heal_broken()["add_two"]["healed"] is True


def test_deprecate_duplicates_unloads(env, tmp_tools_dir):
    janitor, librarian, registry, impl, _ = env
    dup = tmp_tools_dir / "add_two_copy.py"
    dup.write_text(GOOD.replace("add_two", "add_two_copy"), encoding="utf-8")
    librarian.register_tool("add_two_copy", str(dup))
    # Force identical embeddings so they count as duplicates.
    emb = librarian.vector_store.get_tool("add_two")["embedding"]
    meta = librarian.vector_store.get_tool("add_two_copy")["metadata"]
    librarian.vector_store.upsert("add_two_copy", emb, meta)
    registry.load_tool(dup, tool_id="add_two_copy")
    librarian.record_outcome("add_two", True)
    ids = janitor.deprecate_duplicates(threshold=0.97)
    assert ids == ["add_two_copy"]
    assert librarian.get_tool("add_two_copy")["status"] == "deprecated"
    assert [t["tool_id"] for t in registry.list_loaded()] == ["add_two"]


def test_background_scheduler_runs_and_stops(env):
    janitor, *_ = env
    calls = []
    janitor.run_regression = lambda *a, **k: calls.append(1) or {}
    assert janitor.start_background(0.05) is True
    assert janitor.start_background(0.05) is False
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    assert calls and janitor.running
    janitor.stop()
    assert not janitor.running
    n = len(calls)
    time.sleep(0.2)
    assert len(calls) == n
    janitor.stop()  # idempotent


def test_scheduler_disabled_for_zero_interval(env):
    janitor, *_ = env
    assert janitor.start_background(0) is False
    assert not janitor.running
