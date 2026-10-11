"""Gemini chat via plain `requests` — no `google-genai` SDK dependency.

Talks to `https://generativelanguage.googleapis.com/v1beta` directly
(`{model}:generateContent`), wrapped as a LangChain `BaseChatModel` so the
rest of the pipeline keeps working unchanged:

* `llm.invoke([system, user]).content` (used by `agent_graph`, `narrative`, `osint`)
* `llm.bind_tools(tools)` + `tool_calls` on the reply (used by `tool_loop.py`)
* `prompt | llm | parser` chains (used by `llm_json.py`)

Auth uses the `x-goog-api-key` header with a `GEMINI_API_KEY` env var.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import requests
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableBinding
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, PrivateAttr

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiAPIError(RuntimeError):
    """Gemini returned a non-2xx status or a blocked prompt."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Gemini API error {status_code}: {message}")
        self.status_code = status_code


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"x-goog-api-key": api_key, "Content-Type": "application/json"}


def _api_error(response: requests.Response) -> GeminiAPIError:
    """Build an informative error from a failed Gemini response."""
    try:
        body = response.json()
        message = body.get("error", {}).get("message") or response.text
    except (ValueError, AttributeError):
        message = response.text
    return GeminiAPIError(response.status_code, str(message)[:2000])


def list_gemini_models(
    api_key: str, base_url: str = GEMINI_BASE_URL, timeout: int = 30
) -> list[str]:
    """List available Gemini model ids (also validates the API key)."""
    response = requests.get(
        f"{base_url.rstrip('/')}/models",
        headers=_auth_headers(api_key),
        timeout=timeout,
    )
    if response.status_code != 200:
        raise _api_error(response)
    return [m.get("name", "") for m in response.json().get("models", [])]


