"""Decide what kind of filing arrived, using only fast local signals.

XBRL data is recognised by extension or by its root markup in the first bytes.
PDFs are judged page by page with PyMuPDF: a page counts as picture-led when
image blocks cover over half its area, and a filing counts as photographed
when ten or more pages are picture-led — or when no page carries a usable text
layer at all. Everything else is a readable PDF. No model calls, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz

from .models import DocumentClass

IMAGE_BLOCK_KIND: int = 1
PICTURE_LED_COVERAGE: float = 0.50
PHOTOGRAPHED_PAGE_LIMIT: int = 10
READABLE_PAGE_MIN_CHARS: int = 50
SNIPPET_BYTES: int = 4096
XBRL_SIGNATURES: tuple[str, ...] = (
    "<xbrl",
    "<xbrli:xbrl",
    "http://www.xbrl.org/",
    "http://www.w3.org/1999/xhtml",  # iXBRL rides inside XHTML
)
XBRL_SUFFIXES: tuple[str, ...] = (".xbrl",)


class UnsupportedDocumentError(ValueError):
    """The file is neither a PDF nor recognisable XBRL data."""


@dataclass(frozen=True)
class ClassificationReport:
    """Verdict for one filing, plus the evidence behind it."""

    document_class: DocumentClass
    page_count: int | None = None
    image_dominant_pages: int | None = None
    has_text_layer: bool | None = None
    extra: dict = field(default_factory=dict)


class DocumentClassifier:
    """Label one filing so the orchestrator can pick the right reader."""

    def classify(self, source: Path) -> ClassificationReport:
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")
        if self._looks_like_xbrl(source):
            return ClassificationReport(document_class=DocumentClass.XBRL)
        if self._starts_like_pdf(source):
            return self._grade_pdf(source)
        raise UnsupportedDocumentError(f"File is neither PDF nor XBRL: {source}")

    def _looks_like_xbrl(self, source: Path) -> bool:
        if source.suffix.lower() in XBRL_SUFFIXES:
            return True
        try:
            opening = source.read_bytes()[:SNIPPET_BYTES].decode("utf-8", errors="ignore")
        except OSError:
            return False
        return any(marker in opening for marker in XBRL_SIGNATURES)

    def _starts_like_pdf(self, source: Path) -> bool:
        try:
            with source.open("rb") as handle:
                return handle.read(5) == b"%PDF-"
        except OSError:
            return False

    def _grade_pdf(self, source: Path) -> ClassificationReport:
        with fitz.open(source) as filing:
            total = filing.page_count
            picture_led = sum(1 for sheet in filing if self._page_is_picture_led(sheet))
            readable = any(self._sheet_has_words(sheet) for sheet in filing)

        photographed = picture_led >= PHOTOGRAPHED_PAGE_LIMIT or not readable
        label = DocumentClass.SCANNED_PDF if photographed else DocumentClass.TEXT_PDF
        return ClassificationReport(
            document_class=label,
            page_count=total,
            image_dominant_pages=picture_led,
            has_text_layer=readable,
        )

    def _page_is_picture_led(self, sheet: fitz.Page) -> bool:
        box = sheet.rect
        sheet_area = box.width * box.height
        if sheet_area <= 0:
            return False
        blocks = sheet.get_text("dict").get("blocks", [])
        inked = sum(
            self._rectangle_area(block) for block in blocks if block.get("type") == IMAGE_BLOCK_KIND
        )
        return (inked / sheet_area) > PICTURE_LED_COVERAGE

    def _rectangle_area(self, block: dict) -> float:
        left, top, right, bottom = block["bbox"]
        return max(0.0, right - left) * max(0.0, bottom - top)

    def _sheet_has_words(self, sheet: fitz.Page) -> bool:
        return len(sheet.get_text("text").strip()) > READABLE_PAGE_MIN_CHARS
