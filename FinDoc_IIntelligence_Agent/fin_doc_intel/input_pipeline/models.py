"""Data contracts for the input pipeline.

Ported verbatim from FinDoc Pypi's `input_module/models.py`.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class DocumentClass(str, Enum):
    """Classification of an ingested document."""
    TEXT_PDF = "text_pdf"
    SCANNED_PDF = "scanned_pdf"
    XBRL = "xbrl"


class ExtractionResult(BaseModel):
    """Outcome of processing one document.

    markdown_path is None for classes not extracted yet (e.g. XBRL).
    """
    document_class: DocumentClass
    source_path: Path
    markdown_path: Optional[Path] = None
    page_count: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
