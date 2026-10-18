"""
Test doubles shared across the Weave test suite.

``FakeLLMClient`` implements ``LLMClientProtocol`` with scripted responses so
tests never touch the network or need an API key.
"""

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

Responder = Union[str, Callable[[str, Optional[str]], str], Exception]


@dataclass
class FakeCall:
    """One recorded ``generate`` invocation."""

    prompt: str
    system: Optional[str]
    agent: Optional[str]
    max_tokens: int
    temperature: float


@dataclass
class _Rule:
    keyword: str
    responses: List[Responder]
    agent: Optional[str] = None
    repeat_last: bool = True
    used: int = 0


class FakeLLMClient:
    """
    Scripted stand-in for ``LLMClient``.

    Rules are matched in registration order. A rule matches when its
    ``keyword`` appears (case-insensitively) in the prompt or system text and,
    if ``agent`` was given, the calling agent matches (the ``agent`` kwarg, or
    a substring of the system prompt as fallback). A rule's responses are
    consumed in order; the last one repeats unless ``repeat_last=False``.
    A response may be a string, an Exception (raised), or a callable
    ``(prompt, system) -> str``.

    Example:
        llm = FakeLLMClient()
        llm.add_response("critic", '{"valid": true}')
        llm.add_response("fibonacci", ["bad code", "good code"], agent="architect")
    """

    def __init__(self, default: Optional[str] = None, responses: Optional[Dict[str, Any]] = None):
        self.default = default
        self.calls: List[FakeCall] = []
        self.rules: List[_Rule] = []
        self.usage_by_agent: Dict[str, Dict[str, Any]] = {}
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._lock = threading.Lock()
        for keyword, resp in (responses or {}).items():
            self.add_response(keyword, resp)

    # -- scripting -------------------------------------------------------
    def add_response(
        self,
        keyword: str,
        response: Union[Responder, List[Responder]],
        agent: Optional[str] = None,
        repeat_last: bool = True,
    ) -> "FakeLLMClient":
        """Register a scripted response (or ordered list of them)."""
        items = list(response) if isinstance(response, (list, tuple)) else [response]
        self.rules.append(_Rule(keyword.lower(), items, agent, repeat_last))
        return self

    # -- LLMClientProtocol -------------------------------------------------
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        stop_sequences: Optional[List[str]] = None,
        agent: Optional[str] = None,
    ) -> str:
        with self._lock:
            self.calls.append(FakeCall(prompt, system, agent, max_tokens, temperature))
            text = self._resolve(prompt, system, agent)
            label = agent or "unknown"
            entry = self.usage_by_agent.setdefault(
                label, {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0}
            )
            in_tok, out_tok = len(prompt) // 4, len(text) // 4
            entry["input_tokens"] += in_tok
            entry["output_tokens"] += out_tok
            entry["calls"] += 1
            self.total_input_tokens += in_tok
            self.total_output_tokens += out_tok
        return text

    def _resolve(self, prompt: str, system: Optional[str], agent: Optional[str]) -> str:
        haystack = f"{prompt}\n{system or ''}".lower()
        for rule in self.rules:
            if rule.keyword not in haystack:
                continue
            if rule.agent and not (
                agent == rule.agent
                or (agent is None and rule.agent.lower() in (system or "").lower())
            ):
                continue
            if rule.used >= len(rule.responses) and not rule.repeat_last:
                continue
            idx = min(rule.used, len(rule.responses) - 1)
            rule.used += 1
            item = rule.responses[idx]
            if isinstance(item, Exception):
                raise item
            return item(prompt, system) if callable(item) else item
        if self.default is not None:
            return self.default
        raise AssertionError(
            f"FakeLLMClient: no scripted response for agent={agent!r}, prompt={prompt[:120]!r}"
        )

    # -- helpers ---------------------------------------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(self, agent: str) -> List[FakeCall]:
        return [c for c in self.calls if c.agent == agent]

    def get_token_usage(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "estimated_cost_usd": 0.0,
        }

    def get_usage_by_agent(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self.usage_by_agent.items()}

    def reset_token_usage(self) -> None:
        self.total_input_tokens = self.total_output_tokens = 0
        self.usage_by_agent = {}


# Canned, valid LLM payloads reusable by other tests -------------------------

SAMPLE_ADD_TESTS = """import pytest


def test_add_basic():
    assert add(1, 2) == 3


def test_add_negative():
    assert add(-1, -2) == -3
"""

SAMPLE_ADD_IMPL = """def add(a: int, b: int) -> int:
    \"\"\"Add two integers.\"\"\"
    return a + b
"""

SAMPLE_CRITIC_APPROVE = (
    '{"valid": true, "assertion_count": 2, "total_lines": 8, "assertion_density": 0.25, '
    '"issues": [], "recommendation": "APPROVE", "reason": "ok"}'
)

SAMPLE_PLAN_ONE_TASK = (
    '[{"description": "Add two integers", "dependencies": [], '
    '"input_schema": {"a": "int", "b": "int"}, "output_schema": {"result": "int"}}]'
)
