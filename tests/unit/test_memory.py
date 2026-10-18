"""Unit tests for embeddings, vector store and tool indexer."""

import pytest

from src.core.schemas import TestExecutionResult as ExecResult
from src.memory.embeddings import (
    HashEmbeddingGenerator, get_embedding_generator,
)
from src.memory.tool_index import ToolIndexer, bump_patch_version
from src.memory.vector_store import VectorStore

SRC_V1 = '''
def _helper(x):
    return x

def add_numbers(a: int, b: int = 2, *, tags: list[str] | None = None) -> int:
    """Add two numbers together.

    More detail here.
    """
    return _helper(a) + b
'''


@pytest.fixture
def store(tmp_path):
    return VectorStore(str(tmp_path / "db"))


@pytest.fixture
def indexer(store):
    return ToolIndexer(store, embedding_generator=HashEmbeddingGenerator())


def _ok(tid="t"):
    return ExecResult(task_id=tid, success=True, exit_code=0)


def test_hash_embedding_deterministic_and_normalized():
    g = HashEmbeddingGenerator()
    a = g.generate_embedding("Add two numbers")
    assert a == g.generate_embedding("Add two numbers")
    assert len(a) == g.embedding_dim
    assert g.similarity(a, a) == pytest.approx(1.0)
    assert g.similarity(a, g.generate_embedding("reverse a string")) < 0.5
    assert g.generate_embedding("")  # no crash on empty


def test_get_embedding_generator_hash_cached():
    assert isinstance(get_embedding_generator("hash"), HashEmbeddingGenerator)
    assert get_embedding_generator("hash") is get_embedding_generator("hash")


def test_vector_store_upsert_is_idempotent(store):
    emb = HashEmbeddingGenerator().generate_embedding("x")
    store.upsert("a", emb, {"description": "one", "status": "active"})
    store.upsert("a", emb, {"description": "two", "status": "active"})
    assert store.count() == 1
    assert store.get_tool("a")["metadata"]["description"] == "two"


def test_update_metadata_merges_and_missing(store):
    emb = HashEmbeddingGenerator().generate_embedding("x")
    store.upsert("a", emb, {"description": "d", "status": "active", "n": 1})
    assert store.update_metadata("a", {"status": "broken"})
    meta = store.get_tool("a")["metadata"]
    assert meta["status"] == "broken" and meta["n"] == 1
    assert not store.update_metadata("zzz", {"x": 1})


def test_search_empty_store(store):
    r = store.search(HashEmbeddingGenerator().generate_embedding("x"), 3)
    assert r["ids"] == []


def test_persistence_across_instances(tmp_path):
    emb = HashEmbeddingGenerator().generate_embedding("x")
    s1 = VectorStore(str(tmp_path / "p"))
    s1.upsert("a", emb, {"description": "d"})
    s2 = VectorStore(str(tmp_path / "p"))
    assert s2.count() == 1


def test_index_tool_metadata_and_schema(indexer, tmp_path):
    f = tmp_path / "add.py"
    f.write_text(SRC_V1, encoding="utf-8")
    md = indexer.index_tool("t1", str(f), _ok())
    assert md.name == "add_numbers"  # skips private helper
    assert md.version == "1.0.0" and md.status == "active"
    assert md.input_schema["properties"]["a"] == {"type": "integer"}
    assert md.input_schema["required"] == ["a"]
    assert md.input_schema["properties"]["tags"]["type"] == "array"
    assert md.output_schema == {"type": "integer"}
    stored = indexer.vector_store.get_tool("t1")["metadata"]
    assert stored["function_name"] == "add_numbers"
    assert stored["module_name"] == "add"
    assert len(stored["source_hash"]) == 64
    assert stored["usage_count"] == 0 and stored["last_verified_at"]


def test_index_idempotent_and_version_bump(indexer, tmp_path):
    f = tmp_path / "add.py"
    f.write_text(SRC_V1, encoding="utf-8")
    indexer.index_tool("t1", str(f))
    first = indexer.vector_store.get_tool("t1")["metadata"]
    indexer.index_tool("t1", str(f))
    again = indexer.vector_store.get_tool("t1")["metadata"]
    assert indexer.vector_store.count() == 1
    assert again["version"] == "1.0.0"
    assert again["registered_at"] == first["registered_at"]
    f.write_text(SRC_V1 + "\n# change\n", encoding="utf-8")
    md = indexer.index_tool("t1", str(f))
    assert md.version == "1.0.1"


def test_failed_result_marks_broken_and_bad_source(indexer, tmp_path):
    f = tmp_path / "add.py"
    f.write_text(SRC_V1, encoding="utf-8")
    bad = ExecResult(task_id="t", success=False, exit_code=1)
    assert indexer.index_tool("t1", str(f), bad).status == "broken"
    g = tmp_path / "bad.py"
    g.write_text("def (:", encoding="utf-8")
    with pytest.raises(ValueError):
        indexer.index_tool("t2", str(g))
    with pytest.raises(ValueError):
        indexer.index_tool("t3", str(tmp_path / "nope.py"))


def test_bump_patch_version():
    assert bump_patch_version("1.2.9") == "1.2.10"
    assert bump_patch_version("garbage") == "1.0.1"
