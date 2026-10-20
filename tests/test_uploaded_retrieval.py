import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pymupdf
import pytest

from eegagent.knowledge.chunking import MAX_CHUNK_CHARS, _split_long
from eegagent.knowledge.documents import document_dir, get_document_chunk, load_document, search_documents
from eegagent.worker.attachments import register_attachment, read_document
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.signals import load_signal
from eegagent.worker.tools import run_tool


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    return tmp_path


def submit_document(storage, session_id, manifest):
    snapshots = storage / "sessions"
    snapshots.mkdir(exist_ok=True)
    (snapshots / f"{session_id}.json").write_text(json.dumps({"manifests": [manifest]}), encoding="utf-8")


def paper(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((50, 60), "Methods\nThe recording rate is 128 Hz.\nTwo frontal electrodes are used.")
        pdf.new_page().insert_text((50, 60), "Results\nThe unique alpha peak is 10 Hz.\nNo emotion classifier was executed.")
        pdf.save(path)


def test_uploaded_pdf_is_retrievable_with_original_page_and_session_scope(storage):
    path = storage / "uploads/ses_111111/paper.pdf"
    paper(path)
    manifest = register_attachment(str(path), "ses_111111")["manifest"]
    submit_document(storage, "ses_111111", manifest)
    assert manifest["document_index"]["status"] == "ready"
    result = run_tool("search_knowledge", {"query": "unique alpha peak", "scope": "session", "_session_id": "ses_111111"})
    assert result["chunks"]
    top = result["chunks"][0]
    assert top["page"] == 2 and top["title"] == "paper.pdf" and "10 Hz" in top["text"]
    assert "第 2 页" in top["source"]
    assert top["evidence_role"] == "document_claim" and top["source_kind"] == "user_upload"
    assert top["extraction_method"] == "native_text" and len(top["content_sha256"]) == 64
    assert not run_tool("search_knowledge", {"query": "unique alpha peak", "scope": "session", "_session_id": "ses_222222"})["chunks"]
    assert get_document_chunk(top["chunk_id"], "ses_111111") is not None
    assert get_document_chunk(top["chunk_id"], "ses_222222") is None
    result = read_document(manifest["manifest_id"], page=2, limit=1)
    assert "10 Hz" in result["pages"][0]["text"]
    (citation,) = result["chunks"]
    assert citation["page"] == 2 and citation["text"] == result["pages"][0]["text"]
    assert get_document_chunk(citation["chunk_id"], "ses_111111") == citation
    assert get_document_chunk(citation["chunk_id"], "ses_222222") is None


def test_scanned_page_is_reported_without_invented_text(storage):
    path = storage / "uploads/ses_111111/scan.pdf"
    path.parent.mkdir(parents=True)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20))
    pixmap.clear_with(255)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_image(pymupdf.Rect(50, 50, 250, 250), stream=pixmap.tobytes("png"))
        pdf.save(path)
    manifest = register_attachment(str(path), "ses_111111")["manifest"]
    submit_document(storage, "ses_111111", manifest)
    assert manifest["document_index"]["status"] == "ocr_required"
    assert manifest["document_index"]["n_chunks"] == 0
    assert manifest["document_index"]["pages_with_images"] == [1]
    assert not manifest["document_index"]["image_content_extracted"]
    result = read_document(manifest["manifest_id"])
    assert result["pages"][0]["needs_ocr"] and result["pages"][0]["text"] == ""
    assert result["chunks"] == []
    assert "read_chunks" not in load_document(manifest["manifest_id"], "ses_111111")


def test_pdf_table_remains_a_cited_table(storage):
    path = storage / "uploads/ses_111111/table.pdf"
    path.parent.mkdir(parents=True)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for y in (100, 130, 160):
            page.draw_line((50, y), (250, y))
        for x in (50, 150, 250):
            page.draw_line((x, 100), (x, 160))
        for x, y, text in ((60, 120, "Channel"), (160, 120, "Rate"), (60, 150, "Fp1"), (160, 150, "128 Hz")):
            page.insert_text((x, y), text)
        pdf.save(path)
    manifest = register_attachment(str(path), "ses_111111")["manifest"]
    submit_document(storage, "ses_111111", manifest)
    chunks = load_document(manifest["manifest_id"], "ses_111111")["chunks"]
    tables = [chunk for chunk in chunks if chunk["kind"] == "table"]
    assert tables and "Channel" in tables[0]["text"] and "128 Hz" in tables[0]["text"]
    assert tables[0]["page"] == 1


