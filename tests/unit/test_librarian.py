"""Unit tests for LibrarianAgent lifecycle and search."""

import pytest

from src.agents.librarian import LibrarianAgent
from src.core.schemas import TaskNode
from src.memory.embeddings import HashEmbeddingGenerator
from src.memory.vector_store import VectorStore

ADD = '''
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b
'''
REV = '''
def reverse_string(text: str) -> str:
    """Reverse the characters of a string."""
    return text[::-1]
'''


@pytest.fixture
def lib(tmp_path):
    return LibrarianAgent(
        vector_store=VectorStore(str(tmp_path / "db")),
        similarity_threshold=0.7,
        high_confidence_threshold=0.8,
        embedding_generator=HashEmbeddingGenerator(),
    )


def _write(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    return str(p)


def _task(desc, props=None):
    schema = {"type": "object", "properties": {k: {} for k in props}} if props else {}
    return TaskNode(id="task1", description=desc, input_schema=schema)


def test_register_search_and_stats(lib, tmp_path):
    lib.register_tool("add", _write(tmp_path, "add.py", ADD))
    lib.register_tool("rev", _write(tmp_path, "rev.py", REV))
    m = lib.get_best_match(_task("Add two numbers together"))
    assert m and m.tool_id == "add" and m.similarity > 0.8
    assert lib.has_high_confidence_match(_task("Add two numbers together"))
    assert lib.get_best_match(_task("send an email to the user")) is None
    stats = lib.get_library_stats()
    assert stats["total_tools"] == 2 and stats["active_tools"] == 2


def test_search_excludes_non_active(lib, tmp_path):
    lib.register_tool("add", _write(tmp_path, "add.py", ADD))
    task = _task("Add two numbers together")
    for status in ("broken", "deprecated"):
        assert lib.set_status("add", status)
        assert lib.search_for_task(task) == []
    lib.set_status("add", "active")
    assert len(lib.search_for_task(task)) == 1
    assert lib.get_library_stats()["active_tools"] == 1


def test_set_status_validation(lib, tmp_path):
    lib.register_tool("add", _write(tmp_path, "add.py", ADD))
    with pytest.raises(ValueError):
        lib.set_status("add", "weird")
    assert lib.set_status("missing", "broken") is False


def test_record_outcome_and_get_tool(lib, tmp_path):
    lib.register_tool("add", _write(tmp_path, "add.py", ADD))
    assert lib.record_outcome("add", True)
    assert lib.record_outcome("add", True)
    assert lib.record_outcome("add", False)
    t = lib.get_tool("add")
    assert (t["usage_count"], t["success_count"], t["fail_count"]) == (3, 2, 1)
    assert t["success_rate"] == pytest.approx(2 / 3)
    assert t["input_schema"]["properties"]["a"] == {"type": "integer"}
    assert lib.record_outcome("nope", True) is False
    assert lib.get_tool("nope") is None


def test_list_tools_filter(lib, tmp_path):
    lib.register_tool("add", _write(tmp_path, "add.py", ADD))
    lib.register_tool("rev", _write(tmp_path, "rev.py", REV))
    lib.set_status("rev", "broken")
    assert {t["id"] for t in lib.list_tools()} == {"add", "rev"}
    assert [t["id"] for t in lib.list_tools("broken")] == ["rev"]
    assert [t["id"] for t in lib.list_tools(status="active")] == ["add"]


def test_verify_match(lib, tmp_path):
    path = _write(tmp_path, "add.py", ADD)
    lib.register_tool("add", path)
    task = _task("Add two numbers together", ["a", "b"])
    m = lib.search_text("Add two numbers together")[0]
    assert lib.verify_match(m, task)
    assert lib.verify_match(m, _task("x"))  # no schema -> ok
    # incompatible parameter names
    assert not lib.verify_match(m, _task("x", ["x", "y"]))
    # missing required param
    assert not lib.verify_match(m, _task("x", ["a"]))
    # hash mismatch after edit
    (tmp_path / "add.py").write_text(ADD + "\n# tampered\n", encoding="utf-8")
    assert not lib.verify_match(m, task)
    # file deleted
    (tmp_path / "add.py").unlink()
    assert not lib.verify_match(m, task)


def test_verify_match_kwargs_accepts_extra(lib, tmp_path):
    src = 'def f(a, **kw):\n    """Do f things."""\n    return a\n'
    lib.register_tool("f", _write(tmp_path, "f.py", src))
    m = lib.search_text("Do f things")[0]
    assert lib.verify_match(m, _task("x", ["a", "other"]))


def test_deprecate_duplicates_keeps_best(lib, tmp_path):
    lib.register_tool("add1", _write(tmp_path, "add1.py", ADD))
    lib.register_tool("add2", _write(tmp_path, "add2.py", ADD))
    lib.register_tool("rev", _write(tmp_path, "rev.py", REV))
    lib.record_outcome("add2", True)
    gone = lib.deprecate_duplicates(0.97)
    assert gone == ["add1"]
    assert lib.get_tool("add1")["status"] == "deprecated"
    assert lib.get_tool("add2")["status"] == "active"
    assert lib.get_tool("rev")["status"] == "active"
    assert lib.deprecate_duplicates(0.97) == []


def test_register_idempotent(lib, tmp_path):
    path = _write(tmp_path, "add.py", ADD)
    lib.register_tool("add", path)
    lib.register_tool("add", path)
    assert lib.vector_store.count() == 1
