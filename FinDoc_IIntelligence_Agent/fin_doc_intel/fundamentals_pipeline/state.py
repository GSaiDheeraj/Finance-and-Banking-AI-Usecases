"""LangGraph state and intermediate types for the extraction pipeline.

Ported from FinDoc Pypi's `fundamentals_module/state.py`, using a TypedDict state
(matching `agent_graph.py::ResearchState`'s style) instead of the reference's
Pydantic-model state — the two are otherwise structurally identical.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from .models import ConsolidationMeta, LineItem, RelevanceVerdict, StatementClassification, StatementType
from .table_finder import StatementCandidate


class RelevantTable(TypedDict):
    """A statement candidate plus the relevance node's verdict."""
    candidate: StatementCandidate
    verdict: RelevanceVerdict


class ClassifiedTable(TypedDict):
    """A relevant candidate plus the statement-classifier verdict."""
    candidate: StatementCandidate
    classification: StatementClassification


class ConsolidationFiltered(TypedDict):
    """A classified candidate plus the consolidation-classifier verdict."""
    candidate: StatementCandidate
    statement_type: StatementType
    is_breakdown: bool
    breakdown_of: Optional[str]
    meta: ConsolidationMeta


class ExtractedTable(TypedDict):
    """One table after line-item extraction."""
    candidate: StatementCandidate
    statement_type: StatementType
    is_breakdown: bool
    breakdown_of: Optional[str]
    segment_id: Optional[str]
    line_items: List[LineItem]
    # Index into `consolidation_filtered` this table came from — kept explicit
    # (rather than relied on via list position) because a table whose
    # extraction call raises is dropped from this list, which would otherwise
    # shift every later table's positional index out of alignment.
    source_index: int


class PipelineState(TypedDict):
    """State threaded through the extraction graph for one document.

    Earlier fields stay populated as later nodes run, so diagnostics can
    replay any stage without re-deriving it.
    """
    md_path: Path
    small_model: str
    large_model: str

    raw_tables: List[StatementCandidate]
    relevant_tables: List[RelevantTable]
    classified_tables: List[ClassifiedTable]
    consolidation_filtered: List[ConsolidationFiltered]
    extracted_per_table: List[ExtractedTable]

    aggregated: Dict[str, List[LineItem]]
    diagnostics: List[Dict[str, Any]]

    # Populated between validation-loop iterations (see extraction.py): maps a
    # `consolidation_filtered`/`extracted_per_table` index to the validation
    # issues attributed to that table, so extract_line_items can ask the model
    # to fix only what was actually flagged on ITS table.
    correction_notes: Dict[int, List[str]]
