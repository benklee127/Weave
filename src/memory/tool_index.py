"""
Tool indexing system for semantic search.

This module extracts metadata from generated tools and indexes them
in the vector database, including lifecycle metadata (status, version,
source hash, usage counters).
"""

import ast
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.schemas import ToolMetadata, TestExecutionResult
from src.memory.vector_store import VectorStore
from src.memory.embeddings import get_embedding_generator

logger = logging.getLogger(__name__)

_JSON_TYPES = {
    "str": "string", "int": "integer", "float": "number", "bool": "boolean",
    "list": "array", "List": "array", "tuple": "array", "Tuple": "array",
    "set": "array", "Set": "array", "Sequence": "array",
    "dict": "object", "Dict": "object", "Mapping": "object",
    "bytes": "string", "None": "null",
}


def compute_source_hash(source_code: str) -> str:
    """Return the sha256 hex digest of the source text."""
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def bump_patch_version(version: str) -> str:
    """Bump the patch component of a semantic version string."""
    parts = (version or "1.0.0").split(".")
    try:
        nums = [int(p) for p in parts[:3]]
    except ValueError:
        return "1.0.1"
    while len(nums) < 3:
        nums.append(0)
    nums[2] += 1
    return ".".join(str(n) for n in nums)


def annotation_to_schema(annotation: Optional[ast.expr]) -> Dict[str, Any]:
    """Convert a type annotation AST node to a (best effort) JSON schema."""
    if annotation is None:
        return {}
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return {"type": "null"}
    if isinstance(annotation, ast.Name):
        t = _JSON_TYPES.get(annotation.id)
        return {"type": t} if t else {}
    if isinstance(annotation, ast.Attribute):
        t = _JSON_TYPES.get(annotation.attr)
        return {"type": t} if t else {}
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        base_name = base.id if isinstance(base, ast.Name) else (
            base.attr if isinstance(base, ast.Attribute) else ""
        )
        if base_name == "Optional":
            return annotation_to_schema(annotation.slice)
        if base_name in ("List", "list", "Sequence", "Set", "set"):
            schema: Dict[str, Any] = {"type": "array"}
            items = annotation_to_schema(annotation.slice)
            if items:
                schema["items"] = items
            return schema
        t = _JSON_TYPES.get(base_name)
        return {"type": t} if t else {}
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left = annotation_to_schema(annotation.left)
        right = annotation_to_schema(annotation.right)
        if right.get("type") == "null":
            return left
        if left.get("type") == "null":
            return right
    return {}


