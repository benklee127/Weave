"""
LLM client wrapper for Anthropic Claude API.

Provides error handling, retries, token/cost tracking with per-agent
attribution, and an optional budget limit. The underlying SDK client is
created lazily so importing/constructing this class needs no API key.
"""

import os
import time
import logging
import threading
from typing import Optional, List, Dict, Any, Protocol, runtime_checkable

from anthropic import APIError, RateLimitError, APIConnectionError

logger = logging.getLogger(__name__)

# Rough USD prices per million tokens (input, output) used for cost attribution.
DEFAULT_PRICING = (3.0, 15.0)


class LLMEmptyResponseError(ValueError):
    """Raised when the model returns no usable text."""


class LLMBudgetExceededError(RuntimeError):
    """Raised when the configured token or cost budget has been used up."""


@runtime_checkable
class LLMClientProtocol(Protocol):
    """Minimal interface agents need from an LLM client."""

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
        agent: Optional[str] = None,
    ) -> str:
        """Return the model completion for ``prompt``."""
        ...


class LLMClient:
    """
    Wrapper for Anthropic Claude API with error handling and retries.

    Features:
    - Automatic retries with exponential backoff
    - Token usage tracking, attributed per agent (``usage_by_agent``)
    - Optional token / cost budget
    - Robust handling of empty responses
    - Lazy SDK client creation (no API key needed until the first call)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
        max_total_tokens: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        pricing: Optional[tuple] = None,
    ):
        """
        Initialize the LLM client.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            model: Model identifier (defaults to ANTHROPIC_MODEL env var)
            max_retries: Maximum number of retry attempts
            max_total_tokens: Optional budget on input+output tokens
            max_cost_usd: Optional budget on estimated cost
            pricing: (input, output) USD per million tokens
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        self.max_retries = max_retries
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.pricing = pricing or DEFAULT_PRICING
        self._client = None
        self._lock = threading.Lock()

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.usage_by_agent: Dict[str, Dict[str, Any]] = {}

    @property
    def client(self):
        """Lazily create the Anthropic client (requires an API key)."""
        if self._client is None:
            if not self.api_key:
                raise ValueError("ANTHROPIC_API_KEY must be set")
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self.api_key)
        return self._client

    @client.setter
    def client(self, value) -> None:
        self._client = value

    def estimated_cost(self) -> float:
        """Estimated total cost in USD across all agents."""
        return (
            self.total_input_tokens * self.pricing[0]
            + self.total_output_tokens * self.pricing[1]
        ) / 1_000_000

    def _check_budget(self) -> None:
        """Raise if the configured budget is exhausted."""
        total = self.total_input_tokens + self.total_output_tokens
        if self.max_total_tokens is not None and total >= self.max_total_tokens:
            raise LLMBudgetExceededError(
                f"Token budget exhausted ({total}/{self.max_total_tokens})"
            )
        if self.max_cost_usd is not None and self.estimated_cost() >= self.max_cost_usd:
            raise LLMBudgetExceededError(
                f"Cost budget exhausted "
                f"(${self.estimated_cost():.4f}/${self.max_cost_usd:.4f})"
            )

    def _record_usage(
        self, agent: Optional[str], input_tokens: int, output_tokens: int
    ) -> None:
        """Attribute token usage and cost to an agent."""
        cost = (
            input_tokens * self.pricing[0] + output_tokens * self.pricing[1]
        ) / 1_000_000
        with self._lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            entry = self.usage_by_agent.setdefault(
                agent or "unknown",
                {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0},
            )
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            entry["calls"] += 1
            entry["cost_usd"] += cost

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
        agent: Optional[str] = None,
    ) -> str:
        """
        Generate a completion from the LLM.

        Args:
            prompt: User prompt
            system: System prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0-1)
            stop_sequences: Sequences that stop generation
            agent: Name of the calling agent, for usage attribution

        Returns:
            Generated text

        Raises:
            APIError: If API call fails after all retries
            LLMEmptyResponseError: If the model returns no text
            LLMBudgetExceededError: If the configured budget is exhausted
        """
        messages = [{"role": "user", "content": prompt}]

        return self._call_api(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
            agent=agent,
        )

    def generate_with_history(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        agent: Optional[str] = None,
    ) -> str:
        """
        Generate with message history (for multi-turn conversations).

        Args:
            messages: List of message dicts with 'role' and 'content'
            system: System prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            agent: Name of the calling agent, for usage attribution

        Returns:
            Generated text
        """
        return self._call_api(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            agent=agent,
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Concatenate all text blocks of a response (empty string if none)."""
        parts = []
        for block in getattr(response, "content", None) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    def _call_api(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
        agent: Optional[str] = None,
    ) -> str:
        """
        Internal method to call the API with retries.

        Returns:
            Generated text

        Raises:
            APIError: If all retries fail
            LLMEmptyResponseError: If the response has no text
        """
        last_error = None
        self._check_budget()

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

                usage = getattr(response, "usage", None)
                in_tok = int(getattr(usage, "input_tokens", 0) or 0)
                out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                self._record_usage(agent, in_tok, out_tok)

                logger.info(
                    f"API call successful. Tokens: input={in_tok}, output={out_tok}"
                )

                text = self._extract_text(response)
                if not text.strip():
                    raise LLMEmptyResponseError(
                        f"Model returned an empty response (agent={agent or 'unknown'})"
                    )
                return text

            except (LLMEmptyResponseError, LLMBudgetExceededError):
                raise

            except RateLimitError as e:
                last_error = e
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"Rate limit hit. Waiting {wait_time}s before retry "
                    f"{attempt + 1}/{self.max_retries}"
                )
                time.sleep(wait_time)

            except APIConnectionError as e:
                last_error = e
                wait_time = 2 ** attempt
                logger.warning(
                    f"API connection error. Waiting {wait_time}s before retry "
                    f"{attempt + 1}/{self.max_retries}"
                )
                time.sleep(wait_time)

            except APIError as e:
                last_error = e
                # Don't retry on certain errors
                if getattr(e, "status_code", None) in [400, 401, 403]:
                    logger.error(f"Non-retryable API error: {e}")
                    raise

                wait_time = 2 ** attempt
                logger.warning(
                    f"API error: {e}. Waiting {wait_time}s before retry "
                    f"{attempt + 1}/{self.max_retries}"
                )
                time.sleep(wait_time)

        # All retries exhausted
        logger.error(f"All {self.max_retries} API call attempts failed")
        if last_error is None:
            raise RuntimeError("LLM call failed without an error (max_retries < 1?)")
        raise last_error

    def get_token_usage(self) -> Dict[str, Any]:
        """
        Get total token usage statistics.

        Returns:
            Dict with input_tokens, output_tokens, total_tokens, estimated_cost_usd
        """
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "estimated_cost_usd": round(self.estimated_cost(), 6),
        }

    def get_usage_by_agent(self) -> Dict[str, Dict[str, Any]]:
        """Per-agent token/cost attribution (copy)."""
        return {k: dict(v) for k, v in self.usage_by_agent.items()}

    def reset_token_usage(self) -> None:
        """Reset token usage counters."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.usage_by_agent = {}
        logger.debug("Token usage counters reset")