def _content_to_str(content: Any) -> str:
    """Coerce LangChain message content (str or blocks) to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


_ALLOWED_SCHEMA_KEYS = {
    "type",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "format",
    "nullable",
    "default",
}


def _clean_schema(node: Any) -> Any:
    """Strip JSON-schema keys Gemini rejects ($schema, additionalProperties, ...).

    Property *names* under `properties` are data, not keywords — they are always
    kept; only unknown keyword keys are dropped.
    """
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {name: _clean_schema(sub) for name, sub in value.items()}
            elif key in _ALLOWED_SCHEMA_KEYS:
                cleaned[key] = _clean_schema(value)
        return cleaned
    return node


def _to_function_declaration(tool: Any) -> dict[str, Any]:
    """Convert a LangChain tool to a Gemini function declaration."""
    openai_tool = convert_to_openai_tool(tool)
    function = openai_tool.get("function", openai_tool)
    return {
        "name": function.get("name", ""),
        "description": function.get("description", "") or "",
        "parameters": _clean_schema(function.get("parameters", {"type": "object"})),
    }


class GeminiChat(BaseChatModel):
    """Chat model hitting Gemini's `generateContent` with `requests`."""

    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    api_key: str = Field(default="", repr=False)
    base_url: str = GEMINI_BASE_URL
    timeout: int = 120
    # tool_call id -> thoughtSignature from Gemini's reply. Multi-turn tool
    # calls must echo the signature back or the API 400s.
    _thought_signatures: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "gemini-requests"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> RunnableBinding:
        """Bind LangChain tools as Gemini function declarations for the next call."""
        declarations = [_to_function_declaration(tool) for tool in tools]
        payload_kwargs: dict[str, Any] = {
            "tools": [{"functionDeclarations": declarations}],
            **kwargs,
        }
        if isinstance(tool_choice, str):
            mode = {
                "auto": "AUTO",
                "any": "ANY",
                "required": "ANY",
                "none": "NONE",
            }.get(tool_choice.lower())
            if mode:
                payload_kwargs["tool_config"] = {"functionCallingConfig": {"mode": mode}}
        return RunnableBinding(bound=self, kwargs=payload_kwargs)

    def _function_name_for(self, messages: list[BaseMessage], tool_call_id: str) -> str:
        """Resolve a tool_call_id to its function name from transcript history."""
        for message in messages:
            if isinstance(message, AIMessage):
                for call in message.tool_calls or []:
                    if call.get("id") == tool_call_id:
                        return call.get("name", "")
        return ""

    def _tool_result_part(
        self,
        messages: list[BaseMessage],
        declared_names: list[str],
        message: ToolMessage,
    ) -> dict[str, Any]:
        """Build the turn carrying one tool result back to Gemini."""
        name = self._function_name_for(messages, message.tool_call_id)
        if not name and len(declared_names) == 1:
            name = declared_names[0]
        try:
            result = json.loads(str(message.content))
        except (ValueError, TypeError):
            result = {"result": _content_to_str(message.content)}
        if not isinstance(result, dict):
            result = {"result": result}
        # NOTE: this backend rejects role "function" (400) — tool results
        # go back as a "user" turn carrying the functionResponse part.
        return {
            "role": "user",
            "parts": [{"functionResponse": {"name": name, "response": result}}],
        }

    def _assistant_part(self, message: AIMessage) -> dict[str, Any]:
        """Build the turn replaying one assistant message (text + tool calls)."""
        parts: list[dict[str, Any]] = []
        text = _content_to_str(message.content)
        if text:
            parts.append({"text": text})
        for call in message.tool_calls or []:
            part: dict[str, Any] = {
                "functionCall": {
                    "name": call.get("name", ""),
                    "args": call.get("args", {}),
                }
            }
            # thoughtSignature is a sibling of functionCall (not nested).
            signature = self._thought_signatures.get(call.get("id", ""))
            if signature:
                part["thoughtSignature"] = signature
            parts.append(part)
        return {"role": "model", "parts": parts or [{"text": ""}]}

    def _build_payload(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None,
        tools: Any,
        tool_config: Any,
    ) -> dict[str, Any]:
        """Convert a LangChain transcript into a Gemini generateContent payload."""
        declared_names = [
            declaration.get("name", "")
            for tool in (tools or [])
            for declaration in tool.get("functionDeclarations", [])
        ]
        contents: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if isinstance(message, HumanMessage):
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": _content_to_str(message.content)}],
                    }
                )
            elif isinstance(message, ToolMessage):
                contents.append(self._tool_result_part(messages, declared_names, message))
            elif isinstance(message, AIMessage):
                contents.append(self._assistant_part(message))
            elif isinstance(message, ChatMessage):
                role = "model" if message.role == "assistant" else "user"
                contents.append(
                    {
                        "role": role,
                        "parts": [{"text": _content_to_str(message.content)}],
                    }
                )

        payload: dict[str, Any] = {"contents": contents}
        system_texts = [
            _content_to_str(m.content) for m in messages if isinstance(m, SystemMessage)
        ]
        if system_texts:
            payload["system_instruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
        generation_config: dict[str, Any] = {"temperature": self.temperature}
        if self.max_tokens is not None:
            generation_config["maxOutputTokens"] = self.max_tokens
        if stop:
            generation_config["stopSequences"] = stop
        payload["generationConfig"] = generation_config
        if tools is not None:
            payload["tools"] = tools
        if tool_config is not None:
            payload["toolConfig"] = tool_config
        return payload

    def _parse_candidate(self, data: dict[str, Any]) -> AIMessage:
        """Parse a generateContent response into an assistant message."""
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "unknown")
            raise GeminiAPIError(400, f"Gemini returned no candidates (blockReason: {reason})")

        candidate = candidates[0]
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for part in (candidate.get("content") or {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"] or "")
            if "functionCall" in part:
                call = part["functionCall"]
                call_id = call.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                # thoughtSignature is a sibling of functionCall, not nested in it.
                if part.get("thoughtSignature"):
                    self._thought_signatures[call_id] = part["thoughtSignature"]
                tool_calls.append(
                    {
                        "name": call.get("name", ""),
                        "args": call.get("args", {}) or {},
                        "id": call_id,
                        "type": "tool_call",
                    }
                )
        return AIMessage(
            content="".join(text_parts),
            tool_calls=tool_calls,
            response_metadata={
                "model": self.model,
                "usage": data.get("usageMetadata", {}),
                "finish_reason": candidate.get("finishReason", ""),
            },
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._build_payload(
            messages, stop, kwargs.get("tools"), kwargs.get("tool_config")
        )
        response = requests.post(
            f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent",
            headers=_auth_headers(self.api_key),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise _api_error(response)
        return ChatResult(
            generations=[ChatGeneration(message=self._parse_candidate(response.json()))]
        )