class ToolIndexer:
    """
    Indexes generated tools for semantic search.

    Extracts metadata from Python code and stores embeddings in the vector database.
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_generator: Optional[Any] = None,
    ):
        """
        Initialize the tool indexer.

        Args:
            vector_store: VectorStore instance (creates new if None)
            embedding_model: Name of embedding model to use
            embedding_generator: Pre-built generator (overrides embedding_model)
        """
        self.vector_store = vector_store or VectorStore()
        self.embedding_generator = (
            embedding_generator or get_embedding_generator(embedding_model)
        )

        logger.info("Tool indexer initialized")

    def index_tool(
        self,
        tool_id: str,
        source_path: str,
        test_result: Optional[TestExecutionResult] = None,
        function_name: Optional[str] = None,
    ) -> ToolMetadata:
        """
        Index a generated tool (idempotent upsert).

        Re-indexing identical source keeps the version; changed source bumps the
        patch version. Usage counters and registration time are preserved.

        Args:
            tool_id: Unique tool identifier
            source_path: Path to Python source file
            test_result: Optional test execution results
            function_name: Explicit function to expose (auto-detected if None)

        Returns:
            ToolMetadata object

        Raises:
            ValueError: If source file is invalid or cannot be parsed
        """
        logger.info(f"Indexing tool: {tool_id}")

        source_file = Path(source_path)
        if not source_file.exists():
            raise ValueError(f"Source file not found: {source_path}")

        source_code = source_file.read_text(encoding="utf-8")
        meta = self._extract_metadata(source_code, source_path, function_name)
        source_hash = compute_source_hash(source_code)
        now = datetime.now()

        existing = self.vector_store.get_tool(tool_id)
        prev = (existing or {}).get("metadata") or {}

        version = prev.get("version") or "1.0.0"
        if prev and prev.get("source_hash") != source_hash:
            version = bump_patch_version(version)

        verified = bool(test_result and test_result.success)
        failed = bool(test_result and not test_result.success)
        status = "broken" if failed else "active"

        last_verified = prev.get("last_verified_at")
        if verified:
            last_verified = now.isoformat()

        usage = int(prev.get("usage_count", 0))
        succ = int(prev.get("success_count", 0))
        fail = int(prev.get("fail_count", 0))

        embedding = self.embedding_generator.generate_tool_embedding(
            tool_name=meta["name"],
            description=meta["description"],
            signature=meta["signature"],
            docstring=meta["docstring"],
        )

        stored = {
            "name": meta["name"],
            "function_name": meta["name"],
            "module_name": source_file.stem,
            "description": meta["description"],
            "signature": meta["signature"],
            "docstring": meta["docstring"],
            "source_path": str(source_file),
            "source_hash": source_hash,
            "status": status,
            "version": version,
            "usage_count": usage,
            "success_count": succ,
            "fail_count": fail,
            "last_verified_at": last_verified,
            "registered_at": prev.get("registered_at") or now.isoformat(),
            "created_at": prev.get("created_at") or now.isoformat(),
            "updated_at": now.isoformat(),
            "input_schema": meta["input_schema"],
            "output_schema": meta["output_schema"],
        }
        self.vector_store.upsert(tool_id, embedding, stored)

        logger.info(f"Tool indexed successfully: {tool_id} ({meta['name']} v{version})")

        total = succ + fail
        return ToolMetadata(
            id=tool_id,
            name=meta["name"],
            description=meta["description"],
            signature=meta["signature"],
            source_code=source_code,
            docstring=meta["docstring"],
            input_schema=meta["input_schema"],
            output_schema=meta["output_schema"],
            test_results=test_result,
            version=version,
            status=status,
            created_at=datetime.fromisoformat(stored["created_at"]),
            last_verified_at=(
                datetime.fromisoformat(last_verified) if last_verified else None
            ),
            usage_count=usage,
            success_rate=(succ / total) if total else (1.0 if verified else 0.0),
        )

    def update_tool(
        self,
        tool_id: str,
        source_path: str,
        test_result: Optional[TestExecutionResult] = None,
    ) -> ToolMetadata:
        """Update an existing tool in the index (same as idempotent re-index)."""
        return self.index_tool(tool_id, source_path, test_result)

    # ------------------------------------------------------------------
    # AST helpers
    # ------------------------------------------------------------------

    def _select_function(
        self, tree: ast.Module, function_name: Optional[str] = None
    ) -> Optional[ast.FunctionDef]:
        """Pick the function to expose: explicit name, else the main public one."""
        funcs = [
            n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if function_name:
            for f in funcs:
                if f.name == function_name:
                    return f
            return None
        public = [f for f in funcs if not f.name.startswith("_")]
        if not public:
            return None
        # Prefer public functions not called by other functions (helpers are).
        called = set()
        for f in funcs:
            for node in ast.walk(f):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id != f.name:
                        called.add(node.func.id)
        entry = [f for f in public if f.name not in called] or public
        documented = [f for f in entry if ast.get_docstring(f)]
        return (documented or entry)[0]

    def _extract_metadata(
        self,
        source_code: str,
        source_path: str,
        function_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract metadata from Python source code using AST.

        Raises:
            ValueError: If code cannot be parsed or contains no functions
        """
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python syntax in {source_path}: {e}")

        function_def = self._select_function(tree, function_name)
        if function_def is None:
            raise ValueError(f"No function definition found in {source_path}")

        docstring = ast.get_docstring(function_def) or ""
        lines = [ln.strip() for ln in docstring.split("\n") if ln.strip()]
        description = lines[0] if lines else ""

        return {
            "name": function_def.name,
            "signature": self._build_signature(function_def),
            "docstring": docstring,
            "description": description,
            "input_schema": self._build_input_schema(function_def),
            "output_schema": annotation_to_schema(function_def.returns),
        }

    def _build_input_schema(self, fn: ast.FunctionDef) -> Dict[str, Any]:
        """Build a JSON schema for the function's parameters from type hints."""
        args = fn.args
        positional = list(args.posonlyargs) + list(args.args)
        defaults: List[Optional[ast.expr]] = (
            [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        )
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for arg, default in zip(positional, defaults):
            properties[arg.arg] = annotation_to_schema(arg.annotation)
            if default is None:
                required.append(arg.arg)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            properties[arg.arg] = annotation_to_schema(arg.annotation)
            if default is None:
                required.append(arg.arg)
        schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _build_signature(self, function_def: ast.FunctionDef) -> str:
        """Build a function signature string from AST."""
        params = []
        for arg in function_def.args.args:
            param_str = arg.arg
            if arg.annotation:
                param_str += f": {ast.unparse(arg.annotation)}"
            params.append(param_str)

        return_type = ""
        if function_def.returns:
            return_type = f" -> {ast.unparse(function_def.returns)}"

        return f"def {function_def.name}({', '.join(params)}){return_type}"
