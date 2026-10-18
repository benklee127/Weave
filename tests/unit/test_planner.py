"""Tests for PlannerAgent."""

import pytest

from src.agents.planner import PlannerAgent
from src.core.schemas import DecompositionRequest, TaskState


def req(n=5):
    return DecompositionRequest(user_request="do things", max_tasks=n, correlation_id="c")


def test_decompose_with_prose_and_nested(fake_llm):
    fake_llm.add_response(
        "do things",
        'Sure! Plan:\n```json\n[{"description": "A", "dependencies": [], '
        '"input_schema": {"properties": {"x": [1, 2]}}},'
        '{"description": "B", "dependencies": [0]}]\n```\nDone [ok].',
    )
    resp = PlannerAgent(fake_llm).decompose(req())
    a, b = resp.task_graph.nodes
    assert b.dependencies == [a.id]
    assert a.state == TaskState.PENDING
    assert fake_llm.calls[0].agent == "planner"


def test_tasks_wrapper_accepted(fake_llm):
    fake_llm.add_response("do things", '{"tasks": [{"description": "A"}]}')
    assert len(PlannerAgent(fake_llm).decompose(req()).task_graph.nodes) == 1


@pytest.mark.parametrize(
    "payload, msg",
    [
        ("no json at all", "extract"),
        ("[]", "extract"),
        ('[{"description": "A", "dependencies": [5]}]', "Invalid dependency"),
        ('[{"description": "A", "dependencies": ["x"]}]', "integer"),
        ('[{"description": "A", "dependencies": [0]}]', "cycle"),
        ('[{"description": "A", "dependencies": [1]}, {"description": "B", "dependencies": [0]}]', "cycle"),
        ('[{"foo": 1}]', "description"),
    ],
)
def test_invalid_plans(fake_llm, payload, msg):
    fake_llm.add_response("do things", payload)
    with pytest.raises(ValueError, match=msg):
        PlannerAgent(fake_llm).decompose(req())
