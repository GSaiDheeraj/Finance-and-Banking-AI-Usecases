"""Document understanding entry points for annual reports and filings.

Reads a filing and returns one flat row per reported fact — label, amount,
reporting period, scale, and the page it came from — so scoring and review
layers never have to parse PDFs themselves.
"""

from .extraction import detect_issuer_metadata, extract_document_content

__all__ = ["detect_issuer_metadata", "extract_document_content"]
