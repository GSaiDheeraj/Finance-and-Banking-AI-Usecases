"""Turn one filing into flat, auditable fact rows.

Tables are located and read by the staged table pipeline; this module runs the
cheap organising passes once, then repeats only the reading pass inside a
small check-and-retry loop before flattening each cell into its own row.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_config, get_llm
from .fundamentals_pipeline import nodes, validators
from .fundamentals_pipeline.models import Cell
from .fundamentals_pipeline.models import LineItem as PipelineLineItem
from .fundamentals_pipeline.state import PipelineState
from .fundamentals_pipeline.validators import ValidationFlag, ValidationIssue
from .input_pipeline.processor import InputProcessor
from .pdf_index import PageIndex
from .schemas import LineItem, StatementType
from .storage import DocumentStorage

_MONTHS_BY_TERM = {"3M": 3, "6M": 6, "9M": 9, "12M": 12}
_COVER_LOOKUP_PHRASE = "company name annual report financial year reporting currency document type"

# Reads repeat at most this often; the final pass is kept with its findings.
MAX_EXTRACTION_ITERATIONS = 3

# One reader per process: a heavy vision model loads on first scanned page and
# is then reused for every later filing.
_shared_reader: InputProcessor | None = None


def _reader() -> InputProcessor:
    global _shared_reader
    if _shared_reader is None:
        _shared_reader = InputProcessor()
    return _shared_reader


def reset_input_processor() -> None:
    """Forget the cached reader (intended for tests)."""
    global _shared_reader
    _shared_reader = None


def _unwrap_fences(raw: str) -> str:
    """Drop markdown code fences when the model decorates its JSON."""
    trimmed = raw.strip()
    if not trimmed.startswith("```"):
        return trimmed
    body = trimmed.split("\n")[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def _parse_json(raw: str) -> Any | None:
    try:
        return json.loads(_unwrap_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return None


def _cover_evidence(lookup: PageIndex, phrase: str, depth: int) -> str:
    sheets = lookup.search(phrase, k=depth)
    blocks = [f"--- PAGE {sheet.page_number} ---\n{sheet.text}" for sheet in sheets]
    return "\n\n".join(blocks)


@dataclass
class ExtractionRunResult:
    """Handoff from the readers to the persistence step."""

    line_items: list[LineItem] = field(default_factory=list)
    llm_call_count: int = 0
    diagnostics: list[dict] = field(default_factory=list)
    iterations_run: int = 0


def _finding_key(kind: str, caption: str, trail: list[str], position: dict[str, str]) -> tuple:
    return (kind, caption, tuple(trail), tuple(sorted(position.items())))


def _unit_for(magnitude: str | None, money: str | None) -> str | None:
    """Translate the pipeline's magnitude word into the stored unit column."""
    del money  # Money has its own column and never decides the unit.
    word = (magnitude or "").lower()
    if word in ("thousands", "millions", "billions", "percentage"):
        return word
    return "absolute"


def _as_date(iso_day: str | None) -> date | None:
    if not iso_day:
        return None
    try:
        return date.fromisoformat(iso_day)
    except ValueError:
        return None


def _as_page(raw_page: str | None) -> int | None:
    if raw_page and raw_page.isdigit():
        return int(raw_page)
    return None


def _flatten_cell(
    kind: StatementType,
    kind_name: str,
    drafted: PipelineLineItem,
    cell: Cell,
    flags_by_finding: dict[tuple, list[ValidationFlag]],
) -> LineItem:
    lookup = _finding_key(kind_name, drafted.label, drafted.section_path, cell.coordinates)
    finale = cell.coordinates.get("period_end")
    return LineItem(
        statement=kind,
        label=drafted.label,
        section_path=drafted.section_path,
        value=cell.value,
        unit=_unit_for(cell.scale, cell.currency),
        currency=cell.currency,
        period=finale,
        period_end_date=_as_date(finale),
        period_length_months=_MONTHS_BY_TERM.get(cell.coordinates.get("period_type")),
        scale=cell.scale,
        consolidated=cell.coordinates.get("consolidation") == "consolidated",
        page=_as_page(cell.coordinates.get("page")),
        source_snippet=cell.coordinates.get("source_table"),
        validation_errors=flags_by_finding.get(lookup, []),
    )


def _flatten_grouped(
    grouped: dict[str, list[PipelineLineItem]],
    findings: list[ValidationIssue],
) -> list[LineItem]:
    """Expand one-cell-per-period drafts into one stored row per cell.

    Findings line up by (statement, label, trail, full coordinates) because the
    loop validates and stops before touching the drafts again.
    """
    flags_by_finding: dict[tuple, list[ValidationFlag]] = defaultdict(list)
    for finding in findings:
        key = _finding_key(
            finding.statement_type, finding.label, finding.section_path, finding.cell_coordinates
        )
        flags_by_finding[key].append(
            ValidationFlag(check=finding.check, severity=finding.severity, message=finding.message)
        )

    flat: list[LineItem] = []
    for kind_name, drafts in grouped.items():
        try:
            kind = StatementType(kind_name)
        except ValueError:
            kind = StatementType.OTHER
        for drafted in drafts:
            for cell in drafted.values:
                flat.append(_flatten_cell(kind, kind_name, drafted, cell, flags_by_finding))
    return flat


