"""Tests for ArchitectAgent."""

from pathlib import Path

import pytest

from src.agents.architect import ArchitectAgent
from src.core.schemas import TaskNode, TestExecutionResult
from tests.fakes import SAMPLE_ADD_IMPL, SAMPLE_ADD_TESTS


def test_synthesize_unique_path_and_cleaning(fake_llm, tmp_tools_dir):
    fake_llm.default = "Here:\n```python\n" + SAMPLE_ADD_IMPL + "```"
    arch = ArchitectAgent(fake_llm, tmp_tools_dir)
    t1 = TaskNode(id="task_a", description="Add two integers")
    t2 = TaskNode(id="task_b", description="Add two integers")
    p1 = arch.synthesize_implementation(t1, SAMPLE_ADD_TESTS)
    p2 = arch.synthesize_implementation(t2, SAMPLE_ADD_TESTS)
    assert p1 != p2 and Path(p1).parent == tmp_tools_dir
    assert t1.implementation_path == p1
    code = Path(p1).read_text(encoding="utf-8")
    assert code.startswith("def add(") and "```" not in code
    assert Path(p1).name.startswith("add_two_integers_task_a_")
    assert fake_llm.calls[0].agent == "architect"


def test_dependency_context(fake_llm, tmp_tools_dir):
    fake_llm.default = SAMPLE_ADD_IMPL
    ArchitectAgent(fake_llm, tmp_tools_dir).synthesize_implementation(
        TaskNode(id="t", description="x"), "tests", dependency_context="def dep(a)"
    )
    assert "def dep(a)" in fake_llm.calls[0].prompt


@pytest.mark.parametrize("bad", ["def f(:\n  pass", "def f():\n    return eval('1')"])
def test_rejects_bad_code(fake_llm, tmp_tools_dir, bad):
    fake_llm.default = bad
    with pytest.raises(ValueError):
        ArchitectAgent(fake_llm, tmp_tools_dir).synthesize_implementation(
            TaskNode(id="t", description="x"), "tests"
        )
    assert list(tmp_tools_dir.iterdir()) == []


def test_refine_overwrites_same_file_and_caps_iterations(fake_llm, tmp_tools_dir):
    fake_llm.default = SAMPLE_ADD_IMPL
    arch = ArchitectAgent(fake_llm, tmp_tools_dir, max_refinement_iterations=2)
    task = TaskNode(id="t", description="Add numbers")
    path = arch.synthesize_implementation(task, "tests")
    fake_llm.default = SAMPLE_ADD_IMPL.replace("a + b", "b + a")
    res = TestExecutionResult(task_id="t", success=False, exit_code=1, stderr="AssertionError")
    assert arch.refine_implementation(task, "tests", res, iteration=1) == path
    assert "b + a" in Path(path).read_text(encoding="utf-8")
    assert "AssertionError" in fake_llm.calls[-1].prompt
    with pytest.raises(ValueError, match="Max refinement"):
        arch.refine_implementation(task, "tests", res, iteration=3)
