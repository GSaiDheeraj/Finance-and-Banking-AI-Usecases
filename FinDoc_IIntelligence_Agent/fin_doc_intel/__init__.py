"""Financial Document Intelligence Agent — table-based fundamentals extraction.

Parses a financial document (via `fundamentals_pipeline`, ported from FinDoc
Pypi's `fundamentals_module`) into a flat table of line items: label, value,
period end date, period length, unit, scale, and which company/document each
row came from.
"""

from .extraction import extract_document_content

__all__ = ["extract_document_content"]
