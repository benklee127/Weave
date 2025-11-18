"""
LLM client wrapper for Anthropic Claude API.

Provides error handling, retries, and token tracking.
"""

import os
import time
import logging
from typing import Optional, List, Dict, Any
from anthropic import Anthropic, APIError, RateLimitError, APIConnectionError

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Wrapper for Anthropic Claude API with error handling and retries.

    Features:
    - Automatic retries with exponential backoff
    - Token usage tracking
    - Structured logging
    - Support for system prompts and message history
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
    ):
        """
        Initialize the LLM client.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            model: Model identifier (defaults to ANTHROPIC_MODEL env var)
            max_retries: Maximum number of retry attempts
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY must be set")

        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        self.max_retries = max_retries
        self.client = Anthropic(api_key=self.api_key)

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
    ) -> str:
        """
        Generate a completion from the LLM.

        Args:
            prompt: User prompt
            system: System prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0-1)
            stop_sequences: Sequences that stop generation

        Returns:
            Generated text

        Raises:
            APIError: If API call fails after all retries
        """
        messages = [{"role": "user", "content": prompt}]

        return self._call_api(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
        )

    def generate_with_history(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> str:
        """
        Generate with message history (for multi-turn conversations).

        Args:
            messages: List of message dicts with 'role' and 'content'
            system: System prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Generated text
        """
        return self._call_api(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _call_api(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
    ) -> str:
        """
        Internal method to call the API with retries.

        Args:
            messages: Message list
            system: System prompt
            max_tokens: Maximum tokens
            temperature: Temperature
            stop_sequences: Stop sequences

        Returns:
            Generated text

        Raises:
            APIError: If all retries fail
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    f"API call attempt {attempt + 1}/{self.max_retries} "
                    f"(model: {self.model}, max_tokens: {max_tokens})"
                )

                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": messages,
                    "temperature": temperature,
                }

                if system:
                    kwargs["system"] = system

                if stop_sequences:
                    kwargs["stop_sequences"] = stop_sequences

                response = self.client.messages.create(**kwargs)

                # Track tokens
                self.total_input_tokens += response.usage.input_tokens
                self.total_output_tokens += response.usage.output_tokens

                logger.info(
                    f"API call successful. Tokens: "
                    f"input={response.usage.input_tokens}, "
                    f"output={response.usage.output_tokens}"
                )

                # Extract text from response
                content = response.content[0]
                if hasattr(content, "text"):
                    return content.text
                else:
                    return str(content)

            except RateLimitError as e:
                last_error = e
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"Rate limit hit. Waiting {wait_time}s before retry {attempt + 1}/{self.max_retries}"
                )
                time.sleep(wait_time)

            except APIConnectionError as e:
                last_error = e
                wait_time = 2 ** attempt
                logger.warning(
                    f"API connection error. Waiting {wait_time}s before retry {attempt + 1}/{self.max_retries}"
                )
                time.sleep(wait_time)

            except APIError as e:
                last_error = e
                # Don't retry on certain errors
                if e.status_code in [400, 401, 403]:  # Bad request, unauthorized, forbidden
                    logger.error(f"Non-retryable API error: {e}")
                    raise

                wait_time = 2 ** attempt
                logger.warning(
                    f"API error: {e}. Waiting {wait_time}s before retry {attempt + 1}/{self.max_retries}"
                )
                time.sleep(wait_time)

        # All retries exhausted
        logger.error(f"All {self.max_retries} API call attempts failed")
        raise last_error

    def get_token_usage(self) -> Dict[str, int]:
        """
        Get total token usage statistics.

        Returns:
            Dict with input_tokens, output_tokens, and total_tokens
        """
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
        }

    def reset_token_usage(self) -> None:
        """Reset token usage counters."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        logger.debug("Token usage counters reset")
