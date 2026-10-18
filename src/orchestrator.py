"""
Weave MCP server (thin orchestrator).

All pipeline logic lives in ``src.pipeline``; this module only wires the
components together and exposes them as MCP tools. Importing it has no side
effects: no API key, vector database or directories are touched until
``build_server()`` / ``main()`` is called (the module-level ``mcp`` attribute
is created lazily on first access).
"""

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastmcp import Context, FastMCP

from src.approval import ApprovalGate, ApprovedOnlyRegistry

logger = logging.getLogger(__name__)

SERVER_NAME = "weave"
SERVER_VERSION = "0.3.0"

# Names generated tools may not take (they would replace the management tools).
RESERVED_TOOL_NAMES = frozenset({
    "health_check", "generate_tool", "list_generated_tools", "search_tools", "run_tool",
    "approve_tool", "reject_tool", "list_pending_approvals", "run_regression", "heal_tool",
    "deprecate_tool", "tool_stats",
})


@dataclass
class WeaveApp:
    """The wired-up components behind one MCP server."""

    settings: Any
    llm: Any
    server: FastMCP
    pipeline: Any
    planner: Any
    librarian: Any
    auditor: Any
    architect: Any
    warden: Any
    registry: Any
    approval: ApprovalGate
    janitor: Any = None
    watcher: Any = None
    started: bool = field(default=False)

    # -- lifecycle -------------------------------------------------------
    def load_existing_tools(self) -> List[str]:
        """
        Hot-load previously approved library tools (active, file intact).

        Files merely present in the tools directory are NOT loaded: only tools
        known to the library and approved (or all, with auto-approval).
        """
        from src.agents.librarian import ToolMatch

        loaded: List[str] = []
        for tool in self.librarian.list_tools(status="active"):
            if not (tool.get("approved") is True or self.settings.auto_approve_tools):
                continue
            match = ToolMatch(tool_id=tool["id"], similarity=1.0, metadata=tool, rank=1)
            try:
                if not self.librarian.verify_match(match, None):
                    logger.warning(f"Not loading {tool['id']}: source missing or modified")
                    continue
                loaded.append(self.registry.load_tool(tool["source_path"], tool_id=tool["id"]))
            except Exception as e:
                logger.warning(f"Could not load tool {tool['id']}: {e}")
        return loaded

    def start(self) -> None:
        """Load tools, start the file watcher and the janitor schedule."""
        if self.started:
            return
        names = self.load_existing_tools()
        logger.info(f"Loaded {len(names)} existing tool(s): {names}")
        if self.settings.hot_reload:
            from src.registry.hot_reload import ToolWatcher

            self.watcher = ToolWatcher(
                ApprovedOnlyRegistry(self.registry), self.settings.tool_directory
            )
            self.watcher.start()
        interval = self.settings.regression_interval_seconds
        if interval > 0 and self.janitor is not None:
            self.janitor.start_background(interval)
        self.started = True

    def stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        if self.janitor is not None and hasattr(self.janitor, "stop"):
            try:
                self.janitor.stop()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"janitor stop failed: {e}")
        self.started = False


def _build_janitor(settings: Any, warden: Any, librarian: Any, registry: Any, architect: Any, llm: Any):
    """Create the Janitor if that module is available (None otherwise)."""
    try:
        from src.feedback.janitor import Janitor
    except ImportError:
        logger.info("Janitor module not available; regression/heal tools are disabled")
        return None
    return Janitor(warden.runner, librarian, registry, architect, llm=llm, settings=settings)


