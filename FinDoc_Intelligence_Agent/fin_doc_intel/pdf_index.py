"""Readable-page loader plus a small semantic lookup over those pages.

Only filings with real embedded text are handled here (photographed pages are
out of scope). Every kept page becomes one retrievable unit so a later step
can ask "where is the borrowings note?" by meaning and still cite a page.
"""

from __future__ import annotations

import fitz  # PyMuPDF
import numpy as np
from pydantic import BaseModel

from .config import get_embedder

_VECTOR_NORM_FLOOR = 1e-10


class PageChunk(BaseModel):
    page_number: int  # 1-based
    text: str


def load_pdf_as_pages(pdf_path: str) -> list[PageChunk]:
    """Split a readable PDF into one chunk per non-blank page."""
    if not pdf_path:
        raise ValueError("pdf_path must be a non-empty path.")
    collected: list[PageChunk] = []
    with fitz.open(pdf_path) as filing:
        for position, sheet in enumerate(filing):
            sheet_text = sheet.get_text("text")
            if sheet_text and sheet_text.strip():
                collected.append(PageChunk(page_number=position + 1, text=sheet_text))
    if not collected:
        raise ValueError(
            "No extractable text found — the PDF may be scanned/image-only "
            "(OCR is out of scope for this build)."
        )
    return collected


def _unit_length(rows: np.ndarray) -> np.ndarray:
    magnitudes = np.linalg.norm(rows, axis=1, keepdims=True) + _VECTOR_NORM_FLOOR
    return rows / magnitudes


class PageIndex:
    """Nearest-neighbour lookup over page embeddings using cosine similarity."""

    def __init__(self, pages: list[PageChunk]):
        if not pages:
            raise ValueError("pages must contain at least one page.")
        self.pages = pages
        self._by_page_number = {sheet.page_number: sheet for sheet in pages}
        self.embedder = get_embedder()
        self.embeddings = self._vectorise(pages)

    def _vectorise(self, sheets: list[PageChunk]) -> np.ndarray:
        raw_vectors = self.embedder.embed_documents([sheet.text for sheet in sheets])
        return _unit_length(np.asarray(raw_vectors, dtype="float32"))

    def search(self, query: str, k: int = 5) -> list[PageChunk]:
        if not query or not query.strip():
            raise ValueError("query must be a non-empty string.")
        if k <= 0:
            raise ValueError("k must be a positive integer.")
        probe = np.asarray(self.embedder.embed_query(query), dtype="float32")
        probe = probe / (np.linalg.norm(probe) + _VECTOR_NORM_FLOOR)
        scores = self.embeddings @ probe
        best_positions = np.argsort(-scores)[:k]
        return [self.pages[position] for position in best_positions]

    def get_page(self, page_number: int) -> PageChunk:
        try:
            return self._by_page_number[page_number]
        except KeyError:
            raise ValueError(f"Page {page_number} not found.") from None
