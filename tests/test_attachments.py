from __future__ import annotations

import pymupdf
import pytest

from eegagent.worker import attachments, store, tools


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    return store.ensure_layout() / "uploads" / "ses_test"


def test_markdown_and_pdf_are_session_documents(uploads):
    uploads.mkdir(parents=True)
    note = uploads / "protocol.md"
    note.write_text("# 实验说明\n闭眼 10 秒", encoding="utf-8")
    registered = attachments.register_attachment(str(note))["manifest"]
    assert registered["modality"] == "document"
    assert registered["page_count"] == 1
    assert "闭眼 10 秒" in attachments.read_document(registered["manifest_id"])["pages"][0]["text"]

    pdf_path = uploads / "paper.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Alpha protocol")
    pdf.save(pdf_path)
    pdf.close()
    registered = attachments.register_attachment(str(pdf_path))["manifest"]
    assert registered["page_count"] == 1
    assert "Alpha protocol" in attachments.read_document(registered["manifest_id"])["pages"][0]["text"]


def test_video_is_registered_without_an_analysis_executor(uploads, monkeypatch):
    uploads.mkdir(parents=True)
    video = uploads / "face.mp4"
    video.write_bytes(b"test video")
    monkeypatch.setattr(attachments, "_video_metadata", lambda path: ({"duration_s": 3.0, "fps": 25.0, "width": 640, "height": 360, "audio": False}, []))
    registered = attachments.register_attachment(str(video))["manifest"]
    assert registered["modality"] == "video"
    assert registered["duration_s"] == 3.0
    assert registered["role"]["source"] == "unknown"
    assert registered["warnings"] == []


def test_cleaning_schema_reflects_installed_methods(monkeypatch):
    definitions = [item for item in tools.TOOL_DEFINITIONS if item["name"] == "clean_artifacts"]
    assert bool(definitions) == bool(tools.CLEANING_METHODS)
    if definitions:
        assert definitions[0]["input_schema"]["properties"]["method"]["enum"] == tools.CLEANING_METHODS
    monkeypatch.setattr(tools, "CLEANING_METHODS", [])
    monkeypatch.setattr(tools, "CLEANING_MISSING", {"bad_channels": ["pyprep"]})
    with pytest.raises(ImportError, match="pyprep"):
        tools._run_clean({"method": "bad_channels", "source_id": "sig_x"})
