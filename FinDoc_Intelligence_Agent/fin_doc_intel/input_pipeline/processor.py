"""Choose and run the reader for one filing.

Readable PDFs take the hybrid path — plain pages render as text, only
table/picture pages pay for vision. Photographed PDFs take the full-vision
path because no text layer exists to fall back on. The vision weights load
lazily and are shared, so memory holds one copy no matter how many filings
arrive.
"""

from __future__ import annotations

from pathlib import Path

from .classifier import ClassificationReport, DocumentClassifier
from .extractors import HybridPdfExtractor, OcrPdfExtractor, TextExtractor
from .models import DocumentClass, ExtractionResult


class InputProcessor:
    """Composition root: label the filing, then read it with the matching tool."""

    def __init__(
        self,
        classifier: DocumentClassifier | None = None,
        ocr_extractor: OcrPdfExtractor | None = None,
        hybrid_extractor: TextExtractor | None = None,
    ) -> None:
        """Accept collaborators for tests; build production ones lazily.

        The readers default to None so the vision weights download and load
        only when a filing genuinely needs them.
        """
        self._labeller = classifier or DocumentClassifier()
        self._photo_reader = ocr_extractor
        self._hybrid_reader = hybrid_extractor

    def process(self, source: Path, output_dir: Path) -> ExtractionResult:
        """Label `source`, render it into `output_dir`, describe what happened.

        XBRL returns no Markdown — labelling is supported, reading is not.
        """
        verdict = self._labeller.classify(source)
        rendered = self._render(source, output_dir, verdict)
        return ExtractionResult(
            document_class=verdict.document_class,
            source_path=source,
            markdown_path=rendered,
            page_count=verdict.page_count,
            metadata=self._diagnostics(verdict),
        )

    def _render(self, source: Path, output_dir: Path, verdict: ClassificationReport) -> Path | None:
        """Pick the reader by evidence, not by label alone.

        Routing keys on the live text layer: a glossy annual report can trip
        the photographed-page count with photos and infographics while every
        other page reads fine. Per-page hybrid routing already handles those,
        so whole-document vision is reserved for filings with nothing to read.
        """
        if verdict.document_class is DocumentClass.XBRL:
            return None
        if verdict.has_text_layer:
            return self._hybrid().extract(source, output_dir)
        return self._photo().extract(source, output_dir)

    def _photo(self) -> OcrPdfExtractor:
        """Vision reader, loaded the first time a photographed page needs it."""
        if self._photo_reader is None:
            self._photo_reader = OcrPdfExtractor()
        return self._photo_reader

    def _hybrid(self) -> TextExtractor:
        """Hybrid reader, sharing the already-loaded vision weights."""
        if self._hybrid_reader is None:
            self._hybrid_reader = HybridPdfExtractor(ocr_extractor=self._photo())
        return self._hybrid_reader

    def _diagnostics(self, verdict: ClassificationReport) -> dict:
        """Expose the labelling evidence alongside the render result."""
        return {
            "image_dominant_pages": verdict.image_dominant_pages,
            "has_text_layer": verdict.has_text_layer,
        }
