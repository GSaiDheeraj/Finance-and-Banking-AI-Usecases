"""Tidy and audit grids that arrive from vision reads.

Each HTML grid passes three repairs in order:

    1. Joined-value split — one box holding "$ 2,489 | $ 894" becomes two boxes.
    2. Head straightening — in stacked headers whose leaf row matches the top
       row's spans, stray row-spans on side labels are lifted so later steps
       see plain gaps instead of ambiguous overlaps.
    3. Gap plugging — a W3C-style occupancy map is walked and every hole is
       filled with an empty box of the row's own kind.

Pipe-style Markdown grids take a separate, lighter alignment path.

Public surface (stable):
    fix_ocr_output(text) -> str
    validate_ocr_output(text) -> list[ValidationIssue]
    fix_markdown_tables = fix_ocr_output          (alias)
    validate_markdown_tables = validate_ocr_output (alias)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Shared patterns
# --------------------------------------------------------------------------- #

_GRID_RE = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
_ROW_RE = re.compile(r"(<tr[^>]*>)(.*?)(</tr>)", re.DOTALL | re.IGNORECASE)
_HEAD_CELL_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.IGNORECASE)
_BODY_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_ANY_CELL_RE = re.compile(r"<(th|td)([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_SPAN_X_RE = re.compile(r'colspan=["\']?(\d+)["\']?', re.IGNORECASE)
_SPAN_Y_RE = re.compile(r'rowspan=["\']?(\d+)["\']?', re.IGNORECASE)
_HEAD_BLOCK_RE = re.compile(r"(<thead[^>]*>)(.*?)(</thead>)", re.DOTALL | re.IGNORECASE)
_MARKUP_RE = re.compile(r"<[^>]+>")
_BOLD_RE = re.compile(r"<(?:strong|b)>(.*?)</(?:strong|b)>", re.IGNORECASE)
_ITALIC_RE = re.compile(r"<(?:em|i)>(.*?)</(?:em|i)>", re.IGNORECASE)

# Two money amounts sharing one box around a pipe.
_JOINED_AMOUNTS_RE = re.compile(
    r"(\$?\s*\(?-?[\d,]+(?:\.\d+)?\)?|[-–])"
    r"\s*\|\s*"
    r"(\$?\s*\(?-?[\d,]+(?:\.\d+)?\)?|[-–])"
)

# --------------------------------------------------------------------------- #
# Box model and span marker
# --------------------------------------------------------------------------- #


@dataclass
class _GridCell:
    tag: str  # "th" or "td"
    attrs: str  # attribute text from the opening tag
    content: str  # inner markup
    colspan: int = 1
    rowspan: int = 1


# Marks map positions covered by a neighbour's span.
_SPANNED: Any = object()


# --------------------------------------------------------------------------- #
# Text-level helpers
# --------------------------------------------------------------------------- #


def _plain_text(markup: str) -> str:
    wording = _BOLD_RE.sub(r"**\1**", markup)
    wording = _ITALIC_RE.sub(r"*\1*", wording)
    return _MARKUP_RE.sub("", wording).strip()


def _expand_joined(box_text: str) -> list[str]:
    """Split `"$ 2,489 | $ 894"` into `["$ 2,489", "$ 894"]`."""
    pieces = [box_text]
    changed = True
    while changed:
        changed = False
        grown: list[str] = []
        for piece in pieces:
            hit = _JOINED_AMOUNTS_RE.search(piece)
            if hit:
                grown.extend(
                    [
                        (piece[: hit.start()] + hit.group(1)).strip(),
                        (hit.group(2) + piece[hit.end() :]).strip(),
                    ]
                )
                changed = True
            else:
                grown.append(piece)
        pieces = grown
    return pieces


def _span_x(attrs: str) -> int:
    hit = _SPAN_X_RE.search(attrs)
    return int(hit.group(1)) if hit else 1


def _span_y(attrs: str) -> int:
    hit = _SPAN_Y_RE.search(attrs)
    return int(hit.group(1)) if hit else 1


# --------------------------------------------------------------------------- #
# Measuring helpers
# --------------------------------------------------------------------------- #


_MD_FILLER = "---"


def _blank_boxes(tag: str, count: int) -> str:
    return "".join([f"<{tag}></{tag}>"] * count)


def _row_width(row_inner: str) -> int:
    """Column count of a row, expanding every span."""
    total = 0
    for opener in re.finditer(r"<(?:th|td)([^>]*)>", row_inner, re.IGNORECASE):
        total += _span_x(opener.group(1))
    return total


def _top_row_spans(row_inner: str) -> int:
    """Sum of explicit spans in top-row header boxes."""
    total = 0
    for opener in re.finditer(r"<th([^>]*)>", row_inner, re.IGNORECASE):
        hit = _SPAN_X_RE.search(opener.group(1))
        if hit:
            total += int(hit.group(1))
    return total


# --------------------------------------------------------------------------- #
# Repair 2 — stacked-head straightening
# --------------------------------------------------------------------------- #


def _straighten_head(grid_markup: str) -> str:
    """Lift stray row-spans from side labels in stacked headers.

    Runs only when the leaf header row's width equals the top row's spans.
    Afterwards those slots read as plain gaps, so gap-plugging fills them
    with empty header boxes on every sub-row. The corner label keeps its span.
    """

    def mend_head(match: re.Match) -> str:
        head_open, head_inner, head_close = match.group(1), match.group(2), match.group(3)
        rows = list(_ROW_RE.finditer(head_inner))
        if len(rows) < 2:
            return match.group(0)

        top_inner = rows[0].group(2)
        leaf_inner = rows[-1].group(2)

        if _top_row_spans(top_inner) == 0 or _row_width(leaf_inner) != _top_row_spans(top_inner):
            return match.group(0)

        labels = list(
            re.finditer(r"(<th([^>]*)>)(.*?)(</th>)", top_inner, re.DOTALL | re.IGNORECASE)
        )
        if len(labels) < 2:
            return match.group(0)

        side_labels = sum(
            1
            for position, hit in enumerate(labels)
            if position > 0
            and not _SPAN_X_RE.search(hit.group(2))
            and _plain_text(hit.group(3)).strip()
        )
        if side_labels == 0:
            return match.group(0)

        mended_top = top_inner
        for reverse, hit in enumerate(reversed(labels)):
            forward = len(labels) - 1 - reverse
            if forward == 0:
                continue
            attrs = hit.group(2)
            if _SPAN_X_RE.search(attrs):
                continue
            if not _plain_text(hit.group(3)).strip():
                mended_top = mended_top[: hit.start()] + mended_top[hit.end() :]
                continue
            if not _SPAN_Y_RE.search(attrs):
                continue
            cleaned = _SPAN_Y_RE.sub("", attrs).strip()
            fresh_open = f"<th{(' ' + cleaned) if cleaned else ''}>"
            mended_top = (
                mended_top[: hit.start()]
                + fresh_open
                + hit.group(3)
                + hit.group(4)
                + mended_top[hit.end() :]
            )

        fillers = _blank_boxes("th", side_labels)

        mended_inner = head_inner
        for row_hit in reversed(rows[1:]):
            padded = fillers + row_hit.group(2)
            fresh_row = row_hit.group(1) + padded + row_hit.group(3)
            mended_inner = (
                mended_inner[: row_hit.start()] + fresh_row + mended_inner[row_hit.end() :]
            )

        first = rows[0]
        mended_inner = mended_inner[: first.start(2)] + mended_top + mended_inner[first.end(2) :]
        return f"{head_open}{mended_inner}{head_close}"

    return _HEAD_BLOCK_RE.sub(mend_head, grid_markup)


# --------------------------------------------------------------------------- #
# Percent-column realignment (evidence-led fix)
# --------------------------------------------------------------------------- #

_PERCENT_VALUE_RE = re.compile(r"-?\d[\d.]*%")


def _percent_column_in_body(grid_markup: str) -> int:
    """0-based body column holding the most percent readings."""
    tallies: dict[int, int] = {}
    for row_hit in _ROW_RE.finditer(grid_markup):
        inner = row_hit.group(2)
        if _HEAD_CELL_RE.search(inner):
            continue
        for position, box_hit in enumerate(_BODY_CELL_RE.finditer(inner)):
            if _PERCENT_VALUE_RE.search(_plain_text(box_hit.group(1))):
                tallies[position] = tallies.get(position, 0) + 1
    return max(tallies, key=tallies.get) if tallies else -1


def _percent_column_in_head(grid_markup: str) -> int:
    """Full-grid column of the `%` sub-label, honouring spanned corners."""
    for block in _HEAD_BLOCK_RE.finditer(grid_markup):
        rows = list(_ROW_RE.finditer(block.group(2)))
        if len(rows) < 2:
            continue

        top_inner = rows[0].group(2)
        spanned_corners = 0
        for opener in re.finditer(r"<th([^>]*)>", top_inner, re.IGNORECASE):
            attrs = opener.group(1)
            if _SPAN_Y_RE.search(attrs) and not _SPAN_X_RE.search(attrs):
                spanned_corners += 1
            else:
                break

        for position, box_hit in enumerate(_HEAD_CELL_RE.finditer(rows[-1].group(2))):
            if _plain_text(box_hit.group(1)).strip() == "%":
                return spanned_corners + position

    return -1


def _line_up_percent_header(grid_markup: str) -> str:
    """Pad sub-header rows so `%` sits above the percent readings."""
    head_slot = _percent_column_in_head(grid_markup)
    body_slot = _percent_column_in_body(grid_markup)

    if head_slot < 0 or body_slot < 0:
        return grid_markup
    shift = body_slot - head_slot
    if shift <= 0:
        return grid_markup

    padding = _blank_boxes("th", shift)

    def pad_head(match: re.Match) -> str:
        head_open, head_inner, head_close = match.group(1), match.group(2), match.group(3)
        rows = list(_ROW_RE.finditer(head_inner))
        if len(rows) < 2:
            return match.group(0)

        mended = head_inner
        for row_hit in reversed(rows[1:]):
            inner = row_hit.group(2)
            filled = [
                hit
                for hit in re.finditer(r"<th[^>]*>.*?</th>", inner, re.DOTALL | re.IGNORECASE)
                if _plain_text(hit.group(0)).strip()
            ]
            if not filled:
                continue
            at = filled[0].start()
            padded = inner[:at] + padding + inner[at:]
            fresh = row_hit.group(1) + padded + row_hit.group(3)
            mended = mended[: row_hit.start()] + fresh + mended[row_hit.end() :]
        return f"{head_open}{mended}{head_close}"

    return _HEAD_BLOCK_RE.sub(pad_head, grid_markup)


# --------------------------------------------------------------------------- #
# Repair 3 — occupancy map and gap plugging
# --------------------------------------------------------------------------- #


def _read_rows(markup: str) -> tuple[list[dict], list[re.Match]]:
    """Parse every row, expanding joined body values up front.

    Expanding here means the map sees true box counts from the start.
    """
    hits = list(_ROW_RE.finditer(markup))
    rows: list[dict] = []

    for row_hit in hits:
        inner = row_hit.group(2)
        boxes: list[_GridCell] = []

        for box_hit in _ANY_CELL_RE.finditer(inner):
            tag = box_hit.group(1).lower()
            attrs = box_hit.group(2)
            content = box_hit.group(3)
            wide = _span_x(attrs)
            tall = _span_y(attrs)

            if tag == "td":
                wording = _plain_text(content)
                pieces = _expand_joined(wording)
                if len(pieces) > 1:
                    boxes.append(_GridCell(tag, attrs, pieces[0], wide, tall))
                    for piece in pieces[1:]:
                        boxes.append(_GridCell(tag, "", piece, 1, tall))
                else:
                    boxes.append(_GridCell(tag, attrs, wording, wide, tall))
            else:
                boxes.append(_GridCell(tag, attrs, content, wide, tall))

        kind = "th" if any(box.tag == "th" for box in boxes) else "td"
        rows.append(
            {
                "tr_open": row_hit.group(1),
                "tr_close": row_hit.group(3),
                "cells": boxes,
                "row_tag": kind,
            }
        )

    return rows, hits


def _lay_map(rows: list[dict]) -> list[list]:
    """Walk the HTML grid-forming order into a 2-D map.

    Slots hold a `_GridCell` (span origin), `_SPANNED` (covered by a span),
    or None (a hole to plug).
    """
    count = len(rows)
    chart: list[list] = [[] for _ in range(count)]

    def widen(row: int, width: int) -> None:
        while len(chart[row]) < width:
            chart[row].append(None)

    for ri, row in enumerate(rows):
        col = 0
        for box in row["cells"]:
            widen(ri, col + 1)
            while col < len(chart[ri]) and chart[ri][col] is _SPANNED:
                col += 1
                widen(ri, col + 1)

            for r in range(ri, min(ri + box.rowspan, count)):
                widen(r, col + box.colspan)
                for c in range(col, col + box.colspan):
                    chart[r][c] = box if (r == ri and c == col) else _SPANNED

            col += box.colspan

    return chart


def _map_width(chart: list[list]) -> int:
    return max((len(lane) for lane in chart), default=0)


def _plug_holes(chart: list[list], rows: list[dict], width: int) -> None:
    """Fill every hole in place with an empty box of the row's kind."""
    for ri, lane in enumerate(chart):
        while len(lane) < width:
            lane.append(None)
        kind = rows[ri]["row_tag"]
        for ci in range(width):
            if lane[ci] is None:
                lane[ci] = _GridCell(tag=kind, attrs="", content="")


