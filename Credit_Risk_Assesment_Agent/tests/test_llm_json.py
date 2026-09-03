"""Unit tests for credit_risk.llm_json.safe_json_loads / strip_json_fence."""
from __future__ import annotations

from credit_risk.llm_json import safe_json_loads, strip_json_fence


def test_strips_json_fenced_block():
    text = '```json\n{"a": 1}\n```'
    assert strip_json_fence(text) == '{"a": 1}'


def test_strips_bare_fence_without_language_tag():
    text = '```\n{"a": 1}\n```'
    assert strip_json_fence(text) == '{"a": 1}'


def test_returns_text_unchanged_when_no_fence():
    assert strip_json_fence('{"a": 1}') == '{"a": 1}'


def test_parses_clean_json_object():
    assert safe_json_loads('{"a": 1}') == {"a": 1}


def test_parses_clean_json_array():
    assert safe_json_loads('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_parses_fenced_json():
    assert safe_json_loads('```json\n{"a": 1}\n```') == {"a": 1}


def test_parses_json_with_prose_preamble_before_fence():
    """Regression: after a tool-calling exchange, the model often summarizes before
    answering, e.g. 'Based on the search results, ...\\n\\n```json\\n{...}\\n```' —
    the old implementation only stripped a fence at the very start of the string."""
    text = (
        "Based on the search results, I have found the company metadata:\n\n"
        '```json\n{"company_name": "ACME MANUFACTURING LTD.", "industry": "Manufacturing"}\n```'
    )
    assert safe_json_loads(text) == {
        "company_name": "ACME MANUFACTURING LTD.",
        "industry": "Manufacturing",
    }


def test_parses_bare_json_wrapped_in_prose_with_no_fence():
    text = 'Here is the answer: {"a": 1} — let me know if you need anything else.'
    assert safe_json_loads(text) == {"a": 1}


def test_returns_none_for_malformed_json():
    assert safe_json_loads("not json at all") is None


def test_returns_none_for_truncated_json():
    assert safe_json_loads('{"a": 1,') is None


def test_returns_none_for_empty_string():
    assert safe_json_loads("") is None
