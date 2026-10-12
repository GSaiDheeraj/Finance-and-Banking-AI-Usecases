"""Reading contract every filing renderer honours.

Two renderers implement this today — a per-page hybrid for readable PDFs and
a full-vision pass for photographed ones. The orchestrator depends only on
this shape, so a future reader (XBRL, HTML filings) slots in without touching
it. That is the Strategy seam of this package: depend on the contract, inject
the concrete reader.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TextExtractor(Protocol):
    """Render one filing to Markdown stored on disk."""

    def extract(self, source: Path, output_dir: Path) -> Path:
        """Write `source` as Markdown inside `output_dir`; return its path."""
        ...