def _rebuild_markup(
    grid_markup: str, chart: list[list], rows: list[dict], hits: list[re.Match]
) -> str:
    """Swap every row block for its repaired content, last row first."""
    out = grid_markup
    for ri in range(len(rows) - 1, -1, -1):
        rendered: list[str] = []
        for slot in chart[ri]:
            if slot is _SPANNED:
                continue
            if isinstance(slot, _GridCell):
                rendered.append(f"\n      <{slot.tag}{slot.attrs}>{slot.content}</{slot.tag}>")
        inner = "".join(rendered) + "\n    " if rendered else ""
        fresh = f"{rows[ri]['tr_open']}{inner}{rows[ri]['tr_close']}"
        hit = hits[ri]
        out = out[: hit.start()] + fresh + out[hit.end() :]
    return out


def _repair_one_grid(grid_markup: str) -> str:
    """Run all three repairs over a single grid block."""
    grid_markup = _straighten_head(grid_markup)
    grid_markup = _line_up_percent_header(grid_markup)

    rows, hits = _read_rows(grid_markup)
    if not rows:
        return grid_markup

    chart = _lay_map(rows)
    width = _map_width(chart)
    if width == 0:
        return grid_markup

    _plug_holes(chart, rows, width)
    return _rebuild_markup(grid_markup, chart, rows, hits)


