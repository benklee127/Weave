"""Tests for FeedbackStore."""

import pytest

from src.feedback.user_feedback import FeedbackStore


class FakeLibrarian:
    def __init__(self):
        self.outcomes = []

    def record_outcome(self, tool_id, success):
        self.outcomes.append((tool_id, success))
        return True


def test_record_and_read(tmp_path):
    store = FeedbackStore(tmp_path / "d" / "fb.jsonl")
    store.record("t1", 5, "great")
    store.record("t1", 3)
    store.record("t2", 1)
    assert len(store.all()) == 3
    assert [e["comment"] for e in store.all("t1")] == ["great", ""]
    assert store.summary("t1") == {"tool_id": "t1", "count": 2, "mean_rating": 4.0}
    assert store.summary("none")["mean_rating"] is None


def test_invalid_rating(tmp_path):
    store = FeedbackStore(tmp_path / "fb.jsonl")
    for bad in (0, 6, "5", True, 2.5):
        with pytest.raises(ValueError):
            store.record("t", bad)
    assert store.all() == []


def test_feeds_librarian(tmp_path):
    lib = FakeLibrarian()
    store = FeedbackStore(tmp_path / "fb.jsonl", librarian=lib)
    store.record("t", 5)
    store.record("t", 1)
    store.record("t", 3)  # neutral
    assert lib.outcomes == [("t", True), ("t", False)]


def test_flagged_tools(tmp_path):
    store = FeedbackStore(tmp_path / "fb.jsonl")
    store.record("bad", 1)
    store.record("bad", 2)
    store.record("ok", 5)
    store.record("mixed", 1)
    store.record("mixed", 5)
    assert store.flagged_tools() == ["bad"]


def test_min_ratings_and_corrupt_lines(tmp_path):
    path = tmp_path / "fb.jsonl"
    store = FeedbackStore(path, min_ratings=2)
    store.record("t", 1)
    assert store.flagged_tools() == []
    with path.open("a", encoding="utf-8") as f:
        f.write("not json\n\n")
    store.record("t", 1)
    assert len(store.all()) == 2
    assert store.flagged_tools() == ["t"]
