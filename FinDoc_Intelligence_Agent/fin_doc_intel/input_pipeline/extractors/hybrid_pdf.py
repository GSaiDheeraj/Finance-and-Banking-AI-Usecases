"""Page-by-page reader for PDFs that already contain text.

Each sheet is inspected: sheets carrying tables or large pictures are
photographed through the shared vision reader (slow, faithful); plain sheets
render straight from the text layer (fast, keeps headings and lists). The
vision reader arrives injected so its weights are never loaded twice.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal

import fitz
import pymupdf4llm

from .ocr_pdf import OcrPdfExtractor

_logger = logging.getLogger(__name__)

IMAGE_BLOCK_KIND = 1
LARGE_PICTURE_SHARE = 0.05
_LEFT, _TOP, _RIGHT, _BOTTOM = 0, 1, 2, 3

SheetPlan = Literal["ocr", "text"]


def _rectangle_area(box: list[float]) -> float:
    width = max(0.0, box[_RIGHT] - box[_LEFT])
    height = max(0.0, box[_BOTTOM] - box[_TOP])
    return width * height


class HybridPdfExtractor:
    """Vision for rich sheets, text extraction for plain ones."""

    def __init__(self, ocr_extractor: OcrPdfExtractor) -> None:
        self._vision = ocr_extractor

    def extract(self, source: Path, output_dir: Path) -> Path:
        """Render the filing to `output_dir/{stem}.md`, one section per sheet."""
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{source.stem}.md"
        target.write_text(self._render_all(source), encoding="utf-8")
        return target

    def _render_all(self, source: Path) -> str:
        """Walk every sheet, routing each to its cheaper faithful renderer."""
        sections: list[str] = []
        with fitz.open(source) as filing:
            total = filing.page_count
            for position, sheet in enumerate(filing, start=1):
                plan: SheetPlan = "ocr" if self._sheet_needs_vision(sheet) else "text"
                begun = time.perf_counter()
                sections.append(f"## Page {position}\n\n{self._render_sheet(sheet, plan)}")
                spent = time.perf_counter() - begun
                _logger.info("[%s] page %s/%s done in %.1fs", plan, position, total, spent)
        return "\n\n".join(sections) + "\n"

    def _render_sheet(self, sheet: fitz.Page, plan: SheetPlan) -> str:
        if plan == "ocr":
            return self._vision.ocr_page(sheet).strip()
        return self._render_text(sheet)

    def _render_text(self, sheet: fitz.Page) -> str:
        """Text-layer Markdown for a plain sheet, minus library separators."""
        rendered = pymupdf4llm.to_markdown(sheet.parent, pages=[sheet.number], show_progress=False)
        return rendered.strip().strip("-").strip()

    def _sheet_needs_vision(self, sheet: fitz.Page) -> bool:
        """True for sheets with large pictures or any detected grid."""
        if self._sheet_has_large_pictures(sheet):
            return True
        try:
            return len(list(sheet.find_tables().tables)) > 0
        except Exception:  # noqa: BLE001
            _logger.debug("table detection failed; using text extraction", exc_info=True)
            return False

    def _sheet_has_large_pictures(self, sheet: fitz.Page) -> bool:
        """True when picture blocks cover more than a small share of the sheet."""
        sheet_area = sheet.rect.width * sheet.rect.height
        if sheet_area <= 0:
            return False
        blocks = sheet.get_text("dict").get("blocks", [])
        pictured = sum(
            _rectangle_area(block["bbox"])
            for block in blocks
            if block.get("type") == IMAGE_BLOCK_KIND
        )
        return (pictured / sheet_area) > LARGE_PICTURE_SHARE
