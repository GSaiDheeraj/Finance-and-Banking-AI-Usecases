"""Per-page hybrid extractor for native-text PDFs.

Ported verbatim from FinDoc Pypi's `input_module/extractors/hybrid_pdf.py`. Each
page is checked for tables/significant images. Pages with either are rasterized
and run through LightOnOCR (high fidelity but slow); pages without are rendered
as Markdown using `pymupdf4llm` (fast, preserves headings/lists/paragraphs) —
avoids running OCR on every page of a long filing, since most pages of a real
annual report are prose with no tables.

The OCR extractor is injected so the loaded model is shared across extractors
rather than duplicated in memory.
"""
from __future__ import annotations

import time
from pathlib import Path

import fitz
import pymupdf4llm

from .ocr_pdf import OcrPdfExtractor


class HybridPdfExtractor:
    """OCR pages with tables/images, text-extract pages without."""

    def __init__(self, ocr_extractor: OcrPdfExtractor) -> None:
        self._ocr_extractor = ocr_extractor

    def extract(self, source: Path, output_dir: Path) -> Path:
        """Write the PDF's content to `output_dir/{stem}.md`."""
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source.stem}.md"
        output_path.write_text(self._render_markdown(source), encoding="utf-8")
        return output_path

    def _render_markdown(self, source: Path) -> str:
        """Iterate the PDF and route each page based on table/image presence."""
        sections: list[str] = []
        with fitz.open(source) as doc:
            total = doc.page_count
            for page_index, page in enumerate(doc, start=1):
                mode = "ocr" if self._page_has_table(page) else "text"
                print(f"  [{mode}] page {page_index}/{total} starting...", flush=True)
                started = time.perf_counter()
                content = self._render_page(page, mode)
                sections.append(f"## Page {page_index}\n\n{content}")
                print(f"  [{mode}] page {page_index}/{total} done in {time.perf_counter() - started:.1f}s", flush=True)
        return "\n\n".join(sections) + "\n"

    def _render_page(self, page: "fitz.Page", mode: str) -> str:
        """Render one page using the pre-decided `mode` (computed once by the
        caller so find_tables isn't re-run just to log the route)."""
        if mode == "ocr":
            return self._ocr_extractor.ocr_page(page).strip()
        return self._extract_markdown(page)

    def _extract_markdown(self, page: "fitz.Page") -> str:
        """Render a non-table page as Markdown via pymupdf4llm.

        Strips any '-----' page separators the library may emit, since the
        outer _render_markdown already wraps each page in a '## Page N' heading.
        """
        markdown = pymupdf4llm.to_markdown(page.parent, pages=[page.number], show_progress=False)
        return markdown.strip().strip("-").strip()

    def _page_has_table(self, page: "fitz.Page") -> bool:
        """Return True if the page needs OCR: has an image or a detected table.

        1. Image coverage — if image blocks cover more than 5% of the page,
           pymupdf4llm would return garbled/no text — always use OCR.
        2. PyMuPDF line-based table finder — catches tables drawn with visible
           rules in the vector layer.
        """
        if self._page_has_significant_images(page):
            return True
        try:
            return len(list(page.find_tables().tables)) > 0
        except Exception:
            return False

    def _page_has_significant_images(self, page: "fitz.Page") -> bool:
        """Return True when image blocks cover more than 5% of page area."""
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return False
        blocks = page.get_text("dict").get("blocks", [])
        image_area = sum(
            max(0.0, b["bbox"][2] - b["bbox"][0]) * max(0.0, b["bbox"][3] - b["bbox"][1])
            for b in blocks if b.get("type") == 1  # image block type
        )
        return (image_area / page_area) > 0.05
