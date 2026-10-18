"""
Embedding generation for code and text.

Uses sentence-transformers when installed (optional dependency). When it is
missing, or the model cannot be loaded, a deterministic dependency-free
``HashEmbeddingGenerator`` is used instead (also used in tests).
"""

import hashlib
import logging
import math
import re
from typing import Any, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

HASH_MODEL_NAME = "hash"


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
            from sentence_transformers import SentenceTransformer  # optional dep

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
            parts.append(description)  # Triple weight

        # Add docstring body (the first line is usually the description itself)
        if docstring:
            doc_lines = [ln.strip() for ln in docstring.splitlines() if ln.strip()]
            if description and doc_lines and doc_lines[0].rstrip(".") == description.rstrip("."):
                doc_lines = doc_lines[1:]
            if doc_lines:
                parts.append(" ".join(doc_lines))

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


class HashEmbeddingGenerator:
    """
    Deterministic, dependency-free embedding generator.

    Uses the hashing trick over word unigrams/bigrams (identifiers are split on
    snake_case / camelCase). Not as semantic as a neural model, but stable
    across processes and good enough for lexical-overlap matching.
    """

    def __init__(self, dimension: int = 384, model_name: str = HASH_MODEL_NAME):
        self.model_name = model_name
        self.embedding_dim = dimension

    @staticmethod
    def _tokens(text: str) -> List[str]:
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
        return re.findall(r"[a-z0-9]+", text.lower())

    def generate_embedding(self, text: str) -> List[float]:
        """Generate a unit-length embedding for a single text."""
        vec = [0.0] * self.embedding_dim
        tokens = self._tokens(text)
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for feat in features:
            digest = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.embedding_dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # Empty text: deterministic non-zero vector (cosine needs norm > 0)
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        return [self.generate_embedding(t) for t in texts]

    # Shared helpers (same behavior as EmbeddingGenerator)
    generate_tool_embedding = EmbeddingGenerator.generate_tool_embedding
    similarity = EmbeddingGenerator.similarity
    get_embedding_dimension = EmbeddingGenerator.get_embedding_dimension


_embedding_generator_cache: dict = {}


def get_embedding_generator(
    model_name: str = "all-MiniLM-L6-v2",
) -> Any:
    """
    Get a cached embedding generator.

    ``model_name == "hash"`` always returns the hash generator. Otherwise the
    sentence-transformers model is tried and the hash generator is used as a
    fallback when the library is missing or the model fails to load.

    Args:
        model_name: Model to use

    Returns:
        EmbeddingGenerator or HashEmbeddingGenerator instance
    """
    cached = _embedding_generator_cache.get(model_name)
    if cached is not None:
        return cached

    if model_name == HASH_MODEL_NAME:
        generator: Any = HashEmbeddingGenerator()
    else:
        try:
            generator = EmbeddingGenerator(model_name)
        except Exception as e:  # ImportError, offline model download, etc.
            logger.warning(
                f"Falling back to hash embeddings (could not load "
                f"'{model_name}': {e})"
            )
            generator = HashEmbeddingGenerator()

    _embedding_generator_cache[model_name] = generator
    return generator