def _repair_all_grids(text: str) -> str:
    return _GRID_RE.sub(lambda hit: _repair_one_grid(hit.group(0)), text)


# --------------------------------------------------------------------------- #
# Pipe-style Markdown grid path (for model pages emitted as Markdown)
# --------------------------------------------------------------------------- #

_MD_LINE_RE = re.compile(r"^\|.*\|$")
_MD_RULE_RE = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")


def _looks_like_md_line(line: str) -> bool:
    return bool(_MD_LINE_RE.match(line.strip()))


def _looks_like_rule(line: str) -> bool:
    return bool(_MD_RULE_RE.match(line.strip()))


def _split_md_line(line: str) -> list[str]:
    pieces = line.strip().split("|")
    if pieces and not pieces[0].strip():
        pieces = pieces[1:]
    if pieces and not pieces[-1].strip():
        pieces = pieces[:-1]
    return [piece.strip() for piece in pieces]


def _fit_md_cells(boxes: list[str], width: int) -> list[str]:
    grown: list[str] = []
    for box in boxes:
        grown.extend(_expand_joined(box))
    while grown and not grown[-1]:
        grown.pop()
    if len(grown) < width:
        grown += [""] * (width - len(grown))
    elif len(grown) > width:
        grown = grown[:width]
    return grown