def test_single_long_paragraph_respects_chunk_budget():
    text = "alpha rhythm observation " * 900
    chunks = _split_long(text)
    assert len(chunks) > 1 and all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_long_document_page_can_be_read_without_discarding_or_skipping_text(storage):
    path = storage / "uploads/ses_111111/long.md"
    path.parent.mkdir(parents=True)
    text = "脑电研究正文。" * 1000
    path.write_text(text, encoding="utf-8")
    manifest = register_attachment(str(path), "ses_111111")["manifest"]
    submit_document(storage, "ses_111111", manifest)
    first_read = read_document(manifest["manifest_id"], limit=1)
    first = first_read["pages"][0]
    second_read = read_document(manifest["manifest_id"], limit=1, offset=first["next_offset"])
    second = second_read["pages"][0]
    assert first["has_more"] and second["start_offset"] == 2000
    assert first["text"] + second["text"] == text[:4000]
    assert len(first["text"].encode("utf-8")) < 12000
    for result in (first_read, second_read):
        (chunk,) = result["chunks"]
        page = result["pages"][0]
        assert chunk["text"] == text[chunk["start_offset"]:chunk["next_offset"]] == page["text"]
        assert chunk["content_sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
        assert get_document_chunk(chunk["chunk_id"], "ses_111111") == chunk
        assert get_document_chunk(chunk["chunk_id"], "ses_222222") is None
    again = read_document(manifest["manifest_id"], limit=1)
    assert again["chunks"] == first_read["chunks"]
    parsed = load_document(manifest["manifest_id"], "ses_111111")
    assert set(parsed["read_chunks"]) == {chunk["chunk_id"] for result in (first_read, second_read) for chunk in result["chunks"]}
    assert not (set(parsed["read_chunks"]) & {row["chunk_id"] for row in search_documents("脑电研究", "ses_111111", 10)})


def test_pdf_offset_citation_contains_only_the_actual_page_window(storage):
    path = storage / "uploads/ses_111111/long.pdf"
    path.parent.mkdir(parents=True)
    with pymupdf.open() as pdf:
        first = pdf.new_page(width=595, height=2200)
        first.insert_text((50, 60), "\n".join(f"Observation {n:03}: measured alpha rhythm, not an emotion label." for n in range(150)), fontsize=8)
        pdf.new_page().insert_text((50, 60), "This second page has not been read.")
        pdf.save(path)
    manifest = register_attachment(str(path), "ses_111111")["manifest"]
    submit_document(storage, "ses_111111", manifest)
    parsed = load_document(manifest["manifest_id"], "ses_111111")
    result = read_document(manifest["manifest_id"], page=1, limit=1, offset=1200)
    (chunk,) = result["chunks"]
    full_page = parsed["pages"][0]["text"]
    assert len(full_page) > 3200
    assert chunk["text"] == full_page[1200:3200]
    assert chunk["start_offset"] == 1200 and chunk["next_offset"] == 3200
    assert chunk["page"] == 1 and chunk["extraction_method"] == "native_text"
    assert get_document_chunk(chunk["chunk_id"], "ses_111111") == chunk
    assert all(row["page"] == 1 for row in result["chunks"])
    assert "second page" not in chunk["text"]


def test_branched_session_reads_and_resolves_its_own_document_windows(storage):
    path = storage / "uploads/ses_111111/long.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Methods\n" + "Measured alpha rhythm is a spectral observation. " * 150, encoding="utf-8")
    manifest = register_attachment(str(path), "ses_111111")["manifest"]
    submit_document(storage, "ses_111111", manifest)
    parent = run_tool("read_document", {"manifest_id": manifest["manifest_id"], "limit": 1, "_session_id": "ses_111111"})
    shutil.copytree(document_dir("ses_111111"), document_dir("ses_child"))
    submit_document(storage, "ses_child", manifest)
    child = run_tool("read_document", {"manifest_id": manifest["manifest_id"], "limit": 1,
                                        "offset": 2000, "_session_id": "ses_child"})
    (child_chunk,) = child["chunks"]
    assert get_document_chunk(child_chunk["chunk_id"], "ses_child") == child_chunk
    assert get_document_chunk(child_chunk["chunk_id"], "ses_111111") is None
    assert get_document_chunk(parent["chunks"][0]["chunk_id"], "ses_child") == parent["chunks"][0]
    assert set(load_document(manifest["manifest_id"], "ses_111111")["read_chunks"]) == {parent["chunks"][0]["chunk_id"]}
    with pytest.raises(PermissionError):
        run_tool("read_document", {"manifest_id": manifest["manifest_id"], "_session_id": "ses_unrelated"})


def test_confirmed_eeg_metadata_is_used_without_mutating_original(storage):
    path = storage / "uploads/record.npy"
    path.parent.mkdir(parents=True)
    data = np.stack([np.sin(np.arange(256) * 0.5), np.cos(np.arange(256) * 0.5)]) * 1e-5
    np.save(path, data)
    original = inspect_record(str(path))
    assert original["sample_rate_hz"] is None
    updated = update_record_metadata(original["manifest_id"], 128, ["Fp1", "Fp2"], "V")["manifest"]
    assert updated["manifest_id"] != original["manifest_id"]
    assert updated["duration_s"] == 2 and updated["unit"]["source"] == "user_confirmed"
    values, rate, names = load_signal(updated["manifest_id"])
    np.testing.assert_allclose(values, data * 1e6)
    assert rate == 128 and names == ["Fp1", "Fp2"]
    assert original["sample_rate_hz"] is None
