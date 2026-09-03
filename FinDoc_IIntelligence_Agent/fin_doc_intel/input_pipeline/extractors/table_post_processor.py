"""Post-process and validate OCR-extracted tables.

Ported verbatim (logic unchanged) from FinDoc Pypi's
`input_module/extractors/table_post_processor.py`. Three-stage pipeline per HTML
table:

  Stage 1  Merged-value split   - "$ 2,489 | $ 894" inside a single <td>
                                   is expanded into two separate <td> cells.

  Stage 2  Thead normalisation  - In multi-row <thead> blocks where the
                                   guard condition passes (leaf-row cell
                                   count == sum of row-1 colspans), rowspan
                                   attributes are stripped from non-first
                                   standalone header cells. This turns
                                   rowspan-occupied grid positions into plain
                                   None gaps so Stage 3 fills them correctly
                                   regardless of whether rowspan is 2, 3, 4...

  Stage 3  Grid gap fill        - A W3C-style 2D occupancy grid is built from
                                   the table, accounting for every rowspan and
                                   colspan value. Every None gap is filled
                                   with an empty element matching the row type:
                                   <th></th> for header rows, <td></td> for
                                   body rows.

GFM markdown tables are also normalised (separate path).

Public API (unchanged):
  fix_ocr_output(text)      -> str
  validate_ocr_output(text) -> List[ValidationIssue]
  fix_markdown_tables       = fix_ocr_output          (alias)
  validate_markdown_tables  = validate_ocr_output     (alias)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

# --------------------------------------------------------------------------- #
# Shared regex
# --------------------------------------------------------------------------- #

_HTML_TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"(<tr[^>]*>)(.*?)(</tr>)", re.DOTALL | re.IGNORECASE)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<(th|td)([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_COLSPAN_RE = re.compile(r'colspan=["\']?(\d+)["\']?', re.IGNORECASE)
_ROWSPAN_RE = re.compile(r'rowspan=["\']?(\d+)["\']?', re.IGNORECASE)
_THEAD_RE = re.compile(r"(<thead[^>]*>)(.*?)(</thead>)", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_STRONG_RE = re.compile(r"<(?:strong|b)>(.*?)</(?:strong|b)>", re.IGNORECASE)
_EM_RE = re.compile(r"<(?:em|i)>(.*?)</(?:em|i)>", re.IGNORECASE)

# Two financial values merged with a pipe inside a single cell.
_MERGED_VALUE_RE = re.compile(
    r"(\$?\s*\(?-?[\d,]+(?:\.\d+)?\)?|[-–])"
    r"\s*\|\s*"
    r"(\$?\s*\(?-?[\d,]+(?:\.\d+)?\)?|[-–])"
)

# --------------------------------------------------------------------------- #
# Cell model and grid sentinel
# --------------------------------------------------------------------------- #


@dataclass
class _Cell:
    tag: str        # "th" or "td"
    attrs: str      # attribute string from the opening tag
    content: str    # inner HTML content
    colspan: int = 1
    rowspan: int = 1


# Marks grid positions covered by a rowspan or colspan from another cell.
_OCCUPIED: Any = object()


# --------------------------------------------------------------------------- #
# Cell-level helpers
# --------------------------------------------------------------------------- #

def _strip_tags(html: str) -> str:
    text = _STRONG_RE.sub(r"**\1**", html)
    text = _EM_RE.sub(r"*\1*", text)
    return _TAG_RE.sub("", text).strip()


def _split_merged(cell: str) -> List[str]:
    """Expand `$ 2,489 | $ 894` -> `["$ 2,489", "$ 894"]`."""
    parts = [cell]
    changed = True
    while changed:
        changed = False
        expanded: List[str] = []
        for part in parts:
            m = _MERGED_VALUE_RE.search(part)
            if m:
                expanded.extend([
                    (part[: m.start()] + m.group(1)).strip(),
                    (m.group(2) + part[m.end():]).strip(),
                ])
                changed = True
            else:
                expanded.append(part)
        parts = expanded
    return parts


def _colspan(attrs: str) -> int:
    m = _COLSPAN_RE.search(attrs)
    return int(m.group(1)) if m else 1


def _rowspan(attrs: str) -> int:
    m = _ROWSPAN_RE.search(attrs)
    return int(m.group(1)) if m else 1


# --------------------------------------------------------------------------- #
# Stage 1 helpers
# --------------------------------------------------------------------------- #

def _row_col_width(row_inner: str, cell_re: re.Pattern) -> int:
    """Effective column count for a row (expands colspan)."""
    total = 0
    for tag_m in re.finditer(r"<(?:th|td)([^>]*)>", row_inner, re.IGNORECASE):
        cs = _COLSPAN_RE.search(tag_m.group(1))
        total += int(cs.group(1)) if cs else 1
    return total


def _sum_colspans(row_inner: str) -> int:
    """Sum colspan values of cells that actually have a colspan attribute."""
    total = 0
    for m in re.finditer(r"<th([^>]*)>", row_inner, re.IGNORECASE):
        cs = _COLSPAN_RE.search(m.group(1))
        if cs:
            total += int(cs.group(1))
    return total


# --------------------------------------------------------------------------- #
# Stage 2 - thead normalisation
# --------------------------------------------------------------------------- #

def _normalize_thead(table_html: str) -> str:
    """Strip rowspan from non-first standalone row-1 header cells.

    Guard: only fires when the leaf (last) header row's cell count equals the
    sum of colspan values in row 1. After stripping, those column positions
    appear as None gaps in the grid and Stage 3 fills them with <th></th> for
    every sub-header row. The first cell (row label) retains its rowspan.
    """
    def fix_thead(tm: re.Match) -> str:
        thead_open, thead_inner, thead_close = tm.group(1), tm.group(2), tm.group(3)
        tr_list = list(_TR_RE.finditer(thead_inner))
        if len(tr_list) < 2:
            return tm.group(0)

        row1_inner = tr_list[0].group(2)
        last_inner = tr_list[-1].group(2)

        n_last = _row_col_width(last_inner, _TH_RE)
        sum_cs = _sum_colspans(row1_inner)

        if sum_cs == 0 or n_last != sum_cs:
            return tm.group(0)

        th_matches = list(re.finditer(
            r"(<th([^>]*)>)(.*?)(</th>)", row1_inner, re.DOTALL | re.IGNORECASE,
        ))
        if len(th_matches) < 2:
            return tm.group(0)

        standalone_count = sum(
            1 for i, m in enumerate(th_matches)
            if i > 0 and not _COLSPAN_RE.search(m.group(2)) and _strip_tags(m.group(3)).strip()
        )
        if standalone_count == 0:
            return tm.group(0)

        new_row1_inner = row1_inner
        for i, th_m in enumerate(reversed(th_matches)):
            real_idx = len(th_matches) - 1 - i
            if real_idx == 0:
                continue
            attrs = th_m.group(2)
            if _COLSPAN_RE.search(attrs):
                continue
            if not _strip_tags(th_m.group(3)).strip():
                new_row1_inner = new_row1_inner[: th_m.start()] + new_row1_inner[th_m.end():]
                continue
            if not _ROWSPAN_RE.search(attrs):
                continue
            new_attrs = _ROWSPAN_RE.sub("", attrs).strip()
            new_open = f"<th{(' ' + new_attrs) if new_attrs else ''}>"
            new_row1_inner = (
                new_row1_inner[: th_m.start()] + new_open + th_m.group(3) + th_m.group(4)
                + new_row1_inner[th_m.end():]
            )

        placeholders = "".join(["<th></th>"] * standalone_count)

        new_thead_inner = thead_inner
        for tr_m in reversed(tr_list[1:]):
            new_inner = placeholders + tr_m.group(2)
            new_tr = tr_m.group(1) + new_inner + tr_m.group(3)
            new_thead_inner = new_thead_inner[: tr_m.start()] + new_tr + new_thead_inner[tr_m.end():]

        r1 = tr_list[0]
        new_thead_inner = (
            new_thead_inner[: r1.start(2)] + new_row1_inner + new_thead_inner[r1.end(2):]
        )
        return f"{thead_open}{new_thead_inner}{thead_close}"

    return _THEAD_RE.sub(fix_thead, table_html)


# --------------------------------------------------------------------------- #
# % sub-header alignment (data-driven correction)
# --------------------------------------------------------------------------- #

_PCT_VAL_RE = re.compile(r"-?\d[\d.]*%")


def _find_pct_col_in_data(table_html: str) -> int:
    """Return the 0-based column with the most numeric % values in <tbody>."""
    counts: Dict[int, int] = {}
    for tr_m in _TR_RE.finditer(table_html):
        inner = tr_m.group(2)
        if _TH_RE.search(inner):
            continue
        for i, td_m in enumerate(_TD_RE.finditer(inner)):
            if _PCT_VAL_RE.search(_strip_tags(td_m.group(1))):
                counts[i] = counts.get(i, 0) + 1
    return max(counts, key=counts.get) if counts else -1


def _find_pct_col_in_header(table_html: str) -> int:
    """Return the full table column index of the '%' sub-header cell."""
    for tm in _THEAD_RE.finditer(table_html):
        tr_list = list(_TR_RE.finditer(tm.group(2)))
        if len(tr_list) < 2:
            continue

        row1_inner = tr_list[0].group(2)
        leading_occupied = 0
        for th_m in re.finditer(r"<th([^>]*)>", row1_inner, re.IGNORECASE):
            attrs = th_m.group(1)
            if _ROWSPAN_RE.search(attrs) and not _COLSPAN_RE.search(attrs):
                leading_occupied += 1
            else:
                break

        for i, th_m in enumerate(_TH_RE.finditer(tr_list[-1].group(2))):
            if _strip_tags(th_m.group(1)).strip() == "%":
                return leading_occupied + i

    return -1


def _align_pct_subheader(table_html: str) -> str:
    """Insert empty <th></th> cells so the % sub-header aligns with % data."""
    header_col = _find_pct_col_in_header(table_html)
    data_col = _find_pct_col_in_data(table_html)

    if header_col < 0 or data_col < 0:
        return table_html
    offset = data_col - header_col
    if offset <= 0:
        return table_html

    extra = "".join(["<th></th>"] * offset)

    def fix_thead(tm: re.Match) -> str:
        thead_open, thead_inner, thead_close = tm.group(1), tm.group(2), tm.group(3)
        tr_list = list(_TR_RE.finditer(thead_inner))
        if len(tr_list) < 2:
            return tm.group(0)

        new_thead_inner = thead_inner
        for tr_m in reversed(tr_list[1:]):
            row_inner = tr_m.group(2)
            non_empty = [
                m for m in re.finditer(r"<th[^>]*>.*?</th>", row_inner, re.DOTALL | re.IGNORECASE)
                if _strip_tags(m.group(0)).strip()
            ]
            if not non_empty:
                continue
            insert_at = non_empty[0].start()
            new_inner = row_inner[:insert_at] + extra + row_inner[insert_at:]
            new_tr = tr_m.group(1) + new_inner + tr_m.group(3)
            new_thead_inner = new_thead_inner[: tr_m.start()] + new_tr + new_thead_inner[tr_m.end():]
        return f"{thead_open}{new_thead_inner}{thead_close}"

    return _THEAD_RE.sub(fix_thead, table_html)


# --------------------------------------------------------------------------- #
# Stage 3 - grid simulation and gap fill
# --------------------------------------------------------------------------- #

def _parse_rows(html: str) -> Tuple[List[dict], List[re.Match]]:
    """Parse every <tr> block into a structured row dict.

    Merged <td> values are expanded at parse time so the grid sees the
    correct cell count from the start.
    """
    tr_matches = list(_TR_RE.finditer(html))
    rows: List[dict] = []

    for tr_m in tr_matches:
        inner = tr_m.group(2)
        cells: List[_Cell] = []

        for cm in _CELL_RE.finditer(inner):
            tag = cm.group(1).lower()
            attrs = cm.group(2)
            content = cm.group(3)
            cs = _colspan(attrs)
            rs = _rowspan(attrs)

            if tag == "td":
                text = _strip_tags(content)
                parts = _split_merged(text)
                if len(parts) > 1:
                    cells.append(_Cell(tag, attrs, parts[0], cs, rs))
                    for part in parts[1:]:
                        cells.append(_Cell(tag, "", part, 1, rs))
                else:
                    cells.append(_Cell(tag, attrs, text, cs, rs))
            else:
                cells.append(_Cell(tag, attrs, content, cs, rs))

        row_tag = "th" if any(c.tag == "th" for c in cells) else "td"
        rows.append({
            "tr_open": tr_m.group(1), "tr_close": tr_m.group(3),
            "cells": cells, "row_tag": row_tag,
        })

    return rows, tr_matches


def _build_grid(rows: List[dict]) -> List[list]:
    """Simulate the HTML table-forming algorithm.

    Each grid position holds a `_Cell` (span origin), `_OCCUPIED` (covered by
    a rowspan/colspan), or `None` (gap).
    """
    n = len(rows)
    grid: List[list] = [[] for _ in range(n)]

    def ensure(r: int, min_len: int) -> None:
        while len(grid[r]) < min_len:
            grid[r].append(None)

    for ri, row in enumerate(rows):
        col = 0
        for cell in row["cells"]:
            ensure(ri, col + 1)
            while col < len(grid[ri]) and grid[ri][col] is _OCCUPIED:
                col += 1
                ensure(ri, col + 1)

            for r in range(ri, min(ri + cell.rowspan, n)):
                ensure(r, col + cell.colspan)
                for c in range(col, col + cell.colspan):
                    grid[r][c] = cell if (r == ri and c == col) else _OCCUPIED

            col += cell.colspan

    return grid


def _n_cols(grid: List[list]) -> int:
    return max((len(r) for r in grid), default=0)


def _fill_gaps(grid: List[list], rows: List[dict], n_cols: int) -> None:
    """Fill every None position in-place with an empty cell matching the row type."""
    for ri, row_grid in enumerate(grid):
        while len(row_grid) < n_cols:
            row_grid.append(None)
        tag = rows[ri]["row_tag"]
        for ci in range(n_cols):
            if row_grid[ci] is None:
                row_grid[ci] = _Cell(tag=tag, attrs="", content="")


def _reconstruct_table(table_html: str, grid: List[list], rows: List[dict], tr_matches: List[re.Match]) -> str:
    """Replace every <tr> block with the fixed grid content, last to first."""
    result = table_html
    for ri in range(len(rows) - 1, -1, -1):
        cells_html: List[str] = []
        for entry in grid[ri]:
            if entry is _OCCUPIED:
                continue
            if isinstance(entry, _Cell):
                cells_html.append(f"\n      <{entry.tag}{entry.attrs}>{entry.content}</{entry.tag}>")
        inner = "".join(cells_html) + "\n    " if cells_html else ""
        new_tr = f"{rows[ri]['tr_open']}{inner}{rows[ri]['tr_close']}"
        tr_m = tr_matches[ri]
        result = result[: tr_m.start()] + new_tr + result[tr_m.end():]
    return result


def _fix_html_table_inplace(table_html: str) -> str:
    """Apply all three stages to a single <table> block."""
    table_html = _normalize_thead(table_html)
    table_html = _align_pct_subheader(table_html)

    rows, tr_matches = _parse_rows(table_html)
    if not rows:
        return table_html

    grid = _build_grid(rows)
    nc = _n_cols(grid)
    if nc == 0:
        return table_html

    _fill_gaps(grid, rows, nc)
    return _reconstruct_table(table_html, grid, rows, tr_matches)


def _fix_html_tables(text: str) -> str:
    return _HTML_TABLE_RE.sub(lambda m: _fix_html_table_inplace(m.group(0)), text)


# --------------------------------------------------------------------------- #
# GFM markdown table fix (fallback for pages the model outputs as markdown)
# --------------------------------------------------------------------------- #

_MD_ROW_RE = re.compile(r"^\|.*\|$")
_MD_SEP_RE = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")


def _is_md_row(line: str) -> bool:
    return bool(_MD_ROW_RE.match(line.strip()))


def _is_separator(line: str) -> bool:
    return bool(_MD_SEP_RE.match(line.strip()))


def _parse_md_row(line: str) -> List[str]:
    parts = line.strip().split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [p.strip() for p in parts]


def _normalise_cells(cells: List[str], n_cols: int) -> List[str]:
    expanded: List[str] = []
    for c in cells:
        expanded.extend(_split_merged(c))
    while expanded and not expanded[-1]:
        expanded.pop()
    if len(expanded) < n_cols:
        expanded += [""] * (n_cols - len(expanded))
    elif len(expanded) > n_cols:
        expanded = expanded[:n_cols]
    return expanded


def _fix_md_block(lines: List[str]) -> List[str]:
    if not lines:
        return lines
    n_cols = len(_parse_md_row(lines[0]))
    if n_cols == 0:
        return lines
    fixed = [lines[0]]
    for line in lines[1:]:
        if _is_separator(line):
            fixed.append("| " + " | ".join(["---"] * n_cols) + " |")
            continue
        cells = _normalise_cells(_parse_md_row(line), n_cols)
        fixed.append("| " + " | ".join(cells) + " |")
    return fixed


def _fix_markdown_table_alignment(text: str) -> str:
    lines = text.split("\n")
    result: List[str] = []
    i = 0
    while i < len(lines):
        if _is_md_row(lines[i]):
            block: List[str] = []
            while i < len(lines) and _is_md_row(lines[i]):
                block.append(lines[i])
                i += 1
            result.extend(_fix_md_block(block))
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

@dataclass
class ValidationIssue:
    location: str
    message: str


def _validate_html_tables(text: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for t_idx, t_m in enumerate(_HTML_TABLE_RE.finditer(text), start=1):
        table_html = t_m.group(0)

        for tm in _THEAD_RE.finditer(table_html):
            tr_list = list(_TR_RE.finditer(tm.group(2)))
            if len(tr_list) < 2:
                continue
            row1_inner = tr_list[0].group(2)
            last_inner = tr_list[-1].group(2)
            n_last = _row_col_width(last_inner, _TH_RE)
            sum_cs = _sum_colspans(row1_inner)
            if sum_cs > 0 and n_last != sum_cs:
                issues.append(ValidationIssue(
                    location=f"table {t_idx}, thead",
                    message=f"Multi-row header mismatch: leaf-row cells={n_last}, sum of row-1 colspans={sum_cs}",
                ))

        rows, _ = _parse_rows(table_html)
        if not rows:
            continue
        grid = _build_grid(rows)
        nc = _n_cols(grid)

        for ri, row_grid in enumerate(grid):
            row_tag = rows[ri]["row_tag"]
            for ci, entry in enumerate(row_grid):
                if entry is None:
                    issues.append(ValidationIssue(
                        location=f"table {t_idx}, row {ri + 1}, col {ci + 1}",
                        message=f"Gap remains after fix (row type: {row_tag})",
                    ))
                elif isinstance(entry, _Cell) and entry.tag == "td":
                    if _MERGED_VALUE_RE.search(entry.content):
                        issues.append(ValidationIssue(
                            location=f"table {t_idx}, row {ri + 1}, col {ci + 1}",
                            message=f"Residual merged value: {entry.content!r}",
                        ))

        for ri, row_grid in enumerate(grid):
            if rows[ri]["row_tag"] == "th":
                continue
            effective = sum(1 for e in row_grid if isinstance(e, _Cell))
            if effective != nc:
                issues.append(ValidationIssue(
                    location=f"table {t_idx}, data row {ri + 1}",
                    message=f"Column count mismatch: expected {nc}, got {effective}",
                ))

    return issues


def _validate_markdown_tables(text: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    lines = text.split("\n")
    t_idx = 0
    i = 0
    while i < len(lines):
        if not _is_md_row(lines[i]):
            i += 1
            continue
        t_idx += 1
        block: List[str] = []
        while i < len(lines) and _is_md_row(lines[i]):
            block.append(lines[i])
            i += 1
        if len(block) < 2:
            issues.append(ValidationIssue(location=f"md-table {t_idx}", message="Table has no data rows"))
            continue
        n_cols = len(_parse_md_row(block[0]))
        for r_idx, line in enumerate(block[1:], start=2):
            if _is_separator(line):
                continue
            cells = _parse_md_row(line)
            if len(cells) != n_cols:
                issues.append(ValidationIssue(
                    location=f"md-table {t_idx}, row {r_idx}",
                    message=f"Column count mismatch: expected {n_cols}, got {len(cells)}",
                ))
            for c_idx, cell in enumerate(cells):
                if _MERGED_VALUE_RE.search(cell):
                    issues.append(ValidationIssue(
                        location=f"md-table {t_idx}, row {r_idx}, col {c_idx + 1}",
                        message=f"Residual merged value: {cell!r}",
                    ))
    return issues


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def fix_ocr_output(text: str) -> str:
    """Fix all tables in OCR output. HTML stays HTML; GFM tables are column-normalised."""
    text = _fix_html_tables(text)
    text = _fix_markdown_table_alignment(text)
    return text


fix_markdown_tables = fix_ocr_output


def validate_ocr_output(text: str) -> List[ValidationIssue]:
    """Return validation issues from all tables (HTML and markdown)."""
    return _validate_html_tables(text) + _validate_markdown_tables(text)


validate_markdown_tables = validate_ocr_output
