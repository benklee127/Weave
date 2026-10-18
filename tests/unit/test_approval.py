"""Tests for ApprovalGate and ApprovedOnlyRegistry."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.approval import ApprovalGate, ApprovalStatus, ApprovedOnlyRegistry, ToolProposal


def proposal(tool_id="t1", **kw):
    return ToolProposal(tool_id=tool_id, name="add", path="add.py", signature="def add(a, b)", **kw)


def test_auto_approve_decides_immediately():
    gate = ApprovalGate(auto_approve=True)
    assert gate.request(proposal()) is ApprovalStatus.APPROVED
    assert gate.list_pending() == []


def test_without_context_request_is_pending_and_idempotent():
    gate = ApprovalGate()
    assert gate.request(proposal()) is ApprovalStatus.PENDING
    assert gate.request(proposal()) is ApprovalStatus.PENDING
    assert [p["tool_id"] for p in gate.list_pending()] == ["t1"]
    assert gate.get_pending("t1").name == "add"


def test_approve_and_reject_invoke_callbacks_and_clear_pending():
    activated, retired = [], []
    gate = ApprovalGate(
        on_approve=lambda p: activated.append(p.tool_id) or {"loaded": True},
        on_reject=lambda p, reason: retired.append((p.tool_id, reason)),
    )
    gate.request(proposal("a"))
    gate.request(proposal("b"))
    assert gate.approve("a") == {"tool_id": "a", "status": "approved", "activation": {"loaded": True}}
    assert gate.reject("b", "bad")["status"] == "rejected"
    assert activated == ["a"] and retired == [("b", "bad")]
    assert gate.list_pending() == []
    with pytest.raises(KeyError):
        gate.approve("a")
    with pytest.raises(KeyError):
        gate.reject("zzz")


def test_failed_activation_keeps_proposal_pending():
    def boom(_):
        raise RuntimeError("cannot load")

    gate = ApprovalGate(on_approve=boom)
    gate.request(proposal())
    with pytest.raises(RuntimeError):
        gate.approve("t1")
    assert len(gate.list_pending()) == 1


def test_state_is_persisted_and_survives_corruption(tmp_path):
    state = tmp_path / "state" / "pending.json"
    ApprovalGate(state_path=state).request(proposal())
    assert ApprovalGate(state_path=state).get_pending("t1") is not None
    state.write_text("{not json", encoding="utf-8")
    assert ApprovalGate(state_path=state).list_pending() == []


def test_from_settings_reads_auto_approve():
    assert ApprovalGate.from_settings(SimpleNamespace(auto_approve_tools=True)).auto_approve is True
    assert ApprovalGate.from_settings(SimpleNamespace(auto_approve_tools=False)).auto_approve is False


class FakeCtx:
    def __init__(self, action="accept", data="approve", error=None):
        self.action, self.data, self.error, self.messages = action, data, error, []

    async def elicit(self, message, response_type):
        self.messages.append(message)
        if self.error:
            raise self.error
        return SimpleNamespace(action=self.action, data=self.data)


def ask(gate, ctx, tool_id="t1"):
    async def go():
        loop = asyncio.get_running_loop()
        with gate.bind_context(ctx, loop):
            return await asyncio.to_thread(gate.request, proposal(tool_id))

    return asyncio.run(go())


@pytest.mark.parametrize(
    "ctx, expected",
    [
        (FakeCtx(data="approve"), ApprovalStatus.APPROVED),
        (FakeCtx(data="reject"), ApprovalStatus.REJECTED),
        (FakeCtx(action="decline", data=None), ApprovalStatus.PENDING),
        (FakeCtx(action="cancel", data=None), ApprovalStatus.PENDING),
        (FakeCtx(data="maybe"), ApprovalStatus.PENDING),
        (FakeCtx(error=RuntimeError("no elicitation support")), ApprovalStatus.PENDING),
    ],
)
def test_elicitation_outcomes(ctx, expected):
    gate = ApprovalGate()
    assert ask(gate, ctx) is expected
    assert ctx.messages and "add" in ctx.messages[0]
    assert (len(gate.list_pending()) == 1) == (expected is ApprovalStatus.PENDING)


def test_elicitation_timeout_leaves_pending():
    class Slow(FakeCtx):
        async def elicit(self, message, response_type):
            await asyncio.sleep(5)

    gate = ApprovalGate(elicit_timeout=0.1)
    assert ask(gate, Slow()) is ApprovalStatus.PENDING


def test_binding_is_scoped_to_the_with_block():
    gate = ApprovalGate()

    async def go():
        with gate.bind_context(FakeCtx(), asyncio.get_running_loop()):
            pass
        return await asyncio.to_thread(gate.request, proposal())

    assert asyncio.run(go()) is ApprovalStatus.PENDING


def test_approved_only_registry_blocks_unloaded_reloads(tmp_path):
    loaded_file = tmp_path / "loaded.py"
    other = tmp_path / "other.py"
    reloads = []
    inner = SimpleNamespace(
        list_loaded=lambda: [{"path": str(loaded_file.resolve()), "name": "loaded"}],
        reload_tool=lambda p: reloads.append(Path(p).name) or "loaded",
        unload_tool=lambda n: True,
    )
    proxy = ApprovedOnlyRegistry(inner)
    assert proxy.reload_tool(loaded_file) == "loaded"
    assert proxy.reload_tool(other) is None
    assert reloads == ["loaded.py"]
    assert proxy.unload_tool("loaded") is True  # delegated
