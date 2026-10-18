"""Best-effort MCP ``notifications/tools/list_changed`` delivery.

FastMCP does not track connected sessions, so a small middleware records the
sessions that talk to the server; ``notify_tools_changed`` then pushes the
notification to each of them. Nothing here ever raises.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from typing import Any

from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)


class McpNotifier(Middleware):
    """Middleware + sender for tools/list_changed notifications."""

    def __init__(self) -> None:
        self._sessions: "weakref.WeakSet[Any]" = weakref.WeakSet()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    async def on_message(self, context, call_next):  # type: ignore[override]
        try:
            fctx = context.fastmcp_context
            if fctx is not None and fctx.request_context is not None:
                with self._lock:
                    session = fctx.session
                    # SDK v2 builds a ServerSession per request; the stable
                    # per-connection object (when present) is what persists.
                    self._sessions.add(getattr(session, "_connection", session))
                    self._loop = asyncio.get_running_loop()
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not record session", exc_info=True)
        return await call_next(context)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(list(self._sessions))

    async def _send_all(self) -> int:
        with self._lock:
            sessions = list(self._sessions)
        sent = 0
        for session in sessions:
            try:
                await session.send_tool_list_changed()
                sent += 1
            except Exception as exc:
                logger.debug("list_changed to a session failed: %s", exc)
        return sent

    def notify_tools_changed(self) -> None:
        """Schedule a list_changed notification; safe from any thread."""
        try:
            with self._lock:
                loop = self._loop
                has_sessions = bool(self._sessions)
            if loop is None or not has_sessions or loop.is_closed():
                return
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                loop.create_task(self._send_all())
            else:
                asyncio.run_coroutine_threadsafe(self._send_all(), loop)
        except Exception as exc:  # never raise
            logger.debug("notify_tools_changed failed: %s", exc)


def install_notifier(mcp: Any) -> McpNotifier:
    """Attach (once) a notifier middleware to ``mcp`` and return it."""
    existing = getattr(mcp, "_weave_notifier", None)
    if existing is not None:
        return existing
    notifier = McpNotifier()
    try:
        mcp.add_middleware(notifier)
    except Exception as exc:
        logger.warning("Could not install list_changed notifier: %s", exc)
    try:
        mcp._weave_notifier = notifier
    except Exception:
        pass
    return notifier
