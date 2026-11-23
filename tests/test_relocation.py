"""Storage and knowledge records written under another machine's root resolve against current dirs."""
import json
from pathlib import Path

from eegagent.knowledge.chunking import load_all_chunks
from eegagent.worker import files, store, workspaces

WIN_SOURCE = r"E:\EF_Agent\storage\uploads\rec.edf"
WIN_URI = r"E:\EF_Agent\storage\artifacts\art_1234567890ab.png"
WIN_REP = r"E:\EF_Agent\storage\artifacts\art_1234567890ab.svg"


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    root = store.ensure_layout()
    source = root / "uploads" / "rec.edf"
    source.write_bytes(b"eeg")
    for name in ("art_1234567890ab.png", "art_1234567890ab.svg"):
        (root / "artifacts" / name).write_bytes(b"payload")
    return root, source


def test_manifest_and_artifact_records_relocate_windows_paths(tmp_path, monkeypatch):
    root, source = _seed(tmp_path, monkeypatch)
    store.save_manifest({"manifest_id": "sig_relocated", "modality": "eeg", "source_path": WIN_SOURCE, "source_files": [WIN_SOURCE]})
    store.save_artifact({"artifact_id": "art_1234567890ab", "kind": "plot", "uri": WIN_URI, "media_type": "image/png",
                         "representations": {"svg": {"uri": WIN_REP, "media_type": "image/svg+xml"}}})
    manifest = store.load_manifest("sig_relocated")
    assert manifest["source_path"] == str(source)
    assert manifest["source_files"] == [str(source)]
    record = store.load_artifact("art_1234567890ab")
    assert record["uri"] == str(root / "artifacts" / "art_1234567890ab.png")
    assert record["representations"]["svg"]["uri"] == str(root / "artifacts" / "art_1234567890ab.svg")


def test_session_files_collects_relocated_existing_paths(tmp_path, monkeypatch):
    root, source = _seed(tmp_path, monkeypatch)
    sessions = root / "sessions"
    sessions.mkdir(exist_ok=True)
    manifest = {"manifest_id": "sig_relocated", "source_path": WIN_SOURCE, "source_files": [WIN_SOURCE]}
    artifact = {"artifact_id": "art_1234567890ab", "uri": WIN_URI,
                "representations": {"svg": {"uri": WIN_REP, "media_type": "image/svg+xml"}}}
    (sessions / "ses_relocated.json").write_text(json.dumps({"manifests": [manifest], "artifacts": [artifact]}), encoding="utf-8")
    _, paths = files.session_files("ses_relocated")
    assert source in paths
    assert root / "artifacts" / "art_1234567890ab.png" in paths
    assert root / "artifacts" / "art_1234567890ab.svg" in paths


def test_relocate_returns_input_unchanged_when_nothing_matches(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert store.relocate("") == ""
    assert store.relocate(None) is None
    assert store.relocate(r"E:\nowhere\gone.edf") == r"E:\nowhere\gone.edf"


def test_workspace_root_relocates_under_storage(tmp_path, monkeypatch):
    root, _ = _seed(tmp_path, monkeypatch)
    mounted = root / "uploads" / "mounted"
    mounted.mkdir()
    workspaces.save({"workspace_id": "ws_1234567890ab", "name": "old", "root": r"E:\EF_Agent\storage\uploads\mounted",
                     "excluded": [], "included": [], "trash": []})
    assert workspaces.load("ws_1234567890ab")["root"] == str(mounted)
    assert workspaces.list_workspaces()[0]["root"] == str(mounted)


def _write_paper(root: Path, source_path: str) -> Path:
    pdf = root / "papers" / "pdf" / "x.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF")
    doc = root / "papers" / "doc.md"
    doc.write_text("---\ntitle: 论文\ndoc_type: paper\n---\n\n## 方法\n\n滤波器设置。", encoding="utf-8")
    side = {"path": source_path, "sha256": "ab" * 32, "format": "pdf", "name": "x.pdf", "extraction_method": "pymupdf",
            "image_content_extracted": False, "untranscribed_formula_pages": [], "verified_formula_pages": []}
    doc.with_suffix(".source.json").write_text(json.dumps(side), encoding="utf-8")
    return doc


def test_knowledge_sidecar_source_relocates_under_the_knowledge_root(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(storage))
    root = tmp_path / "knowledge"
    _write_paper(root, r"E:\EF_Agent\knowledge\papers\pdf\x.pdf")
    rows = [chunk.row() for chunk in load_all_chunks(root)]
    assert rows and all(row["source_document_sha256"] == "ab" * 32 for row in rows)
    assert (storage / "knowledge_sources" / ("ab" * 32 + ".pdf")).read_bytes() == b"%PDF"


def test_knowledge_sidecar_missing_everywhere_still_yields_rows(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(storage))
    root = tmp_path / "knowledge"
    _write_paper(root, r"E:\gone\papers\pdf\x.pdf")
    rows = [chunk.row() for chunk in load_all_chunks(root)]
    assert rows and all(row["source_document_sha256"] == "ab" * 32 for row in rows)
