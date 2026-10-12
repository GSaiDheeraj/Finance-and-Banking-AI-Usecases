"""Shared progress record threaded through the staged table readers.

Earlier stages stay on the record as later ones run, so any step can be
replayed from its inputs during review without re-reading the filing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from .models import (
    ConsolidationMeta,
    LineItem,
    RelevanceVerdict,
    StatementClassification,
    StatementType,
)
from .table_finder import StatementCandidate


class RelevantTable(TypedDict):
    """A located grid plus its keep-or-drop answer."""

    candidate: StatementCandidate
    verdict: RelevanceVerdict


class ClassifiedTable(TypedDict):
    """A kept grid plus its statement label."""

    candidate: StatementCandidate
    classification: StatementClassification


class ConsolidationFiltered(TypedDict):
    """A labelled grid plus its column-scope answer."""

    candidate: StatementCandidate
    statement_type: StatementType
    is_breakdown: bool
    breakdown_of: str | None
    meta: ConsolidationMeta


class ExtractedTable(TypedDict):
    """One grid after the careful reading pass."""

    candidate: StatementCandidate
    statement_type: StatementType
    is_breakdown: bool
    breakdown_of: str | None
    segment_id: str | None
    line_items: list[LineItem]
    # Origin slot in `consolidation_filtered`, stored explicitly: a grid whose
    # reading call fails leaves this list, which would otherwise shift every
    # later grid's positional slot out of line.
    source_index: int


class PipelineState(TypedDict):
    """Whole-document progress from located grids to grouped drafts."""

    md_path: Path
    small_model: str
    large_model: str

    raw_tables: list[StatementCandidate]
    relevant_tables: list[RelevantTable]
    classified_tables: list[ClassifiedTable]
    consolidation_filtered: list[ConsolidationFiltered]
    extracted_per_table: list[ExtractedTable]

    aggregated: dict[str, list[LineItem]]
    diagnostics: list[dict[str, Any]]

    # Between check-and-retry rounds (see extraction.py): per-grid-slot notes
    # so the next reading pass addresses only its own grid's findings.
    correction_notes: dict[int, list[str]]
