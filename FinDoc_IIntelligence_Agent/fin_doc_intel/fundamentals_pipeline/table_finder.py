"""Locate <table> blocks in markdown and wrap them as extraction candidates.

Ported from FinDoc Pypi's `fundamentals_module/table_finder.py`. The markdown
produced by `input_pipeline` embeds tables as raw HTML and page boundaries as
`## Page N` headings; this module finds the tables and captures surrounding
context (nearest heading, scale annotation, and — an addition beyond the
reference, which has no per-page UI to serve — which page each table came from).

Public API:
    find_tables(md_text) -> List[TableBlock]
    group_into_statements(blocks) -> List[StatementCandidate]  (ported but not
        wired into the graph — see nodes.py::find_tables_node, which matches
        the reference's own wired node in using one table per candidate)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
_PAGE_RE = re.compile(r"^##\s+Page\s+(\d+)\s*$", re.MULTILINE)
_TABLE_OPEN_RE = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
_TABLE_CLOSE_RE = re.compile(r"</table\s*>", re.IGNORECASE)
_SCALE_RE = re.compile(
    r"\(?\s*in\s+(?:[€$£¥]|EUR|USD|GBP|JPY)?\s*"
    r"(thousand|million|billion)s?\b[^)]*\)?",
    re.IGNORECASE,
)


@dataclass
class TableBlock:
    """One raw <table> block lifted out of the markdown."""
    html: str
    nearest_h1: str = ""
    nearest_h2: str = ""
    nearest_h3: str = ""
    caption: str = ""
    outer_annotation: str = ""
    char_offset: int = 0
    page_number: Optional[int] = None


@dataclass
class StatementCandidate:
    """A group of one or more TableBlocks sent to the LLM together.

    The wired graph (nodes.py::find_tables_node) uses exactly one table per
    candidate; `group_into_statements` (grouping consecutive same-heading
    tables) exists for parity with the reference but isn't on the wired path,
    matching FinDoc Pypi's own graph.py.
    """
    tables: List[TableBlock] = field(default_factory=list)
    nearest_h1: str = ""
    nearest_h2: str = ""
    caption: str = ""
    outer_annotation: str = ""
    page_number: Optional[int] = None

    @property
    def html(self) -> str:
        """Concatenated HTML of all tables in the candidate."""
        return "\n\n".join(t.html for t in self.tables)


def _scan_headings(md_text: str) -> List[Tuple[int, int, str]]:
    """Return (char_offset, level, text) for every markdown heading, in source order."""
    out: List[Tuple[int, int, str]] = []
    for match in _HEADING_RE.finditer(md_text):
        out.append((match.start(), len(match.group(1)), match.group(2).strip()))
    return out


def _heading_state_at(headings: List[Tuple[int, int, str]], offset: int) -> Tuple[str, str, str]:
    """Return the (h1, h2, h3) heading state in effect at `offset`."""
    h1 = h2 = h3 = ""
    for h_offset, level, text in headings:
        if h_offset >= offset:
            break
        if level == 1:
            h1, h2, h3 = text, "", ""
        elif level == 2:
            h2, h3 = text, ""
        elif level == 3:
            h3 = text
    return h1, h2, h3


def _scan_pages(md_text: str) -> List[Tuple[int, int]]:
    """Return (char_offset, page_number) for every '## Page N' heading `input_pipeline` writes."""
    return [(m.start(), int(m.group(1))) for m in _PAGE_RE.finditer(md_text)]


def _page_at(pages: List[Tuple[int, int]], offset: int) -> Optional[int]:
    """Return the page number in effect at `offset` (the last '## Page N' before it)."""
    current: Optional[int] = None
    for p_offset, page_number in pages:
        if p_offset > offset:
            break
        current = page_number
    return current


def _pre_table_window(md_text: str, table_start: int, prev_boundary: int) -> str:
    """Return the markdown text between `prev_boundary` and the table."""
    return md_text[prev_boundary:table_start]


def _extract_outer_annotation(window: str) -> str:
    """Find the last scale-annotation phrase in `window`, if any."""
    matches = list(_SCALE_RE.finditer(window))
    return matches[-1].group(0).strip() if matches else ""


def _extract_caption(window: str, outer_annotation: str) -> str:
    """Pick the most plausible caption line from `window` (bottom-up scan)."""
    for line in reversed(window.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if outer_annotation and outer_annotation in stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped
    return ""


def _find_table_spans(md_text: str) -> List[Tuple[int, int]]:
    """Return (start, end) char spans for every top-level <table> block.

    Depth-tracking handles nested <table> tags and unclosed tables (bounded by
    the next top-level <table> open rather than consuming all subsequent text).
    """
    spans: List[Tuple[int, int]] = []
    pos = 0

    while pos < len(md_text):
        open_m = _TABLE_OPEN_RE.search(md_text, pos)
        if not open_m:
            break

        start = open_m.start()
        depth = 1
        search = open_m.end()

        while depth > 0 and search < len(md_text):
            next_open = _TABLE_OPEN_RE.search(md_text, search)
            next_close = _TABLE_CLOSE_RE.search(md_text, search)

            if not next_close:
                search = len(md_text)
                break

            if next_open and next_open.start() < next_close.start():
                if depth == 1:
                    search = next_open.start()
                    depth = 0
                else:
                    depth += 1
                    search = next_open.end()
            else:
                depth -= 1
                search = next_close.end()

        spans.append((start, search))
        pos = search

    return spans


def find_tables(md_text: str) -> List[TableBlock]:
    """Locate every <table> block and capture surrounding context.

    BeautifulSoup validates that each match is well-formed; malformed blocks
    are skipped.
    """
    headings = _scan_headings(md_text)
    pages = _scan_pages(md_text)
    blocks: List[TableBlock] = []
    prev_boundary = 0

    for table_start, table_end in _find_table_spans(md_text):
        html = md_text[table_start:table_end]
        try:
            BeautifulSoup(html, "html.parser").find("table")
        except Exception:
            prev_boundary = table_end
            continue

        h1, h2, h3 = _heading_state_at(headings, table_start)

        last_heading_offset = 0
        for h_offset, _, _ in headings:
            if h_offset < table_start:
                last_heading_offset = h_offset
            else:
                break
        boundary = max(prev_boundary, last_heading_offset)
        window = _pre_table_window(md_text, table_start, boundary)

        outer_annotation = _extract_outer_annotation(window)
        caption = _extract_caption(window, outer_annotation)

        blocks.append(TableBlock(
            html=html,
            nearest_h1=h1, nearest_h2=h2, nearest_h3=h3,
            caption=caption, outer_annotation=outer_annotation,
            char_offset=table_start,
            page_number=_page_at(pages, table_start),
        ))
        prev_boundary = table_end

    return blocks


def group_into_statements(blocks: List[TableBlock]) -> List[StatementCandidate]:
    """Group consecutive blocks that share their (h1, h2) heading.

    Not on the wired path (see module docstring) — ported for parity/future use.
    """
    candidates: List[StatementCandidate] = []
    current: Optional[StatementCandidate] = None

    for block in blocks:
        key = (block.nearest_h1, block.nearest_h2)
        if current is not None and (current.nearest_h1, current.nearest_h2) == key and key != ("", ""):
            current.tables.append(block)
            if not current.outer_annotation and block.outer_annotation:
                current.outer_annotation = block.outer_annotation
            continue

        current = StatementCandidate(
            tables=[block], nearest_h1=block.nearest_h1, nearest_h2=block.nearest_h2,
            caption=block.caption, outer_annotation=block.outer_annotation,
            page_number=block.page_number,
        )
        candidates.append(current)

    return candidates
