"""Input format detection for HAP reports."""
from __future__ import annotations

from pathlib import Path


class UnsupportedReportError(ValueError):
    """Raised when a report format is not supported by the import slice."""


class FileDetector:
    SUPPORTED = {".rtf", ".txt", ".pdf"}

    def detect(self, file_path: str | Path) -> str:
        suffix = Path(file_path).suffix.lower()
        if suffix not in self.SUPPORTED:
            supported = ", ".join(sorted(self.SUPPORTED))
            raise UnsupportedReportError(
                f"Unsupported HAP report format {suffix or '<none>'}; supported: {supported}."
            )
        return suffix[1:]
