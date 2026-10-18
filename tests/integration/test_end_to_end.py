"""
End-to-end tests for Weave.

* Offline tests drive the whole stack through the real MCP protocol
  (``fastmcp.Client``) with a scripted ``FakeLLMClient``: plan, generate tests,
  critic, red check, synthesis, sandboxed verification, library registration,
  approval, hot-loading, reuse, regression and healing. No network, no API key.
* The ``live`` test calls the real Anthropic API and is skipped unless
  ``ANTHROPIC_API_KEY`` is set.
"""

import os
from pathlib import Path

import pytest
from fastmcp import Client

from src.orchestrator import build_app, build_server
from tests.unit.test_pipeline import (
    ADD,
    ADD_BAD_IMPL,
    ADD_IMPL,
    DOUBLE,
    make_llm,
)

try:  # `live` is registered in pyproject (strict markers); degrade if it is not yet.
    live = pytest.mark.live
except BaseException:  # pytest.fail.Exception under --strict-markers
    def live(func):
        return func


@pytest.fixture
def settings(tmp_settings):
    tmp_settings.embedding_model = "hash"
    tmp_settings.reuse_similarity_threshold = 0.3
    tmp_settings.reuse_high_confidence = 0.5
    return tmp_settings


async def call(client, tool, **args):
    return (await client.call_tool(tool, args)).data


async def test_full_lifecycle_over_mcp(settings):
    llm = make_llm([ADD, DOUBLE], refine=ADD_IMPL)
    server = build_server(settings, llm)
    app = server.weave_app

    async with Client(server) as client:
        # 1. generate two dependent tools
        report = await call(client, "generate_tool", description="add then double", max_tasks=5)
        assert report["status"] == "completed", report
        assert report["verified"] == 2 and report["failed"] == 0
        for res in report["results"]:
            assert Path(res["implementation_path"]).is_file()
            assert Path(res["test_path"]).is_file()
            assert res["registered_in_library"] is True and res["loaded"] is True

        # 2. both are live MCP tools immediately (no restart)
        names = {t.name for t in await client.list_tools()}
        assert {"add_two_integers", "double_a_number"} <= names
        assert (await client.call_tool("add_two_integers", {"a": 40, "b": 2})).data == 42
        assert (await client.call_tool("double_a_number", {"x": 21})).data == 42

        # 3. an identical request reuses the library: no new LLM generation
        generated = sum(len(llm.calls_for(a)) for a in ("auditor", "architect"))
        again = await call(client, "generate_tool", description="add then double", max_tasks=5)
        assert again["reused"] == 2 and again["verified"] == 0
        assert sum(len(llm.calls_for(a)) for a in ("auditor", "architect")) == generated

        # 4. regression is green
        regression = await call(client, "run_regression")
        assert len(regression["passed"]) == 2 and regression["broken"] == []

        # 5. break a tool on disk -> regression marks it broken and unloads it
        add_tool = next(t for t in app.librarian.list_tools() if t["name"] == "add_two_integers")
        Path(add_tool["source_path"]).write_text(ADD_BAD_IMPL, encoding="utf-8")
        regression = await call(client, "run_regression")
        assert regression["broken"] == [add_tool["id"]]
        assert "add_two_integers" not in {t.name for t in await client.list_tools()}

        # 6. the janitor heals it from the failing output and the tool comes back
        healed = await call(client, "heal_tool", tool_id=add_tool["id"])
        assert healed["healed"] is True, healed
        assert (await client.call_tool("add_two_integers", {"a": 1, "b": 1})).data == 2

    # 7. a fresh server over the same library/tools directory loads approved tools at startup
    settings.hot_reload = False
    restarted = build_app(settings, make_llm([]))
    restarted.start()
    try:
        assert {e["name"] for e in restarted.registry.list_loaded()} == {
            "add_two_integers", "double_a_number"
        }
    finally:
        restarted.stop()


async def test_human_approval_gate_over_mcp(settings):
    settings.auto_approve_tools = False
    server = build_server(settings, make_llm([ADD]))
    async with Client(server) as client:
        report = await call(client, "generate_tool", description="add numbers")
        res = report["results"][0]
        assert res["status"] == "verified" and res["approval"] == "pending"
        assert "add_two_integers" not in {t.name for t in await client.list_tools()}

        pending = await call(client, "list_pending_approvals")
        assert pending["total"] == 1

        assert (await call(client, "approve_tool", tool_id=res["task_id"]))["status"] == "ok"
        assert (await client.call_tool("add_two_integers", {"a": 3, "b": 4})).data == 7


@live
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_live_fibonacci_generation(tmp_settings):
    """Real LLM, real sandbox: generate a Fibonacci tool end to end."""
    tmp_settings.anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
    tmp_settings.embedding_model = "hash"
    server = build_server(tmp_settings)
    async with Client(server) as client:
        report = await call(
            client,
            "generate_tool",
            description="Create a function that returns the first n Fibonacci numbers as a list",
            max_tasks=3,
        )
        assert report["status"] in ("completed", "partial"), report
        assert report["verified"] + report["reused"] > 0
        for res in report["results"]:
            if res["status"] == "verified":
                assert Path(res["implementation_path"]).is_file()
                assert res["tests_passed"] > 0
