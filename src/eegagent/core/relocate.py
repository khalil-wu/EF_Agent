"""Relocate paths recorded under another machine's root onto a current root."""
from pathlib import Path


def relocate_suffix(path, root) -> str | None:
    """Return ``<root>/<suffix of path>`` for the longest existing suffix of 2+ segments, else None."""
    segments = [segment for segment in str(path).replace("\\", "/").split("/") if segment]
    for i in range(1, len(segments) - 1):
        candidate = Path(root).joinpath(*segments[i:])
        if candidate.exists():
            return str(candidate)
    return None
