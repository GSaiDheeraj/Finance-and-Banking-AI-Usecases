"""Top-level orchestration for the input pipeline.

Ported verbatim from FinDoc Pypi's `input_module/processor.py`.
`InputProcessor.process` classifies a document and dispatches it to the
appropriate extractor:

  * Text PDFs go through HybridPdfExtractor: pages with tables/images are
    OCR'd, pages without are read with PyMuPDF/pymupdf4llm text extraction.
  * Scanned PDFs go through OcrPdfExtractor: every page is OCR'd, since there
    is no usable text layer.

The OCR model is loaded lazily and shared between both paths so the weights
live in memory only once per processor instance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .classifier import ClassificationReport, DocumentClassifier
from .extractors import HybridPdfExtractor, OcrPdfExtractor, TextExtractor
from .models import DocumentClass, ExtractionResult


class InputProcessor:
    """Classify and extract documents into Markdown on disk."""

    def __init__(
        self,
        classifier: Optional[DocumentClassifier] = None,
        ocr_extractor: Optional[OcrPdfExtractor] = None,
        hybrid_extractor: Optional[TextExtractor] = None,
    ) -> None:
        """Wire up the processor's dependencies.

        `ocr_extractor`/`hybrid_extractor` default to lazy construction on
        first use, so the OCR model is only loaded (and its weights only
        downloaded/held in memory) if a document actually needs it.
        """
        self._classifier = classifier or DocumentClassifier()
        self._ocr_extractor = ocr_extractor
        self._hybrid_extractor = hybrid_extractor

    def process(self, source: Path, output_dir: Path) -> ExtractionResult:
        """Classify `source` and extract it into `output_dir`.

        `markdown_path` is None for XBRL inputs — that extraction path isn't
        implemented (matching the reference; we have no XBRL callers today).
        """
        report = self._classifier.classify(source)
        markdown_path = self._extract(source, output_dir, report)
        return ExtractionResult(
            document_class=report.document_class,
            source_path=source,
            markdown_path=markdown_path,
            page_count=report.page_count,
            metadata=self._build_metadata(report),
        )

    def _extract(self, source: Path, output_dir: Path, report: ClassificationReport) -> Optional[Path]:
        """Dispatch to the extractor matching the classification.

        Routes on `has_text_layer`, not the TEXT_PDF/SCANNED_PDF label: a real
        annual report can have enough image-dominant pages (photos, infographics)
        to cross the classifier's SCANNED_PAGE_THRESHOLD while still having a
        perfectly good text layer on every other page. Only a page that actually
        has a table or a significant image needs OCR (HybridPdfExtractor already
        decides this per page); blanket full-document OCR is reserved for the
        genuinely no-text-layer case, where PyMuPDF has nothing to fall back on.
        """
        if report.document_class is DocumentClass.XBRL:
            return None  # classified only, extraction deferred.
        if report.has_text_layer:
            return self._get_hybrid_extractor().extract(source, output_dir)
        return self._get_ocr_extractor().extract(source, output_dir)

    def _get_ocr_extractor(self) -> OcrPdfExtractor:
        """Return the OCR extractor, loading the model on first use."""
        if self._ocr_extractor is None:
            self._ocr_extractor = OcrPdfExtractor()
        return self._ocr_extractor

    def _get_hybrid_extractor(self) -> TextExtractor:
        """Return the hybrid extractor, sharing the loaded OCR model."""
        if self._hybrid_extractor is None:
            self._hybrid_extractor = HybridPdfExtractor(ocr_extractor=self._get_ocr_extractor())
        return self._hybrid_extractor

    def _build_metadata(self, report: ClassificationReport) -> dict:
        """Surface classifier diagnostics in the result metadata."""
        return {
            "image_dominant_pages": report.image_dominant_pages,
            "has_text_layer": report.has_text_layer,
        }
