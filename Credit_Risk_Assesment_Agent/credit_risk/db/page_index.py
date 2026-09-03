"""
Postgres+pgvector-backed page index — the retrieval layer behind every extraction call.

Replaces `doc_index.PageIndex`'s in-memory numpy cosine index: pages are embedded once
and persisted per assessment version, so a rerun or the audit view never has to
re-embed, and a search becomes a plain SQL query instead of an in-process object.

One `PgPageIndex` is constructed per assessment version and passed explicitly to
whatever needs it (extraction, the LangChain tools) — never stored as a module-level
global. That's the direct fix for the old `tools.py`/`agent_graph.py` pattern, where a
single `_page_index` global meant two concurrent assessments would silently clobber each
other's retrieval context.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_embedder
from ..doc_index import PageChunk as PageChunkDTO
from .models import PageChunk as PageChunkRow


class PageNotFoundError(LookupError):
    """Raised when `get_page` is asked for a page number outside this pack."""


class PgPageIndex:
    """Scoped to exactly one assessment version's pack — never shared across requests."""

    def __init__(self, session: Session, assessment_version_id: str):
        self._session = session
        self._version_id = assessment_version_id
        self._embedder = get_embedder()

    def ingest(self, pages: list[PageChunkDTO], document_id: str | None = None) -> None:
        """Embed every page in one batch call and persist it, scoped to this version."""
        if not pages:
            return
        vectors = self._embedder.embed_documents([p.text for p in pages])
        for page, vector in zip(pages, vectors):
            self._session.add(PageChunkRow(
                assessment_version_id=self._version_id,
                document_id=document_id,
                page_number=page.page_number,
                doc_name=page.doc_name,
                text=page.text,
                embedding=vector,
            ))
        self._session.flush()

    def search(self, query: str, k: int = 5) -> list[PageChunkDTO]:
        """Top-k pages by cosine distance, scoped to this assessment version."""
        query_vector = self._embedder.embed_query(query)
        stmt = (
            select(PageChunkRow)
            .where(PageChunkRow.assessment_version_id == self._version_id)
            .order_by(PageChunkRow.embedding.cosine_distance(query_vector))
            .limit(k)
        )
        rows = self._session.scalars(stmt).all()
        return [_to_dto(row) for row in rows]

    def get_page(self, page_number: int) -> PageChunkDTO:
        stmt = select(PageChunkRow).where(
            PageChunkRow.assessment_version_id == self._version_id,
            PageChunkRow.page_number == page_number,
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            raise PageNotFoundError(f"Page {page_number} not found in this assessment's pack.")
        return _to_dto(row)


def _to_dto(row: PageChunkRow) -> PageChunkDTO:
    return PageChunkDTO(page_number=row.page_number, text=row.text, doc_name=row.doc_name)
