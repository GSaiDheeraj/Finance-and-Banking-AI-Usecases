"""Unit tests for credit_risk.gemini_requests (requests-based Gemini chat).

HTTP is mocked at the `requests` boundary — no key or network needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from credit_risk.gemini_requests import (
    GeminiAPIError,
    GeminiChat,
    _clean_schema,
    _content_to_str,
    _to_function_declaration,
    list_gemini_models,
)


@tool
def search_pages(query: str) -> str:
    """Fake page search."""
    return f"hit:{query}"


def _response(status_code=200, payload=None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = payload if payload is not None else {}
    mock.text = str(payload)
    return mock


def test_content_to_str_handles_str_none_and_blocks():
    assert _content_to_str("hi") == "hi"
    assert _content_to_str(None) == ""
    assert _content_to_str([{"type": "text", "text": "a"}, "b"]) == "ab"


def test_clean_schema_keeps_property_names_but_drops_rejected_keys():
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
        "$schema": "http://json-schema.org/draft-07/schema#",
    }
    assert _clean_schema(schema) == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }


def test_to_function_declaration_shape():
    declaration = _to_function_declaration(search_pages)
    assert declaration["name"] == "search_pages"
    assert declaration["parameters"]["properties"]["query"] == {"type": "string"}
    assert declaration["parameters"]["required"] == ["query"]


def test_bind_tools_produces_gemini_declarations():
    llm = GeminiChat(model="gemini-3.5-flash-lite", api_key="key")
    bound = llm.bind_tools([search_pages])
    first_tool = bound.kwargs["tools"][0]["functionDeclarations"][0]
    assert first_tool["name"] == "search_pages"


def test_list_models_returns_names():
    payload = {"models": [{"name": "models/gemini-3.5-flash-lite"}]}
    with patch(
        "credit_risk.gemini_requests.requests.get", return_value=_response(200, payload)
    ):
        assert list_gemini_models("key") == ["models/gemini-3.5-flash-lite"]


def test_list_models_auth_failure_raises():
    payload = {"error": {"message": "API key not valid."}}
    with patch(
        "credit_risk.gemini_requests.requests.get", return_value=_response(400, payload)
    ):
        with pytest.raises(GeminiAPIError, match="API key not valid"):
            list_gemini_models("bad-key")


def test_generate_returns_text_reply():
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": "GEMINI_OK"}]}, "finishReason": "STOP"}
        ]
    }
    llm = GeminiChat(model="gemini-3.5-flash-lite", api_key="key")
    with patch(
        "credit_risk.gemini_requests.requests.post",
        return_value=_response(200, payload),
    ):
        reply = llm.invoke([HumanMessage(content="hi")])
    assert reply.content == "GEMINI_OK"
    assert reply.tool_calls == []


def test_generate_parses_function_call_and_echoes_signature():
    first = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "search_pages",
                                "args": {"query": "q"},
                                "id": "call_1",
                            },
                            "thoughtSignature": "sig-abc",
                        }
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    second = {
        "candidates": [
            {"content": {"parts": [{"text": "done"}]}, "finishReason": "STOP"}
        ]
    }
    llm = GeminiChat(model="gemini-3.5-flash-lite", api_key="key")
    with patch(
        "credit_risk.gemini_requests.requests.post",
        side_effect=[_response(200, first), _response(200, second)],
    ) as mock_post:
        first_reply = llm.invoke([HumanMessage(content="search")])
        assert first_reply.tool_calls[0]["name"] == "search_pages"
        tool_result = ToolMessage(content='{"hit": 1}', tool_call_id="call_1")
        llm.invoke([HumanMessage(content="search"), first_reply, tool_result])

    sent = mock_post.call_args_list[1].kwargs["json"]["contents"]
    replayed_assistant = sent[1]["parts"][0]
    assert replayed_assistant["thoughtSignature"] == "sig-abc"
    assert replayed_assistant["functionCall"]["name"] == "search_pages"
    assert sent[2]["parts"][0]["functionResponse"]["name"] == "search_pages"


def test_generate_api_error_raises():
    payload = {"error": {"message": "Model overloaded."}}
    llm = GeminiChat(model="gemini-3.5-flash-lite", api_key="key")
    with patch(
        "credit_risk.gemini_requests.requests.post",
        return_value=_response(503, payload),
    ):
        with pytest.raises(GeminiAPIError, match="Model overloaded"):
            llm.invoke([HumanMessage(content="hi")])


def test_generate_blocked_prompt_raises():
    payload = {"promptFeedback": {"blockReason": "SAFETY"}}
    llm = GeminiChat(model="gemini-3.5-flash-lite", api_key="key")
    with patch(
        "credit_risk.gemini_requests.requests.post",
        return_value=_response(200, payload),
    ):
        with pytest.raises(GeminiAPIError, match="SAFETY"):
            llm.invoke([HumanMessage(content="hi")])
