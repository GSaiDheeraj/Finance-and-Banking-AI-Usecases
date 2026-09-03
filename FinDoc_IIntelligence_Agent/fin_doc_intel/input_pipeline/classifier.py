"""Document classification.

Ported verbatim from FinDoc Pypi's `input_module/classifier.py`. Decides whether
an input file is a text-based PDF, a scanned (image-based) PDF, or an XBRL/iXBRL
instance — pure PyMuPDF heuristics, no ML.

Classification rules:
    XBRL - file extension is `.xbrl` or the file's first bytes contain an
           XBRL/iXBRL root element or namespace.
    PDF  - a page is "image-dominant" when image-block area covers more than
           50% of the page. The PDF is scanned when 10+ pages are
           image-dominant, OR no page has a usable text layer. Otherwise
           it's a text PDF.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import fitz

from .models import DocumentClass

IMAGE_BLOCK_TYPE: int = 1
IMAGE_DOMINANT_COVERAGE: float = 0.50
SCANNED_PAGE_THRESHOLD: int = 10
TEXT_LAYER_MIN_CHARS: int = 50
XBRL_SNIFF_BYTES: int = 4096
XBRL_MARKERS: Tuple[str, ...] = (
    "<xbrl",
    "<xbrli:xbrl",
    "http://www.xbrl.org/",
    "http://www.w3.org/1999/xhtml",  # iXBRL is XHTML-hosted
)
XBRL_EXTENSIONS: Tuple[str, ...] = (".xbrl",)


class UnsupportedDocumentError(ValueError):
    """Raised when the input file is neither PDF nor XBRL."""


@dataclass(frozen=True)
class ClassificationReport:
    """Result of classifying one document."""
    document_class: DocumentClass
    page_count: Optional[int] = None
    image_dominant_pages: Optional[int] = None
    has_text_layer: Optional[bool] = None
    extra: dict = field(default_factory=dict)


class DocumentClassifier:
    """Classify a document into one of the supported input classes."""

    def classify(self, source: Path) -> ClassificationReport:
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")

        if self._is_xbrl(source):
            return ClassificationReport(document_class=DocumentClass.XBRL)

        if self._is_pdf(source):
            return self._classify_pdf(source)

        raise UnsupportedDocumentError(f"File is neither PDF nor XBRL: {source}")

    def _is_xbrl(self, source: Path) -> bool:
        if source.suffix.lower() in XBRL_EXTENSIONS:
            return True
        try:
            head = source.read_bytes()[:XBRL_SNIFF_BYTES].decode("utf-8", errors="ignore")
        except OSError:
            return False
        return any(marker in head for marker in XBRL_MARKERS)

    def _is_pdf(self, source: Path) -> bool:
        try:
            with source.open("rb") as fh:
                return fh.read(5) == b"%PDF-"
        except OSError:
            return False

    def _classify_pdf(self, source: Path) -> ClassificationReport:
        with fitz.open(source) as doc:
            page_count = doc.page_count
            image_dominant = sum(1 for page in doc if self._is_image_dominant(page))
            has_text = any(self._page_has_text(page) for page in doc)

        is_scanned = image_dominant >= SCANNED_PAGE_THRESHOLD or not has_text
        document_class = DocumentClass.SCANNED_PDF if is_scanned else DocumentClass.TEXT_PDF
        return ClassificationReport(
            document_class=document_class,
            page_count=page_count,
            image_dominant_pages=image_dominant,
            has_text_layer=has_text,
        )

    def _is_image_dominant(self, page: "fitz.Page") -> bool:
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        if page_area <= 0:
            return False
        blocks = page.get_text("dict").get("blocks", [])
        image_area = sum(
            self._block_area(block) for block in blocks if block.get("type") == IMAGE_BLOCK_TYPE
        )
        return (image_area / page_area) > IMAGE_DOMINANT_COVERAGE

    def _block_area(self, block: dict) -> float:
        x0, y0, x1, y1 = block["bbox"]
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def _page_has_text(self, page: "fitz.Page") -> bool:
        return len(page.get_text("text").strip()) > TEXT_LAYER_MIN_CHARS
