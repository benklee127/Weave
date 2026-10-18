"""Tests for the thin FastMCP orchestrator (build_server and its MCP tools)."""

import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from src.memory.embeddings import HashEmbeddingGenerator
from src.orchestrator import RESERVED_TOOL_NAMES, build_app, build_server
from tests.fakes import FakeLLMClient
from tests.unit.test_pipeline import ADD, make_llm

ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_TOOLS = {
    "health_check", "generate_tool", "list_generated_tools", "search_tools", "run_tool",
    "approve_tool", "reject_tool", "list_pending_approvals", "run_regression", "heal_tool",
    "deprecate_tool", "tool_stats",
}


@pytest.fixture
def settings(tmp_settings, monkeypatch):
    # Hash embeddings only (sentence-transformers is optional and never needed here).
    tmp_settings.embedding_model = "hash"
    tmp_settings.reuse_similarity_threshold = 0.3
    tmp_settings.reuse_high_confidence = 0.5
    return tmp_settings


async def call(client, tool, **args):
    return (await client.call_tool(tool, args)).data


def test_import_has_no_side_effects_and_needs_no_api_key(tmp_path):
    code = (
        "import sys, os; os.environ.pop('ANTHROPIC_API_KEY', None); import src.orchestrator as o; "
        "assert callable(o.build_server); "
        "assert 'mcp' not in vars(o)"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "ANTHROPIC_API_KEY"}
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr


def test_build_server_registers_management_tools(settings):
    server = build_server(settings, FakeLLMClient())
    import asyncio

    async def names():
        async with Client(server) as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(names()) == EXPECTED_TOOLS
    assert EXPECTED_TOOLS == set(RESERVED_TOOL_NAMES)


async def test_health_check_and_empty_library(settings):
    server = build_server(settings, FakeLLMClient())
    async with Client(server) as c:
        health = await call(c, "health_check")
        assert health["status"] == "healthy" and health["loaded_tools"] == 0
        assert health["components"]["planner"] == "operational"
        assert (await call(c, "list_pending_approvals"))["total"] == 0
        assert (await call(c, "list_generated_tools"))["total"] == 0


async def test_generate_then_use_search_stats_and_deprecate(settings):
    server = build_server(settings, make_llm([ADD]))
    async with Client(server) as c:
        report = await call(c, "generate_tool", description="add numbers", max_tasks=3)
        assert report["status"] == "completed" and report["verified"] == 1
        tool_id = report["results"][0]["task_id"]

        # the generated tool is now a live MCP tool
        assert "add_two_integers" in {t.name for t in await c.list_tools()}
        assert (await c.call_tool("add_two_integers", {"a": 2, "b": 3})).data == 5

        listed = await call(c, "list_generated_tools")
        assert listed["total"] == 1 and listed["tools"][0]["loaded"] is True

        found = await call(c, "search_tools", query="add two integers")
        assert found["matches"][0]["tool_id"] == tool_id

        ran = await call(c, "run_tool", name="add_two_integers", args_json='{"a": 4, "b": 5}')
        assert ran == {"status": "ok", "tool": "add_two_integers", "result": 9}
        bad = await call(c, "run_tool", name="add_two_integers", args_json='{"a": 1}')
        assert bad["status"] == "error"
        assert (await call(c, "run_tool", name="nope"))["status"] == "error"
        assert (await call(c, "run_tool", name="add_two_integers", args_json="[1]"))["status"] == "error"

        stats = await call(c, "tool_stats", tool_id=tool_id)
        assert stats["tool"]["usage_count"] == 2 and stats["runtime"]["name"] == "add_two_integers"
        assert (await call(c, "tool_stats"))["status"] == "ok"
        assert (await call(c, "tool_stats", tool_id="missing"))["status"] == "error"

        dep = await call(c, "deprecate_tool", tool_id=tool_id)
        assert dep["unloaded"] is True
        assert "add_two_integers" not in {t.name for t in await c.list_tools()}
        assert (await call(c, "deprecate_tool", tool_id="missing"))["status"] == "error"


