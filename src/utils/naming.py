"""
Naming helpers shared by the Auditor and Architect.

Generated files must have unique, non-colliding names so that two tasks with
similar descriptions (or a regenerated task) never overwrite each other.
"""

import hashlib
import keyword
import re
from pathlib import Path


def derive_function_name(description: str, max_length: int = 50) -> str:
    """
    Derive a valid snake_case Python identifier from a task description.

    Examples:
        "Calculate compound interest" -> "calculate_compound_interest"
    """
    name = re.sub(r"[^a-z0-9\s]", "", (description or "").lower())
    name = re.sub(r"\s+", "_", name.strip())[:max_length].strip("_")
    if name and not name[0].isalpha():
        name = "func_" + name
    if not name:
        name = "generated_function"
    if keyword.iskeyword(name):
        name += "_fn"
    return name


def short_hash(*parts: str, length: int = 8) -> str:
    """Short sha256 hex digest of the given strings."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:length]


def _safe_id(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", task_id)


def unique_path(directory: Path, prefix: str, function_name: str, task_id: str, content: str) -> Path:
    """
    Build ``<directory>/<prefix><function_name>_<taskid>_<hash>.py``.

    The hash covers task id and content. In the unlikely event the file
    exists with different content, a numeric suffix is appended.
    """
    base = f"{prefix}{function_name}_{_safe_id(task_id)}_{short_hash(task_id, content)}"
    path = Path(directory) / f"{base}.py"
    counter = 1
    while path.exists() and path.read_text(encoding="utf-8") != content:
        path = Path(directory) / f"{base}_{counter}.py"
        counter += 1
    return path
