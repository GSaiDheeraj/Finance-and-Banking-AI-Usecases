"""
Shared parsing for LLM responses that should be a single JSON value.

Every grounded call in this package prompts the model to "output ONLY JSON" and then has
to tolerate the common ways models violate that instruction anyway:

* wrapping the answer in a ```json fence (or a bare ``` fence),
* adding a prose preamble before the fence — this shows up specifically after a
  tool-calling exchange (e.g. "Based on the search results, ...\n\n```json\n{...}\n```"),
  where the model treats its final turn as a summary rather than a bare completion,
* or, with no fence at all, wrapping a bare `{...}`/`[...]` in a sentence or two.

This one implementation handles all three and replaces three near-identical copies that
had drifted slightly apart (`extraction.py`, `narrative.py`, `osint/sentiment.py`).
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class ProductDetails(BaseModel):
    """Structured product facts extracted from a prose description."""

    name: str = Field(description="Product name as stated in the description.")
    price: float | None = Field(
        default=None, description="Price as a bare number, no currency symbol."
    )
    features: list[str] = Field(
        default_factory=list, description="Salient product features."
    )


def strip_json_fence(text: str) -> str:
    """Pull the content out of a ```json ... ``` fence anywhere in `text`, if present."""
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
    return match.group(1).strip() if match else stripped


def _find_json_span(text: str) -> str | None:
    """Best-effort: the substring from the first `{`/`[` to the last `}`/`]`.

    Used only when there's no fence and a direct parse already failed — i.e. the model
    wrapped a bare JSON value in a sentence or two of prose.
    """
    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1] if end > start else None


def safe_json_loads(text: str) -> Any:
    """Parse `text` as JSON, tolerating a fence and/or prose around the JSON value.

    Returns None if no valid JSON value can be recovered.
    """
    candidate = strip_json_fence(text)
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    span = _find_json_span(candidate)
    if span is None:
        return None
    try:
        return json.loads(span)
    except (json.JSONDecodeError, TypeError):
        return None


PRODUCT_SYSTEM_PROMPT = (
    "Extract product details into JSON with this structure:\n"
    '{{\n  "name": "product name here",\n'
    '  "price": number_here_without_currency_symbol,\n'
    '  "features": ["feature1", "feature2", "feature3"]\n}}'
)


def build_product_chain(llm) -> Any:
    """Build the prompt | Gemini LLM | JSON-parser chain for product extraction.

    Takes any chat LLM (Gemini by default via `config.get_product_llm()`) and returns
    a runnable chain guaranteeing parsed `ProductDetails`-shaped output instead of
    raw text the caller must parse by hand.
    """
    parser = JsonOutputParser(pydantic_object=ProductDetails)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", PRODUCT_SYSTEM_PROMPT),
            ("user", "{input}"),
        ]
    )
    return prompt | llm | parser


def parse_product_description(description: str, llm=None) -> dict:
    """Extract product details from prose, raising on empty input.

    Uses the provided LLM or a default Gemini client at temperature 0.7. Returns a
    plain dict so callers don't depend on langchain types.
    """
    if not description or not description.strip():
        raise ValueError("description must be a non-empty string.")
    from .config import get_product_llm

    active_llm = llm if llm is not None else get_product_llm()
    result = build_product_chain(active_llm).invoke({"input": description})
    return dict(result) if isinstance(result, dict) else result.model_dump()
