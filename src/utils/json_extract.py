"""
Shared helper for pulling JSON values out of free-form LLM responses.

Uses ``json.JSONDecoder.raw_decode`` to scan for the first well-formed value
of the expected type, instead of greedy/non-greedy regexes which break on
nested brackets or on prose containing braces.
"""

import json
from typing import Any, Callable, Optional, Type

_DECODER = json.JSONDecoder()


class JSONExtractionError(ValueError):
    """Raised when no suitable JSON value can be found in the text."""


def extract_json(
    text: str,
    expect: Optional[Type] = None,
    validate: Optional[Callable[[Any], bool]] = None,
) -> Any:
    """
    Extract the first JSON value of the expected type from ``text``.

    Args:
        text: Raw LLM output (may contain prose and markdown fences).
        expect: Required top-level type (``list`` or ``dict``); any if None.
        validate: Optional predicate a candidate must satisfy.

    Returns:
        The decoded JSON value.

    Raises:
        JSONExtractionError: If no matching value is found.
    """
    if not isinstance(text, str) or not text.strip():
        raise JSONExtractionError("Empty response; no JSON to extract")

    def acceptable(value: Any) -> bool:
        if expect is not None and not isinstance(value, expect):
            return False
        return validate(value) if validate is not None else True

    # Fast path: whole text is JSON
    try:
        whole = json.loads(text.strip())
        if acceptable(whole):
            return whole
    except (json.JSONDecodeError, ValueError):
        pass

    # Scan for candidate starts; skip over decoded values that don't qualify
    # so brackets nested inside a rejected value are not re-tried.
    idx = 0
    n = len(text)
    while idx < n:
        ch = text[idx]
        if ch in "[{":
            try:
                value, end = _DECODER.raw_decode(text, idx)
            except json.JSONDecodeError:
                idx += 1
                continue
            if acceptable(value):
                return value
            idx = end
            continue
        idx += 1

    wanted = expect.__name__ if expect is not None else "value"
    raise JSONExtractionError(f"Could not extract a valid JSON {wanted} from response")
