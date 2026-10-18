"""
Librarian Agent: Semantic Tool Search and Library Lifecycle

This agent searches the tool library for existing tools that match
a task specification, enabling tool reuse instead of regeneration. It also
manages tool lifecycle (status, outcomes, duplicate deprecation).
"""

import ast
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from src.core.schemas import TaskNode
from src.memory.vector_store import VectorStore
from src.memory.embeddings import get_embedding_generator
from src.memory.tool_index import ToolIndexer, compute_source_hash

logger = logging.getLogger(__name__)

VALID_STATUSES = ("active", "broken", "deprecated")
DEFAULT_SIMILARITY_THRESHOLD = 0.75
DEFAULT_HIGH_CONFIDENCE = 0.85


@dataclass
class ToolMatch:
    """Represents a tool match from semantic search."""
    tool_id: str
    similarity: float
    metadata: Dict[str, Any]
    rank: int


def _load_settings() -> Any:
    """Best-effort settings load (None if config is unavailable)."""
    try:
        from src.config import get_settings
        return get_settings()
    except Exception as e:  # pragma: no cover - depends on environment
        logger.debug(f"Settings unavailable, using defaults: {e}")
        return None


class LibrarianAgent:
    """
    Agent responsible for semantic tool search, retrieval and lifecycle.

    The Librarian searches the vector database for existing tools that
    match a task specification, enabling fast tool reuse. Only ``active``
    tools are ever returned from searches.
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        similarity_threshold: Optional[float] = None,
        high_confidence_threshold: Optional[float] = None,
        settings: Optional[Any] = None,
        embedding_generator: Optional[Any] = None,
        tool_indexer: Optional[ToolIndexer] = None,
    ):
        """
        Initialize the Librarian Agent.

        Args:
            vector_store: VectorStore instance (created from settings if None)
            similarity_threshold: Minimum similarity for a match (0-1)
            high_confidence_threshold: Similarity for high-confidence matches (0-1)
            settings: Optional Settings object supplying defaults
            embedding_generator: Optional pre-built embedding generator
            tool_indexer: Optional pre-built ToolIndexer
        """
        self.agent_id = "librarian"
        self._lock = threading.RLock()

        needs_settings = (
            settings is None and (
                vector_store is None
                or similarity_threshold is None
                or high_confidence_threshold is None
                or (embedding_generator is None and tool_indexer is None)
            )
        )
        if needs_settings:
            settings = _load_settings()

        if similarity_threshold is None:
            similarity_threshold = getattr(
                settings, "reuse_similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD
            )
        if high_confidence_threshold is None:
            high_confidence_threshold = getattr(
                settings, "reuse_high_confidence", DEFAULT_HIGH_CONFIDENCE
            )

        if vector_store is None:
            db_path = getattr(settings, "vector_db_path", None)
            vector_store = VectorStore(str(db_path)) if db_path else VectorStore()
        self.vector_store = vector_store

        if embedding_generator is None:
            if tool_indexer is not None:
                embedding_generator = tool_indexer.embedding_generator
            else:
                embedding_generator = get_embedding_generator(
                    getattr(settings, "embedding_model", "all-MiniLM-L6-v2")
                )
        self.embedding_generator = embedding_generator
        self.tool_indexer = tool_indexer or ToolIndexer(
            vector_store=self.vector_store,
            embedding_generator=self.embedding_generator,
        )

        self.similarity_threshold = similarity_threshold
        self.high_confidence_threshold = high_confidence_threshold

        logger.info(
            f"Librarian initialized with {self.vector_store.count()} indexed tools"
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_active(
        self,
        query_embedding: List[float],
        n_results: int,
        include_inactive: bool = False,
    ) -> List[ToolMatch]:
        """Run a vector search, dropping non-active tools, ranked by similarity."""
        total = self.vector_store.count()
        if total == 0:
            return []
        # Over-fetch so inactive tools do not starve the result list.
        fetch = min(total, max(n_results * 4, 20))
        results = self.vector_store.search(
            query_embedding=query_embedding, n_results=fetch
        )
        matches: List[ToolMatch] = []
        for tool_id, sim, meta in zip(
            results["ids"], results["similarities"], results["metadatas"]
        ):
            meta = meta or {}
            if not include_inactive and meta.get("status", "active") != "active":
                continue
            matches.append(
                ToolMatch(tool_id=tool_id, similarity=sim, metadata=meta, rank=0)
            )
        matches.sort(key=lambda m: m.similarity, reverse=True)
        matches = matches[:n_results]
        for i, m in enumerate(matches):
            m.rank = i + 1
        return matches

    def search_for_task(
        self,
        task: TaskNode,
        n_results: int = 5
    ) -> List[ToolMatch]:
        """
        Search for existing active tools that match a task specification.

        Args:
            task: TaskNode to search for
            n_results: Maximum number of results to return

        Returns:
            List of ToolMatch objects, sorted by similarity (highest first)
        """
        logger.info(f"Searching for tools matching task: {task.id}")
        logger.debug(f"Task description: {task.description}")

        if self.vector_store.count() == 0:
            logger.info("Vector store is empty - no tools to search")
            return []

        query_embedding = self._generate_query_embedding(task)
        candidates = self._search_active(query_embedding, n_results)

        matches = []
        for m in candidates:
            if m.similarity < self.similarity_threshold:
                logger.debug(
                    f"Filtering out {m.tool_id} (similarity {m.similarity:.3f} "
                    f"< threshold {self.similarity_threshold})"
                )
                continue
            matches.append(m)

        if matches:
            logger.info(
                f"Found {len(matches)} matching tools "
                f"(best: {matches[0].metadata.get('name', 'unknown')} "
                f"with similarity {matches[0].similarity:.3f})"
            )
        else:
            logger.info("No matching tools found above similarity threshold")

        return matches

    def search_text(
        self,
        query: str,
        n_results: int = 5,
        include_inactive: bool = False,
    ) -> List[ToolMatch]:
        """
        Free-text search over the library (no threshold applied).

        Useful for the MCP ``search_tools`` endpoint.
        """
        embedding = self.embedding_generator.generate_embedding(query)
        return self._search_active(embedding, n_results, include_inactive)

    def get_best_match(self, task: TaskNode) -> Optional[ToolMatch]:
        """
        Get the best matching tool for a task, if any.

        Args:
            task: TaskNode to search for

        Returns:
            Best ToolMatch or None if no good match found
        """
        matches = self.search_for_task(task, n_results=1)

        if matches and matches[0].similarity >= self.similarity_threshold:
            return matches[0]

        return None

    def has_high_confidence_match(self, task: TaskNode) -> bool:
        """
        Check if there's a high-confidence match for a task.

        Args:
            task: TaskNode to check

        Returns:
            True if high-confidence match exists
        """
        best_match = self.get_best_match(task)

        if best_match and best_match.similarity >= self.high_confidence_threshold:
            logger.info(
                f"High-confidence match found: {best_match.metadata.get('name')} "
                f"(similarity: {best_match.similarity:.3f})"
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_match(self, match: ToolMatch, task: Optional[TaskNode] = None) -> bool:
        """
        Check that a match is safe to reuse.

        Requires: the source file exists, its sha256 equals the indexed
        ``source_hash``, the target function exists, and (when the task gives
        an input schema) the function's parameters are compatible with it:
        every required parameter is provided by the task and every task input
        is accepted (or the function takes ``**kwargs``).

        Args:
            match: Candidate ToolMatch
            task: TaskNode the match is meant to serve

        Returns:
            True if the match can be reused as is
        """
        meta = match.metadata or {}
        source_path = meta.get("source_path")
        if not source_path:
            return False
        path = Path(source_path)
        if not path.is_file():
            logger.warning(f"verify_match: source missing for {match.tool_id}")
            return False
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"verify_match: cannot read {path}: {e}")
            return False

        expected = meta.get("source_hash")
        if expected and compute_source_hash(source) != expected:
            logger.warning(f"verify_match: hash mismatch for {match.tool_id}")
            return False

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        fn_name = meta.get("function_name") or meta.get("name")
        fn = next(
            (n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == fn_name),
            None,
        )
        if fn is None:
            logger.warning(f"verify_match: function {fn_name} not in {path}")
            return False

        props = ((task.input_schema or {}).get("properties") if task else None)
        if not props:
            return True

        args = fn.args
        positional = list(args.posonlyargs) + list(args.args)
        n_def = len(args.defaults)
        required = {a.arg for a in positional[: len(positional) - n_def]}
        required |= {
            a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None
        }
        accepted = {a.arg for a in positional} | {a.arg for a in args.kwonlyargs}

        provided = set(props.keys())
        missing = required - provided
        extra = provided - accepted
        if missing or (extra and args.kwarg is None):
            logger.info(
                f"verify_match: signature incompatible for {match.tool_id} "
                f"(missing={sorted(missing)}, extra={sorted(extra)})"
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Registration and lifecycle
    # ------------------------------------------------------------------

    def register_tool(
        self,
        tool_id: str,
        source_path: str,
        test_result: Any = None
    ) -> None:
        """
        Register (or re-register) a tool in the library.

        Idempotent: indexes the tool for future semantic search.

        Args:
            tool_id: Unique tool identifier
            source_path: Path to tool source code
            test_result: Optional test execution results
        """
        logger.info(f"Registering tool in library: {tool_id}")

        try:
            with self._lock:
                self.tool_indexer.index_tool(
                    tool_id=tool_id,
                    source_path=source_path,
                    test_result=test_result
                )

            logger.info(
                f"Tool registered successfully: {tool_id} "
                f"(total tools: {self.vector_store.count()})"
            )

        except Exception as e:
            logger.error(f"Failed to register tool {tool_id}: {e}")
            raise

    def record_outcome(self, tool_id: str, success: bool) -> bool:
        """
        Record a usage outcome for a tool.

        Increments usage_count and success_count/fail_count; a success also
        refreshes ``last_verified_at``.

        Returns:
            True if the tool exists
        """
        with self._lock:
            current = self.vector_store.get_tool(tool_id)
            if current is None:
                logger.warning(f"record_outcome: unknown tool {tool_id}")
                return False
            meta = current["metadata"]
            fields: Dict[str, Any] = {
                "usage_count": int(meta.get("usage_count", 0)) + 1,
                "success_count": int(meta.get("success_count", 0)) + (1 if success else 0),
                "fail_count": int(meta.get("fail_count", 0)) + (0 if success else 1),
            }
            if success:
                fields["last_verified_at"] = datetime.now().isoformat()
            return self.vector_store.update_metadata(tool_id, fields)

    def set_status(self, tool_id: str, status: str) -> bool:
        """
        Set a tool's lifecycle status (active | broken | deprecated).

        Returns:
            True if the tool exists

        Raises:
            ValueError: If status is not valid
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}', expected one of {VALID_STATUSES}"
            )
        with self._lock:
            ok = self.vector_store.update_metadata(
                tool_id,
                {"status": status, "updated_at": datetime.now().isoformat()},
            )
        if ok:
            logger.info(f"Tool {tool_id} status -> {status}")
        return ok

    @staticmethod
    def _decode(tool_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten stored metadata into a tool dict with parsed schemas."""
        out = dict(metadata or {})
        for key in ("input_schema", "output_schema"):
            raw = out.get(key)
            if isinstance(raw, str):
                try:
                    out[key] = json.loads(raw)
                except ValueError:
                    out[key] = {}
            elif raw is None:
                out[key] = {}
        out.setdefault("status", "active")
        succ = int(out.get("success_count", 0))
        fail = int(out.get("fail_count", 0))
        out["success_rate"] = (succ / (succ + fail)) if (succ + fail) else 1.0
        out["id"] = tool_id
        return out

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        """Return a tool's metadata as a flat dict (with ``id``), or None."""
        current = self.vector_store.get_tool(tool_id)
        if current is None:
            return None
        return self._decode(tool_id, current["metadata"])

    def list_tools(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List tools, optionally filtered by status."""
        tools = [
            self._decode(t["id"], t["metadata"])
            for t in self.vector_store.list_all_tools()
        ]
        if status is not None:
            tools = [t for t in tools if t["status"] == status]
        tools.sort(key=lambda t: t.get("registered_at", ""))
        return tools

    def deprecate_duplicates(self, threshold: float = 0.97) -> List[str]:
        """
        Deprecate near-duplicate active tools, keeping the best of each group.

        Tools are ranked by success rate, usage, version then registration
        time (newer wins ties).

        Returns:
            IDs of tools that were deprecated
        """
        entries = [
            e for e in self.vector_store.list_with_embeddings()
            if (e["metadata"] or {}).get("status", "active") == "active"
        ]
        if len(entries) < 2:
            return []

        mat = np.array([e["embedding"] for e in entries], dtype=float)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        sims = (mat / norms) @ (mat / norms).T

        def rank(e: Dict[str, Any]) -> tuple:
            d = self._decode(e["id"], e["metadata"])
            ver = tuple(
                int(p) if p.isdigit() else 0
                for p in str(d.get("version", "1.0.0")).split(".")
            )
            return (
                d["success_rate"], int(d.get("usage_count", 0)), ver,
                d.get("registered_at", ""),
            )

        order = sorted(range(len(entries)), key=lambda i: rank(entries[i]), reverse=True)
        kept: List[int] = []
        deprecated: List[str] = []
        for i in order:
            if any(sims[i, k] >= threshold for k in kept):
                deprecated.append(entries[i]["id"])
            else:
                kept.append(i)

        for tool_id in deprecated:
            self.set_status(tool_id, "deprecated")
        if deprecated:
            logger.info(f"Deprecated {len(deprecated)} duplicate tools: {deprecated}")
        return deprecated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _generate_query_embedding(self, task: TaskNode) -> List[float]:
        """
        Generate query embedding from task specification.

        Args:
            task: TaskNode

        Returns:
            Embedding vector
        """
        parts = [task.description]

        if task.input_schema and task.input_schema.get('properties'):
            params = list(task.input_schema['properties'].keys())
            parts.append(f"Parameters: {', '.join(params)}")

        if task.output_schema and task.output_schema.get('properties'):
            outputs = list(task.output_schema['properties'].keys())
            parts.append(f"Returns: {', '.join(outputs)}")

        query_text = "\n".join(parts)

        logger.debug(f"Query text for embedding: {query_text[:100]}...")

        return self.embedding_generator.generate_embedding(query_text)

    def get_library_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the tool library.

        Returns:
            Dictionary with library statistics
        """
        total_tools = self.vector_store.count()
        all_tools = self.vector_store.list_all_tools()

        def count(status: str) -> int:
            return sum(
                1 for t in all_tools
                if (t['metadata'] or {}).get('status', 'active') == status
            )

        return {
            'total_tools': total_tools,
            'active_tools': count('active'),
            'broken_tools': count('broken'),
            'deprecated_tools': count('deprecated'),
            'similarity_threshold': self.similarity_threshold,
            'high_confidence_threshold': self.high_confidence_threshold,
        }
