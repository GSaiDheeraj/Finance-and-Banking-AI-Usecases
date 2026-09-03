"""Bind a Pydantic schema to our LLM client for structured extraction.

Trimmed from the reference's `StructuredExtractor`: that class supported two
providers (Azure OpenAI, or a local HuggingFace model run via `transformers`+
`torch`). We have exactly one provider — our existing OpenAI-compatible gateway
(`fin_doc_intel.config.get_llm`) — so the provider abstraction has no second
caller here and would be dead code; this wraps our own client instead.
"""
from __future__ import annotations

from typing import List, Type, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from ..config import get_llm

_T = TypeVar("_T", bound=BaseModel)


class EmptyStructuredOutputError(RuntimeError):
    """Raised when the underlying call yields no parseable output."""


class StructuredExtractor:
    """Bind a chat model to a Pydantic schema for one-shot structured extraction."""

    def __init__(self, schema: Type[_T], model: str, max_tokens: int = 8192) -> None:
        self.schema = schema
        llm = get_llm(model_name=model, max_tokens=max_tokens)
        self._chain = llm.with_structured_output(schema, method="function_calling")

    def extract(self, messages: List[BaseMessage]) -> _T:
        result = self._chain.invoke(messages)
        if result is None:
            raise EmptyStructuredOutputError(
                f"Structured output for schema {self.schema.__name__} returned None."
            )
        return result
