"""
PDF loading for the credit-submission pack.

Text PDFs only (no OCR). Each page of each document becomes one `PageChunk` so that any
financial table, balance-sheet section, cash-flow statement, ratio summary, note or
management-discussion paragraph can be located by meaning and cited by document + page.
Semantic search over these chunks is `db.page_index.PgPageIndex` (Postgres + pgvector) —
pages are embedded once and persisted per assessment version, not held in an in-memory
index here.
"""
from __future__ import annotations

import os
from typing import List

import fitz  # PyMuPDF
from pydantic import BaseModel


class PageChunk(BaseModel):
    page_number: int          # 1-based, global across the merged pack
    text: str
    doc_name: str = ""        # source filename the page came from


def load_pdf_as_pages(pdf_path: str) -> List[PageChunk]:
    """Read a text PDF into per-page chunks, preserving 1-based page numbers."""
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
