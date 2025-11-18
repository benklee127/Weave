"""
Vector database integration using ChromaDB.

This module provides persistent storage and retrieval of tool embeddings
for semantic search.
"""

import logging
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

    def add_tool(
        self,
        tool_id: str,
        embedding: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        """
        Add a tool to the vector store.

        Args:
            tool_id: Unique identifier for the tool
            embedding: Vector embedding of the tool
            metadata: Tool metadata (name, description, code, etc.)
        """
        try:
            # ChromaDB expects documents for text content
            # We'll use the description as the document
            document = metadata.get("description", "")

            self.collection.add(
                ids=[tool_id],
                embeddings=[embedding],
                documents=[document],
                metadatas=[self._sanitize_metadata(metadata)]
            )

            logger.info(f"Added tool to vector store: {tool_id}")

        except Exception as e:
            logger.error(f"Failed to add tool {tool_id}: {e}")
            raise

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
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
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
                    'embedding': results['embeddings'][0] if results['embeddings'] else None,
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
                # Truncate large objects
                str_value = str(value)
                if len(str_value) > 1000:
                    str_value = str_value[:997] + "..."
                sanitized[key] = str_value
            elif isinstance(value, (str, int, float, bool)):
                # Keep simple types as-is
                # Truncate long strings
                if isinstance(value, str) and len(value) > 1000:
                    sanitized[key] = value[:997] + "..."
                else:
                    sanitized[key] = value
            else:
                # Convert other types to string
                sanitized[key] = str(value)

        return sanitized
