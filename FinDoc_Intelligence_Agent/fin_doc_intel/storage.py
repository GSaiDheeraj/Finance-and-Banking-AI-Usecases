"""Durable keepsakes for each uploaded filing and its intermediate outputs.

Every document lands on local disk first — the readers downstream only know
how to open filesystem paths. When an object-store bucket is configured, the
same bytes are additionally copied there as an archive; a failed copy never
blocks the run because the local file is already safely written. This module
owns the "<document_id>/<filename>" layout so no caller branches on backends.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    import boto3
except ImportError:
    boto3 = None

from .config import get_config

logger = logging.getLogger(__name__)

_ARCHIVE_PREFIX = "Financial document extraction"
_PDF_MEDIA_TYPE = "application/pdf"
_JSON_MEDIA_TYPE = "application/json"


class DocumentStorage:
    """Persists one filing's PDF plus its per-pass JSON reports."""

    def __init__(self) -> None:
        settings = get_config()
        self._disk_base = Path(settings.document_storage_dir)
        self._archive_bucket = settings.s3_bucket
        self._archive_client = self._connect_archive() if self._archive_bucket else None

    def _connect_archive(self):
        if boto3 is None:
            logger.exception(
                "Archive bucket %s is set but boto3 is missing; continuing local-only",
                self._archive_bucket,
            )
            return None
        try:
            return boto3.client("s3", region_name=get_config().s3_region)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Archive bucket %s is unreachable; continuing local-only",
                self._archive_bucket,
            )
            return None

    def _disk_path(self, document_id: str, relative_name: str) -> Path:
        return self._disk_base / document_id / relative_name

    def _archive_key(self, document_id: str, relative_name: str) -> str:
        return f"{_ARCHIVE_PREFIX}/{document_id}/{relative_name}"

    def _archive_location(self, document_id: str, relative_name: str) -> str:
        return f"s3://{self._archive_bucket}/{self._archive_key(document_id, relative_name)}"

    def _write_disk(self, document_id: str, relative_name: str, raw: bytes) -> Path:
        target = self._disk_path(document_id, relative_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def save_pdf(self, document_id: str, filename: str, content: bytes) -> tuple[str, str | None]:
        """Store the PDF locally and mirror it to the archive on a best effort basis.

        Returns the local path (the pipeline keeps reading that) together with
        the archive URI, which is None unless the copy genuinely succeeded.
        """
        if not document_id:
            raise ValueError("document_id must be a non-empty string.")
        if not filename:
            raise ValueError("filename must be a non-empty string.")
        if not content:
            raise ValueError("content must be non-empty bytes.")
        disk_file = self._write_disk(document_id, filename, content)
        copied = self._copy_to_archive(document_id, filename, content, _PDF_MEDIA_TYPE)
        archive_uri = self._archive_location(document_id, filename) if copied else None
        return str(disk_file), archive_uri

    def save_json(self, document_id: str, relative_name: str, report: Any) -> None:
        """Store one JSON report locally and mirror it to the archive if possible."""
        if not document_id:
            raise ValueError("document_id must be a non-empty string.")
        if not relative_name:
            raise ValueError("relative_name must be a non-empty string.")
        encoded = json.dumps(report, indent=2, default=str).encode("utf-8")
        self._write_disk(document_id, relative_name, encoded)
        self._copy_to_archive(document_id, relative_name, encoded, _JSON_MEDIA_TYPE)

    def _copy_to_archive(
        self, document_id: str, relative_name: str, raw: bytes, media_type: str
    ) -> bool:
        if self._archive_client is None:
            return False
        try:
            self._archive_client.put_object(
                Bucket=self._archive_bucket,
                Key=self._archive_key(document_id, relative_name),
                Body=raw,
                ContentType=media_type,
            )
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "archive copy failed for %s of document %s; local file already kept",
                relative_name,
                document_id,
            )
            return False
