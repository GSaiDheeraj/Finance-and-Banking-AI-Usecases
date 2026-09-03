"""Table-based fundamentals extraction pipeline.

Ported from FinDoc Pypi's `fundamentals_module` (find_tables -> filter_relevant ->
classify_statement -> classify_consolidation -> extract_line_items -> aggregate_all),
with PyMuPDF native table detection (`table_finder.py`) in place of the reference's
Markdown+OCR ingestion, and a single LLM provider (`structured_llm.py`) in place of
its Azure/local-HuggingFace dual-provider abstraction.
"""
