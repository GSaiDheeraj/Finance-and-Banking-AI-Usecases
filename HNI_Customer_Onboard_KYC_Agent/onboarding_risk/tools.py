"""
Agent tools exposed to the LLM during retrieval.

The current pack's PageIndex is injected via `set_page_index` so the tools stay
stateless from the model's point of view (it just calls them by name).
"""

from __future__ import annotations

from typing import Any

from langchain.tools import tool

from .doc_index import PageChunk, PageIndex

_DEFAULT_SEARCH_TOP_K = 5

_page_index: PageIndex | None = None


def set_page_index(index: PageIndex) -> None:
    global _page_index
    _page_index = index


def _require_index() -> PageIndex:
    if _page_index is None:
        raise ValueError("Page index not initialized — call set_page_index() first.")
    return _page_index


def _serialize_page(page: PageChunk) -> dict[str, Any]:
    return {"page_number": page.page_number, "doc_name": page.doc_name, "text": page.text}


@tool("search_pages", return_direct=False)
def search_pages(query: str, k: int = _DEFAULT_SEARCH_TOP_K) -> list[dict[str, Any]]:
    """Semantically search the onboarding pack and return the top-k matching pages.

    Use this to locate parties, ownership/shareholding tables, trust roles, or
    source-of-wealth declarations by meaning, e.g. "register of beneficial owners",
    "trustee and protector", "source of wealth declaration".
    """
    matching_pages: list[PageChunk] = _require_index().search(query, k=k)
    return [_serialize_page(page) for page in matching_pages]


@tool("get_page", return_direct=False)
def get_page(page_number: int) -> dict[str, Any]:
    """Return the full text of a specific page number (1-based, global across the pack)."""
    found_page = _require_index().get_page(page_number)
    return _serialize_page(found_page)


TOOLS = [search_pages, get_page]
TOOL_MAP = {entry.name: entry for entry in TOOLS}
