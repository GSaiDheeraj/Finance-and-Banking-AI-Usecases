"""One-shot typed answers from the configured chat tier.

Resolves the model through the shared settings (gateway first, Gemini
fallback) and binds the wanted schema with function calling, so both tiers
serve structured reads through this single small helper.
"""

from __future__ import annotations

from typing import TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from ..config import get_llm

_T = TypeVar("_T", bound=BaseModel)


class EmptyStructuredOutputError(RuntimeError):
    """The model round-trip produced nothing parseable."""


class StructuredExtractor:
    """Ask the chat tier once and receive a validated schema instance."""

    def __init__(self, schema: type[_T], model: str, max_tokens: int = 8192) -> None:
        self.schema = schema
        chat = get_llm(model_name=model, max_tokens=max_tokens)
        self._typed_call = chat.with_structured_output(schema, method="function_calling")

    def extract(self, messages: list[BaseMessage]) -> _T:
        answer = self._typed_call.invoke(messages)
        if answer is None:
            raise EmptyStructuredOutputError(
                f"Structured output for schema {self.schema.__name__} returned None."
            )
        return answer
