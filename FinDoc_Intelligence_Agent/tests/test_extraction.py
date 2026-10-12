"""Tests for the extraction functionality."""

from fin_doc_intel import detect_issuer_metadata, extract_document_content
from fin_doc_intel.extraction import ExtractionRunResult


def test_extract_document_content_is_callable():
    """Extraction entry point exists and is callable."""
    assert callable(extract_document_content)
    assert callable(detect_issuer_metadata)


def test_extraction_result_shape():
    """Extraction result carries the fields persistence depends on."""
    empty_run = ExtractionRunResult()
    assert empty_run.line_items == []
    assert empty_run.llm_call_count == 0
    assert empty_run.iterations_run == 0
