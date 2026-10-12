"""Shapes describing what kind of filing arrived and what came out of it.

A filing is first labelled (readable PDF, photographed PDF, or XBRL data),
then rendered to Markdown on disk when a reader exists for that label. XBRL
has no reader here, so its render path stays empty by design.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DocumentClass(str, Enum):  # noqa: UP042
    """Kind of source material, decided before any reading starts."""

    TEXT_PDF = "text_pdf"
    SCANNED_PDF = "scanned_pdf"
    XBRL = "xbrl"


class ExtractionResult(BaseModel):
    """What rendering one filing produced.

    `markdown_path` stays None for labels without a reader (XBRL today).
    """

    document_class: DocumentClass
    source_path: Path
    markdown_path: Path | None = None
    page_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
