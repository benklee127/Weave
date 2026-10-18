"""
Human approval gate for newly generated tools.

A verified tool must be approved before it is hot-loaded into the live MCP
server. Approval can be:

* automatic (``settings.auto_approve_tools``),
* interactive, via MCP elicitation when a FastMCP ``Context`` is bound to the
  current call (see :meth:`ApprovalGate.bind_context`), or
* deferred: the proposal is recorded as *pending* and later approved or
  rejected through the ``approve_tool`` / ``reject_tool`` MCP tools.

Also provides :class:`ApprovedOnlyRegistry`, a proxy handed to the file
watcher so that dropping an unapproved file into the tools directory can never
bypass the gate.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# (context, event loop) of the MCP request that is currently driving a pipeline run.
_bound_context: contextvars.ContextVar = contextvars.ContextVar("weave_approval_ctx", default=None)


class ApprovalStatus(str, Enum):
    """Outcome of an approval request."""

    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass
class ToolProposal:
    """A verified tool awaiting a go-live decision."""

    tool_id: str
    name: str
    path: str
    description: str = ""
    signature: str = ""
    test_path: Optional[str] = None
    task_id: Optional[str] = None
    tests_passed: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """Human-readable text shown to the approver."""
        lines = [
            f"Approve new tool '{self.name}'?",
            f"Signature: {self.signature or self.name + '(...)'}",
        ]
        if self.description:
            lines.append(f"Description: {self.description}")
        lines.append(f"Verified by {self.tests_passed} passing test(s).")
        lines.append(f"Source: {self.path}")
        return "\n".join(lines)


class ApprovalGate:
    """
    Decides whether a verified tool may go live.

    Args:
        auto_approve: Approve every proposal immediately.
        on_approve: ``callable(proposal) -> Any`` that activates an approved
            tool (loads it into the registry). Called by :meth:`approve`.
        on_reject: ``callable(proposal, reason) -> Any`` called by :meth:`reject`.
        state_path: Optional JSON file used to persist pending proposals
            across server restarts.
        elicit_timeout: Seconds to wait for an interactive answer before the
            proposal is left pending.
    """

    def __init__(
        self,
        auto_approve: bool = False,
        on_approve: Optional[Callable[[ToolProposal], Any]] = None,
        on_reject: Optional[Callable[[ToolProposal, str], Any]] = None,
        state_path: Optional[Path] = None,
        elicit_timeout: float = 300.0,
    ):
        self.auto_approve = auto_approve
        self.on_approve = on_approve
        self.on_reject = on_reject
        self.state_path = Path(state_path) if state_path else None
        self.elicit_timeout = elicit_timeout
        self._pending: Dict[str, ToolProposal] = {}
        self._lock = threading.RLock()
        self._load_state()

    @classmethod
    def from_settings(cls, settings: Any, state_path: Optional[Path] = None, **kwargs: Any):
        """Build a gate from Settings (``auto_approve_tools``)."""
        return cls(auto_approve=bool(settings.auto_approve_tools), state_path=state_path, **kwargs)

    # -- persistence -----------------------------------------------------
    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            for item in data:
                proposal = ToolProposal(**item)
                self._pending[proposal.tool_id] = proposal
        except (OSError, ValueError, TypeError) as e:
            logger.warning(f"Could not load pending approvals from {self.state_path}: {e}")

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [p.to_dict() for p in self._pending.values()]
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning(f"Could not persist pending approvals: {e}")

    # -- interactive context ---------------------------------------------
    @contextlib.contextmanager
    def bind_context(self, ctx: Any, loop: Optional[asyncio.AbstractEventLoop] = None) -> Iterator[None]:
        """
        Make ``ctx`` (a FastMCP Context) available for elicitation while the
        ``with`` block runs. The pipeline may run in a worker thread
        (``asyncio.to_thread`` copies this binding); ``loop`` is the event
        loop that owns ``ctx``.
        """
        if loop is None:
            loop = asyncio.get_running_loop()
        token = _bound_context.set((ctx, loop))
        try:
            yield
        finally:
            _bound_context.reset(token)

    def _elicit(self, proposal: ToolProposal) -> Optional[ApprovalStatus]:
        """Ask the connected client; None means no usable answer."""
        bound = _bound_context.get()
        if bound is None:
            return None
        ctx, loop = bound
        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                ctx.elicit(proposal.summary(), response_type=["approve", "reject"]), loop
            )
            response = future.result(timeout=self.elicit_timeout)
        except BaseException as e:  # client without elicitation support, timeout, ...
            if future is not None:
                future.cancel()
            logger.info(f"Interactive approval unavailable for {proposal.tool_id}: {e!r}")
            return None
        if getattr(response, "action", None) != "accept":
            return None
        answer = str(getattr(response, "data", "")).strip().lower()
        if answer == "approve":
            return ApprovalStatus.APPROVED
        if answer == "reject":
            return ApprovalStatus.REJECTED
        return None

    # -- decisions ---------------------------------------------------------
    def request(self, proposal: ToolProposal) -> ApprovalStatus:
        """
        Ask for approval of ``proposal``.

        Returns APPROVED / REJECTED when decided now, PENDING when the
        decision was deferred (the proposal is then listed by
        :meth:`list_pending`). This method does not activate the tool; the
        caller acts on the returned status.
        """
        if self.auto_approve:
            logger.info(f"Auto-approved tool {proposal.tool_id}")
            return ApprovalStatus.APPROVED

        with self._lock:
            if proposal.tool_id in self._pending:
                return ApprovalStatus.PENDING

        decision = self._elicit(proposal)
        if decision is not None:
            logger.info(f"Interactive decision for {proposal.tool_id}: {decision.value}")
            return decision

        with self._lock:
            self._pending[proposal.tool_id] = proposal
            self._save_state()
        logger.info(f"Tool {proposal.tool_id} awaiting approval")
        return ApprovalStatus.PENDING

    def list_pending(self) -> List[Dict[str, Any]]:
        """Pending proposals, oldest first."""
        with self._lock:
            return [p.to_dict() for p in sorted(self._pending.values(), key=lambda p: p.created_at)]

    def get_pending(self, tool_id: str) -> Optional[ToolProposal]:
        with self._lock:
            return self._pending.get(tool_id)

    def approve(self, tool_id: str) -> Dict[str, Any]:
        """
        Approve a pending tool and activate it through ``on_approve``.

        Raises:
            KeyError: If no such pending approval exists.
        """
        with self._lock:
            proposal = self._pending.get(tool_id)
        if proposal is None:
            raise KeyError(f"No pending approval for tool '{tool_id}'")
        activation = self.on_approve(proposal) if self.on_approve else None
        with self._lock:
            self._pending.pop(tool_id, None)
            self._save_state()
        logger.info(f"Tool {tool_id} approved")
        return {"tool_id": tool_id, "status": ApprovalStatus.APPROVED.value, "activation": activation}

    def reject(self, tool_id: str, reason: str = "") -> Dict[str, Any]:
        """
        Reject a pending tool.

        Raises:
            KeyError: If no such pending approval exists.
        """
        with self._lock:
            proposal = self._pending.get(tool_id)
        if proposal is None:
            raise KeyError(f"No pending approval for tool '{tool_id}'")
        if self.on_reject:
            self.on_reject(proposal, reason)
        with self._lock:
            self._pending.pop(tool_id, None)
            self._save_state()
        logger.info(f"Tool {tool_id} rejected: {reason or 'no reason given'}")
        return {"tool_id": tool_id, "status": ApprovalStatus.REJECTED.value, "reason": reason}


class ApprovedOnlyRegistry:
    """
    Registry proxy for the hot-reload file watcher.

    The watcher reloads any changed ``*.py`` in the tools directory. Newly
    generated tools are written there *before* they are approved, so the
    proxy only forwards reloads of files that are already loaded (i.e.
    approved). Everything else delegates to the real registry.
    """

    def __init__(self, registry: Any):
        self._registry = registry

    def reload_tool(self, path: Any) -> Optional[str]:
        resolved = str(Path(path).resolve())
        if any(Path(info["path"]).resolve() == Path(resolved) for info in self._registry.list_loaded()):
            return self._registry.reload_tool(path)
        logger.info(f"Ignoring change to {Path(path).name}: tool is not approved/loaded")
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)
