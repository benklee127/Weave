"""Watchdog-based hot reload of tool files into a ToolRegistry."""
from __future__ import annotations

import ast
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class ToolWatcher:
    """Watches ``tools_dir``; reloads changed ``*.py``, unloads deleted ones."""

    def __init__(self, registry, tools_dir: Path | str, debounce_seconds: float = 0.3) -> None:
        self.registry = registry
        self.tools_dir = Path(tools_dir).resolve()
        self.debounce = debounce_seconds
        self._observer = None
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._observer is not None:
            return
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        obs = Observer()
        obs.schedule(_Handler(self), str(self.tools_dir), recursive=False)
        obs.daemon = True
        obs.start()
        self._observer = obs
        logger.info("ToolWatcher started on %s", self.tools_dir)

    def stop(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    @property
    def running(self) -> bool:
        return self._observer is not None

    # -- event handling --------------------------------------------------
    @staticmethod
    def _relevant(path: Path) -> bool:
        return path.suffix == ".py" and path.name != "__init__.py" and not path.name.startswith(".")

    def schedule(self, path: Path) -> None:
        if not self._relevant(path):
            return
        with self._lock:
            old = self._timers.pop(path, None)
            if old:
                old.cancel()
            t = threading.Timer(self.debounce, self._process, args=(path,))
            t.daemon = True
            self._timers[path] = t
            t.start()

    def _process(self, path: Path) -> None:
        with self._lock:
            self._timers.pop(path, None)
        try:
            if not path.exists():
                if self._unload_by_path(path):
                    logger.info("Unloaded deleted tool %s", path.name)
                return
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, ValueError, OSError) as exc:
                logger.warning("Skipping reload of %s: %s", path.name, exc)
                return
            self.registry.reload_tool(path)
        except Exception as exc:
            logger.warning("Hot reload of %s failed: %s", path.name, exc)

    def _unload_by_path(self, path: Path) -> bool:
        for info in self.registry.list_loaded():
            if Path(info["path"]) == path.resolve():
                return self.registry.unload_tool(info["name"])
        return False


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: ToolWatcher) -> None:
        self.w = watcher

    def _go(self, p) -> None:
        if p:
            if isinstance(p, bytes):
                p = p.decode()
            self.w.schedule(Path(p).resolve())

    def on_created(self, event):
        if not event.is_directory:
            self._go(event.src_path)

    on_modified = on_created
    on_deleted = on_created

    def on_moved(self, event):
        if not event.is_directory:
            self._go(event.src_path)
            self._go(getattr(event, "dest_path", None))