async def test_pending_approval_flow_via_mcp_tools(settings):
    settings.auto_approve_tools = False
    server = build_server(settings, make_llm([ADD]))
    async with Client(server) as c:
        report = await call(c, "generate_tool", description="add numbers")
        res = report["results"][0]
        assert res["approval"] == "pending" and res["loaded"] is False
        assert "add_two_integers" not in {t.name for t in await c.list_tools()}

        pending = await call(c, "list_pending_approvals")
        assert pending["total"] == 1
        approved = await call(c, "approve_tool", tool_id=res["task_id"])
        assert approved["status"] == "ok"
        assert "add_two_integers" in {t.name for t in await c.list_tools()}
        assert (await call(c, "approve_tool", tool_id=res["task_id"]))["status"] == "error"
        assert (await call(c, "reject_tool", tool_id="missing"))["status"] == "error"


async def test_reject_tool_via_mcp(settings):
    settings.auto_approve_tools = False
    server = build_server(settings, make_llm([ADD]))
    async with Client(server) as c:
        tool_id = (await call(c, "generate_tool", description="x"))["results"][0]["task_id"]
        out = await call(c, "reject_tool", tool_id=tool_id, reason="nope")
        assert out["status"] == "ok" and out["reason"] == "nope"
        assert (await call(c, "list_pending_approvals"))["total"] == 0
        assert (await call(c, "list_generated_tools", status="deprecated"))["total"] == 1


async def test_interactive_approval_through_elicitation(settings):
    settings.auto_approve_tools = False
    seen = []

    async def handler(message, response_type, params, context):
        seen.append(message)
        return "approve"

    server = build_server(settings, make_llm([ADD]))
    async with Client(server, mode="legacy", elicitation_handler=handler) as c:
        res = (await call(c, "generate_tool", description="x"))["results"][0]
        assert res["approval"] == "approved" and res["loaded"] is True
        assert "add_two_integers" in {t.name for t in await c.list_tools()}
    assert seen and "add_two_integers" in seen[0]


async def test_interactive_rejection_fails_task(settings):
    settings.auto_approve_tools = False

    async def handler(message, response_type, params, context):
        return "reject"

    server = build_server(settings, make_llm([ADD]))
    async with Client(server, mode="legacy", elicitation_handler=handler) as c:
        report = await call(c, "generate_tool", description="x")
        assert report["status"] == "failed"
        assert "rejected" in report["results"][0]["error"]


async def test_janitor_tools_degrade_gracefully_or_work(settings):
    server = build_server(settings, make_llm([ADD]))
    async with Client(server) as c:
        await call(c, "generate_tool", description="x")
        reg = await call(c, "run_regression")
        assert isinstance(reg, dict)
        assert (await call(c, "heal_tool", tool_id="missing")).get("status") != "healed"


def test_start_loads_only_approved_library_tools(settings):
    first = build_app(settings, make_llm([ADD]))
    report = first.pipeline.run("add numbers")
    tool_id = report["results"][0]["task_id"]

    settings.hot_reload = False
    second = build_app(settings, FakeLLMClient())
    assert second.registry.list_loaded() == []
    second.start()
    assert [e["tool_id"] for e in second.registry.list_loaded()] == [tool_id]
    second.stop()

    # Unapproved library tools stay unloaded after a restart.
    settings.auto_approve_tools = False
    first.librarian.vector_store.update_metadata(tool_id, {"approved": False})
    third = build_app(settings, FakeLLMClient())
    third.start()
    assert third.registry.list_loaded() == []
    third.stop()


def test_hash_generator_used_when_model_is_hash(settings):
    app = build_app(settings, FakeLLMClient())
    assert isinstance(app.librarian.embedding_generator, HashEmbeddingGenerator)
