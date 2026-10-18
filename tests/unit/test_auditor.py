"""Tests for AuditorAgent."""

import pytest

from src.agents.auditor import AuditorAgent
from src.core.schemas import TaskNode
from tests.fakes import SAMPLE_ADD_TESTS, SAMPLE_CRITIC_APPROVE


def test_generate_unique_names_and_cleaning(fake_llm, tmp_tests_dir):
    fake_llm.add_response("AUTOMATED TEST", "```python\n" + SAMPLE_ADD_TESTS + "\n```", agent=None)
    fake_llm.default = "```python\n" + SAMPLE_ADD_TESTS + "\n```"
    aud = AuditorAgent(fake_llm, tmp_tests_dir)
    t1 = TaskNode(id="task_1", description="Add two numbers")
    t2 = TaskNode(id="task_2", description="Add two numbers")
    m1 = aud.generate_test_suite(t1)
    m2 = aud.generate_test_suite(t2)
    assert m1.test_file_path != m2.test_file_path
    assert t1.test_suite_path == m1.test_file_path
    text = open(m1.test_file_path, encoding="utf-8").read()
    assert "```" not in text and m1.assertion_count >= 2
    assert m1.test_functions == ["test_add_basic", "test_add_negative"]
    assert "add_two_numbers" in m1.test_file_path and "task_1" in m1.test_file_path
    assert fake_llm.calls[0].agent == "auditor"
    assert len(list(tmp_tests_dir.glob("*.py"))) == 2


def test_dependency_context_in_prompt(fake_llm, tmp_tests_dir):
    fake_llm.default = SAMPLE_ADD_TESTS
    AuditorAgent(fake_llm, tmp_tests_dir).generate_test_suite(
        TaskNode(id="t", description="x"), dependency_context="def helper(a: int) -> int"
    )
    assert "def helper(a: int) -> int" in fake_llm.calls[0].prompt


def test_critic_parses_json_with_prose(fake_llm, tmp_tests_dir):
    fake_llm.default = "Analysis: {braces} here.\n" + SAMPLE_CRITIC_APPROVE + "\nthanks }"
    res = AuditorAgent(fake_llm, tmp_tests_dir).validate_test_quality(SAMPLE_ADD_TESTS)
    assert res.valid and res.recommendation == "APPROVE"


def test_critic_normalizes_and_defaults(fake_llm, tmp_tests_dir):
    fake_llm.default = '{"valid": false, "recommendation": "reject"}'
    res = AuditorAgent(fake_llm, tmp_tests_dir).validate_test_quality("x")
    assert res.recommendation == "REJECT" and not res.valid


def test_critic_garbage_raises(fake_llm, tmp_tests_dir):
    fake_llm.default = "nothing"
    with pytest.raises(ValueError):
        AuditorAgent(fake_llm, tmp_tests_dir).validate_test_quality("x")