def build_app(settings: Any = None, llm: Any = None) -> WeaveApp:
    """
    Construct every component and the FastMCP server (nothing is started).

    Args:
        settings: Settings (defaults to ``get_settings()``).
        llm: LLM client implementing ``LLMClientProtocol`` (a lazy
            ``LLMClient`` is created from settings when omitted).
    """
    from src.agents.architect import ArchitectAgent
    from src.agents.auditor import AuditorAgent
    from src.agents.librarian import LibrarianAgent
    from src.agents.planner import PlannerAgent
    from src.agents.warden import WardenAgent
    from src.config import get_settings
    from src.memory.vector_store import VectorStore
    from src.pipeline import Pipeline
    from src.registry.tool_registry import ToolRegistry
    from src.sandbox.factory import create_runner

    settings = settings or get_settings()
    if llm is None:
        from src.utils.llm_client import LLMClient

        llm = LLMClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    server = FastMCP(name=SERVER_NAME, version=SERVER_VERSION)

    tools_dir = settings.tool_directory
    tests_dir = settings.tests_directory
    planner = PlannerAgent(llm)
    auditor = AuditorAgent(llm, test_output_dir=tests_dir)
    architect = ArchitectAgent(llm, tool_output_dir=tools_dir)
    warden = WardenAgent(runner=create_runner(settings))
    librarian = LibrarianAgent(
        vector_store=VectorStore(str(settings.vector_db_path)), settings=settings
    )
    registry = ToolRegistry(server, tools_dir)
    gate = ApprovalGate.from_settings(
        settings, state_path=settings.vector_db_path.parent / "pending_approvals.json"
    )
    janitor = _build_janitor(settings, warden, librarian, registry, architect, llm)
    pipeline = Pipeline(
        settings, llm, planner, librarian, auditor, architect, warden, registry, gate,
        janitor=janitor, reserved_names=set(RESERVED_TOOL_NAMES),
    )
    app = WeaveApp(
        settings=settings, llm=llm, server=server, pipeline=pipeline, planner=planner,
        librarian=librarian, auditor=auditor, architect=architect, warden=warden,
        registry=registry, approval=gate, janitor=janitor,
    )
    _register_tools(app)
    server.weave_app = app  # type: ignore[attr-defined]
    return app


def build_server(settings: Any = None, llm: Any = None) -> FastMCP:
    """Build the MCP server (see :func:`build_app`); the app is ``server.weave_app``."""
    return build_app(settings, llm).server


# ----------------------------------------------------------------------
# MCP tools
# ----------------------------------------------------------------------

