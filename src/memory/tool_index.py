"""
Tool indexing system for semantic search.

This module extracts metadata from generated tools and indexes them
in the vector database.
"""

import ast
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from src.core.schemas import ToolMetadata, TestExecutionResult
from src.memory.vector_store import VectorStore
from src.memory.embeddings import get_embedding_generator

logger = logging.getLogger(__name__)


class ToolIndexer:
    """
    Indexes generated tools for semantic search.

    Extracts metadata from Python code and stores embeddings in the vector database.
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        embedding_model: str = "all-MiniLM-L6-v2"
    ):
        """
        Initialize the tool indexer.

        Args:
            vector_store: VectorStore instance (creates new if None)
            embedding_model: Name of embedding model to use
        """
        self.vector_store = vector_store or VectorStore()
        self.embedding_generator = get_embedding_generator(embedding_model)

        logger.info("Tool indexer initialized")

    def index_tool(
        self,
        tool_id: str,
        source_path: str,
        test_result: Optional[TestExecutionResult] = None
    ) -> ToolMetadata:
        """
        Index a generated tool.

        Extracts metadata from the source code and adds to vector database.

        Args:
            tool_id: Unique tool identifier
            source_path: Path to Python source file
            test_result: Optional test execution results

        Returns:
            ToolMetadata object

        Raises:
            ValueError: If source file is invalid or cannot be parsed
        """
        logger.info(f"Indexing tool: {tool_id}")

        # Read source code
        source_file = Path(source_path)
        if not source_file.exists():
            raise ValueError(f"Source file not found: {source_path}")

        source_code = source_file.read_text()

        # Extract metadata from AST
        metadata = self._extract_metadata(source_code, source_path)

        # Generate embedding
        embedding = self.embedding_generator.generate_tool_embedding(
            tool_name=metadata['name'],
            description=metadata['description'],
            signature=metadata['signature'],
            docstring=metadata['docstring']
        )

        # Create ToolMetadata object
        tool_metadata = ToolMetadata(
            id=tool_id,
            name=metadata['name'],
            description=metadata['description'],
            signature=metadata['signature'],
            source_code=source_code,
            docstring=metadata['docstring'],
            input_schema=metadata.get('input_schema', {}),
            output_schema=metadata.get('output_schema', {}),
            test_results=test_result,
            version="1.0.0",
            status="active",
            created_at=datetime.now(),
            last_verified_at=datetime.now() if test_result and test_result.success else None,
            usage_count=0,
            success_rate=1.0 if test_result and test_result.success else 0.0,
        )

        # Store in vector database
        self.vector_store.add_tool(
            tool_id=tool_id,
            embedding=embedding,
            metadata={
                'name': tool_metadata.name,
                'description': tool_metadata.description,
                'signature': tool_metadata.signature,
                'docstring': tool_metadata.docstring,
                'source_path': source_path,
                'status': tool_metadata.status,
                'version': tool_metadata.version,
                'created_at': tool_metadata.created_at.isoformat(),
            }
        )

        logger.info(f"Tool indexed successfully: {tool_id} ({metadata['name']})")

        return tool_metadata

    def update_tool(
        self,
        tool_id: str,
        source_path: str,
        test_result: Optional[TestExecutionResult] = None
    ) -> ToolMetadata:
        """
        Update an existing tool in the index.

        Args:
            tool_id: Tool identifier
            source_path: Updated source path
            test_result: Updated test results

        Returns:
            Updated ToolMetadata
        """
        logger.info(f"Updating tool index: {tool_id}")

        # Extract updated metadata
        source_code = Path(source_path).read_text()
        metadata = self._extract_metadata(source_code, source_path)

        # Generate new embedding
        embedding = self.embedding_generator.generate_tool_embedding(
            tool_name=metadata['name'],
            description=metadata['description'],
            signature=metadata['signature'],
            docstring=metadata['docstring']
        )

        # Update in vector store
        self.vector_store.update_tool(
            tool_id=tool_id,
            embedding=embedding,
            metadata={
                'name': metadata['name'],
                'description': metadata['description'],
                'signature': metadata['signature'],
                'docstring': metadata['docstring'],
                'source_path': source_path,
                'status': 'active',
                'updated_at': datetime.now().isoformat(),
            }
        )

        # Create updated ToolMetadata
        tool_metadata = ToolMetadata(
            id=tool_id,
            name=metadata['name'],
            description=metadata['description'],
            signature=metadata['signature'],
            source_code=source_code,
            docstring=metadata['docstring'],
            test_results=test_result,
            status="active",
            last_verified_at=datetime.now() if test_result and test_result.success else None,
        )

        logger.info(f"Tool index updated: {tool_id}")

        return tool_metadata

    def _extract_metadata(self, source_code: str, source_path: str) -> Dict[str, Any]:
        """
        Extract metadata from Python source code using AST.

        Args:
            source_code: Python source code
            source_path: Path to source file (for error messages)

        Returns:
            Dictionary with name, signature, docstring, description

        Raises:
            ValueError: If code cannot be parsed or contains no functions
        """
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python syntax in {source_path}: {e}")

        # Find the first function definition
        function_def = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                function_def = node
                break

        if not function_def:
            raise ValueError(f"No function definition found in {source_path}")

        # Extract function name
        name = function_def.name

        # Extract docstring
        docstring = ast.get_docstring(function_def) or ""

        # Extract first line of docstring as description
        description = ""
        if docstring:
            lines = [line.strip() for line in docstring.split('\n') if line.strip()]
            description = lines[0] if lines else ""

        # Build function signature
        signature = self._build_signature(function_def)

        # Try to extract input/output schemas from docstring
        # (This is basic - could be enhanced with proper parsing)
        input_schema = {}
        output_schema = {}

        logger.debug(
            f"Extracted metadata: name={name}, "
            f"signature={signature[:50]}..., "
            f"description={description[:50]}..."
        )

        return {
            'name': name,
            'signature': signature,
            'docstring': docstring,
            'description': description,
            'input_schema': input_schema,
            'output_schema': output_schema,
        }

    def _build_signature(self, function_def: ast.FunctionDef) -> str:
        """
        Build a function signature string from AST.

        Args:
            function_def: ast.FunctionDef node

        Returns:
            Function signature string
        """
        # Build parameter list
        params = []

        for arg in function_def.args.args:
            param_str = arg.arg

            # Add type annotation if present
            if arg.annotation:
                param_str += f": {ast.unparse(arg.annotation)}"

            params.append(param_str)

        # Build return type
        return_type = ""
        if function_def.returns:
            return_type = f" -> {ast.unparse(function_def.returns)}"

        # Combine
        signature = f"def {function_def.name}({', '.join(params)}){return_type}"

        return signature