def _mend_md_block(lines: list[str]) -> list[str]:
    if not lines:
        return lines
    width = len(_split_md_line(lines[0]))
    if width == 0:
        return lines
    mended = [lines[0]]
    for line in lines[1:]:
        if _looks_like_rule(line):
            mended.append("| " + " | ".join([_MD_FILLER] * width) + " |")
            continue
        boxes = _fit_md_cells(_split_md_line(line), width)
        mended.append("| " + " | ".join(boxes) + " |")
    return mended


def _align_md_grids(text: str) -> str:
    rows = text.split("\n")
    out: list[str] = []
    cursor = 0
    while cursor < len(rows):
        if _looks_like_md_line(rows[cursor]):
            block: list[str] = []
            while cursor < len(rows) and _looks_like_md_line(rows[cursor]):
                block.append(rows[cursor])
                cursor += 1
            out.extend(_mend_md_block(block))
        else:
            out.append(rows[cursor])
            cursor += 1
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


@dataclass
class ValidationIssue:
    location: str
    message: str


def _audit_html_grids(text: str) -> list[ValidationIssue]:
    findings: list[ValidationIssue] = []
    for grid_no, grid_hit in enumerate(_GRID_RE.finditer(text), start=1):
        grid_markup = grid_hit.group(0)

        for block in _HEAD_BLOCK_RE.finditer(grid_markup):
            rows = list(_ROW_RE.finditer(block.group(2)))
            if len(rows) < 2:
                continue
            leaf_width = _row_width(rows[-1].group(2))
            top_spans = _top_row_spans(rows[0].group(2))
            if top_spans > 0 and leaf_width != top_spans:
                findings.append(
                    ValidationIssue(
                        location=f"table {grid_no}, thead",
                        message=f"Multi-row header mismatch: leaf-row cells={leaf_width}, "
                        f"sum of row-1 colspans={top_spans}",
                    )
                )

        rows, _ = _read_rows(grid_markup)
        if not rows:
            continue
        chart = _lay_map(rows)
        width = _map_width(chart)

        for ri, lane in enumerate(chart):
            kind = rows[ri]["row_tag"]
            for ci, slot in enumerate(lane):
                if slot is None:
                    findings.append(
                        ValidationIssue(
                            location=f"table {grid_no}, row {ri + 1}, col {ci + 1}",
                            message=f"Gap remains after fix (row type: {kind})",
                        )
                    )
                elif isinstance(slot, _GridCell) and slot.tag == "td":
                    if _JOINED_AMOUNTS_RE.search(slot.content):
                        findings.append(
                            ValidationIssue(
                                location=f"table {grid_no}, row {ri + 1}, col {ci + 1}",
                                message=f"Residual merged value: {slot.content!r}",
                            )
                        )

        for ri, lane in enumerate(chart):
            if rows[ri]["row_tag"] == "th":
                continue
            present = sum(1 for slot in lane if isinstance(slot, _GridCell))
            if present != width:
                findings.append(
                    ValidationIssue(
                        location=f"table {grid_no}, data row {ri + 1}",
                        message=f"Column count mismatch: expected {width}, got {present}",
                    )
                )

    return findings


