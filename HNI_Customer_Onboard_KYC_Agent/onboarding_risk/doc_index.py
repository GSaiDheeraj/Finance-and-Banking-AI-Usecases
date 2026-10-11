"""
PDF loading and a lightweight semantic page index for the onboarding pack.

Text PDFs only (no OCR). Each page of each document becomes one searchable chunk so
that any clause (trust deed), register entry, ownership-chart row or source-of-wealth
declaration can be located by meaning and cited by document + page.
"""

from __future__ import annotations

import os

import fitz  # PyMuPDF
import numpy as np
from pydantic import BaseModel

from .config import get_embedder

_NORM_EPSILON = 1e-10
_DEFAULT_TOP_K = 5


class PageChunk(BaseModel):
    page_number: int  # 1-based, global across the merged pack
    text: str
    doc_name: str = ""  # source filename the page came from


class PageIndexError(ValueError):
    """Raised when a PDF pack cannot be indexed (missing file, image-only PDF)."""


def load_pdf_as_pages(pdf_path: str) -> list[PageChunk]:
    """Read a text PDF into per-page chunks, preserving 1-based page numbers."""
    if not os.path.exists(pdf_path):
        raise PageIndexError(f"PDF not found: {pdf_path}")
    document_name = os.path.basename(pdf_path)
    try:
        with fitz.open(pdf_path) as document:
            pages = [
                PageChunk(page_number=page_number, text=page_text, doc_name=document_name)
                for page_number, page in enumerate(document, start=1)
                if (page_text := page.get_text("text")) and page_text.strip()
            ]
    except fitz.FileDataError as exc:
        raise PageIndexError(f"Cannot read PDF {pdf_path}: {exc}") from exc
    if not pages:
        raise PageIndexError(
            "No extractable text found — the document may be scanned/image-only "
            "(OCR is out of scope for this build)."
        )
    return pages


def load_pack_as_pages(pdf_paths: list[str]) -> list[PageChunk]:
    """Load several documents into one pack, assigning continuous global page numbers."""
    pack: list[PageChunk] = []
    for pdf_path in pdf_paths:
        for source_page in load_pdf_as_pages(pdf_path):
            pack.append(
                PageChunk(
                    page_number=len(pack) + 1,
                    text=source_page.text,
                    doc_name=source_page.doc_name,
                )
            )
    return pack


def _normalize_rows(vectors: list[list[float]]) -> np.ndarray:
    embedding_array = np.asarray(vectors, dtype="float32")
    row_norms = np.linalg.norm(embedding_array, axis=1, keepdims=True) + _NORM_EPSILON
    return embedding_array / row_norms


def _normalize_vector(vector: list[float]) -> np.ndarray:
    query_array = np.asarray(vector, dtype="float32")
    return query_array / (np.linalg.norm(query_array) + _NORM_EPSILON)


class PageIndex:
    """Cosine-similarity index over page embeddings for the onboarding pack."""

    def __init__(self, pages: list[PageChunk]):
        if not pages:
            raise PageIndexError("Cannot build an index over zero pages.")
        self.pages = pages
        self.embedder = get_embedder()
        self.embeddings = self._embed_pages(pages)
        self._pages_by_number = {page.page_number: page for page in pages}

    def _embed_pages(self, pages: list[PageChunk]) -> np.ndarray:
        vectors = self.embedder.embed_documents([page.text for page in pages])
        return _normalize_rows(vectors)

    def search(self, query: str, k: int = _DEFAULT_TOP_K) -> list[PageChunk]:
        """Return the top-k pages most similar to the query."""
        if not query.strip():
            return []
        query_vector = _normalize_vector(self.embedder.embed_query(query))
        similarities = self.embeddings @ query_vector
        top_indices = np.argsort(-similarities)[: max(1, k)]
        return [self.pages[index] for index in top_indices]

    def get_page(self, page_number: int) -> PageChunk:
        """Return one page by its global 1-based number."""
        try:
            return self._pages_by_number[page_number]
        except KeyError as exc:
            raise PageIndexError(f"Page {page_number} not found.") from exc
