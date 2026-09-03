"""
LangChain tools exposed to the extraction LLM during retrieval.

`make_tools(page_index)` builds a fresh pair of tools bound to one specific
`PgPageIndex` instance via closure. Each assessment run constructs its own tools from
its own index — there is no module-level index/global here. The previous version of
this module held a single `_page_index` set via `set_page_index()`, which meant two
concurrent assessments (unavoidable once this runs behind a multi-request API server)
would silently clobber each other's retrieval context; a request-scoped factory removes
that failure mode entirely rather than papering over it with a lock.
"""
from __future__ import annotations

from typing import Any, Protocol

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field


class PageSource(Protocol):
    """The subset of `PgPageIndex`'s interface the extraction tools need."""

    def search(self, query: str, k: int = 5) -> list[Any]: ...
    def get_page(self, page_number: int) -> Any: ...


class _SearchPagesArgs(BaseModel):
    query: str = Field(..., description=(
        "What to look for, e.g. 'consolidated balance sheet', 'statement of cash "
        "flows', 'interest expense and finance costs', 'contingent liabilities and "
        "guarantees', \"auditor's opinion\"."
    ))
    k: int = Field(5, description="How many matching pages to return.")


class _GetPageArgs(BaseModel):
    page_number: int = Field(..., description="1-based page number, global across the pack.")


def _page_to_dict(page: Any) -> dict[str, Any]:
    return {"page_number": page.page_number, "doc_name": page.doc_name, "text": page.text}


def make_tools(page_index: PageSource) -> list[BaseTool]:
    """Build `search_pages`/`get_page` tools scoped to one assessment's page index."""

    def search_pages(query: str, k: int = 5) -> list[dict[str, Any]]:
        return [_page_to_dict(p) for p in page_index.search(query, k=k)]

    def get_page(page_number: int) -> dict[str, Any]:
        return _page_to_dict(page_index.get_page(page_number))

    return [
        StructuredTool.from_function(
            func=search_pages,
            name="search_pages",
            description=(
                "Semantically search the credit-submission pack and return the top-k "
                "matching pages. Use this to locate financial statements or notes by "
                "meaning."
            ),
            args_schema=_SearchPagesArgs,
        ),
        StructuredTool.from_function(
            func=get_page,
            name="get_page",
            description="Return the full text of a specific page number (1-based, global across the pack).",
            args_schema=_GetPageArgs,
        ),
    ]