def _audit_md_grids(text: str) -> list[ValidationIssue]:
    findings: list[ValidationIssue] = []
    rows = text.split("\n")
    grid_no = 0
    cursor = 0
    while cursor < len(rows):
        if not _looks_like_md_line(rows[cursor]):
            cursor += 1
            continue
        grid_no += 1
        block: list[str] = []
        while cursor < len(rows) and _looks_like_md_line(rows[cursor]):
            block.append(rows[cursor])
            cursor += 1
        if len(block) < 2:
            findings.append(
                ValidationIssue(location=f"md-table {grid_no}", message="Table has no data rows")
            )
            continue
        width = len(_split_md_line(block[0]))
        for line_no, line in enumerate(block[1:], start=2):
            if _looks_like_rule(line):
                continue
            boxes = _split_md_line(line)
            if len(boxes) != width:
                findings.append(
                    ValidationIssue(
                        location=f"md-table {grid_no}, row {line_no}",
                        message=f"Column count mismatch: expected {width}, got {len(boxes)}",
                    )
                )
            for position, box in enumerate(boxes):
                if _JOINED_AMOUNTS_RE.search(box):
                    findings.append(
                        ValidationIssue(
                            location=f"md-table {grid_no}, row {line_no}, col {position + 1}",
                            message=f"Residual merged value: {box!r}",
                        )
                    )
    return findings


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


def fix_ocr_output(text: str) -> str:
    """Repair every grid in vision output. HTML stays HTML; pipes re-align."""
    text = _repair_all_grids(text)
    text = _align_md_grids(text)
    return text


fix_markdown_tables = fix_ocr_output


def validate_ocr_output(text: str) -> list[ValidationIssue]:
    """List what is still wrong across HTML and pipe-style grids."""
    return _audit_html_grids(text) + _audit_md_grids(text)


validate_markdown_tables = validate_ocr_output
