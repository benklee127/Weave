"""
Embedding generation for code and text using sentence-transformers.

This module provides semantic embeddings for tool descriptions and code.
"""

import logging
from typing import List, Union
from sentence_transformers import SentenceTransformer
import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """
    Generate semantic embeddings for tool search.

    Uses sentence-transformers for fast, local embedding generation.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize the embedding generator.

        Args:
            model_name: Name of the sentence-transformers model to use
                       Default: all-MiniLM-L6-v2 (fast, 384 dimensions)
                       Alternative: all-mpnet-base-v2 (better quality, 768 dimensions)
        """
        self.model_name = model_name
        logger.info(f"Loading embedding model: {model_name}")

        try:
            self.model = SentenceTransformer(model_name)
            self.embedding_dim = self.model.get_sentence_embedding_dimension()

            logger.info(
                f"Embedding model loaded successfully "
                f"(dimension: {self.embedding_dim})"
            )

        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        try:
            # Generate embedding
            embedding = self.model.encode(
                text,
                convert_to_numpy=True,
                show_progress_bar=False
            )

            # Convert to list
            return embedding.tolist()

        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts (batched for efficiency).

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        try:
            # Batch encode for efficiency
            embeddings = self.model.encode(
                texts,
                convert_to_numpy=True,
                show_progress_bar=False,
                batch_size=32
            )

            # Convert to list of lists
            return embeddings.tolist()

        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise

    def generate_tool_embedding(
        self,
        tool_name: str,
        description: str,
        signature: str = "",
        docstring: str = "",
    ) -> List[float]:
        """
        Generate embedding for a tool by combining its metadata.

        This creates a rich semantic representation by concatenating
        key information about the tool.

        Args:
            tool_name: Function name
            description: Brief description
            signature: Function signature
            docstring: Full docstring

        Returns:
            Embedding vector
        """
        # Combine information for embedding
        # Weight: description (high), docstring (medium), signature (low)
        parts = []

        # Add description multiple times for higher weight
        if description:
            parts.append(description)
            parts.append(description)  # Double weight

        # Add docstring
        if docstring:
            parts.append(docstring)

        # Add signature (helps match technical details)
        if signature:
            parts.append(signature)

        # Add function name (helps with exact matches)
        if tool_name:
            parts.append(f"Function: {tool_name}")

        # Combine with newlines
        combined_text = "\n".join(parts)

        logger.debug(
            f"Generating tool embedding for '{tool_name}' "
            f"({len(combined_text)} chars)"
        )

        return self.generate_embedding(combined_text)

    def similarity(
        self,
        embedding1: Union[List[float], np.ndarray],
        embedding2: Union[List[float], np.ndarray]
    ) -> float:
        """
        Calculate cosine similarity between two embeddings.

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Similarity score (0-1, higher is more similar)
        """
        # Convert to numpy arrays
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        # Cosine similarity
        similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

        return float(similarity)

    def get_embedding_dimension(self) -> int:
        """
        Get the dimension of embeddings produced by this model.

        Returns:
            Embedding dimension
        """
        return self.embedding_dim


# Cache for singleton instance
_embedding_generator_cache = None


def get_embedding_generator(model_name: str = "all-MiniLM-L6-v2") -> EmbeddingGenerator:
    """
    Get a singleton embedding generator instance.

    This caches the model to avoid reloading on every call.

    Args:
        model_name: Model to use

    Returns:
        EmbeddingGenerator instance
    """
    global _embedding_generator_cache

    if _embedding_generator_cache is None:
        _embedding_generator_cache = EmbeddingGenerator(model_name)

    return _embedding_generator_cache