def _dump_grouped(grouped: dict[str, list[PipelineLineItem]]) -> dict[str, list]:
    return {kind: [draft.model_dump() for draft in drafts] for kind, drafts in grouped.items()}


def _fresh_state(rendered: Path) -> PipelineState:
    settings = get_config()
    return {
        "md_path": rendered,
        "small_model": settings.small_llm_model_name,
        "large_model": settings.large_llm_model_name,
        "raw_tables": [],
        "relevant_tables": [],
        "classified_tables": [],
        "consolidation_filtered": [],
        "extracted_per_table": [],
        "aggregated": {},
        "diagnostics": [],
        "correction_notes": {},
    }


_ORGANISING_PASSES = (
    nodes.find_tables_node,
    nodes.filter_relevant,
    nodes.classify_statement,
    nodes.classify_consolidation,
)


def _organise_once(progress: PipelineState) -> int:
    for organising_pass in _ORGANISING_PASSES:
        progress.update(organising_pass(progress))
    return (
        len(progress["raw_tables"])
        + len(progress["relevant_tables"])
        + len(progress["classified_tables"])
    )


def _store_pass(
    keeper: DocumentStorage,
    document_id: str,
    round_no: int,
    grouped: dict[str, list[PipelineLineItem]],
    findings: list[ValidationIssue],
) -> None:
    keeper.save_json(
        document_id,
        f"iter {round_no} output/extraction.json",
        _dump_grouped(grouped),
    )
    keeper.save_json(
        document_id,
        f"iter {round_no} output/validation_report.json",
        [finding.model_dump() for finding in findings],
    )


def _read_until_clean(progress: PipelineState, document_id: str, keeper: DocumentStorage) -> tuple:
    findings: list[ValidationIssue] = []
    reading_calls = 0
    rounds_done = 0
    for round_no in range(1, MAX_EXTRACTION_ITERATIONS + 1):
        rounds_done = round_no
        progress.update(nodes.extract_line_items(progress))
        progress.update(nodes.aggregate_all(progress))
        reading_calls += len(progress["consolidation_filtered"])

        findings = validators.run_all(progress["aggregated"])
        _store_pass(keeper, document_id, round_no, progress["aggregated"], findings)

        if not findings or round_no == MAX_EXTRACTION_ITERATIONS:
            break
        progress["correction_notes"] = validators.attribute_to_tables(
            findings, progress["extracted_per_table"]
        )
    return findings, reading_calls, rounds_done


def extract_document_content(
    pdf_path: str,
    document_id: str,
    storage: DocumentStorage,
) -> ExtractionRunResult:
    """Render the filing, organise its tables once, then read them with retries.

    Organising passes never depend on reading quality, so repeating them per
    retry would only spend model calls. Each retry note stays scoped to the
    tables that actually failed.
    """
    if not pdf_path:
        raise ValueError("pdf_path must be a non-empty path.")
    if not document_id:
        raise ValueError("document_id must be a non-empty string.")
    origin = Path(pdf_path)
    rendered = _reader().process(origin, origin.parent)
    if rendered.markdown_path is None:
        return ExtractionRunResult(
            diagnostics=[
                {
                    "node": "input_pipeline",
                    "error": f"no markdown produced (document_class={rendered.document_class})",
                }
            ]
        )

    progress = _fresh_state(rendered.markdown_path)
    organising_calls = _organise_once(progress)
    findings, reading_calls, rounds_done = _read_until_clean(progress, document_id, storage)

    return ExtractionRunResult(
        line_items=_flatten_grouped(progress["aggregated"], findings),
        llm_call_count=organising_calls + reading_calls,
        diagnostics=progress["diagnostics"],
        iterations_run=rounds_done,
    )


_COVER_WRITER_BRIEF = (
    "Identify the reporting entity metadata from the text. "
    "`document_type` is a short free-text label for what kind of filing this is "
    '(e.g. "Annual Report", "10-K", "20-F", "Quarterly Report", "Prospectus") '
    "— use the label the document itself uses, or your best judgment if it doesn't say. "
    "Output ONLY a JSON object: "
    '{"issuer": str|null, "period": str|null, "currency": str|null, "document_type": str|null}'
)

_BLANK_COVER = {"issuer": None, "period": None, "currency": None, "document_type": None}


def detect_issuer_metadata(lookup: PageIndex) -> dict[str, str | None]:
    """Read issuer, period, currency, and filing kind from the opening pages."""
    evidence = _cover_evidence(lookup, _COVER_LOOKUP_PHRASE, depth=3)
    brief = SystemMessage(content=_COVER_WRITER_BRIEF)
    reply = get_llm().invoke([brief, HumanMessage(content=evidence)]).content
    decoded = _parse_json(reply)
    if not isinstance(decoded, dict):
        return dict(_BLANK_COVER)
    return {
        "issuer": decoded.get("issuer"),
        "period": decoded.get("period"),
        "currency": decoded.get("currency"),
        "document_type": decoded.get("document_type"),
    }