def _register_tools(app: WeaveApp) -> None:
    mcp = app.server

    def _janitor():
        if app.janitor is None:
            raise RuntimeError("Janitor is not available")
        return app.janitor

    def _usage() -> Dict[str, Any]:
        getter = getattr(app.llm, "get_token_usage", None)
        return getter() if getter else {}

    @mcp.tool()
    def health_check() -> dict:
        """Report server status, library statistics, loaded tools and token usage."""
        return {
            "status": "healthy",
            "version": SERVER_VERSION,
            "components": {
                name: "operational"
                for name in ("planner", "auditor", "architect", "warden", "librarian", "registry")
            } | {"janitor": "operational" if app.janitor is not None else "unavailable"},
            "sandbox": app.settings.sandbox_type,
            "hot_reload": bool(app.watcher is not None and app.watcher.running),
            "library": app.librarian.get_library_stats(),
            "loaded_tools": len(app.registry.list_loaded()),
            "pending_approvals": len(app.approval.list_pending()),
            "token_usage": _usage(),
        }

    @mcp.tool()
    async def generate_tool(description: str, ctx: Context, max_tasks: int = 10) -> dict:
        """
        Generate, verify and (once approved) hot-load new tool(s) from a description.

        Existing verified tools are reused instead of regenerated. When approval
        is required and the client supports it, you are asked to approve; otherwise
        the tool stays pending until approve_tool / reject_tool is called.

        Args:
            description: Natural language description of the desired tool.
            max_tasks: Maximum number of subtasks to decompose into.
        """
        loop = asyncio.get_running_loop()
        with app.approval.bind_context(ctx, loop):
            try:
                return await asyncio.to_thread(app.pipeline.run, description, max_tasks)
            except Exception as e:
                logger.error(f"generate_tool failed: {e}", exc_info=True)
                return {"status": "error", "description": description, "error": str(e)}

    @mcp.tool()
    def list_generated_tools(status: Optional[str] = None) -> dict:
        """List tools in the library (optionally filtered by status) and whether each is live."""
        loaded = {e["tool_id"]: e for e in app.registry.list_loaded()}
        tools = []
        for t in app.librarian.list_tools(status=status):
            live = loaded.get(t["id"])
            tools.append({
                "tool_id": t["id"],
                "name": t.get("name"),
                "description": t.get("description", ""),
                "signature": t.get("signature", ""),
                "status": t.get("status"),
                "version": t.get("version"),
                "approved": t.get("approved") is True,
                "loaded": live is not None,
                "path": t.get("source_path"),
            })
        return {"total": len(tools), "tools": tools}

    @mcp.tool()
    def search_tools(query: str, n_results: int = 5) -> dict:
        """Semantic search over the tool library."""
        matches = app.librarian.search_text(query, n_results=max(1, min(n_results, 50)))
        return {
            "query": query,
            "matches": [
                {
                    "tool_id": m.tool_id,
                    "name": m.metadata.get("name"),
                    "similarity": round(m.similarity, 4),
                    "description": m.metadata.get("description", ""),
                    "signature": m.metadata.get("signature", ""),
                }
                for m in matches
            ],
        }

    @mcp.tool()
    def run_tool(name: str, args_json: str = "{}") -> dict:
        """
        Call a loaded tool in-process and record the outcome.

        Args:
            name: Tool name or tool id.
            args_json: JSON object of keyword arguments.
        """
        try:
            kwargs = json.loads(args_json or "{}")
            if not isinstance(kwargs, dict):
                raise ValueError("args_json must be a JSON object")
        except ValueError as e:
            return {"status": "error", "error": f"Invalid args_json: {e}"}

        entry = next(
            (e for e in app.registry.list_loaded() if name in (e["name"], e["tool_id"])), None
        )
        if entry is None:
            return {"status": "error", "error": f"Tool '{name}' is not loaded"}
        try:
            result = app.registry.call(entry["name"], **kwargs)
        except Exception as e:
            app.librarian.record_outcome(entry["tool_id"], False)
            return {"status": "error", "tool": entry["name"], "error": f"{type(e).__name__}: {e}"}
        app.librarian.record_outcome(entry["tool_id"], True)
        return {"status": "ok", "tool": entry["name"], "result": result}

    @mcp.tool()
    def list_pending_approvals() -> dict:
        """List generated tools waiting for approval."""
        pending = app.approval.list_pending()
        return {"total": len(pending), "pending": pending}

    @mcp.tool()
    def approve_tool(tool_id: str) -> dict:
        """Approve a pending tool and hot-load it."""
        try:
            out = app.approval.approve(tool_id)
            return {"status": "ok", "tool_id": tool_id, "decision": out["status"],
                    "activation": out["activation"]}
        except KeyError as e:
            return {"status": "error", "error": str(e.args[0])}
        except Exception as e:
            return {"status": "error", "error": f"Activation failed: {e}"}

    @mcp.tool()
    def reject_tool(tool_id: str, reason: str = "") -> dict:
        """Reject a pending tool; it is deprecated and never loaded."""
        try:
            out = app.approval.reject(tool_id, reason)
            return {"status": "ok", "tool_id": tool_id, "decision": out["status"],
                    "reason": out["reason"]}
        except KeyError as e:
            return {"status": "error", "error": str(e.args[0])}

    @mcp.tool()
    def run_regression() -> dict:
        """Re-run the stored tests of every active tool (Janitor)."""
        try:
            return _janitor().run_regression()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool()
    def heal_tool(tool_id: str, max_attempts: int = 3) -> dict:
        """Try to repair a broken tool using its failing test output (Janitor)."""
        try:
            return _janitor().heal(tool_id, max_attempts=max_attempts)
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @mcp.tool()
    def deprecate_tool(tool_id: str) -> dict:
        """Deprecate a tool: it is unloaded and never reused."""
        if not app.librarian.set_status(tool_id, "deprecated"):
            return {"status": "error", "error": f"Unknown tool '{tool_id}'"}
        unloaded = app.registry.unload_tool(tool_id)
        return {"status": "ok", "tool_id": tool_id, "unloaded": unloaded}

    @mcp.tool()
    def tool_stats(tool_id: Optional[str] = None) -> dict:
        """Usage and health statistics for one tool, or for the whole library."""
        loaded = {e["tool_id"]: e for e in app.registry.list_loaded()}
        if tool_id is not None:
            tool = app.librarian.get_tool(tool_id)
            if tool is None:
                return {"status": "error", "error": f"Unknown tool '{tool_id}'"}
            return {"status": "ok", "tool": tool, "runtime": loaded.get(tool_id)}
        return {
            "status": "ok",
            "library": app.librarian.get_library_stats(),
            "loaded": list(loaded.values()),
            "token_usage": _usage(),
            "usage_by_agent": getattr(app.llm, "get_usage_by_agent", lambda: {})(),
        }


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

_default_server: Optional[FastMCP] = None


def __getattr__(name: str) -> Any:
    """Lazy module attribute ``mcp``: the default server, built on first access."""
    global _default_server
    if name == "mcp":
        if _default_server is None:
            _default_server = build_server()
        return _default_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def configure_logging(settings: Any) -> None:
    """Log to stderr (stdout carries the MCP stdio protocol) and to the log file."""
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )


def main() -> None:
    """Run the Weave MCP server."""
    from dotenv import load_dotenv

    from src.config import get_settings

    load_dotenv()
    settings = get_settings()
    configure_logging(settings)
    app = build_app(settings)
    logger.info(f"Starting Weave MCP server (sandbox={settings.sandbox_type})")
    app.start()
    try:
        app.server.run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
