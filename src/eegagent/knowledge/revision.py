"""A completed ingestion publishes one lightweight revision for the running index."""
from pathlib import Path


def revision(root: Path) -> str:
    marker = root / ".index-revision"
    return str(marker.stat().st_mtime_ns) if marker.exists() else "0"


def mark_updated(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".index-revision").touch()
    return revision(root)
