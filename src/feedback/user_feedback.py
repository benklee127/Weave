"""
User feedback store.

Ratings (1-5) are appended to a JSONL file. Each rating is also fed to the
Librarian as a usage outcome (>= 4 success, <= 2 failure, 3 neutral), and
tools whose ratings are low are flagged so the Janitor re-tests them.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HIGH_RATING = 4


class FeedbackStore:
    """Append-only JSONL feedback log with simple aggregation."""

    def __init__(
        self,
        path: Optional[Path] = None,
        librarian: Optional[Any] = None,
        low_rating_threshold: float = 2.5,
        min_ratings: int = 1,
    ):
        """
        Args:
            path: JSONL file (defaults to <project>/data/feedback.jsonl)
            librarian: Optional LibrarianAgent receiving record_outcome calls
            low_rating_threshold: Mean rating below which a tool is flagged
            min_ratings: Minimum number of ratings before a tool can be flagged
        """
        if path is None:
            path = Path(__file__).resolve().parent.parent.parent / "data" / "feedback.jsonl"
        self.path = Path(path)
        self.librarian = librarian
        self.low_rating_threshold = low_rating_threshold
        self.min_ratings = min_ratings
        self._lock = threading.RLock()

    def record(self, tool_id: str, rating: int, comment: str = "") -> Dict[str, Any]:
        """
        Record a rating.

        Raises:
            ValueError: If rating is not an integer in 1..5
        """
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError("rating must be an integer between 1 and 5")
        entry = {
            "tool_id": tool_id,
            "rating": rating,
            "comment": comment or "",
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        if self.librarian is not None and rating != 3:
            try:
                self.librarian.record_outcome(tool_id, rating >= HIGH_RATING)
            except Exception as e:
                logger.warning(f"Could not feed feedback to librarian: {e}")
        return entry

    def all(self, tool_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all entries (optionally for one tool); corrupt lines are skipped."""
        with self._lock:
            if not self.path.is_file():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
        out: List[Dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                logger.warning("Skipping corrupt feedback line")
                continue
            if isinstance(entry, dict) and (tool_id is None or entry.get("tool_id") == tool_id):
                out.append(entry)
        return out

    def summary(self, tool_id: str) -> Dict[str, Any]:
        """Return count and mean rating for a tool."""
        ratings = [e["rating"] for e in self.all(tool_id) if isinstance(e.get("rating"), int)]
        return {
            "tool_id": tool_id,
            "count": len(ratings),
            "mean_rating": (sum(ratings) / len(ratings)) if ratings else None,
        }

    def flagged_tools(self) -> List[str]:
        """Tool ids whose mean rating is below the threshold."""
        by_tool: Dict[str, List[int]] = {}
        for e in self.all():
            if isinstance(e.get("rating"), int) and e.get("tool_id"):
                by_tool.setdefault(e["tool_id"], []).append(e["rating"])
        return sorted(
            t for t, rs in by_tool.items()
            if len(rs) >= self.min_ratings and sum(rs) / len(rs) < self.low_rating_threshold
        )
