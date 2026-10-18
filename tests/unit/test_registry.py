"""Unit tests for ToolRegistry, policy, notifier and ToolWatcher."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from src.registry import ToolLoadError, ToolRegistry, ToolWatcher, check_source

ADD = "def add_numbers(a: int, b: int) -> int:\n    return a + b\n"
ADD2 = "def add_numbers(a: int, b: int) -> int:\n    return a + b + 100\n"


def _write(d: Path, name: str, src: str) -> Path:
    p = d / name
    p.write_text(src, encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path):
    mcp = FastMCP("t")
    tools = tmp_path / "tools"
    tools.mkdir()
    return mcp, tools, ToolRegistry(mcp, tools)


async def _names(mcp):
    async with Client(mcp) as c:
        return {t.name for t in await c.list_tools()}


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


async def test_load_call_unload_via_client(env):
    mcp, tools, reg = env
    p = _write(tools, "add_numbers_ab12.py", ADD)
    assert await _names(mcp) == set()
    name = reg.load_tool(p, tool_id="tid1")
    assert name == "add_numbers"
    assert await _names(mcp) == {"add_numbers"}
    async with Client(mcp) as c:
        res = await c.call_tool("add_numbers", {"a": 2, "b": 3})
        assert res.data == 5
    info = reg.list_loaded()[0]
    assert info["tool_id"] == "tid1" and info["calls"] == 1 and info["errors"] == 0
    assert reg.unload_tool("tid1") is True
    assert await _names(mcp) == set()
    assert reg.unload_tool("tid1") is False


async def test_reload_replaces_registration(env):
    mcp, tools, reg = env
    p = _write(tools, "add_numbers.py", ADD)
    reg.load_tool(p)
    _write(tools, "add_numbers.py", ADD2)
    reg.reload_tool(p)
    assert len(reg.list_loaded()) == 1
    async with Client(mcp) as c:
        assert len(await c.list_tools()) == 1
        assert (await c.call_tool("add_numbers", {"a": 1, "b": 1})).data == 102


def test_call_and_error_counters(env):
    _, tools, reg = env
    p = _write(tools, "boom.py", "def boom(x: int) -> int:\n    return 1 // x\n")
    reg.load_tool(p)
    assert reg.call("boom", x=1) == 1
    with pytest.raises(ZeroDivisionError):
        reg.call("boom", x=0)
    info = reg.list_loaded()[0]
    assert (info["calls"], info["errors"]) == (2, 1)
    with pytest.raises(KeyError):
        reg.call("nope")


async def test_async_tool(env):
    mcp, tools, reg = env
    p = _write(tools, "slow.py", "async def slow(x: int) -> int:\n    return x * 2\n")
    reg.load_tool(p)
    assert reg.call("slow", x=4) == 8
    async with Client(mcp) as c:
        assert (await c.call_tool("slow", {"x": 3})).data == 6


def test_policy_rejects_unsafe(env):
    _, tools, reg = env
    bad = {
        "a.py": "def a():\n    return eval('1')\n",
        "b.py": "import subprocess\ndef b():\n    return 1\n",
        "c.py": "import os\ndef c():\n    os.system('x')\n",
        "d.py": "def d():\n    return __import__('os')\n",
        "e.py": "import socket\ndef e():\n    pass\n",
    }
    for n, s in bad.items():
        with pytest.raises(ToolLoadError, match="policy"):
            reg.load_tool(_write(tools, n, s))
    assert reg.list_loaded() == []
    unsafe = ToolRegistry(FastMCP("u"), tools, allow_unsafe=True)
    assert unsafe.load_tool(tools / "a.py") == "a"
    assert check_source(ADD) == []


def test_syntax_error_and_import_error_and_no_function(env):
    _, tools, reg = env
    with pytest.raises(ToolLoadError):
        reg.load_tool(_write(tools, "s.py", "def x(:\n"))
    with pytest.raises(ToolLoadError):
        reg.load_tool(_write(tools, "i.py", "raise RuntimeError('x')\ndef i(): pass\n"))
    with pytest.raises(ToolLoadError):
        reg.load_tool(_write(tools, "n.py", "X = 1\ndef _p(): pass\n"))
    with pytest.raises(ToolLoadError):
        reg.load_tool(tools / "missing.py")
    assert reg.list_loaded() == []


def test_function_selection_prefers_stem(env):
    _, tools, reg = env
    src = "def helper_a(x: int) -> int:\n    return x\n\ndef main_tool(x: int) -> int:\n    return x\n"
    assert reg.load_tool(_write(tools, "main_tool_1234.py", src)) == "main_tool"


def test_load_all_skips_bad_and_init(env):
    _, tools, reg = env
    _write(tools, "__init__.py", "")
    _write(tools, "good.py", "def good() -> int:\n    return 1\n")
    _write(tools, "bad.py", "def bad(:\n")
    assert reg.load_all() == ["good"]


def test_on_change_callback(env):
    mcp, tools, _ = env
    events = []
    reg = ToolRegistry(mcp, tools, on_change=lambda ev, n: events.append((ev, n)))
    p = _write(tools, "add_numbers.py", ADD)
    reg.load_tool(p)
    reg.unload_tool("add_numbers")
    assert events == [("loaded", "add_numbers"), ("unloaded", "add_numbers")]


async def test_list_changed_sent_to_recorded_sessions(env):
    """Notifier pushes list_changed to recorded connections (best effort).

    fastmcp 4.1 / MCP 2026-07-28 delivers list_changed only via
    subscriptions/listen, so a real in-memory client may not see it; use fakes.
    """
    mcp, tools, reg = env
    sent = []

    class FakeConn:
        async def send_tool_list_changed(self):
            sent.append(1)

    class BadConn:
        async def send_tool_list_changed(self):
            raise RuntimeError("closed")

    n = reg._notifier
    good, bad = FakeConn(), BadConn()
    n._sessions.add(good)
    n._sessions.add(bad)
    n._loop = asyncio.get_running_loop()
    reg.load_tool(_write(tools, "add_numbers.py", ADD))
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0.02)
    assert sent == [1]


async def test_real_client_session_is_recorded(env):
    mcp, tools, reg = env
    async with Client(mcp) as c:
        await c.list_tools()
        assert reg._notifier.session_count >= 1


def test_notifier_never_raises(env):
    mcp, _, reg = env
    reg._notifier.notify_tools_changed()  # no sessions, no loop


def test_watcher_load_modify_delete(env):
    mcp, tools, reg = env
    w = ToolWatcher(reg, tools, debounce_seconds=0.1)
    w.start()
    try:
        p = _write(tools, "add_numbers.py", ADD)
        assert _wait(lambda: reg.list_loaded())
        assert reg.call("add_numbers", a=1, b=1) == 2

        _write(tools, "add_numbers.py", ADD2)
        assert _wait(lambda: reg.list_loaded() and reg.call("add_numbers", a=1, b=1) == 102)

        # invalid syntax leaves the previous version in place
        _write(tools, "add_numbers.py", "def add_numbers(:\n")
        time.sleep(0.8)
        assert reg.call("add_numbers", a=1, b=1) == 102

        p.unlink()
        assert _wait(lambda: not reg.list_loaded())
        _write(tools, "__init__.py", "def init_fn(): pass\n")
        time.sleep(0.5)
        assert reg.list_loaded() == []
    finally:
        w.stop()
    assert not w.running
