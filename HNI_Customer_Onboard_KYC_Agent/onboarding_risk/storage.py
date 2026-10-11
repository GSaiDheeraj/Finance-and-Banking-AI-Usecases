"""File storage management for Fraud Detection Agent.

This module handles storage of analysis files with timestamped folders to support
multiple analyses for the same client. Currently implements local file storage,
designed to be easily extended to S3 or ADLS.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
_METADATA_FILENAME = "metadata.json"
_RESULT_FILENAME = "analysis_result.json"
_MAX_FILENAME_LENGTH = 100
_FALLBACK_FILENAME = "unnamed"
_INVALID_FILENAME_CHARS = '<>:"/\\|?*'

UploadedFile = tuple[str, str | bytes | BinaryIO]


class StorageManager:
    """Manages file storage for analysis results and documents."""

    def __init__(self, base_storage_dir: str = "analysis_storage"):
        """Initialize storage manager.

        Args:
            base_storage_dir: Base directory for storing analysis files
        """
        self.base_storage_dir = Path(base_storage_dir)
        self.base_storage_dir.mkdir(parents=True, exist_ok=True)

    def get_client_storage_path(self, client_name: str, timestamp: datetime | None = None) -> Path:
        """Get storage path for a client analysis with timestamp.

        Args:
            client_name: Name of the client
            timestamp: Optional timestamp for the analysis (defaults to current time)

        Returns:
            Path object for the client's timestamped storage folder
        """
        effective_timestamp = timestamp or datetime.now(UTC)
        safe_client_name = self._sanitize_filename(client_name)
        timestamp_str = effective_timestamp.strftime(_TIMESTAMP_FORMAT)
        return self.base_storage_dir / f"{safe_client_name}_{timestamp_str}"

    def save_analysis_files(
        self,
        client_name: str,
        files: list[UploadedFile],
        metadata: dict,
        analysis_result: dict | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        """Save analysis files to timestamped client folder.

        Args:
            client_name: Name of the client
            files: List of (filename, content) tuples (can be empty)
            metadata: Dictionary of metadata to save as JSON
            analysis_result: Optional complete analysis result to save as JSON
            timestamp: Optional timestamp for the analysis

        Returns:
            Relative path to the storage folder
        """
        storage_path = self.get_client_storage_path(client_name, timestamp)
        storage_path.mkdir(parents=True, exist_ok=True)

        for filename, content in files:
            self._write_uploaded_file(storage_path / filename, content)

        (storage_path / _METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, default=str))
        if analysis_result:
            (storage_path / _RESULT_FILENAME).write_text(
                json.dumps(analysis_result, indent=2, default=str)
            )
        return str(storage_path.relative_to(self.base_storage_dir))

    def _write_uploaded_file(self, file_path: Path, content: str | bytes | BinaryIO) -> None:
        if isinstance(content, str):
            file_path.write_text(content)
        elif isinstance(content, bytes):
            file_path.write_bytes(content)
        else:
            with open(file_path, "wb") as binary_file:
                shutil.copyfileobj(content, binary_file)

    def get_client_analyses(self, client_name: str) -> list[dict]:
        """Get all historical analyses for a client.

        Args:
            client_name: Name of the client

        Returns:
            List of analysis info dictionaries with path, timestamp, etc.
        """
        safe_client_name = self._sanitize_filename(client_name)
        if not self.base_storage_dir.exists():
            return []

        analyses = [
            analysis
            for folder in self.base_storage_dir.iterdir()
            if folder.is_dir() and folder.name.startswith(safe_client_name + "_")
            for analysis in [self._read_analysis_folder(folder, safe_client_name)]
            if analysis is not None
        ]
        analyses.sort(key=lambda item: item["timestamp"], reverse=True)
        return analyses

    def _read_analysis_folder(self, folder: Path, safe_client_name: str) -> dict | None:
        try:
            timestamp_str = folder.name.replace(f"{safe_client_name}_", "")
            timestamp = datetime.strptime(timestamp_str, _TIMESTAMP_FORMAT)
        except ValueError:
            return None
        metadata_path = folder / _METADATA_FILENAME
        metadata = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text())
            except (OSError, json.JSONDecodeError):
                metadata = {}
        return {
            "path": str(folder.relative_to(self.base_storage_dir)),
            "timestamp": timestamp.isoformat(),
            "metadata": metadata,
        }

    def get_latest_analysis(self, client_name: str) -> dict | None:
        """Get the most recent analysis for a client.

        Args:
            client_name: Name of the client

        Returns:
            Latest analysis info or None if no analyses exist
        """
        analyses = self.get_client_analyses(client_name)
        if not analyses:
            return None
        return analyses[0]

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for filesystem compatibility.

        Args:
            filename: Original filename

        Returns:
            Sanitized filename
        """
        for invalid_char in _INVALID_FILENAME_CHARS:
            filename = filename.replace(invalid_char, "_")
        filename = filename.strip(". ")
        if len(filename) > _MAX_FILENAME_LENGTH:
            filename = filename[:_MAX_FILENAME_LENGTH]
        return filename or _FALLBACK_FILENAME

    def delete_analysis(self, client_name: str, timestamp: datetime) -> bool:
        """Delete a specific analysis folder.

        Args:
            client_name: Name of the client
            timestamp: Timestamp of the analysis to delete

        Returns:
            True if deletion was successful, False otherwise
        """
        storage_path = self.get_client_storage_path(client_name, timestamp)
        if storage_path.exists() and storage_path.is_dir():
            shutil.rmtree(storage_path)
            return True
        return False


_storage_manager: StorageManager | None = None


def get_storage_manager() -> StorageManager:
    """Get the global storage manager instance."""
    global _storage_manager
    if _storage_manager is None:
        _storage_manager = StorageManager()
    return _storage_manager
