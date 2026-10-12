"""Conversational access to Gemini without a vendor SDK.

Calls `https://generativelanguage.googleapis.com/v1beta` with plain HTTP
(`{model}:generateContent`) and presents the result as a LangChain chat model,
so existing `invoke`, tool-binding, and `prompt | model | parser` flows work:

* `model.invoke([system, user]).content` for plain prose reads
* `model.bind_tools(tools)` plus `tool_calls` on the answer for grounded reads
* prompt-piped chains for structured JSON answers

The key travels in the `x-goog-api-key` header.
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
    """The service refused the prompt or the HTTP call failed."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Gemini API error {status_code}: {message}")
        self.status_code = status_code


def _credential_headers(secret: str) -> dict[str, str]:
    return {"x-goog-api-key": secret, "Content-Type": "application/json"}


def _failure_from_response(failed: requests.Response) -> GeminiAPIError:
    """Shape a readable error from a rejected Gemini call."""
    try:
        payload = failed.json()
        detail = payload.get("error", {}).get("message") or failed.text
    except (ValueError, AttributeError):
        detail = failed.text
    return GeminiAPIError(failed.status_code, str(detail)[:2000])


def list_gemini_models(
    api_key: str, base_url: str = GEMINI_BASE_URL, timeout: int = 30
) -> list[str]:
    """Fetch usable model names; doubles as a key check."""
    answer = requests.get(
        f"{base_url.rstrip('/')}/models",
        headers=_credential_headers(api_key),
        timeout=timeout,
    )
    if answer.status_code != 200:
        raise _failure_from_response(answer)
    return [entry.get("name", "") for entry in answer.json().get("models", [])]


def _message_text(body: Any) -> str:
    """Flatten LangChain content (plain string or text blocks) to text."""
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    collected = []
    for block in body:
        if isinstance(block, str):
            collected.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            collected.append(str(block.get("text", "")))
    return "".join(collected)


