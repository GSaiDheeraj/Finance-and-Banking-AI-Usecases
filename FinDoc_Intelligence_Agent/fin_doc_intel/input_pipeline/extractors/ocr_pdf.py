"""Full-vision reader for photographed filings.

Every sheet is photographed at print resolution and read by a vision-language
model whose per-sheet Markdown is joined under `## Page N` headings. Weights
load once per instance and serve every later sheet and filing.
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path

import fitz
import torch
from PIL import Image
from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

from .table_post_processor import fix_markdown_tables, validate_markdown_tables

DEFAULT_MODEL_ID: str = "lightonai/LightOnOCR-2-1B"
DEFAULT_DPI: int = 300
DEFAULT_MAX_NEW_TOKENS: int = 4096
MIN_DPI: int = 200

_logger = logging.getLogger(__name__)


class OcrPdfExtractor:
    """Read photographed filings sheet by sheet into Markdown."""

    # Grids leave as HTML so cell edges stay unambiguous and stacked headers
    # keep their spans with no flattening guesses.
    _READING_BRIEF: str = (
        "Extract the full page content. "
        "Output ALL tables as HTML (<table>, <thead>, <tbody>, <tr>, <th>, <td>). "
        "Output all non-table content as standard markdown.\n\n"
        "Table rules — follow ALL of these exactly:\n\n"
        "1. MULTI-LEVEL HEADERS — if a table has a top header row that spans "
        "multiple columns (e.g. a date or period label covering several "
        "sub-columns), preserve it as-is using the colspan attribute: "
        "<th colspan='3'>January 1, 2023</th>. Put the sub-column headers in "
        "a second <tr> inside <thead>. Do NOT merge or flatten these rows.\n\n"
        "2. COLUMN COUNT — count the leaf-level (bottom) header row in <thead> "
        "to determine the total number of columns. Every <tr> in <tbody> MUST "
        "have exactly that many <td> cells.\n\n"
        "3. ONE VALUE PER CELL — each <td> contains exactly one printed value. "
        "Never place a '|' character inside a <td> value. If two numbers appear "
        "side-by-side, put each in its own <td>.\n\n"
        "4. PRESERVE DATA — copy every number, dollar sign, parenthesis, dash, "
        "and label exactly as printed.\n\n"
        "5. EMPTY CELLS — use <td></td> for blank cells; never omit a cell or "
        "reduce the column count."
    )

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        dpi: int = DEFAULT_DPI,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        device: str | None = None,
    ) -> None:
        """Load the vision weights and their processor.

        Raises:
            ValueError: If `dpi` falls below the legibility floor.
        """
        if dpi < MIN_DPI:
            raise ValueError(f"DPI must be >= {MIN_DPI} for OCR, got {dpi}")

        self._resolution = dpi
        self._budget = max_new_tokens
        self._hardware = device or self._pick_hardware()
        # Half-width floats keep float32 range at half the memory and roughly
        # twice the throughput on both supported accelerators.
        self._precision = torch.bfloat16

        _logger.info(
            "Loading %s (device=%s, dtype=%s)...", model_id, self._hardware, self._precision
        )
        begun = time.perf_counter()
        self._reader = LightOnOcrForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=self._precision
        ).to(self._hardware)
        self._tokenizer = LightOnOcrProcessor.from_pretrained(model_id)
        _logger.info("Model loaded in %.1fs", time.perf_counter() - begun)

    def extract(self, source: Path, output_dir: Path) -> Path:
        """Read the whole filing to `output_dir/{stem}.md`."""
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{source.stem}.md"
        target.write_text(self._render_all(source), encoding="utf-8")
        return target

    def _render_all(self, source: Path) -> str:
        """Photograph and read every sheet, keeping page order."""
        sections: list[str] = []
        with fitz.open(source) as filing:
            total = filing.page_count
            for position, sheet in enumerate(filing, start=1):
                begun = time.perf_counter()
                sections.append(f"## Page {position}\n\n{self.ocr_page(sheet).strip()}")
                spent = time.perf_counter() - begun
                _logger.info("[ocr] page %s/%s done in %.1fs", position, total, spent)
        return "\n\n".join(sections) + "\n"

    def ocr_page(self, sheet: fitz.Page) -> str:
        """Photograph, read, and tidy one sheet.

        Public so the hybrid reader reuses these weights instead of loading
        a second copy.
        """
        snapshot = self._photograph(sheet)
        draft = self._read_snapshot(snapshot)
        tidied = fix_markdown_tables(draft)
        for finding in validate_markdown_tables(tidied):
            _logger.warning("[table-validate] %s: %s", finding.location, finding.message)
        return tidied

    def _photograph(self, sheet: fitz.Page) -> Image.Image:
        """Rasterize one sheet to a print-resolution colour image."""
        raster = sheet.get_pixmap(dpi=self._resolution)
        return Image.open(io.BytesIO(raster.tobytes("png"))).convert("RGB")

    def _send_to_hardware(self, batch: dict) -> dict:
        placed = {}
        for name, tensor in batch.items():
            if tensor.is_floating_point():
                placed[name] = tensor.to(device=self._hardware, dtype=self._precision)
            else:
                placed[name] = tensor.to(self._hardware)
        return placed

    def _read_snapshot(self, snapshot: Image.Image) -> str:
        """Run the vision model on one sheet image and return its text."""
        # Rules ride in the system turn and the sheet alone in the user turn,
        # so the model never echoes the rules into its answer.
        dialogue = [
            {"role": "system", "content": self._READING_BRIEF},
            {"role": "user", "content": [{"type": "image", "image": snapshot}]},
        ]
        prepared = self._tokenizer.apply_chat_template(
            dialogue,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        fed = self._send_to_hardware(prepared)

        try:
            with torch.inference_mode():
                token_ids = self._reader.generate(
                    **fed,
                    max_new_tokens=self._budget,
                    do_sample=False,
                )
                prompt_width = fed["input_ids"].shape[1]
                fresh = token_ids[0, prompt_width:].detach().cpu()
            return self._tokenizer.decode(fresh, skip_special_tokens=True)
        finally:
            del fed
            if "token_ids" in locals():
                del token_ids
            if "fresh" in locals():
                del fresh
            self._release_scratch()

    def _release_scratch(self) -> None:
        """Hand pooled accelerator memory back to the system."""
        if self._hardware == "mps":
            torch.mps.empty_cache()
        elif self._hardware == "cuda":
            torch.cuda.empty_cache()

    def _pick_hardware(self) -> str:
        """Prefer platform accelerators, falling back to CPU."""
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
