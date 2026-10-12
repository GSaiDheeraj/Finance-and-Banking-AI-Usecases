"""Retrieval tools the language model may call while reading a filing.

The active document lookup is handed in once per run, so from the model's side
the tools look stateless — it simply names them.
"""

from __future__ import annotations

from typing import Any

from langchain.tools import tool

from .pdf_index import PageChunk, PageIndex

_active_index: PageIndex | None = None


def set_page_index(index: PageIndex) -> None:
    global _active_index
    _active_index = index


def reset_page_index() -> None:
    """Forget the injected lookup (intended for tests)."""
    global _active_index
    _active_index = None


def _current_index() -> PageIndex:
    if _active_index is None:
        raise ValueError("Page index not initialized — call set_page_index() first.")
    return _active_index


def _serialise_page(sheet: PageChunk) -> dict[str, Any]:
    return {"page_number": sheet.page_number, "text": sheet.text}


@tool("search_pages", return_direct=False)
def search_pages(query: str, k: int = 5) -> list[dict[str, Any]]:
    """Find the pages whose meaning best matches the query.

    Ask for statements, notes, or disclosures by description, e.g.
    "consolidated balance sheet", "borrowings maturity", "interest expense".
    """
    hits: list[PageChunk] = _current_index().search(query, k=k)
    return [_serialise_page(sheet) for sheet in hits]


@tool("get_page", return_direct=False)
def get_page(page_number: int) -> dict[str, Any]:
    """Fetch the complete text of one numbered page (1-based)."""
    return _serialise_page(_current_index().get_page(page_number))


TOOLS = [search_pages, get_page]
TOOL_MAP = {tool_fn.name: tool_fn for tool_fn in TOOLS}
