"""Storage layout shared with the gateway (single machine in S1/S2).

STORAGE_DIR/
├── uploads/     raw files
├── manifests/   <manifest_id>.json
├── artifacts/   <artifact_id>.json (record) + <artifact_id>.<ext> (payload; JSON payloads are <artifact_id>.data.json)
└── weights/
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_SUBDIRS = ("uploads", "manifests", "artifacts", "weights", "scratch")


def storage_dir() -> Path:
    return Path(os.environ.get("EEGAGENT_STORAGE_DIR", Path(__file__).resolve().parents[3] / "storage"))


def ensure_layout() -> Path:
    root = storage_dir()
    for sub in ALLOWED_SUBDIRS:
        directory = root / sub
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
    return root


def resolve_inside_storage(path: str | Path) -> Path:
    """Reject any path outside the storage whitelist. This is the worker's sandbox rule."""
    root = ensure_layout().resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"path outside storage dir: {candidate}") from exc
    return candidate


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- manifests

def save_manifest(manifest: dict) -> Path:
    root = ensure_layout()
    out = root / "manifests" / f"{manifest['manifest_id']}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    from eegagent.worker.files import grant_files
    grant_files([out, manifest.get("source_path"), manifest.get("vision_path"), *manifest.get("source_files", [])])
    return out


def load_manifest(manifest_id: str) -> dict:
    root = ensure_layout()
    path = root / "manifests" / f"{manifest_id}.json"
    from eegagent.worker.files import scoped_metadata
    scoped_metadata(path)
    if not path.exists():
        raise FileNotFoundError(f"unknown manifest_id: {manifest_id}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- artifacts

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_artifact_id(tool: str, version: str, inputs: dict) -> str:
    """Content-addressed id: same tool, version and canonical inputs → same id (dedupes reruns)."""
    payload = json.dumps({"tool": tool, "version": version, "inputs": inputs}, sort_keys=True, ensure_ascii=False, default=str)
    return "art_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def artifact_payload_path(artifact_id: str, ext: str) -> Path:
    ext = ext.lstrip(".")
    # <id>.json is the record; a JSON payload must not overwrite it
    name = f"{artifact_id}.data.json" if ext == "json" else f"{artifact_id}.{ext}"
    return ensure_layout() / "artifacts" / name


def save_artifact(record: dict) -> dict:
    """Persist the artifact record (contracts/artifact.schema.json). The payload is written by the caller."""
    record.setdefault("provenance", {}).setdefault("created_at", now_iso())
    record["provenance"].setdefault("params", {})
    record["provenance"].setdefault("steps", [])
    out = ensure_layout() / "artifacts" / f"{record['artifact_id']}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    from eegagent.worker.files import grant_files
    grant_files([out, record["uri"], *(value["uri"] for value in record.get("representations", {}).values())])
    return record


def load_artifact(artifact_id: str) -> dict:
    path = ensure_layout() / "artifacts" / f"{artifact_id}.json"
    from eegagent.worker.files import scoped_metadata
    scoped_metadata(path)
    if not path.exists():
        raise FileNotFoundError(f"unknown artifact_id: {artifact_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_exists(artifact_id: str) -> bool:
    return (ensure_layout() / "artifacts" / f"{artifact_id}.json").exists()


def _json_default(o):
    try:
        import numpy as np

        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover
        pass
    return str(o)
