"""Tests for src.utils.json_extract."""

import pytest

from src.utils.json_extract import JSONExtractionError, extract_json


def test_whole_text_is_json():
    assert extract_json('[{"a": 1}]', expect=list) == [{"a": 1}]


def test_prose_around_array():
    text = 'Here you go:\n[{"description": "x", "dependencies": []}]\nHope it helps.'
    assert extract_json(text, expect=list) == [{"description": "x", "dependencies": []}]


def test_markdown_fence():
    text = '```json\n{"valid": true}\n```'
    assert extract_json(text, expect=dict) == {"valid": True}


def test_nested_brackets_and_braces_in_strings():
    text = 'noise {"k": "a ] } [ {", "n": [1, [2, 3]]} trailing } ]'
    assert extract_json(text, expect=dict) == {"k": "a ] } [ {", "n": [1, [2, 3]]}


def test_skips_wrong_type_without_entering_it():
    text = '{"a": [1, 2]} then [{"b": 2}]'
    assert extract_json(text, expect=list, validate=lambda v: all(isinstance(i, dict) for i in v)) == [{"b": 2}]


def test_skips_invalid_candidates():
    assert extract_json('{oops} {"ok": 1}', expect=dict) == {"ok": 1}


@pytest.mark.parametrize("text", ["", "   ", "no json here", "[1, 2", None])
def test_failures(text):
    with pytest.raises(JSONExtractionError):
        extract_json(text, expect=dict)


def test_error_is_valueerror():
    assert issubclass(JSONExtractionError, ValueError)
