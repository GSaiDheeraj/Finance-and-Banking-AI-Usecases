"""Deterministic re-reads of grouped drafts before they are stored.

Each check walks the same grouped shape the aggregation step emits and
returns findings for the bounded retry loop in `extraction.py`. No model
calls here, so every check runs on hand-made data in unit tests.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from .models import Cell, LineItem

_PURE_NUMBER_LABEL = re.compile(r"^[\d,.\s]+$|^\d+\s*[-–]\s*\d+$")
_WRAPPED_IN_BRACKETS = re.compile(r"^\(.*\)$")
_SNAPSHOT_KIND = "point"
_FLOW_KINDS = {"3M", "6M", "9M", "12M"}


class ValidationIssue(BaseModel):
    """One flaw in one draft, pinned to the cell that proves it.

    `cell_coordinates` names a single cell of the draft's grid. For whole-row
    flaws (a numeric label) any of the row's cells will do — every cell of a
    draft comes from one grid, so one cell's `page` entry is enough to send
    the finding back to its grid on retry.
    """

    check: str
    severity: Literal["warning", "error"]
    message: str
    statement_type: str
    label: str
    section_path: list[str] = Field(default_factory=list)
    cell_coordinates: dict[str, str] = Field(default_factory=dict)


class ValidationFlag(BaseModel):
    """Stored shadow of a finding — the owning row already carries identity."""

    check: str
    severity: Literal["warning", "error"]
    message: str


def _every_cell(grouped: dict[str, list[LineItem]]):
    """Yield (statement, draft, cell) across the whole grouped result."""
    for kind, drafts in grouped.items():
        for draft in drafts:
            for cell in draft.values:
                yield kind, draft, cell


def _flag(
    name: str,
    level: Literal["warning", "error"],
    wording: str,
    kind: str,
    draft: LineItem,
    cell: Cell,
) -> ValidationIssue:
    return ValidationIssue(
        check=name,
        severity=level,
        message=wording,
        statement_type=kind,
        label=draft.label,
        section_path=draft.section_path,
        cell_coordinates=dict(cell.coordinates),
    )


def _source_claims_negative(printed: str) -> bool:
    tidied = printed.strip()
    return bool(_WRAPPED_IN_BRACKETS.match(tidied)) or tidied.startswith("-")


def check_bracket_sign(grouped: dict[str, list[LineItem]]) -> list[ValidationIssue]:
    """Printed brackets or a leading minus must agree with the stored sign."""
    findings: list[ValidationIssue] = []
    for kind, draft, cell in _every_cell(grouped):
        if not cell.raw_text:
            continue
        negative_on_page = _source_claims_negative(cell.raw_text)
        if negative_on_page and cell.value >= 0:
            findings.append(
                _flag(
                    "bracket_sign",
                    "error",
                    f'"{draft.label}" has raw_text "{cell.raw_text}" (negative in the '
                    f"source) but was extracted as {cell.value} — fix the sign.",
                    kind,
                    draft,
                    cell,
                )
            )
        elif not negative_on_page and cell.value < 0:
            findings.append(
                _flag(
                    "bracket_sign",
                    "error",
                    f'"{draft.label}" was extracted as {cell.value} (negative) but its '
                    f'raw_text "{cell.raw_text}" shows no parentheses or minus sign in '
                    "the source — verify the sign.",
                    kind,
                    draft,
                    cell,
                )
            )
    return findings


def check_numeric_label(grouped: dict[str, list[LineItem]]) -> list[ValidationIssue]:
    """A label that is only digits usually means the row header was dropped.

    A bucket value or a neighbouring numeric column ends up standing in for
    the real wording, so the row can no longer be identified.
    """
    findings: list[ValidationIssue] = []
    for kind, drafts in grouped.items():
        for draft in drafts:
            if draft.values and _PURE_NUMBER_LABEL.match(draft.label.strip()):
                findings.append(
                    _flag(
                        "numeric_label",
                        "error",
                        f'Label "{draft.label}" is purely numeric — likely a misread column '
                        "(a bucket/header value stood in for the row label). Re-read the row's "
                        "actual header cell and attach it as the label.",
                        kind,
                        draft,
                        draft.values[0],
                    )
                )
    return findings


def _same_reading(left: Cell, right: Cell) -> bool:
    return (
        left.value == right.value and left.currency == right.currency and left.scale == right.scale
    )


def _different_slot(left: Cell, right: Cell) -> bool:
    return left.coordinates.get("period_end") != right.coordinates.get(
        "period_end"
    ) or left.coordinates.get("segment_id") != right.coordinates.get("segment_id")


def check_duplicate_value_across_periods(
    grouped: dict[str, list[LineItem]],
) -> list[ValidationIssue]:
    """One exact reading under two periods is almost always a copied column.

    Genuine figures essentially never repeat to the digit across periods, so a
    match means one column was read twice.
    """
    findings: list[ValidationIssue] = []
    for kind, drafts in grouped.items():
        shelved: dict[tuple, list[LineItem]] = defaultdict(list)
        for draft in drafts:
            shelved[(draft.label, tuple(draft.section_path))].append(draft)

        for (caption, _trail), siblings in shelved.items():
            readings = [(draft, cell) for draft in siblings for cell in draft.values]
            for (first_draft, first), (second_draft, second) in itertools.combinations(readings, 2):
                if not (_same_reading(first, second) and _different_slot(first, second)):
                    continue
                for draft, cell, other_day in (
                    (first_draft, first, second.coordinates.get("period_end")),
                    (second_draft, second, first.coordinates.get("period_end")),
                ):
                    findings.append(
                        _flag(
                            "duplicate_value",
                            "warning",
                            f'"{caption}" repeats the exact value {cell.value} for period '
                            f"{cell.coordinates.get('period_end')} and period {other_day} — "
                            "verify each period's own column was read, not the same cell twice.",
                            kind,
                            draft,
                            cell,
                        )
                    )
    return findings


def check_ambiguous_duplicate_label(grouped: dict[str, list[LineItem]]) -> list[ValidationIssue]:
    """Same address, different numbers means hidden metric columns.

    When several cells share label, trail, day, and segment yet disagree on
    value, the grid's columns are distinct named metrics — and the metric
    names never reached the labels, leaving readers unable to tell the rows
    apart. This is the mirror of the duplicate-value check, which catches the
    same reading reused across periods instead.
    """
    findings: list[ValidationIssue] = []
    for kind, drafts in grouped.items():
        addressed: dict[tuple, list[tuple]] = defaultdict(list)
        for draft in drafts:
            for cell in draft.values:
                address = (
                    draft.label,
                    tuple(draft.section_path),
                    cell.coordinates.get("period_end"),
                    cell.coordinates.get("segment_id"),
                )
                addressed[address].append((draft, cell))

        for (caption, _trail, _day, _segment), readings in addressed.items():
            distinct = sorted({cell.value for _, cell in readings})
            if len(readings) <= 1 or len(distinct) <= 1:
                continue
            shown = distinct[:5]
            tail = ", ..." if len(distinct) > 5 else ""
            for draft, cell in readings:
                findings.append(
                    _flag(
                        "ambiguous_duplicate_label",
                        "error",
                        f'"{caption}" appears {len(readings)} times for the same period with '
                        f"{len(distinct)} different values ({shown}{tail}) — these are "
                        "different metric columns, not repeats. Re-read the table's column headers "
                        f'and append each one to its row\'s label (e.g. "{caption} — <column header>") '
                        "so every metric becomes uniquely identifiable.",
                        kind,
                        draft,
                        cell,
                    )
                )
    return findings


def check_period_type_consistency(grouped: dict[str, list[LineItem]]) -> list[ValidationIssue]:
    """Snapshot grids take point readings; flow grids take dated spans."""
    findings: list[ValidationIssue] = []
    for kind, draft, cell in _every_cell(grouped):
        grain = cell.coordinates.get("period_type")
        if kind == "balance_sheet" and grain != _SNAPSHOT_KIND:
            findings.append(
                _flag(
                    "period_type_mismatch",
                    "error",
                    f'"{draft.label}" is on the balance sheet but has period_type '
                    f'"{grain}" — balance-sheet cells must use "point".',
                    kind,
                    draft,
                    cell,
                )
            )
        elif kind != "balance_sheet" and grain not in _FLOW_KINDS:
            findings.append(
                _flag(
                    "period_type_mismatch",
                    "error",
                    f'"{draft.label}" is a {kind} line but has period_type '
                    f'"{grain}" — expected one of {sorted(_FLOW_KINDS)}.',
                    kind,
                    draft,
                    cell,
                )
            )
    return findings


_CHECKS = (
    check_bracket_sign,
    check_numeric_label,
    check_duplicate_value_across_periods,
    check_ambiguous_duplicate_label,
    check_period_type_consistency,
)


def run_all(grouped: dict[str, list[LineItem]]) -> list[ValidationIssue]:
    """Run every check and return the combined findings."""
    findings: list[ValidationIssue] = []
    for check in _CHECKS:
        findings.extend(check(grouped))
    return findings


def attribute_to_tables(
    findings: list[ValidationIssue],
    extracted_per_table: list[dict],
) -> dict[int, list[str]]:
    """Route each finding to the origin slot of the grid it came from.

    Matching uses each cell's `page` entry. Keys are `source_index` values —
    not positions in `extracted_per_table`, because a grid whose reading call
    failed is missing from that list and would shift later positions.
    """
    sheet_to_origin = {
        str(entry["candidate"].page_number): entry["source_index"] for entry in extracted_per_table
    }
    routed: dict[int, list[str]] = defaultdict(list)
    for finding in findings:
        origin = sheet_to_origin.get(finding.cell_coordinates.get("page"))
        if origin is not None:
            routed[origin].append(finding.message)
    return dict(routed)
