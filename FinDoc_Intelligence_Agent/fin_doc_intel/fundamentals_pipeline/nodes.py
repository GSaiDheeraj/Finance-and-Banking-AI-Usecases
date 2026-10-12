"""One narrow job per staged reader, each returning its slice of progress.

A pass reads only what earlier passes stored, appends one review entry, and
hands a partial record back for merging — no pass reaches into another's
internals.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup
from langchain_core.messages import HumanMessage, SystemMessage

from .models import (
    ConsolidationMeta,
    ExtractedLineItems,
    LineItem,
    RelevanceVerdict,
    StatementClassification,
)
from .prompts import (
    CONSOLIDATION_PROMPT,
    LINE_ITEM_EXTRACTION_PROMPT,
    RELEVANCE_PROMPT,
    STATEMENT_CLASSIFIER_PROMPT,
    build_correction_addendum,
)
from .state import (
    ClassifiedTable,
    ConsolidationFiltered,
    ExtractedTable,
    PipelineState,
    RelevantTable,
)
from .structured_llm import StructuredExtractor
from .table_finder import StatementCandidate, find_tables

_logger = logging.getLogger(__name__)

_READING_BUDGET = 8192
_REVIEW_SAMPLE = 10


def _note(progress: PipelineState, **entry: Any) -> list[dict]:
    """Review trail with one more entry appended."""
    return progress["diagnostics"] + [entry]


def _sample(rows: list[dict]) -> list[dict]:
    return rows[:_REVIEW_SAMPLE]


def _grid_brief(grid: StatementCandidate) -> str:
    """Heading context the judging passes see before the markup."""
    sheet = grid.page_number if grid.page_number is not None else "(unknown)"
    return (
        f"Nearest H1: {grid.nearest_h1 or '(none)'}\n"
        f"Nearest H2: {grid.nearest_h2 or '(none)'}\n"
        f"Caption: {grid.caption or '(none)'}\n"
        f"Outer scale annotation: {grid.outer_annotation or '(none)'}\n"
        f"Page: {sheet}\n"
    )


def _grid_prompt(grid: StatementCandidate) -> str:
    return f"{_grid_brief(grid)}\nTable HTML:\n{grid.html}"


def _heading_words(header) -> list[str]:
    return [cell.get_text(strip=True) for cell in header.find_all(["th", "td"])]


def _top_headers(grid: StatementCandidate) -> list[str]:
    """Flattest useful header row: the fullest thead row, else the first row.

    Some balance sheets park period days in the first row and delta labels
    (Amount, %) in the last, so fullness beats position.
    """
    if not grid.tables:
        return []
    soup = BeautifulSoup(grid.tables[0].html, "html.parser")
    head = soup.find("thead")
    if head is not None:
        rows = head.find_all("tr")
        if rows:
            fullest = max(
                rows,
                key=lambda row: sum(
                    1 for cell in row.find_all(["th", "td"]) if cell.get_text(strip=True)
                ),
            )
            if words := _heading_words(fullest):
                return words
    opening = soup.find("tr")
    if opening is None:
        return []
    return _heading_words(opening)


def find_tables_node(progress: PipelineState) -> dict[str, Any]:
    """Lift every grid; each becomes its own candidate for judging."""
    rendered = progress["md_path"].read_text(encoding="utf-8")
    lifted = find_tables(rendered)
    candidates = [
        StatementCandidate(
            tables=[grid],
            nearest_h1=grid.nearest_h1,
            nearest_h2=grid.nearest_h2,
            caption=grid.caption,
            outer_annotation=grid.outer_annotation,
            page_number=grid.page_number,
        )
        for grid in lifted
    ]
    return {
        "raw_tables": candidates,
        "diagnostics": _note(progress, node="find_tables", tables_detected=len(candidates)),
    }


def filter_relevant(progress: PipelineState) -> dict[str, Any]:
    """Keep only grids an analyst would actually read."""
    judge = StructuredExtractor(RelevanceVerdict, model=progress["small_model"])

    kept: list[RelevantTable] = []
    passed_over: list[dict] = []
    for position, grid in enumerate(progress["raw_tables"]):
        try:
            answer = judge.extract(
                [
                    SystemMessage(content=RELEVANCE_PROMPT),
                    HumanMessage(content=_grid_prompt(grid)),
                ]
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("relevance check failed for table %s: %s", position, exc)
            passed_over.append({"index": position, "page": grid.page_number, "error": repr(exc)})
            continue

        if answer.relevant:
            kept.append({"candidate": grid, "verdict": answer})
        else:
            passed_over.append(
                {"index": position, "page": grid.page_number, "reason": answer.reason}
            )

    return {
        "relevant_tables": kept,
        "diagnostics": _note(
            progress,
            node="filter_relevant",
            kept=len(kept),
            dropped=len(passed_over),
            dropped_detail=_sample(passed_over),
        ),
    }


def classify_statement(progress: PipelineState) -> dict[str, Any]:
    """Tag each kept grid with its statement bucket."""
    labeller = StructuredExtractor(StatementClassification, model=progress["small_model"])

    labelled: list[ClassifiedTable] = []
    tallies: dict[str, int] = {}
    failures: list[dict] = []
    for position, entry in enumerate(progress["relevant_tables"]):
        grid = entry["candidate"]
        try:
            answer = labeller.extract(
                [
                    SystemMessage(content=STATEMENT_CLASSIFIER_PROMPT),
                    HumanMessage(content=_grid_prompt(grid)),
                ]
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("statement classification failed for table %s: %s", position, exc)
            failures.append({"index": position, "page": grid.page_number, "error": repr(exc)})
            continue

        labelled.append({"candidate": grid, "classification": answer})
        tallies[answer.statement_type] = tallies.get(answer.statement_type, 0) + 1

    return {
        "classified_tables": labelled,
        "diagnostics": _note(
            progress,
            node="classify_statement",
            counts=tallies,
            errors=len(failures),
            error_detail=_sample(failures),
        ),
    }


def _scope_question(grid: StatementCandidate, labelling) -> str:
    numbered = "\n".join(
        f"  [{position}] {header or '(empty)'}"
        for position, header in enumerate(_top_headers(grid))
    )
    return (
        f"{_grid_brief(grid)}\n"
        f"Statement type: {labelling.statement_type}\n"
        f"is_breakdown: {labelling.is_breakdown}\n\n"
        f"Column headers (0-based):\n{numbered}"
    )


def classify_consolidation(progress: PipelineState) -> dict[str, Any]:
    """Settle which columns to read and whether the grid covers a segment."""
    scoper = StructuredExtractor(ConsolidationMeta, model=progress["small_model"])

    settled: list[ConsolidationFiltered] = []
    skipped: list[dict] = []
    for position, entry in enumerate(progress["classified_tables"]):
        grid = entry["candidate"]
        labelling = entry["classification"]
        try:
            answer = scoper.extract(
                [
                    SystemMessage(content=CONSOLIDATION_PROMPT),
                    HumanMessage(content=_scope_question(grid, labelling)),
                ]
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("consolidation classification failed for table %s: %s", position, exc)
            skipped.append({"page": grid.page_number, "error": repr(exc)})
            continue

        if answer.skip and answer.segment_id is None:
            skipped.append(
                {"page": grid.page_number, "reason": answer.reason or "no consolidated columns"}
            )
            continue

        settled.append(
            {
                "candidate": grid,
                "statement_type": labelling.statement_type,
                "is_breakdown": labelling.is_breakdown,
                "breakdown_of": labelling.breakdown_of,
                "meta": answer,
            }
        )

    return {
        "consolidation_filtered": settled,
        "diagnostics": _note(
            progress,
            node="classify_consolidation",
            kept=len(settled),
            skipped=len(skipped),
            skipped_detail=_sample(skipped),
        ),
    }


def _reading_question(entry, scope, retry_note: str | None) -> str:
    kept = scope.consolidated_columns if scope.consolidated_columns else None
    lines = [
        _grid_brief(entry["candidate"]),
        f"Statement type: {entry['statement_type']}",
        f"Segment id (apply to coordinates if set): {scope.segment_id or '(none)'}",
        f"Is detail breakdown: {entry['is_breakdown']}",
        f"Breakdown of (if applicable): {entry['breakdown_of'] or '(n/a)'}",
        f"Column indices to KEEP: {kept if kept is not None else 'all numeric columns'}",
        "",
        "Table HTML:",
        entry["candidate"].html,
    ]
    if retry_note:
        lines.append(build_correction_addendum(retry_note))
    return "\n".join(lines)


def extract_line_items(progress: PipelineState) -> dict[str, Any]:
    """Read careful consolidated-only drafts from each surviving grid."""
    reader = StructuredExtractor(
        ExtractedLineItems,
        model=progress["large_model"],
        max_tokens=_READING_BUDGET,
    )

    retry_notes = progress.get("correction_notes", {})

    read: list[ExtractedTable] = []
    failures: list[dict] = []
    for slot, entry in enumerate(progress["consolidation_filtered"]):
        grid = entry["candidate"]
        scope = entry["meta"]
        try:
            answer = reader.extract(
                [
                    SystemMessage(content=LINE_ITEM_EXTRACTION_PROMPT),
                    HumanMessage(content=_reading_question(entry, scope, retry_notes.get(slot))),
                ]
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("line-item extraction failed for table %s: %s", slot, exc)
            failures.append({"page": grid.page_number, "error": repr(exc)})
            continue

        read.append(
            {
                "candidate": grid,
                "statement_type": entry["statement_type"],
                "is_breakdown": entry["is_breakdown"],
                "breakdown_of": entry["breakdown_of"],
                "segment_id": scope.segment_id,
                "line_items": answer.line_items,
                "source_index": slot,
            }
        )

    return {
        "extracted_per_table": read,
        "diagnostics": _note(
            progress,
            node="extract_line_items",
            extracted=len(read),
            errors=len(failures),
            error_detail=_sample(failures),
        ),
    }


def _stamp_cells(draft: LineItem, sheet: int | None) -> LineItem:
    stamped = dict(draft.coordinates)
    stamped.setdefault("source_table", f"page {sheet}")
    stamped.setdefault("page", str(sheet))
    return draft.model_copy(update={"coordinates": stamped})


def aggregate_all(progress: PipelineState) -> dict[str, Any]:
    """Gather every draft under its statement bucket, stamping each cell."""
    buckets: dict[str, list[LineItem]] = {}

    for grid in progress["extracted_per_table"]:
        sheet = grid["candidate"].page_number
        bucket = buckets.setdefault(grid["statement_type"], [])
        for draft in grid["line_items"]:
            stamped = [_stamp_cells(cell, sheet) for cell in draft.values]
            bucket.append(draft.model_copy(update={"values": stamped}))

    return {
        "aggregated": buckets,
        "diagnostics": _note(
            progress,
            node="aggregate_all",
            counts={kind: len(drafts) for kind, drafts in buckets.items()},
        ),
    }
