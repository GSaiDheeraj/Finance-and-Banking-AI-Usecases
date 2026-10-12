"""Find grid blocks in rendered Markdown and wrap them for the readers.

The renderer embeds grids as raw HTML and sheet edges as `## Page N`
headings. This module lifts each grid out with its neighbourhood — closest
headings, scale note, caption line, and sheet number — so later passes never
re-scan the document.

Public surface:
    find_tables(md_text) -> list[TableBlock]
    group_into_statements(blocks) -> list[StatementCandidate]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
_SHEET_PATTERN = re.compile(r"^##\s+Page\s+(\d+)\s*$", re.MULTILINE)
_GRID_OPEN_PATTERN = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
_GRID_CLOSE_PATTERN = re.compile(r"</table\s*>", re.IGNORECASE)
_MAGNITUDE_PATTERN = re.compile(
    r"\(?\s*in\s+(?:[€$£¥]|EUR|USD|GBP|JPY)?\s*"
    r"(thousand|million|billion)s?\b[^)]*\)?",
    re.IGNORECASE,
)


@dataclass
class TableBlock:
    """One raw grid lifted from the Markdown with its neighbourhood."""

    html: str
    nearest_h1: str = ""
    nearest_h2: str = ""
    nearest_h3: str = ""
    caption: str = ""
    outer_annotation: str = ""
    char_offset: int = 0
    page_number: int | None = None


@dataclass
class StatementCandidate:
    """One or more grids read together by the model passes.

    The wired flow sends exactly one grid per candidate; grouping neighbours
    under shared headings exists for callers that want larger batches.
    """

    tables: list[TableBlock] = field(default_factory=list)
    nearest_h1: str = ""
    nearest_h2: str = ""
    caption: str = ""
    outer_annotation: str = ""
    page_number: int | None = None

    @property
    def html(self) -> str:
        """Joined markup of every grid in this candidate."""
        return "\n\n".join(grid.html for grid in self.tables)


def _collect_headings(rendered: str) -> list[tuple[int, int, str]]:
    """Every Markdown heading as (offset, depth, text) in document order."""
    found: list[tuple[int, int, str]] = []
    for hit in _HEADING_PATTERN.finditer(rendered):
        found.append((hit.start(), len(hit.group(1)), hit.group(2).strip()))
    return found


def _headings_in_force(headings: list[tuple[int, int, str]], offset: int) -> tuple[str, str, str]:
    """The (h1, h2, h3) context applying at a character offset."""
    top = mid = low = ""
    for at, depth, wording in headings:
        if at >= offset:
            break
        if depth == 1:
            top, mid, low = wording, "", ""
        elif depth == 2:
            mid, low = wording, ""
        elif depth == 3:
            low = wording
    return top, mid, low


def _collect_sheets(rendered: str) -> list[tuple[int, int]]:
    """Every `## Page N` marker as (offset, sheet number)."""
    return [(hit.start(), int(hit.group(1))) for hit in _SHEET_PATTERN.finditer(rendered)]


def _sheet_in_force(sheets: list[tuple[int, int]], offset: int) -> int | None:
    """The most recent sheet marker at or before an offset."""
    current: int | None = None
    for at, number in sheets:
        if at > offset:
            break
        current = number
    return current


def _window_before(rendered: str, grid_start: int, previous_edge: int) -> str:
    """Markdown between the previous edge and a grid's opening tag."""
    return rendered[previous_edge:grid_start]


def _newest_magnitude_note(window: str) -> str:
    """The last scale note (e.g. `(in millions)`) inside a window, if any."""
    hits = list(_MAGNITUDE_PATTERN.finditer(window))
    return hits[-1].group(0).strip() if hits else ""


def _likely_caption(window: str, magnitude_note: str) -> str:
    """Best-guess caption: lowest non-blank, non-heading line, skipping the note."""
    for line in reversed(window.splitlines()):
        tidied = line.strip()
        if not tidied:
            continue
        if magnitude_note and magnitude_note in tidied:
            continue
        if tidied.startswith("#"):
            continue
        return tidied
    return ""


def _grid_spans(rendered: str) -> list[tuple[int, int]]:
    """Character spans of top-level grids, tolerant of nesting and cut-offs.

    An inner opening tag while already inside a grid ends the outer span at
    that point rather than swallowing the rest of the document.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0

    while cursor < len(rendered):
        opening = _GRID_OPEN_PATTERN.search(rendered, cursor)
        if not opening:
            break

        start = opening.start()
        depth = 1
        probe = opening.end()

        while depth > 0 and probe < len(rendered):
            inner_open = _GRID_OPEN_PATTERN.search(rendered, probe)
            inner_close = _GRID_CLOSE_PATTERN.search(rendered, probe)

            if not inner_close:
                probe = len(rendered)
                break

            if inner_open and inner_open.start() < inner_close.start():
                if depth == 1:
                    probe = inner_open.start()
                    depth = 0
                else:
                    depth += 1
                    probe = inner_open.end()
            else:
                depth -= 1
                probe = inner_close.end()

        spans.append((start, probe))
        cursor = probe

    return spans


def _previous_heading_edge(headings: list[tuple[int, int, str]], offset: int) -> int:
    edge = 0
    for at, _, _ in headings:
        if at >= offset:
            break
        edge = at
    return edge


def _markup_has_grid(markup: str) -> bool:
    return BeautifulSoup(markup, "html.parser").find("table") is not None


def find_tables(rendered: str) -> list[TableBlock]:
    """Lift every parseable grid with headings, note, caption, and sheet.

    Unparseable fragments are skipped.
    """
    headings = _collect_headings(rendered)
    sheets = _collect_sheets(rendered)
    lifted: list[TableBlock] = []
    previous_edge = 0

    for grid_start, grid_end in _grid_spans(rendered):
        markup = rendered[grid_start:grid_end]
        if not _markup_has_grid(markup):
            previous_edge = grid_end
            continue

        top, mid, low = _headings_in_force(headings, grid_start)
        edge = max(previous_edge, _previous_heading_edge(headings, grid_start))
        window = _window_before(rendered, grid_start, edge)

        magnitude_note = _newest_magnitude_note(window)
        caption = _likely_caption(window, magnitude_note)

        lifted.append(
            TableBlock(
                html=markup,
                nearest_h1=top,
                nearest_h2=mid,
                nearest_h3=low,
                caption=caption,
                outer_annotation=magnitude_note,
                char_offset=grid_start,
                page_number=_sheet_in_force(sheets, grid_start),
            )
        )
        previous_edge = grid_end

    return lifted


def group_into_statements(blocks: list[TableBlock]) -> list[StatementCandidate]:
    """Batch neighbours sharing their (h1, h2) context into one candidate.

    Kept for batch callers; the wired flow still sends one grid per candidate.
    """
    batched: list[StatementCandidate] = []
    open_batch: StatementCandidate | None = None

    for block in blocks:
        key = (block.nearest_h1, block.nearest_h2)
        if (
            open_batch is not None
            and (open_batch.nearest_h1, open_batch.nearest_h2) == key
            and key != ("", "")
        ):
            open_batch.tables.append(block)
            if not open_batch.outer_annotation and block.outer_annotation:
                open_batch.outer_annotation = block.outer_annotation
            continue

        open_batch = StatementCandidate(
            tables=[block],
            nearest_h1=block.nearest_h1,
            nearest_h2=block.nearest_h2,
            caption=block.caption,
            outer_annotation=block.outer_annotation,
            page_number=block.page_number,
        )
        batched.append(open_batch)

    return batched
