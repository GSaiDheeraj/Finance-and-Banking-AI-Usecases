"""
File storage management for Fraud Detection Agent.

This module handles storage of analysis files with timestamped folders to support
multiple analyses for the same client. Currently implements local file storage,
designed to be easily extended to S3 or ADLS.
"""

import os
import shutil
import json
from datetime import datetime
from typing import Optional, List
from pathlib import Path


class StorageManager:
    """Manages file storage for analysis results and documents."""
    
    def __init__(self, base_storage_dir: str = "analysis_storage"):
        """
        Initialize storage manager.
        
        Args:
            base_storage_dir: Base directory for storing analysis files
        """
        self.base_storage_dir = Path(base_storage_dir)
        self.base_storage_dir.mkdir(parents=True, exist_ok=True)
    
    def get_client_storage_path(self, client_name: str, timestamp: Optional[datetime] = None) -> Path:
        """
        Get storage path for a client analysis with timestamp.
        
        Args:
            client_name: Name of the client
            timestamp: Optional timestamp for the analysis (defaults to current time)
            
        Returns:
            Path object for the client's timestamped storage folder
        """
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        # Sanitize client name for filesystem
        safe_client_name = self._sanitize_filename(client_name)
        
        # Create timestamped folder name
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
        folder_name = f"{safe_client_name}_{timestamp_str}"
        
        return self.base_storage_dir / folder_name
    
    def save_analysis_files(
        self, 
        client_name: str, 
        files: List[tuple], 
        metadata: dict,
        analysis_result: Optional[dict] = None,
        timestamp: Optional[datetime] = None
    ) -> str:
        """
        Save analysis files to timestamped client folder.
        
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
        
        # Save files (if any)
        for filename, content in files:
            file_path = storage_path / filename
            if isinstance(content, str):
                file_path.write_text(content)
            elif isinstance(content, bytes):
                file_path.write_bytes(content)
            else:
                # Assume it's a file-like object
                with open(file_path, 'wb') as f:
                    shutil.copyfileobj(content, f)
        
        # Always save metadata
        metadata_path = storage_path / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str))
        
        # Save complete analysis result if provided
        if analysis_result:
            result_path = storage_path / "analysis_result.json"
            result_path.write_text(json.dumps(analysis_result, indent=2, default=str))
        
        # Return relative path from base storage dir
        return str(storage_path.relative_to(self.base_storage_dir))
    
    def get_client_analyses(self, client_name: str) -> List[dict]:
        """
        Get all historical analyses for a client.
        
        Args:
            client_name: Name of the client
            
        Returns:
            List of analysis info dictionaries with path, timestamp, etc.
        """
        safe_client_name = self._sanitize_filename(client_name)
        analyses = []
        
        if not self.base_storage_dir.exists():
            return analyses
        
        for folder in self.base_storage_dir.iterdir():
            if folder.is_dir() and folder.name.startswith(safe_client_name + "_"):
                # Extract timestamp from folder name
                try:
                    timestamp_str = folder.name.replace(f"{safe_client_name}_", "")
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                    
                    # Check if metadata exists
                    metadata_path = folder / "metadata.json"
                    metadata = {}
                    if metadata_path.exists():
                        metadata = json.loads(metadata_path.read_text())
                    
                    analyses.append({
                        "path": str(folder.relative_to(self.base_storage_dir)),
                        "timestamp": timestamp.isoformat(),
                        "metadata": metadata
                    })
                except (ValueError, IndexError):
                    # Skip folders that don't match the expected format
                    continue
        
        # Sort by timestamp, newest first
        analyses.sort(key=lambda x: x["timestamp"], reverse=True)
        return analyses
    
    def get_latest_analysis(self, client_name: str) -> Optional[dict]:
        """
        Get the most recent analysis for a client.
        
        Args:
            client_name: Name of the client
            
        Returns:
            Latest analysis info or None if no analyses exist
        """
        analyses = self.get_client_analyses(client_name)
        return analyses[0] if analyses else None
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename for filesystem compatibility.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Replace characters that are problematic in filenames
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Remove leading/trailing spaces and dots
        filename = filename.strip('. ')
        
        # Limit length
        if len(filename) > 100:
            filename = filename[:100]
        
        return filename or "unnamed"
    
    def delete_analysis(self, client_name: str, timestamp: datetime) -> bool:
        """
        Delete a specific analysis folder.
        
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


# Global storage manager instance
_storage_manager = None


def get_storage_manager() -> StorageManager:
    """Get the global storage manager instance."""
    global _storage_manager
    if _storage_manager is None:
        _storage_manager = StorageManager()
    return _storage_manager