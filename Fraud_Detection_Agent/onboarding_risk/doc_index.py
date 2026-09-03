"""
PDF loading and a lightweight semantic page index for the onboarding pack.

Text PDFs only (no OCR). Each page of each document becomes one searchable chunk so
that any clause (trust deed), register entry, ownership-chart row or source-of-wealth
declaration can be located by meaning and cited by document + page.
"""
from __future__ import annotations

from typing import List

import fitz  # PyMuPDF
import numpy as np
from pydantic import BaseModel

from .config import get_embedder


class PageChunk(BaseModel):
    page_number: int          # 1-based, global across the merged pack
    text: str
    doc_name: str = ""        # source filename the page came from


def load_pdf_as_pages(pdf_path: str) -> List[PageChunk]:
    """Read a text PDF into per-page chunks, preserving 1-based page numbers."""
    import os

    doc = fitz.open(pdf_path)
    name = os.path.basename(pdf_path)
    pages: List[PageChunk] = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if text and text.strip():
            pages.append(PageChunk(page_number=i + 1, text=text, doc_name=name))
    doc.close()
    if not pages:
        raise ValueError(
            "No extractable text found — the document may be scanned/image-only "
            "(OCR is out of scope for this build)."
        )
    return pages


def load_pack_as_pages(pdf_paths: List[str]) -> List[PageChunk]:
    """Load several documents into one pack, assigning continuous global page numbers."""
    pack: List[PageChunk] = []
    running = 0
    for path in pdf_paths:
        for p in load_pdf_as_pages(path):
            running += 1
            pack.append(PageChunk(page_number=running, text=p.text, doc_name=p.doc_name))
    return pack


class PageIndex:
    """Cosine-similarity index over page embeddings for the onboarding pack."""

    def __init__(self, pages: List[PageChunk]):
        self.pages = pages
        self.embedder = get_embedder()
        self.embeddings = self._embed_pages(pages)

    def _embed_pages(self, pages: List[PageChunk]) -> np.ndarray:
        vectors = self.embedder.embed_documents([p.text for p in pages])
        arr = np.asarray(vectors, dtype="float32")
        norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-10
        return arr / norms

    def search(self, query: str, k: int = 5) -> List[PageChunk]:
        q = np.asarray(self.embedder.embed_query(query), dtype="float32")
        q = q / (np.linalg.norm(q) + 1e-10)
        sims = self.embeddings @ q
        top = np.argsort(-sims)[:k]
        return [self.pages[i] for i in top]

    def get_page(self, page_number: int) -> PageChunk:
        for p in self.pages:
            if p.page_number == page_number:
                return p
        raise ValueError(f"Page {page_number} not found.")
