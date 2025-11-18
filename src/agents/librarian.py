"""
Librarian Agent: Semantic Tool Search

This agent searches the tool library for existing tools that match
a task specification, enabling tool reuse instead of regeneration.
"""

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from src.core.schemas import TaskNode
from src.memory.vector_store import VectorStore
from src.memory.embeddings import get_embedding_generator
from src.memory.tool_index import ToolIndexer

logger = logging.getLogger(__name__)


@dataclass
class ToolMatch:
    """Represents a tool match from semantic search."""
    tool_id: str
    similarity: float
    metadata: Dict[str, Any]
    rank: int


class LibrarianAgent:
    """
    Agent responsible for semantic tool search and retrieval.

    The Librarian searches the vector database for existing tools that
    match a task specification, enabling 60x faster tool reuse.
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        similarity_threshold: float = 0.75,
        high_confidence_threshold: float = 0.85,
    ):
        """
        Initialize the Librarian Agent.

        Args:
            vector_store: VectorStore instance (creates new if None)
            similarity_threshold: Minimum similarity for a match (0-1)
            high_confidence_threshold: Similarity for high-confidence matches (0-1)
        """
        self.agent_id = "librarian"
        self.vector_store = vector_store or VectorStore()
        self.embedding_generator = get_embedding_generator()
        self.tool_indexer = ToolIndexer(vector_store=self.vector_store)

        self.similarity_threshold = similarity_threshold
        self.high_confidence_threshold = high_confidence_threshold

        logger.info(
            f"Librarian initialized with {self.vector_store.count()} indexed tools"
        )

    def search_for_task(
        self,
        task: TaskNode,
        n_results: int = 5
    ) -> List[ToolMatch]:
        """
        Search for existing tools that match a task specification.

        Args:
            task: TaskNode to search for
            n_results: Maximum number of results to return

        Returns:
            List of ToolMatch objects, sorted by similarity (highest first)
        """
        logger.info(f"Searching for tools matching task: {task.id}")
        logger.debug(f"Task description: {task.description}")

        # Check if vector store is empty
        if self.vector_store.count() == 0:
            logger.info("Vector store is empty - no tools to search")
            return []

        # Generate query embedding from task description
        query_embedding = self._generate_query_embedding(task)

        # Search vector database
        results = self.vector_store.search(
            query_embedding=query_embedding,
            n_results=n_results
        )

        # Convert to ToolMatch objects
        matches = []
        for i, (tool_id, similarity, metadata) in enumerate(
            zip(results['ids'], results['similarities'], results['metadatas'])
        ):
            # Filter by similarity threshold
            if similarity < self.similarity_threshold:
                logger.debug(
                    f"Filtering out {tool_id} (similarity {similarity:.3f} "
                    f"< threshold {self.similarity_threshold})"
                )
                continue

            match = ToolMatch(
                tool_id=tool_id,
                similarity=similarity,
                metadata=metadata,
                rank=i + 1
            )
            matches.append(match)

        # Log results
        if matches:
            logger.info(
                f"Found {len(matches)} matching tools "
                f"(best: {matches[0].metadata.get('name', 'unknown')} "
                f"with similarity {matches[0].similarity:.3f})"
            )

            for match in matches:
                logger.debug(
                    f"  Rank {match.rank}: {match.metadata.get('name', 'unknown')} "
                    f"(similarity: {match.similarity:.3f})"
                )
        else:
            logger.info("No matching tools found above similarity threshold")

        return matches

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

        High-confidence matches can be used without user confirmation.

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

    def register_tool(
        self,
        tool_id: str,
        source_path: str,
        test_result: Any = None
    ) -> None:
        """
        Register a new tool in the library.

        This indexes the tool for future semantic search.

        Args:
            tool_id: Unique tool identifier
            source_path: Path to tool source code
            test_result: Optional test execution results
        """
        logger.info(f"Registering tool in library: {tool_id}")

        try:
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

    def _generate_query_embedding(self, task: TaskNode) -> List[float]:
        """
        Generate query embedding from task specification.

        Args:
            task: TaskNode

        Returns:
            Embedding vector
        """
        # Combine task description with schema information
        parts = [task.description]

        # Add input parameters if available
        if task.input_schema and task.input_schema.get('properties'):
            params = list(task.input_schema['properties'].keys())
            parts.append(f"Parameters: {', '.join(params)}")

        # Add output information if available
        if task.output_schema and task.output_schema.get('properties'):
            outputs = list(task.output_schema['properties'].keys())
            parts.append(f"Returns: {', '.join(outputs)}")

        # Combine
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

        # Get all tools to calculate stats
        all_tools = self.vector_store.list_all_tools()

        # Count by status
        active_count = sum(
            1 for t in all_tools
            if t['metadata'].get('status') == 'active'
        )

        return {
            'total_tools': total_tools,
            'active_tools': active_count,
            'similarity_threshold': self.similarity_threshold,
            'high_confidence_threshold': self.high_confidence_threshold,
        }
