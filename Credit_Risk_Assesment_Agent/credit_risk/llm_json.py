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
from typing import Any, Optional

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def strip_json_fence(text: str) -> str:
    """Pull the content out of a ```json ... ``` fence anywhere in `text`, if present."""
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
    return match.group(1).strip() if match else stripped


def _find_json_span(text: str) -> Optional[str]:
    """Best-effort: the substring from the first `{`/`[` to the last `}`/`]`.

    Used only when there's no fence and a direct parse already failed — i.e. the model
    wrapped a bare JSON value in a sentence or two of prose.
    """
    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start:end + 1] if end > start else None


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
