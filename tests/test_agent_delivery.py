"""Cross-layer delivery contracts; synthetic model replies are not emotion truth."""
import csv
import json
import pickle
from pathlib import Path

import numpy as np
import pytest

from eegagent.knowledge.chunking import chunk_markdown
from eegagent.knowledge.documents import get_document_chunk, search_documents
from eegagent.worker.attachments import read_document, register_attachment
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.labels import label_definitions
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.signals import load_signal
from eegagent.worker.tools import run_tool


def submit(storage, session, manifests, drafts=()):
    directory = storage / "sessions"
    directory.mkdir(exist_ok=True)
    (directory / f"{session}.json").write_text(json.dumps({"manifests": manifests, "staged_manifests": list(drafts)}), encoding="utf-8")


def test_staged_and_removed_documents_are_excluded_until_message_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    first = uploads / "selected.md"
    second = uploads / "removed.md"
    first.write_text("# Methods\nUNIQUE_SELECTED_PROTOCOL alpha at 11 Hz.", encoding="utf-8")
    second.write_text("# Methods\nUNIQUE_REMOVED_PROTOCOL alpha at 19 Hz.", encoding="utf-8")
    selected = register_attachment(str(first), "ses_abcdef")["manifest"]
    removed = register_attachment(str(second), "ses_abcdef")["manifest"]
    submit(tmp_path, "ses_abcdef", [], [selected, removed])
    assert search_documents("UNIQUE_SELECTED_PROTOCOL", "ses_abcdef", 5) == []
    with pytest.raises(PermissionError):
        read_document(selected["manifest_id"], session_id="ses_abcdef")
    submit(tmp_path, "ses_abcdef", [selected])
    rows = search_documents("alpha", "ses_abcdef", 5)
    assert rows and {row["doc_id"] for row in rows} == {selected["manifest_id"]}
    result = read_document(selected["manifest_id"], session_id="ses_abcdef")
    assert get_document_chunk(result["chunks"][0]["chunk_id"], "ses_abcdef") is not None
    with pytest.raises(PermissionError):
        read_document(removed["manifest_id"], session_id="ses_abcdef")


@pytest.mark.parametrize("marker", ["```", "~~~~"])
def test_source_excerpt_headings_do_not_break_the_cited_section(tmp_path, marker):
    source = tmp_path / "recipe.md"
    source.write_text(f"---\ndoc_id: recipe\ntitle: Recipe\n---\n## Contract\n{marker}text\n## Original heading\n22 subjects and 1-second windows.\n{marker}\n## Other\nEnd.", encoding="utf-8")
    chunks = chunk_markdown(source)
    contract = next(chunk for chunk in chunks if chunk.section == "Contract")
    assert "22 subjects" in contract.text
    assert [chunk.section for chunk in chunks] == ["Contract", "Other"]


def test_trial_selection_keeps_acquisition_facts_and_reads_the_selected_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    path = tmp_path / "uploads/trials.dat"
    path.parent.mkdir()
    data = np.ones((2, 40, 8064))
    data[1] *= 2
    with path.open("wb") as stream:
        pickle.dump({"data": data, "labels": np.array([[1, 2, 3, 4], [5, 6, 7, 8]])}, stream)
    initial = inspect_record(str(path))
    assert initial["trial_count"] == 2 and initial["trial_selection"] == "default_first"
    confirmed = update_record_metadata(initial["manifest_id"], preprocessing="deap_official_preprocessed")["manifest"]
    selected = run_tool("inspect_record", {"manifest_id": confirmed["manifest_id"], "trial_index": 1, "include_labels": True})["manifest"]
    assert selected["trial_index"] == 1 and selected["trial_count"] == 2
    assert selected["trial_selection"] == "explicit" and selected["reference"] == "average"
    assert selected["unit"]["value"] == "dataset_native"
    assert selected["labels"]["values"] == [5, 6, 7, 8]
    np.testing.assert_array_equal(load_signal(selected["manifest_id"])[0], data[1, :32])
    np.testing.assert_array_equal(load_signal(confirmed["manifest_id"])[0], data[0, :32])


def test_label_contract_defines_valence_and_arousal_without_order_ambiguity():
    definitions = {item["label"]: item for item in label_definitions(["HVLA", "HALV", "happy"])}
    assert definitions["HVLA"] == {"label": "HVLA", "label_text": "高效价、低唤醒", "valence": "high", "arousal": "low"}
    assert definitions["HALV"]["valence"] == "low" and definitions["HALV"]["arousal"] == "high"
    assert definitions["happy"]["label_text"] == "愉快"
