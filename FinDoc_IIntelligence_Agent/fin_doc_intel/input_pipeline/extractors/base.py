"""Abstract contract for text-extraction backends.

Ported verbatim from FinDoc Pypi's `input_module/extractors/base.py`.
"""
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TextExtractor(Protocol):
    """Convert a single document into Markdown on disk."""

    def extract(self, source: Path, output_dir: Path) -> Path:
        """Extract `source` to a Markdown file inside `output_dir`.

        Returns the path to the written Markdown file.
        """
        ...
