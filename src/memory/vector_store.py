"""
Vector database integration using ChromaDB.

This module provides persistent storage and retrieval of tool embeddings
for semantic search.
"""

import json
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


class VectorStore:
    """
    Persistent vector database for tool embeddings.

    Uses ChromaDB for local storage with cosine similarity search.
    """

    def __init__(self, persist_directory: str = "./data/vector_db"):
        """
        Initialize the vector store.

        Args:
            persist_directory: Directory to persist the database
        """
        self._lock = threading.RLock()
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # Initialize ChromaDB client
        self.client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            )
        )

        # Create or get the tools collection
        self.collection = self.client.get_or_create_collection(
            name="tool_library",
            metadata={"hnsw:space": "cosine"}  # Use cosine similarity
        )

        logger.info(
            f"Vector store initialized with {self.collection.count()} existing tools"
        )

    def upsert(
        self,
        tool_id: str,
        embedding: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        """
        Insert or replace a tool (idempotent).

        Args:
            tool_id: Unique identifier for the tool
            embedding: Vector embedding of the tool
            metadata: Tool metadata (name, description, lifecycle fields, ...)
        """
        try:
            with self._lock:
                self.collection.upsert(
                    ids=[tool_id],
                    embeddings=[embedding],
                    documents=[metadata.get("description", "") or ""],
                    metadatas=[self._sanitize_metadata(metadata)],
                )
            logger.info(f"Upserted tool in vector store: {tool_id}")
        except Exception as e:
            logger.error(f"Failed to upsert tool {tool_id}: {e}")
            raise

    def add_tool(
        self,
        tool_id: str,
        embedding: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        """Add a tool (kept for compatibility; behaves as upsert)."""
        self.upsert(tool_id, embedding, metadata)

    def update_metadata(self, tool_id: str, fields: Dict[str, Any]) -> bool:
        """
        Merge fields into a tool's stored metadata without re-embedding.

        Returns:
            True if the tool existed and was updated.
        """
        with self._lock:
            existing = self.collection.get(ids=[tool_id], include=["metadatas"])
            if not existing["ids"]:
                return False
            merged = dict(existing["metadatas"][0] or {})
            merged.update(self._sanitize_metadata(fields))
            self.collection.update(ids=[tool_id], metadatas=[merged])
        return True

    def list_with_embeddings(self) -> List[Dict[str, Any]]:
        """List all tools including their embeddings."""
        results = self.collection.get(include=["metadatas", "embeddings"])
        ids = list(results["ids"])
        embs = results["embeddings"] if results["embeddings"] is not None else []
        out = []
        for i, tool_id in enumerate(ids):
            out.append({
                "id": tool_id,
                "metadata": results["metadatas"][i],
                "embedding": [float(x) for x in embs[i]],
            })
        return out

    def update_tool(
        self,
        tool_id: str,
        embedding: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        """
        Update an existing tool in the vector store.

        Args:
            tool_id: Tool identifier
            embedding: Updated vector embedding
            metadata: Updated metadata
        """
        try:
            document = metadata.get("description", "")

            with self._lock:
                self.collection.update(
                    ids=[tool_id],
                    embeddings=[embedding],
                    documents=[document],
                    metadatas=[self._sanitize_metadata(metadata)]
                )

            logger.info(f"Updated tool in vector store: {tool_id}")

        except Exception as e:
            logger.error(f"Failed to update tool {tool_id}: {e}")
            raise

    def search(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Search for similar tools using vector similarity.

        Args:
            query_embedding: Query vector
            n_results: Number of results to return
            where: Optional metadata filter

        Returns:
            Dictionary with ids, distances, and metadatas
        """
        try:
            total = self.collection.count()
            if total == 0:
                return {'ids': [], 'similarities': [], 'metadatas': [], 'documents': []}
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=max(1, min(n_results, total)),
                where=where,
            )

            # Calculate similarity scores (1 - distance for cosine)
            # ChromaDB returns distances, we want similarity scores
            if results['distances'] and results['distances'][0]:
                similarities = [1 - d for d in results['distances'][0]]
            else:
                similarities = []

            logger.debug(
                f"Vector search returned {len(results['ids'][0])} results"
            )

            return {
                'ids': results['ids'][0] if results['ids'] else [],
                'similarities': similarities,
                'metadatas': results['metadatas'][0] if results['metadatas'] else [],
                'documents': results['documents'][0] if results['documents'] else [],
            }

        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific tool by ID.

        Args:
            tool_id: Tool identifier

        Returns:
            Tool metadata or None if not found
        """
        try:
            results = self.collection.get(
                ids=[tool_id],
                include=["metadatas", "documents", "embeddings"]
            )

            if results['ids']:
                return {
                    'id': results['ids'][0],
                    'metadata': results['metadatas'][0],
                    'document': results['documents'][0],
                    'embedding': (
                        [float(x) for x in results['embeddings'][0]]
                        if results.get('embeddings') is not None
                        and len(results['embeddings']) > 0
                        else None
                    ),
                }

            return None

        except Exception as e:
            logger.error(f"Failed to get tool {tool_id}: {e}")
            return None

    def delete_tool(self, tool_id: str) -> None:
        """
        Delete a tool from the vector store.

        Args:
            tool_id: Tool identifier
        """
        try:
            self.collection.delete(ids=[tool_id])
            logger.info(f"Deleted tool from vector store: {tool_id}")

        except Exception as e:
            logger.error(f"Failed to delete tool {tool_id}: {e}")
            raise

    def list_all_tools(self) -> List[Dict[str, Any]]:
        """
        List all tools in the vector store.

        Returns:
            List of tool metadata dictionaries
        """
        try:
            # Get all items
            results = self.collection.get(
                include=["metadatas", "documents"]
            )

            tools = []
            if results['ids']:
                for i, tool_id in enumerate(results['ids']):
                    tools.append({
                        'id': tool_id,
                        'metadata': results['metadatas'][i],
                        'document': results['documents'][i],
                    })

            logger.debug(f"Listed {len(tools)} tools from vector store")
            return tools

        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            return []

    def count(self) -> int:
        """
        Get the number of tools in the vector store.

        Returns:
            Tool count
        """
        return self.collection.count()

    def reset(self) -> None:
        """
        Delete all tools from the vector store.

        WARNING: This is destructive and cannot be undone.
        """
        logger.warning("Resetting vector store - all tools will be deleted")
        self.client.delete_collection(name="tool_library")
        self.collection = self.client.create_collection(
            name="tool_library",
            metadata={"hnsw:space": "cosine"}
        )
        logger.info("Vector store reset complete")

    def _sanitize_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize metadata for ChromaDB storage.

        ChromaDB has restrictions on metadata types and sizes.

        Args:
            metadata: Raw metadata dictionary

        Returns:
            Sanitized metadata dictionary
        """
        sanitized = {}

        for key, value in metadata.items():
            # Skip None values
            if value is None:
                continue

            # Convert complex types to strings
            if isinstance(value, (dict, list)):
                # Store as JSON so it can be parsed back (no truncation)
                try:
                    sanitized[key] = json.dumps(value, default=str)
                except (TypeError, ValueError):
                    sanitized[key] = str(value)
            elif isinstance(value, (str, int, float, bool)):
                # Keep simple types as-is
                # Truncate long strings
                if isinstance(value, str) and len(value) > 8000:
                    sanitized[key] = value[:7997] + "..."
                else:
                    sanitized[key] = value
            else:
                # Convert other types to string
                sanitized[key] = str(value)

        return sanitized