_PERMITTED_SCHEMA_WORDS = {
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


def _prune_schema(branch: Any) -> Any:
    """Drop schema keywords the service rejects.

    Field *names* below `properties` are payload data and always survive; only
    unrecognised keyword keys are removed.
    """
    if isinstance(branch, list):
        return [_prune_schema(leaf) for leaf in branch]
    if isinstance(branch, dict):
        pruned: dict[str, Any] = {}
        for word, value in branch.items():
            if word == "properties" and isinstance(value, dict):
                pruned[word] = {title: _prune_schema(sub) for title, sub in value.items()}
            elif word in _PERMITTED_SCHEMA_WORDS:
                pruned[word] = _prune_schema(value)
        return pruned
    return branch


def _as_function_spec(convertible: Any) -> dict[str, Any]:
    """Express a LangChain tool as a Gemini function spec."""
    openai_shape = convert_to_openai_tool(convertible)
    function = openai_shape.get("function", openai_shape)
    return {
        "name": function.get("name", ""),
        "description": function.get("description", "") or "",
        "parameters": _prune_schema(function.get("parameters", {"type": "object"})),
    }


class GeminiChat(BaseChatModel):
    """LangChain-compatible chat over Gemini's `generateContent` endpoint."""

    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    api_key: str = Field(default="", repr=False)
    base_url: str = GEMINI_BASE_URL
    timeout: int = 120
    # Call id -> thoughtSignature echoed from the previous turn. Follow-up tool
    # rounds must repeat the signature or the service answers 400.
    _echo_signatures: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "gemini-requests"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> RunnableBinding:
        """Offer LangChain tools as function specs for the upcoming turn."""
        specs = [_as_function_spec(candidate) for candidate in tools]
        extra: dict[str, Any] = {
            "tools": [{"functionDeclarations": specs}],
            **kwargs,
        }
        if isinstance(tool_choice, str):
            calling = {
                "auto": "AUTO",
                "any": "ANY",
                "required": "ANY",
                "none": "NONE",
            }.get(tool_choice.lower())
            if calling:
                extra["tool_config"] = {"functionCallingConfig": {"mode": calling}}
        return RunnableBinding(bound=self, kwargs=extra)

    def _name_for_call(self, transcript: list[BaseMessage], call_id: str) -> str:
        """Recover a function name for a tool result from earlier turns."""
        for turn in transcript:
            if isinstance(turn, AIMessage):
                for pending in turn.tool_calls or []:
                    if pending.get("id") == call_id:
                        return pending.get("name", "")
        return ""

    def _result_turn(
        self,
        transcript: list[BaseMessage],
        offered: list[str],
        outcome: ToolMessage,
    ) -> dict[str, Any]:
        """Wrap one tool outcome the way the service expects it back."""
        label = self._name_for_call(transcript, outcome.tool_call_id)
        if not label and len(offered) == 1:
            label = offered[0]
        try:
            decoded = json.loads(str(outcome.content))
        except (ValueError, TypeError):
            decoded = {"result": _message_text(outcome.content)}
        if not isinstance(decoded, dict):
            decoded = {"result": decoded}
        # The service rejects a "function" role here — outcomes travel as a
        # "user" turn holding a functionResponse part.
        return {
            "role": "user",
            "parts": [{"functionResponse": {"name": label, "response": decoded}}],
        }

    def _assistant_turn(self, turn: AIMessage) -> dict[str, Any]:
        """Replay one assistant turn (prose plus any requested calls)."""
        fragments: list[dict[str, Any]] = []
        prose = _message_text(turn.content)
        if prose:
            fragments.append({"text": prose})
        for pending in turn.tool_calls or []:
            fragment: dict[str, Any] = {
                "functionCall": {
                    "name": pending.get("name", ""),
                    "args": pending.get("args", {}),
                }
            }
            # The signature sits next to functionCall, never inside it.
            echoed = self._echo_signatures.get(pending.get("id", ""))
            if echoed:
                fragment["thoughtSignature"] = echoed
            fragments.append(fragment)
        return {"role": "model", "parts": fragments or [{"text": ""}]}

    def _request_body(
        self,
        transcript: list[BaseMessage],
        stop: list[str] | None,
        functions: Any,
        calling: Any,
    ) -> dict[str, Any]:
        """Translate LangChain turns into a generateContent body."""
        offered = [
            spec.get("name", "")
            for bundle in (functions or [])
            for spec in bundle.get("functionDeclarations", [])
        ]
        turns: list[dict[str, Any]] = []
        for entry in transcript:
            if isinstance(entry, SystemMessage):
                continue
            if isinstance(entry, HumanMessage):
                turns.append({"role": "user", "parts": [{"text": _message_text(entry.content)}]})
            elif isinstance(entry, ToolMessage):
                turns.append(self._result_turn(transcript, offered, entry))
            elif isinstance(entry, AIMessage):
                turns.append(self._assistant_turn(entry))
            elif isinstance(entry, ChatMessage):
                who = "model" if entry.role == "assistant" else "user"
                turns.append({"role": who, "parts": [{"text": _message_text(entry.content)}]})

        body: dict[str, Any] = {"contents": turns}
        briefs = [
            _message_text(turn.content) for turn in transcript if isinstance(turn, SystemMessage)
        ]
        if briefs:
            body["system_instruction"] = {"parts": [{"text": "\n\n".join(briefs)}]}
        sampling: dict[str, Any] = {"temperature": self.temperature}
        if self.max_tokens is not None:
            sampling["maxOutputTokens"] = self.max_tokens
        if stop:
            sampling["stopSequences"] = stop
        body["generationConfig"] = sampling
        if functions is not None:
            body["tools"] = functions
        if calling is not None:
            body["toolConfig"] = calling
        return body

    def _answer_from(self, payload: dict[str, Any]) -> AIMessage:
        """Read the first candidate into an assistant turn."""
        options = payload.get("candidates") or []
        if not options:
            blocker = (payload.get("promptFeedback") or {}).get("blockReason", "unknown")
            raise GeminiAPIError(400, f"Gemini returned no candidates (blockReason: {blocker})")

        chosen = options[0]
        prose_parts: list[str] = []
        requested: list[dict[str, Any]] = []
        for fragment in (chosen.get("content") or {}).get("parts", []):
            if "text" in fragment:
                prose_parts.append(fragment["text"] or "")
            if "functionCall" in fragment:
                invoked = fragment["functionCall"]
                call_id = invoked.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                # The signature arrives beside functionCall, not within it.
                if fragment.get("thoughtSignature"):
                    self._echo_signatures[call_id] = fragment["thoughtSignature"]
                requested.append(
                    {
                        "name": invoked.get("name", ""),
                        "args": invoked.get("args", {}) or {},
                        "id": call_id,
                        "type": "tool_call",
                    }
                )
        return AIMessage(
            content="".join(prose_parts),
            tool_calls=requested,
            response_metadata={
                "model": self.model,
                "usage": payload.get("usageMetadata", {}),
                "finish_reason": chosen.get("finishReason", ""),
            },
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        body = self._request_body(messages, stop, kwargs.get("tools"), kwargs.get("tool_config"))
        answer = requests.post(
            f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent",
            headers=_credential_headers(self.api_key),
            json=body,
            timeout=self.timeout,
        )
        if answer.status_code != 200:
            raise _failure_from_response(answer)
        return ChatResult(generations=[ChatGeneration(message=self._answer_from(answer.json()))])
