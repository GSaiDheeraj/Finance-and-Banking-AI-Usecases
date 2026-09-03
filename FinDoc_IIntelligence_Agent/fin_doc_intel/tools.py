"""
Agent tools exposed to the LLM during retrieval.

The current document's PageIndex is injected via `set_page_index` so the tools stay
stateless from the model's point of view (it just calls them by name).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain.tools import tool

from .pdf_index import PageChunk, PageIndex

_page_index: Optional[PageIndex] = None


def set_page_index(index: PageIndex) -> None:
    global _page_index
    _page_index = index


def _require_index() -> PageIndex:
    if _page_index is None:
        raise ValueError("Page index not initialized — call set_page_index() first.")
    return _page_index


@tool("search_pages", return_direct=False)
def search_pages(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """Semantically search the document and return the top-k matching pages.

    Use this to locate financial statements, notes, or risk disclosures by meaning,
    e.g. "consolidated balance sheet", "borrowings maturity", "interest expense".
    """
    hits: List[PageChunk] = _require_index().search(query, k=k)
    return [{"page_number": p.page_number, "text": p.text} for p in hits]


@tool("get_page", return_direct=False)
def get_page(page_number: int) -> Dict[str, Any]:
    """Return the full text of a specific page number (1-based)."""
    p = _require_index().get_page(page_number)
    return {"page_number": p.page_number, "text": p.text}


TOOLS = [search_pages, get_page]
TOOL_MAP = {t.name: t for t in TOOLS}
