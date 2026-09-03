"""PDF ingestion: classify a document and extract it to Markdown-with-HTML-tables.

Ported from FinDoc Pypi's `input_module`. `extractors/text_pdf.py` is not ported —
confirmed dead code in the reference (never wired into its own `InputProcessor`,
pulls in pandas/pdfplumber/pdfminer.six/tabulate that nothing else here needs).

Text PDFs go through `HybridPdfExtractor` (pymupdf4llm for text-only pages, OCR for
pages with a table or significant image coverage); scanned PDFs go through
`OcrPdfExtractor` for every page. The OCR backend is a local vision-language model
(`lightonai/LightOnOCR-2-1B` via `transformers`+`torch`), loaded once and shared.
"""
