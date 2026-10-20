"""Uploads are routed by content: signal tables become EEG records, labels, events and notes stay documents."""

from __future__ import annotations

import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from eegagent.worker import attachments, signals, store
from eegagent.worker.text import classify_text_table
from eegagent.worker.tools import run_tool


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    directory = store.ensure_layout() / "uploads" / "ses_upload"
    directory.mkdir(parents=True)
    return directory


def signal_csv(path, encoding="utf-8", comment="# 被试 02 静息记录，单位 微伏", extra_column=None):
    sr, names = 250, ["Fp1", "Fp2", "O1", "O2"]
    t = np.arange(sr * 4) / sr
    x = np.stack([np.sin(2 * np.pi * 10 * t) * (i + 1) for i in range(len(names))])
    header = ["time", *names, *([extra_column] if extra_column else [])]
    rows = [",".join([f"{t[i]:.4f}", *(f"{v:.3f}" for v in x[:, i]), *(["0"] if extra_column else [])]) for i in range(len(t))]
    path.write_bytes(("\n".join([comment, ",".join(header), *rows]) + "\n").encode(encoding))
    return path


def upload(path, **form):
    return run_tool("register_upload", {"path": str(path), "_session_id": "ses_upload", **form})["manifest"]


def test_label_and_event_tables_are_documents(uploads):
    labels = uploads / "self_report.csv"
    labels.write_text("trial,valence,arousal,dominance\n1,7.5,2.5,6.0\n", encoding="utf-8")
    events = uploads / "events.tsv"
    events.write_text("onset\tduration\ttrial_type\n12.5\t1.0\tstim\n30.0\t1.0\tstim\n", encoding="utf-8")
    for path in (labels, events):
        manifest = upload(path)
        assert manifest["modality"] == "document" and manifest["source_format"] == path.suffix[1:]
    page = attachments.read_document(upload(labels)["manifest_id"])["pages"][0]["text"]
    assert "7.5" in page and "2.5" in page


@pytest.mark.parametrize("encoding", ["utf-8", "gbk"])
def test_chinese_protocol_text_is_a_readable_document(uploads, encoding):
    note = uploads / "采集说明.txt"
    note.write_bytes("采集说明\n采样率：250 Hz\n通道顺序：Fp1 Fp2 C3 C4 P7 P8 O1 O2\n".encode(encoding))
    manifest = upload(note)
    assert manifest["modality"] == "document"
    assert "采样率：250 Hz" in attachments.read_document(manifest["manifest_id"])["pages"][0]["text"]
    from eegagent.worker.app import app
    with TestClient(app) as client:
        preview = client.get(f"/manifests/{manifest['manifest_id']}/preview")
    assert preview.status_code == 200
    assert "采样率：250 Hz" in preview.json()["content"]
    assert not preview.json()["content_truncated"]
    assert not (uploads.parents[1] / "sessions" / "ses_upload.json").exists()


@pytest.mark.parametrize("encoding", ["utf-8", "gbk"])
def test_signal_csv_with_chinese_comment_reads_on_any_locale(uploads, encoding):
    manifest = upload(signal_csv(uploads / f"被试02_{encoding}.csv", encoding))
    assert manifest["modality"] == "eeg"
    assert manifest["sample_rate_hz"] == 250 and manifest["channel_names"] == ["Fp1", "Fp2", "O1", "O2"]
    values, rate, _ = signals.load_signal(manifest["manifest_id"])
    assert rate == 250 and values.shape == (4, 1000)


def test_signal_table_with_a_marker_column_stays_a_signal(uploads):
    path = signal_csv(uploads / "with_marker.csv", extra_column="event")
    assert classify_text_table(path) == "signal"


def test_openbci_text_export_is_a_signal(uploads):
    path = uploads / "OpenBCI-RAW.txt"
    lines = ["%OpenBCI Raw EEG Data", "%Number of channels = 2", "%Sample Rate = 250 Hz",
             "Sample Index, EXG Channel 0, EXG Channel 1"]
    lines += [f"{i % 256}, {np.sin(i / 10):.4f}, {np.cos(i / 10):.4f}" for i in range(600)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = upload(path)
    assert manifest["modality"] == "eeg" and manifest["sample_rate_hz"] == 250


def test_short_table_with_uploaded_acquisition_facts_is_a_signal(uploads):
    path = uploads / "short.csv"
    path.write_text("a,b\n" + "\n".join(f"{i},{-i}" for i in range(100)) + "\n", encoding="utf-8")
    assert upload(path)["modality"] == "document"
    assert upload(path, sample_rate_hz=100)["modality"] == "eeg"


def test_explicit_clock_does_not_turn_events_or_protocol_text_into_eeg(uploads):
    event = uploads / "events.tsv"
    event.write_text("onset\tduration\ttrial_type\n1\t0.1\tstim\n", encoding="utf-8")
    protocol = uploads / "说明.txt"
    protocol.write_text("采样率250 Hz，参考乳突；事件表另附。", encoding="utf-8")
    assert upload(event, sample_rate_hz=250)["modality"] == "document"
    assert upload(protocol, sample_rate_hz=250)["modality"] == "document"


def test_docx_paragraphs_and_tables_are_extracted(uploads):
    path = uploads / "protocol.docx"
    body = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>静息采集说明</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>采样率</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>500 Hz</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    manifest = upload(path)
    text = attachments.read_document(manifest["manifest_id"])["pages"][0]["text"]
    assert manifest["modality"] == "document" and "静息采集说明" in text and "采样率 | 500 Hz" in text
    preview = attachments.document_preview(manifest["manifest_id"])
    assert preview["content"] == text


def test_spreadsheet_upload_explains_the_supported_route(uploads):
    path = uploads / "ratings.xlsx"
    path.write_bytes(b"PK\x03\x04")
    with pytest.raises(ValueError, match="另存为 CSV"):
        upload(path)


def test_single_sample_table_with_explicit_rate_keeps_channels(uploads):
    path = uploads / "one_row.csv"
    path.write_text("F3,F4\n1.0,2.0\n", encoding="utf-8")
    manifest = upload(path, sample_rate_hz=250)
    values, rate, _ = signals.load_signal(manifest["manifest_id"])
    assert rate == 250 and manifest["channel_names"] == ["F3", "F4"]
    np.testing.assert_array_equal(values, [[1.0], [2.0]])


def test_long_label_and_event_tables_stay_documents(uploads):
    for header in ("trial,valence,arousal,dominance", "onset,duration,trial_type"):
        path = uploads / "annotations.csv"
        path.write_text(header + "\n" + "\n".join(",".join([str(i)] * len(header.split(','))) for i in range(1000)), encoding="utf-8")
        assert upload(path)["modality"] == "document"


def test_json_document_preview_and_retrieval_share_the_same_text(uploads):
    path = uploads / "protocol.json"
    content = '{"设备":"Cyton","sample_rate_hz":250}'
    path.write_text(content, encoding="utf-8")
    manifest = upload(path)
    assert manifest["modality"] == "document"
    assert attachments.document_preview(manifest["manifest_id"])["content"] == content
    assert attachments.read_document(manifest["manifest_id"])["pages"][0]["text"] == content


def test_label_metadata_on_a_record_without_labels_reports_the_actual_issue(uploads):
    from eegagent.worker.metadata import update_record_metadata

    manifest = upload(signal_csv(uploads / "record.csv"))
    with pytest.raises(ValueError, match="文件没有标签列"):
        update_record_metadata(manifest["manifest_id"], label_names=[])
