"""
Grounded structured extraction.

Line items come from `fundamentals_pipeline` — a table-detection + multi-stage
classification pipeline (ported from FinDoc Pypi's `fundamentals_module`; see
the plan for the port's scope). This module flattens the pipeline's cell-based
output (one item, many periods) into one `schemas.LineItem` row per cell/period,
and wraps line-item extraction in a bounded validate/correct/re-extract loop
(see `MAX_EXTRACTION_ITERATIONS`) before returning.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_config, get_llm
from .fundamentals_pipeline import nodes, validators
from .fundamentals_pipeline.models import LineItem as PipelineLineItem
from .fundamentals_pipeline.state import PipelineState
from .fundamentals_pipeline.validators import ValidationFlag, ValidationIssue
from .input_pipeline.processor import InputProcessor
from .pdf_index import PageIndex
from .schemas import LineItem, StatementType
from .storage import DocumentStorage

_PERIOD_TYPE_MONTHS = {"3M": 3, "6M": 6, "9M": 9, "12M": 12}

# How many times to run extract_line_items + aggregate_all before giving up and
# persisting whatever the last attempt produced, validation issues attached.
MAX_EXTRACTION_ITERATIONS = 3

# One InputProcessor per process: its OCR model (when actually needed) is
# loaded lazily on first use and reused across every document after that.
_input_processor: Optional[InputProcessor] = None


def _get_input_processor() -> InputProcessor:
    global _input_processor
    if _input_processor is None:
        _input_processor = InputProcessor()
    return _input_processor


def _strip_json(text: str) -> str:
    """Remove ```json fences if the model wrapped its output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _safe_json(text: str) -> Any:
    try:
        return json.loads(_strip_json(text))
    except Exception:
        return None


def _evidence_from_pages(index: PageIndex, query: str, k: int) -> str:
    hits = index.search(query, k=k)
    blocks = [f"--- PAGE {p.page_number} ---\n{p.text}" for p in hits]
    return "\n\n".join(blocks)


@dataclass
class ExtractionRunResult:
    """Handoff from `extract_document_content` to `db.persistence` — a plain
    dataclass rather than a Pydantic model since nothing here crosses a
    validated/serialized boundary; it's a structured internal accumulator
    passed straight into building `LineItemRecord`/`Document` rows."""
    line_items: List[LineItem] = field(default_factory=list)
    llm_call_count: int = 0
    diagnostics: List[dict] = field(default_factory=list)
    iterations_run: int = 0


def _issue_key(statement_type: str, label: str, section_path: List[str], coordinates: Dict[str, str]) -> tuple:
    return (statement_type, label, tuple(section_path), tuple(sorted(coordinates.items())))


def _flatten_pipeline_items(
    aggregated: Dict[str, List[PipelineLineItem]], issues: List[ValidationIssue],
) -> List[LineItem]:
    """Flatten the pipeline's cell-based line items into one flat schemas.LineItem
    row per cell (one row per period/axis point), attaching any ValidationFlags
    that survived to the specific cell they're about.

    Matching key: (statement, label, section_path, full coordinates dict) —
    `issues` was produced by validating this exact `aggregated` value (the
    retry loop breaks immediately after validating, before mutating state
    again), so every issue's `cell_coordinates` is a copy of one of these
    cells' own `coordinates` dict and the key lines up exactly.
    """
    flags_by_key: Dict[tuple, List[ValidationFlag]] = defaultdict(list)
    for issue in issues:
        key = _issue_key(issue.statement_type, issue.label, issue.section_path, issue.cell_coordinates)
        flags_by_key[key].append(ValidationFlag(check=issue.check, severity=issue.severity, message=issue.message))

    items: List[LineItem] = []
    for statement_value, pipeline_items in aggregated.items():
        try:
            statement = StatementType(statement_value)
        except ValueError:
            statement = StatementType.OTHER
        for pipeline_item in pipeline_items:
            for cell in pipeline_item.values:
                period_end = cell.coordinates.get("period_end")
                try:
                    period_end_date = date.fromisoformat(period_end) if period_end else None
                except ValueError:
                    period_end_date = None
                page_str = cell.coordinates.get("page")
                key = _issue_key(statement_value, pipeline_item.label, pipeline_item.section_path, cell.coordinates)
                items.append(LineItem(
                    statement=statement,
                    label=pipeline_item.label,
                    section_path=pipeline_item.section_path,
                    value=cell.value,
                    unit=None,
                    currency=cell.currency,
                    period=period_end,
                    period_end_date=period_end_date,
                    period_length_months=_PERIOD_TYPE_MONTHS.get(cell.coordinates.get("period_type")),
                    scale=cell.scale,
                    consolidated=cell.coordinates.get("consolidation") == "consolidated",
                    page=int(page_str) if page_str and page_str.isdigit() else None,
                    source_snippet=cell.coordinates.get("source_table"),
                    validation_errors=flags_by_key.get(key, []),
                ))
    return items


def _serializable_aggregated(aggregated: Dict[str, List[PipelineLineItem]]) -> Dict[str, list]:
    return {statement: [item.model_dump() for item in items] for statement, items in aggregated.items()}


def extract_document_content(
    pdf_path: str, document_id: str, storage: DocumentStorage,
) -> ExtractionRunResult:
    """Convert `pdf_path` to Markdown-with-HTML-tables (via `input_pipeline`), run
    the table-based fundamentals pipeline on it, and flatten its output into flat
    LineItem rows.

    The classification stages (find_tables..classify_consolidation) run once —
    their output doesn't depend on extraction quality, so re-running them per
    retry would cost 3 extra LLM calls per table for nothing. Only
    extract_line_items + aggregate_all repeat, up to MAX_EXTRACTION_ITERATIONS
    times, validated after each pass; a validated table's correction note (see
    validators.attribute_to_tables) is scoped to that table's own issues, not
    the whole document's. Every iteration's aggregate + validation report is
    archived via `storage` before the loop decides whether to continue.
    """
    config = get_config()
    source = Path(pdf_path)
    extraction_result = _get_input_processor().process(source, source.parent)
    if extraction_result.markdown_path is None:
        return ExtractionRunResult(diagnostics=[{
            "node": "input_pipeline",
            "error": f"no markdown produced (document_class={extraction_result.document_class})",
        }])

    state: PipelineState = {
        "md_path": extraction_result.markdown_path,
        "small_model": config.small_llm_model_name,
        "large_model": config.large_llm_model_name,
        "raw_tables": [],
        "relevant_tables": [],
        "classified_tables": [],
        "consolidation_filtered": [],
        "extracted_per_table": [],
        "aggregated": {},
        "diagnostics": [],
        "correction_notes": {},
    }
    for classify_node in (
        nodes.find_tables_node, nodes.filter_relevant, nodes.classify_statement, nodes.classify_consolidation,
    ):
        state.update(classify_node(state))
    classification_llm_calls = (
        len(state["raw_tables"]) + len(state["relevant_tables"]) + len(state["classified_tables"])
    )

    issues: List[ValidationIssue] = []
    extraction_llm_calls = 0
    iteration = 0
    for iteration in range(1, MAX_EXTRACTION_ITERATIONS + 1):
        state.update(nodes.extract_line_items(state))
        state.update(nodes.aggregate_all(state))
        extraction_llm_calls += len(state["consolidation_filtered"])

        issues = validators.run_all(state["aggregated"])
        storage.save_json(
            document_id, f"iter {iteration} output/extraction.json",
            _serializable_aggregated(state["aggregated"]),
        )
        storage.save_json(
            document_id, f"iter {iteration} output/validation_report.json",
            [issue.model_dump() for issue in issues],
        )

        if not issues or iteration == MAX_EXTRACTION_ITERATIONS:
            break
        state["correction_notes"] = validators.attribute_to_tables(issues, state["extracted_per_table"])

    return ExtractionRunResult(
        line_items=_flatten_pipeline_items(state["aggregated"], issues),
        llm_call_count=classification_llm_calls + extraction_llm_calls,
        diagnostics=state["diagnostics"],
        iterations_run=iteration,
    )


def detect_issuer_metadata(index: PageIndex) -> Dict[str, Optional[str]]:
    """Best-effort issuer / period / currency / document-type detection from the
    cover & statements — one LLM call covers all four rather than a second call
    just for document type, since they're all read off the same cover-page evidence."""
    evidence = _evidence_from_pages(
        index, "company name annual report financial year reporting currency document type", k=3
    )
    system = SystemMessage(content=(
        "Identify the reporting entity metadata from the text. "
        "`document_type` is a short free-text label for what kind of filing this is "
        "(e.g. \"Annual Report\", \"10-K\", \"20-F\", \"Quarterly Report\", \"Prospectus\") "
        "— use the label the document itself uses, or your best judgment if it doesn't say. "
        "Output ONLY a JSON object: "
        '{"issuer": str|null, "period": str|null, "currency": str|null, "document_type": str|null}'
    ))
    raw = get_llm().invoke([system, HumanMessage(content=evidence)]).content
    data = _safe_json(raw)
    if not isinstance(data, dict):
        return {"issuer": None, "period": None, "currency": None, "document_type": None}
    return {
        "issuer": data.get("issuer"),
        "period": data.get("period"),
        "currency": data.get("currency"),
        "document_type": data.get("document_type"),
    }
