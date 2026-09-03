"""Extract text from a scanned PDF using LightOnOCR-2-1B.

Ported verbatim from FinDoc Pypi's `input_module/extractors/ocr_pdf.py`. Each
page is rasterized at 300 DPI, sent through the LightOnOCR vision-language model,
and the model's markdown output for each page is concatenated under a
`## Page N` heading. The model and processor are loaded once per extractor
instance and reused for every page and every document the instance handles.
"""
from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Optional

import fitz
import torch
from PIL import Image
from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

from .table_post_processor import fix_markdown_tables, validate_markdown_tables

DEFAULT_MODEL_ID: str = "lightonai/LightOnOCR-2-1B"
DEFAULT_DPI: int = 300
DEFAULT_MAX_NEW_TOKENS: int = 4096
MIN_DPI: int = 200


class OcrPdfExtractor:
    """OCR a scanned PDF with LightOnOCR-2-1B and emit markdown."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        dpi: int = DEFAULT_DPI,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        device: Optional[str] = None,
    ) -> None:
        """Load the OCR model and processor.

        Raises:
            ValueError: If `dpi` is below `MIN_DPI`.
        """
        if dpi < MIN_DPI:
            raise ValueError(f"DPI must be >= {MIN_DPI} for OCR, got {dpi}")

        self._dpi = dpi
        self._max_new_tokens = max_new_tokens
        self._device = device or self._select_device()
        # bfloat16 has the dynamic range of float32 but half the memory and
        # ~2x the throughput; supported on both MPS (>=2.4) and CUDA.
        self._dtype = torch.bfloat16

        print(f"Loading {model_id} (device={self._device}, dtype={self._dtype})...", flush=True)
        load_started = time.perf_counter()
        self._model = LightOnOcrForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=self._dtype
        ).to(self._device)
        self._processor = LightOnOcrProcessor.from_pretrained(model_id)
        print(f"Model loaded in {time.perf_counter() - load_started:.1f}s", flush=True)

    def extract(self, source: Path, output_dir: Path) -> Path:
        """Write the OCR'd markdown to `output_dir/{stem}.md`."""
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source.stem}.md"

        markdown = self._render_markdown(source)
        output_path.write_text(markdown, encoding="utf-8")
        return output_path

    def _render_markdown(self, source: Path) -> str:
        """OCR every page and concatenate the results as markdown."""
        sections: list[str] = []
        with fitz.open(source) as doc:
            total = doc.page_count
            for page_index, page in enumerate(doc, start=1):
                print(f"  [ocr] page {page_index}/{total} starting...", flush=True)
                started = time.perf_counter()
                page_markdown = self.ocr_page(page).strip()
                sections.append(f"## Page {page_index}\n\n{page_markdown}")
                print(f"  [ocr] page {page_index}/{total} done in {time.perf_counter() - started:.1f}s", flush=True)
        return "\n\n".join(sections) + "\n"

    def ocr_page(self, page: "fitz.Page") -> str:
        """Rasterize, OCR, and post-process a single PDF page.

        Public so other extractors (e.g. HybridPdfExtractor) can reuse the
        same loaded model rather than instantiating a second copy of the weights.
        """
        image = self._rasterize(page)
        raw = self._ocr_image(image)
        fixed = fix_markdown_tables(raw)
        issues = validate_markdown_tables(fixed)
        for issue in issues:
            print(f"  [table-validate] {issue.location}: {issue.message}", flush=True)
        return fixed

    def _rasterize(self, page: "fitz.Page") -> Image.Image:
        """Render a PDF page to a PIL image at the configured DPI."""
        pixmap = page.get_pixmap(dpi=self._dpi)
        return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")

    # Instruction prepended to every OCR call. Tables are emitted as HTML so
    # <td> boundaries are unambiguous and multi-level headers can use colspan
    # without any flattening heuristic.
    _OCR_INSTRUCTION: str = (
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

    def _ocr_image(self, image: Image.Image) -> str:
        """Run LightOnOCR on a single page image and return text."""
        # System role carries the extraction rules; the user turn contains only
        # the image, so the model doesn't echo the rule text in its output.
        conversation = [
            {"role": "system", "content": self._OCR_INSTRUCTION},
            {"role": "user", "content": [{"type": "image", "image": image}]},
        ]
        inputs = self._processor.apply_chat_template(
            conversation, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        )
        inputs = {
            key: (
                value.to(device=self._device, dtype=self._dtype)
                if value.is_floating_point() else value.to(self._device)
            )
            for key, value in inputs.items()
        }

        try:
            with torch.inference_mode():
                output_ids = self._model.generate(
                    **inputs, max_new_tokens=self._max_new_tokens, do_sample=False,
                )
                prompt_len = inputs["input_ids"].shape[1]
                generated_ids = output_ids[0, prompt_len:].detach().cpu()

            return self._processor.decode(generated_ids, skip_special_tokens=True)
        finally:
            del inputs
            if "output_ids" in locals():
                del output_ids
            if "generated_ids" in locals():
                del generated_ids
            self._free_device_cache()

    def _free_device_cache(self) -> None:
        """Return any pooled device memory to the system."""
        if self._device == "mps":
            torch.mps.empty_cache()
        elif self._device == "cuda":
            torch.cuda.empty_cache()

    def _select_device(self) -> str:
        """Pick MPS, CUDA, or CPU based on what is available."""
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
