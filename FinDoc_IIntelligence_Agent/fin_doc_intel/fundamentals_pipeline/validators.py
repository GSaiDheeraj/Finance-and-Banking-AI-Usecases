"""Post-extraction validation for the fundamentals pipeline.

Each validator inspects the pipeline's cell-based aggregate (the same shape
`aggregate_all` produces) and returns `ValidationIssue`s that the bounded
retry loop in `extraction.py` feeds back into the next `extract_line_items`
call. Kept independent of the LLM client so these are unit-testable against
synthetic data with no network access.
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from typing import Dict, List, Literal

from pydantic import BaseModel, Field

from .models import Cell, LineItem

_NUMERIC_LABEL_RE = re.compile(r"^[\d,.\s]+$|^\d+\s*[-–]\s*\d+$")
_PARENTHESIZED_RE = re.compile(r"^\(.*\)$")
_POINT_PERIOD_TYPE = "point"
_FLOW_PERIOD_TYPES = {"3M", "6M", "9M", "12M"}


class ValidationIssue(BaseModel):
    """One thing wrong with one line item, found after extraction.

    `cell_coordinates` pins the issue to the specific cell it's about (or, for
    a whole-row problem like a numeric label, to any one of that row's cells —
    every cell in a LineItem comes from the same source table, so one cell's
    `page` coordinate is enough for `attribute_to_tables` to route the issue
    back to that table).
    """
    check: str
    severity: Literal["warning", "error"]
    message: str
    statement_type: str
    label: str
    section_path: List[str] = Field(default_factory=list)
    cell_coordinates: Dict[str, str] = Field(default_factory=dict)


class ValidationFlag(BaseModel):
    """Slim, persisted form of a ValidationIssue — no join keys, since the DB
    row it attaches to already carries label/statement/period as its own
    columns."""
    check: str
    severity: Literal["warning", "error"]
    message: str


def _issue(
    check: str, severity: Literal["warning", "error"], message: str,
    statement_type: str, item: LineItem, cell: Cell,
) -> ValidationIssue:
    return ValidationIssue(
        check=check, severity=severity, message=message, statement_type=statement_type,
        label=item.label, section_path=item.section_path, cell_coordinates=dict(cell.coordinates),
    )


def check_bracket_sign(aggregated: Dict[str, List[LineItem]]) -> List[ValidationIssue]:
    """A cell whose source text was parenthesized must be negative, and vice versa."""
    issues: List[ValidationIssue] = []
    for statement_type, items in aggregated.items():
        for item in items:
            for cell in item.values:
                if not cell.raw_text:
                    continue
                raw = cell.raw_text.strip()
                should_be_negative = bool(_PARENTHESIZED_RE.match(raw)) or raw.startswith("-")
                if should_be_negative and cell.value >= 0:
                    issues.append(_issue(
                        "bracket_sign", "error",
                        f'"{item.label}" has raw_text "{cell.raw_text}" (negative in the '
                        f"source) but was extracted as {cell.value} — fix the sign.",
                        statement_type, item, cell,
                    ))
                elif not should_be_negative and cell.value < 0:
                    issues.append(_issue(
                        "bracket_sign", "error",
                        f'"{item.label}" was extracted as {cell.value} (negative) but its '
                        f'raw_text "{cell.raw_text}" shows no parentheses or minus sign in '
                        "the source — verify the sign.",
                        statement_type, item, cell,
                    ))
    return issues


def check_numeric_label(aggregated: Dict[str, List[LineItem]]) -> List[ValidationIssue]:
    """A line-item label that is itself just a number is almost always a
    misread column — the real label (a row header) got dropped and a bucket
    or another numeric column ended up standing in for it."""
    issues: List[ValidationIssue] = []
    for statement_type, items in aggregated.items():
        for item in items:
            if item.values and _NUMERIC_LABEL_RE.match(item.label.strip()):
                issues.append(_issue(
                    "numeric_label", "error",
                    f'Label "{item.label}" is purely numeric — likely a misread column '
                    "(a bucket/header value stood in for the row label). Re-read the row's "
                    "actual header cell and attach it as the label.",
                    statement_type, item, item.values[0],
                ))
    return issues


def check_duplicate_value_across_periods(
    aggregated: Dict[str, List[LineItem]],
) -> List[ValidationIssue]:
    """Two cells of the same line item with different periods/segments but an
    identical value+currency+scale are a likely copy-paste from the wrong
    column — real figures essentially never repeat exactly across periods."""
    issues: List[ValidationIssue] = []
    for statement_type, items in aggregated.items():
        by_line: Dict[tuple, List[LineItem]] = defaultdict(list)
        for item in items:
            by_line[(item.label, tuple(item.section_path))].append(item)

        for (label, _section_path), grouped_items in by_line.items():
            cells = [(item, cell) for item in grouped_items for cell in item.values]
            for (item_a, cell_a), (item_b, cell_b) in itertools.combinations(cells, 2):
                same_value = (
                    cell_a.value == cell_b.value
                    and cell_a.currency == cell_b.currency
                    and cell_a.scale == cell_b.scale
                )
                different_period = (
                    cell_a.coordinates.get("period_end") != cell_b.coordinates.get("period_end")
                    or cell_a.coordinates.get("segment_id") != cell_b.coordinates.get("segment_id")
                )
                if not (same_value and different_period):
                    continue
                for item, cell, other_period in (
                    (item_a, cell_a, cell_b.coordinates.get("period_end")),
                    (item_b, cell_b, cell_a.coordinates.get("period_end")),
                ):
                    issues.append(_issue(
                        "duplicate_value", "warning",
                        f'"{label}" repeats the exact value {cell.value} for period '
                        f"{cell.coordinates.get('period_end')} and period {other_period} — "
                        "verify each period's own column was read, not the same cell twice.",
                        statement_type, item, cell,
                    ))
    return issues


def check_ambiguous_duplicate_label(aggregated: Dict[str, List[LineItem]]) -> List[ValidationIssue]:
    """Cells sharing the same (label, section_path, period_end, segment_id) but
    carrying genuinely different values mean the table's columns are distinct
    named metrics, not periods — and the metric name never made it into the
    label, so the row's identity is now ambiguous (which of the N values is
    "the" value for this label?). The mirror image of
    check_duplicate_value_across_periods, which flags the opposite failure:
    the SAME value reused under different periods."""
    issues: List[ValidationIssue] = []
    for statement_type, items in aggregated.items():
        by_identity: Dict[tuple, List[tuple]] = defaultdict(list)
        for item in items:
            for cell in item.values:
                identity = (
                    item.label, tuple(item.section_path),
                    cell.coordinates.get("period_end"), cell.coordinates.get("segment_id"),
                )
                by_identity[identity].append((item, cell))

        for (label, _section_path, _period_end, _segment_id), cells in by_identity.items():
            distinct_values = sorted({cell.value for _, cell in cells})
            if len(cells) <= 1 or len(distinct_values) <= 1:
                continue
            preview = distinct_values[:5]
            suffix = ", ..." if len(distinct_values) > 5 else ""
            for item, cell in cells:
                issues.append(_issue(
                    "ambiguous_duplicate_label", "error",
                    f'"{label}" appears {len(cells)} times for the same period with '
                    f"{len(distinct_values)} different values ({preview}{suffix}) — these are "
                    "different metric columns, not repeats. Re-read the table's column headers "
                    f'and append each one to its row\'s label (e.g. "{label} — <column header>") '
                    "so every metric becomes uniquely identifiable.",
                    statement_type, item, cell,
                ))
    return issues


def check_period_type_consistency(aggregated: Dict[str, List[LineItem]]) -> List[ValidationIssue]:
    """balance_sheet cells must be a point-in-time fact; the flow statements
    must carry a period length — catches the extractor missing its own
    coordinate contract (see prompts.py's LINE_ITEM_EXTRACTION_PROMPT)."""
    issues: List[ValidationIssue] = []
    for statement_type, items in aggregated.items():
        for item in items:
            for cell in item.values:
                period_type = cell.coordinates.get("period_type")
                if statement_type == "balance_sheet" and period_type != _POINT_PERIOD_TYPE:
                    issues.append(_issue(
                        "period_type_mismatch", "error",
                        f'"{item.label}" is on the balance sheet but has period_type '
                        f'"{period_type}" — balance-sheet cells must use "point".',
                        statement_type, item, cell,
                    ))
                elif statement_type != "balance_sheet" and period_type not in _FLOW_PERIOD_TYPES:
                    issues.append(_issue(
                        "period_type_mismatch", "error",
                        f'"{item.label}" is a {statement_type} line but has period_type '
                        f'"{period_type}" — expected one of {sorted(_FLOW_PERIOD_TYPES)}.',
                        statement_type, item, cell,
                    ))
    return issues


_VALIDATORS = (
    check_bracket_sign,
    check_numeric_label,
    check_duplicate_value_across_periods,
    check_ambiguous_duplicate_label,
    check_period_type_consistency,
)


def run_all(aggregated: Dict[str, List[LineItem]]) -> List[ValidationIssue]:
    """Run every validator and return the combined issue list."""
    issues: List[ValidationIssue] = []
    for validator in _VALIDATORS:
        issues.extend(validator(aggregated))
    return issues


def attribute_to_tables(
    issues: List[ValidationIssue], extracted_per_table: List[dict],
) -> Dict[int, List[str]]:
    """Map each issue back to the `consolidation_filtered` index of the table
    it came from (via each cell's `page` coordinate), so `extract_line_items`
    can hand each table only the issues about its own rows on retry.

    Keyed by `source_index` (each `ExtractedTable`'s origin in
    `consolidation_filtered`) rather than `extracted_per_table`'s own list
    position — a table whose extraction call raised is absent from that list,
    which would otherwise shift every later table's positional index.
    """
    page_to_source_index = {
        str(table["candidate"].page_number): table["source_index"]
        for table in extracted_per_table
    }
    notes: Dict[int, List[str]] = defaultdict(list)
    for issue in issues:
        source_index = page_to_source_index.get(issue.cell_coordinates.get("page"))
        if source_index is not None:
            notes[source_index].append(issue.message)
    return dict(notes)
