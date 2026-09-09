"""Artifact storage for uploaded documents and pipeline intermediates.

Writes to S3 (best-effort) when `Config.s3_bucket` is set, alongside an
always-written local copy under the same relative layout — this class is the
only place that knows the "Financial document extraction/<document_id>/…"
convention, so callers never branch on which backend is active.

The local copy is authoritative for processing: `input_pipeline`/PyMuPDF read
a filesystem path, never S3 (see the plan's Design §4 — teaching four
unrelated modules to stream from S3 buys nothing for a POC where S3 is about
the durable archive, not the read path). S3, when configured, mirrors both
the PDF and the per-iteration JSON artifacts for that archive.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .config import get_config

logger = logging.getLogger(__name__)

_S3_KEY_ROOT = "Financial document extraction"


class DocumentStorage:
    """Writes one document's PDF and per-iteration JSON artifacts."""

    def __init__(self) -> None:
        config = get_config()
        self._local_root = Path(config.document_storage_dir)
        self._bucket = config.s3_bucket
        self._s3_client = self._build_s3_client() if self._bucket else None

    def _build_s3_client(self):
        try:
            import boto3
            return boto3.client("s3", region_name=get_config().s3_region)
        except Exception:
            logger.exception(
                "S3 configured (bucket=%s) but the boto3 client could not be built; "
                "falling back to local storage only", self._bucket,
            )
            return None

    def save_pdf(self, document_id: str, filename: str, content: bytes) -> tuple[str, Optional[str]]:
        """Write the PDF locally and best-effort mirror it to S3.

        Returns `(local_path, s3_uri)` — `local_path` is what `Document.storage_uri`
        keeps pointing at (the pipeline's read path, unchanged); `s3_uri` is
        `None` unless S3 is configured AND the mirror write actually succeeded.
        """
        local_path = self._local_path(document_id, filename)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        mirrored = self._mirror_to_s3(document_id, filename, content, "application/pdf")
        return str(local_path), (self._s3_uri(document_id, filename) if mirrored else None)

    def save_json(self, document_id: str, relative_path: str, data: Any) -> None:
        """Write one JSON artifact (an iteration's extraction/validation report)
        locally and best-effort mirror it to S3."""
        payload = json.dumps(data, indent=2, default=str).encode("utf-8")
        local_path = self._local_path(document_id, relative_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(payload)
        self._mirror_to_s3(document_id, relative_path, payload, "application/json")

    def _local_path(self, document_id: str, relative_path: str) -> Path:
        return self._local_root / document_id / relative_path

    def _s3_key(self, document_id: str, relative_path: str) -> str:
        return f"{_S3_KEY_ROOT}/{document_id}/{relative_path}"

    def _s3_uri(self, document_id: str, relative_path: str) -> str:
        return f"s3://{self._bucket}/{self._s3_key(document_id, relative_path)}"

    def _mirror_to_s3(self, document_id: str, relative_path: str, payload: bytes, content_type: str) -> bool:
        if self._s3_client is None:
            return False
        try:
            self._s3_client.put_object(
                Bucket=self._bucket,
                Key=self._s3_key(document_id, relative_path),
                Body=payload,
                ContentType=content_type,
            )
            return True
        except Exception:
            logger.exception(
                "failed to mirror %s to S3 for document %s; local copy still written",
                relative_path, document_id,
            )
            return False
