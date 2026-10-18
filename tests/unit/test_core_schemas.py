"""Tests for core schemas, graph helpers, message bus and LLM client."""

import asyncio
from types import SimpleNamespace

import pytest

from src.core.message_bus import MessageBus
from src.core.schemas import AgentMessage, TaskGraph, TaskNode, TaskState, ToolStatus


def make_graph(edges):
    """edges: {id: [deps]}"""
    g = TaskGraph()
    for tid, deps in edges.items():
        g.add_node(TaskNode(id=tid, description=tid, dependencies=deps))
    return g


def test_new_fields_have_defaults():
    n = TaskNode(id="a", description="d")
    assert n.reused_tool_id is None and n.attempts == 0
    assert ToolStatus.ACTIVE.value == "active"
    assert {s.value for s in ToolStatus} == {"active", "broken", "deprecated"}


def test_topological_order():
    g = make_graph({"c": ["a", "b"], "b": ["a"], "a": []})
    order = [n.id for n in g.topological_order()]
    assert order.index("a") < order.index("b") < order.index("c")


def test_cycle_detection():
    g = make_graph({"a": ["b"], "b": ["a"], "c": []})
    assert g.has_cycle()
    with pytest.raises(ValueError, match="cycle"):
        g.topological_order()
    assert not make_graph({"a": [], "b": ["a"]}).has_cycle()


def test_unknown_dependency_rejected():
    with pytest.raises(ValueError, match="unknown"):
        make_graph({"a": ["zzz"]}).validate_acyclic()


def test_propagate_failure_transitive_only():
    g = make_graph({"a": [], "b": ["a"], "c": ["b"], "d": [], "e": ["d"]})
    g.get_node("a").update_state(TaskState.FAILED, "boom")
    affected = g.propagate_failure("a")
    assert set(affected) == {"b", "c"}
    assert g.get_node("c").state == TaskState.FAILED
    assert "a" in g.get_node("c").error_trace
    assert g.get_node("d").state == TaskState.PENDING
    assert g.get_node("e").state == TaskState.PENDING
    assert g.propagate_failure("a") == []


def test_propagate_keeps_verified():
    g = make_graph({"a": [], "b": ["a"]})
    g.get_node("b").update_state(TaskState.VERIFIED)
    assert g.propagate_failure("a") == []
    assert g.get_node("b").state == TaskState.VERIFIED


def test_message_bus_imports_and_history():
    bus = MessageBus()
    got = []
    bus.subscribe("evt", lambda m: got.append(m.sender))
    msg = AgentMessage(event_type="evt", sender="x", correlation_id="c1")
    asyncio.run(bus.publish(msg))
    assert got == ["x"]
    assert bus.get_history(event_type="evt", correlation_id="c1") == [msg]


# --- LLM client --------------------------------------------------------------

from src.utils import llm_client as lc  # noqa: E402


def _resp(text, i=10, o=5):
    blocks = [SimpleNamespace(text=text)] if text is not None else []
    return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=i, output_tokens=o))


class _Msgs:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        return self.responses.pop(0)


def _client(responses, **kw):
    c = lc.LLMClient(api_key="k", **kw)
    c.client = SimpleNamespace(messages=_Msgs(responses))
    return c


def test_import_safe_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = lc.LLMClient()
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        c.client  # noqa: B018


def test_protocol_satisfied(fake_llm):
    assert isinstance(fake_llm, lc.LLMClientProtocol)
    assert isinstance(lc.LLMClient(api_key="k"), lc.LLMClientProtocol)


def test_usage_by_agent_and_cost():
    c = _client([_resp("a", 1000, 100), _resp("b", 500, 50), _resp("c", 10, 10)])
    c.generate("p", agent="planner")
    c.generate("p", agent="planner")
    c.generate("p", agent="auditor")
    u = c.get_usage_by_agent()
    assert u["planner"]["calls"] == 2 and u["planner"]["input_tokens"] == 1500
    assert u["auditor"]["output_tokens"] == 10
    assert c.get_token_usage()["total_tokens"] == 1500 + 150 + 20
    assert c.estimated_cost() == pytest.approx(
        sum(v["cost_usd"] for v in u.values())
    )
    c.reset_token_usage()
    assert c.get_usage_by_agent() == {}


@pytest.mark.parametrize("empty", [_resp(None), _resp(""), _resp("   ")])
def test_empty_response_raises(empty):
    c = _client([empty])
    with pytest.raises(lc.LLMEmptyResponseError):
        c.generate("p", agent="x")


def test_multiple_text_blocks_joined():
    r = SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(text="a"), SimpleNamespace(text="b")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    assert _client([r]).generate("p") == "ab"


def test_token_budget():
    c = _client([_resp("ok", 60, 60), _resp("no")], max_total_tokens=100)
    c.generate("p")
    with pytest.raises(lc.LLMBudgetExceededError):
        c.generate("p")
    assert c.client.messages.calls == 1


def test_cost_budget():
    c = _client([_resp("ok", 1_000_000, 0), _resp("no")], max_cost_usd=1.0)
    c.generate("p")
    with pytest.raises(lc.LLMBudgetExceededError):
        c.generate("p")
