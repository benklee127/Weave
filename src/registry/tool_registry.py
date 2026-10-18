"""ToolRegistry: runtime load/unload of generated tools into a FastMCP server."""
from __future__ import annotations

import ast
import asyncio
import functools
import hashlib
import importlib.util
import inspect
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastmcp.tools import Tool

from .mcp_notifier import install_notifier
from .policy import check_source

logger = logging.getLogger(__name__)


class ToolLoadError(Exception):
    """Raised when a tool cannot be loaded."""


async def _await(aw):
    return await aw


class ToolRegistry:
    """Loads tool modules and exposes their target function as an MCP tool.

    ``on_change`` is invoked as ``on_change(event, tool_name)`` where event is
    "loaded" or "unloaded" (a zero-argument callable is also accepted).
    """

    def __init__(
        self,
        mcp: Any,
        tools_dir: Path | str,
        on_change: Callable[..., None] | None = None,
        allow_unsafe: bool = False,
    ) -> None:
        self.mcp = mcp
        self.tools_dir = Path(tools_dir).resolve()
        self.on_change = on_change
        self.allow_unsafe = allow_unsafe
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}  # keyed by tool name
        self._notifier = install_notifier(mcp)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _select_function(tree: ast.Module, stem: str, tool_id: str) -> str:
        funcs = [
            n.name
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
        ]
        if not funcs:
            raise ToolLoadError("module defines no public top-level function")
        for cand in (tool_id, stem):
            if cand in funcs:
                return cand
        for name in funcs:
            if name in stem:
                return name
        return funcs[0]

    def _wrap(self, fn: Callable[..., Any], name: str) -> Callable[..., Any]:
        def _bump(key: str) -> None:
            with self._lock:
                e = self._entries.get(name)
                if e is not None:
                    e[key] += 1

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*a, **kw):
                _bump("calls")
                try:
                    return await fn(*a, **kw)
                except Exception:
                    _bump("errors")
                    raise
        else:
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                _bump("calls")
                try:
                    return fn(*a, **kw)
                except Exception:
                    _bump("errors")
                    raise
        return wrapper

    def _fire(self, event: str, name: str) -> None:
        self._notifier.notify_tools_changed()
        if self.on_change is not None:
            try:
                try:
                    self.on_change(event, name)
                except TypeError:
                    self.on_change()
            except Exception as exc:
                logger.warning("on_change callback failed: %s", exc)

    def _remove_registration(self, name: str) -> None:
        try:
            self.mcp.local_provider.remove_tool(name)
        except KeyError:
            pass
        except Exception as exc:
            logger.debug("remove_tool(%s): %s", name, exc)

    @staticmethod
    def _drop_module(entry: dict[str, Any]) -> None:
        sys.modules.pop(entry.get("module_name", ""), None)

    # -- API -------------------------------------------------------------
    def load_tool(self, path: Path | str, tool_id: str | None = None) -> str:
        """Import ``path`` and register its target function. Returns the tool name."""
        path = Path(path).resolve()
        if not path.is_file():
            raise ToolLoadError(f"tool file not found: {path}")
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise ToolLoadError(f"syntax error in {path.name}: {exc}") from exc
        if not self.allow_unsafe:
            problems = check_source(source, str(path))
            if problems:
                raise ToolLoadError(
                    f"{path.name} rejected by safety policy: " + "; ".join(problems)
                )
        tool_id = tool_id or path.stem
        func_name = self._select_function(tree, path.stem, tool_id)

        digest = hashlib.sha1(f"{path}:{time.time_ns()}".encode()).hexdigest()[:8]
        mod_name = f"weave_tool_{path.stem}_{digest}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ToolLoadError(f"cannot create import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
            fn = getattr(module, func_name)
        except Exception as exc:
            sys.modules.pop(mod_name, None)
            raise ToolLoadError(f"import of {path.name} failed: {exc!r}") from exc

        with self._lock:
            for old_name, e in list(self._entries.items()):
                if old_name == func_name or e["tool_id"] == tool_id or e["path"] == str(path):
                    self._remove_registration(old_name)
                    self._drop_module(e)
                    del self._entries[old_name]
            self._entries[func_name] = {
                "name": func_name,
                "tool_id": tool_id,
                "path": str(path),
                "module_name": mod_name,
                "loaded_at": time.time(),
                "calls": 0,
                "errors": 0,
                "fn": None,
            }
            wrapped = self._wrap(fn, func_name)
            try:
                self.mcp.add_tool(Tool.from_function(wrapped, name=func_name))
            except Exception as exc:
                del self._entries[func_name]
                sys.modules.pop(mod_name, None)
                raise ToolLoadError(f"could not register {func_name}: {exc}") from exc
            self._entries[func_name]["fn"] = wrapped
        logger.info("Loaded tool %s (%s)", func_name, path.name)
        self._fire("loaded", func_name)
        return func_name

    def _resolve(self, name_or_id: str) -> str | None:
        with self._lock:
            if name_or_id in self._entries:
                return name_or_id
            for n, e in self._entries.items():
                if e["tool_id"] == name_or_id or Path(e["path"]).stem == name_or_id:
                    return n
        return None

    def unload_tool(self, name_or_id: str) -> bool:
        with self._lock:
            name = self._resolve(name_or_id)
            if name is None:
                return False
            entry = self._entries.pop(name)
            self._remove_registration(name)
            self._drop_module(entry)
        logger.info("Unloaded tool %s", name)
        self._fire("unloaded", name)
        return True

    def reload_tool(self, path: Path | str) -> str:
        """Reload ``path``, keeping its previously assigned tool_id."""
        path = Path(path).resolve()
        tool_id = None
        with self._lock:
            for e in self._entries.values():
                if e["path"] == str(path):
                    tool_id = e["tool_id"]
        return self.load_tool(path, tool_id=tool_id)

    def list_loaded(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {k: e[k] for k in ("name", "tool_id", "path", "loaded_at", "calls", "errors")}
                for e in self._entries.values()
            ]

    def load_all(self) -> list[str]:
        """Load every ``*.py`` in tools_dir; failures are logged and skipped."""
        loaded: list[str] = []
        if not self.tools_dir.is_dir():
            return loaded
        for p in sorted(self.tools_dir.glob("*.py")):
            if p.name == "__init__.py":
                continue
            try:
                loaded.append(self.load_tool(p))
            except Exception as exc:
                logger.warning("Skipping %s: %s", p.name, exc)
        return loaded

    def call(self, name: str, **kwargs: Any) -> Any:
        """In-process call (by tool name or id) with usage/error counters."""
        resolved = self._resolve(name)
        if resolved is None:
            raise KeyError(f"tool not loaded: {name}")
        result = self._entries[resolved]["fn"](**kwargs)
        if not inspect.isawaitable(result):
            return result
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_await(result))
        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["r"] = asyncio.run(_await(result))
            except BaseException as exc:
                box["e"] = exc

        t = threading.Thread(target=_run)
        t.start()
        t.join()
        if "e" in box:
            raise box["e"]
        return box["r"]
