"""Regressions for the complete input, delivery and knowledge workflows."""
import importlib
import json
from pathlib import Path

import mne
import numpy as np
import pytest

from eegagent.worker import signals, store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.outputs import export
from eegagent.worker.processing import segment_signal, extract_features
from eegagent.worker.tools import run_tool
from eegagent.worker.sandbox import run_python
from eegagent.worker.models import list_models, model_evaluation


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("EEGAGENT_DATA_DIRS", "")
    monkeypatch.setenv("EEGAGENT_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    signals._CACHE.clear()
    signals._CHECKED_SOURCES.clear()
    return store.ensure_layout()


def signal(storage, count=512, rate=128, amplitude=1):
    path = storage / "uploads/signal.npz"
    data = np.tile(amplitude*(1+np.sin(2*np.pi*10*np.arange(count)/rate)), (2, 1))
    np.savez(path, data=data, fs=rate, ch_names=["C3", "C4"], unit="uV")
    return path, inspect_record(str(path))


def commit(storage, sid, manifests=(), artifacts=()):
    path = storage / "sessions" / (sid+".json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"manifests": list(manifests), "artifacts": list(artifacts)}), encoding="utf-8")


def test_modified_input_cannot_change_an_old_result_even_after_cache_eviction(storage):
    path, original = signal(storage)
    windows = segment_signal(original["manifest_id"], 1)["artifact"]["artifact_id"]
    first = extract_features(windows, ["rms"])["artifact"]
    with np.load(first["uri"]) as archive:
        old = archive["rms"].copy()
    signal(storage, amplitude=2)
    for cached in (True, False):
        if not cached:
            signals._CACHE.clear()
        with pytest.raises(ValueError, match="来源文件已改变"):
            extract_features(windows, ["rms"])
    changed = inspect_record(str(path))
    next_windows = segment_signal(changed["manifest_id"], 1)["artifact"]["artifact_id"]
    second = extract_features(next_windows, ["rms"])["artifact"]
    assert second["artifact_id"] != first["artifact_id"]
    with np.load(first["uri"]) as archive:
        np.testing.assert_array_equal(archive["rms"], old)
    with np.load(second["uri"]) as archive:
        np.testing.assert_allclose(archive["rms"], old*2)


def test_removed_uploads_and_other_session_metadata_are_not_model_inputs(storage):
    sid = "ses_scope"
    selected = storage / "uploads/selected.txt";selected.write_text("SELECTED", encoding="utf-8")
    removed = storage / "uploads/removed.txt";removed.write_text("REMOVED", encoding="utf-8")
    manifest = {"manifest_id": "doc_selected", "modality": "document", "source_path": str(selected)}
    store.save_manifest(manifest)
    commit(storage, sid, [manifest])
    other = storage / "sessions/ses_other.json";other.write_text('{"text":"OTHER"}', encoding="utf-8")
    listing = run_tool("list_files", {"_session_id": sid, "kind": "all"})
    assert [row["name"] for row in listing["files"]] == ["selected.txt"]
    for path in (removed, other):
        with pytest.raises(PermissionError):
            run_tool("read_file", {"_session_id": sid, "path": str(path)})
    assert run_tool("read_file", {"_session_id": sid, "path": str(selected)})["content"] == "SELECTED"
    script = run_python(f"open({str(removed)!r}).read()", sid, 15)
    assert script["error"]["code"] == "PermissionError"


def test_new_gateway_session_accepts_null_collections_from_go_json(storage):
    path = storage / "sessions/ses_empty.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"manifests": None, "artifacts": None}), encoding="utf-8")
    result = run_tool("list_files", {"_session_id": "ses_empty", "kind": "all"})
    assert result["status"] == "ok" and result["files"] == []


def test_committed_input_is_listed_once_when_its_folder_is_also_mounted(storage, monkeypatch):
    path, manifest = signal(storage)
    monkeypatch.setenv("EEGAGENT_DATA_DIRS", str(path.parent))
    commit(storage, "ses_list_once", [manifest])
    result = run_tool("list_files", {"_session_id": "ses_list_once", "kind": "eeg"})
    assert result["total"] == 1 and result["files"][0]["path"] == str(path)


def test_scoped_script_can_chain_native_tools_and_deliver_new_products(storage):
    _, manifest = signal(storage)
    commit(storage, "ses_chain", [manifest])
    code = f"""m=eeg.call('inspect_record',path={manifest['source_path']!r})['manifest']
w=eeg.call('segment_signal',source_id=m['manifest_id'],window_s=1)['artifact']['artifact_id']
f=eeg.call('extract_features',windows_id=w,features=['rms'])['artifact']['artifact_id']
data=eeg.load_features(f)
print(data['arrays']['rms'].shape)
eeg.call('export',kind='csv',source_id=f)
"""
    result = run_python(code, "ses_chain", 30)
    assert result["status"] == "ok", result
    assert result["artifacts"]


def test_committed_products_keep_their_source_version_after_metadata_revision(storage):
    _, original = signal(storage)
    windows = segment_signal(original["manifest_id"], 1)["artifact"]
    from eegagent.worker.metadata import update_record_metadata
    revised = update_record_metadata(original["manifest_id"], reference="average")["manifest"]
    commit(storage, "ses_revised", [revised], [windows])
    result = run_tool("extract_features", {"_session_id": "ses_revised", "windows_id": windows["artifact_id"], "features": ["rms"]})
    assert result["artifact"]["source_manifest_ids"] == [original["manifest_id"]]


@pytest.mark.parametrize("rate,count,amplitude", [(128,320,20), (128,320,1.23456789e-5), (128,320,1.23456789e8), (128.5,514,20)])
def test_edf_download_roundtrip_keeps_clock_length_and_voltage(storage, rate, count, amplitude):
    _, manifest = signal(storage, count, rate, amplitude)
    expected = signals.load_signal(manifest["manifest_id"])[0]
    result = export("signal_edf", manifest["manifest_id"])["artifact"]
    raw = mne.io.read_raw_edf(result["uri"], preload=True, verbose="ERROR")
    assert raw.n_times == count
    assert raw.info["sfreq"] == rate
    np.testing.assert_allclose(raw.get_data()*1e6, expected, rtol=0, atol=amplitude*1e-4)
    metadata = json.loads(Path(result["representations"]["json"]["uri"]).read_text(encoding="utf-8"))
    assert metadata["valid_samples"] == metadata["stored_samples"] == count


def test_fractional_edf_padding_is_explicit_in_its_downloadable_metadata(storage):
    _, manifest = signal(storage, 320, 128.5, 20)
    record = export("signal_edf", manifest["manifest_id"])["artifact"]
    raw = mne.io.read_raw_edf(record["uri"], preload=True, verbose="ERROR")
    meta = json.loads(Path(record["representations"]["json"]["uri"]).read_text(encoding="utf-8"))
    assert raw.n_times == meta["stored_samples"]
    assert meta["padding_samples"] == raw.n_times-320
    assert meta["file_duration_s"] == raw.n_times/raw.info["sfreq"]
    assert record["summary"]["file_duration_s"] == meta["file_duration_s"]


def test_long_multibyte_json_can_be_read_to_completion_without_a_stationary_cursor(storage):
    text = json.dumps({"内容": "脑电与视频"*20000}, ensure_ascii=False)
    path = storage / "uploads/long.json";path.write_text(text, encoding="utf-8")
    offset, char_offset, parts = 1, 0, []
    from eegagent.worker.files import read_file
    for _ in range(20):
        result = read_file(str(path), offset=offset, char_offset=char_offset)
        parts.append(result["content"])
        if not result["truncated"]:break
        next_cursor = result["next_offset"], result["next_char_offset"]
        assert next_cursor > (offset, char_offset)
        offset, char_offset = next_cursor
    assert "".join(parts) == text


def test_completed_project_ingestion_is_visible_to_the_existing_index(storage, monkeypatch):
    module = importlib.import_module("eegagent.knowledge.index")
    monkeypatch.setattr(module, "_INDEX", None)
    monkeypatch.setattr(module, "make_embedder", lambda: None)
    monkeypatch.setattr(module, "make_reranker", lambda: None)
    root = Path(__import__('os').environ["EEGAGENT_KNOWLEDGE_DIR"])
    root.mkdir();(root/"one.md").write_text('---\ndoc_id: one\ntitle: One\n---\n\nAlpha measurement.',encoding="utf-8")
    first = module.index()
    from eegagent.knowledge.ingest import ingest_file
    source = storage / "second.txt";source.write_text("QuartzPilot protocol\nMethods\nQuartzPilot calibration.", encoding="utf-8")
    ingest_file(source, doc_id="second", title="Second")
    fresh = module.index()
    assert fresh is not first and fresh.revision != first.revision
    assert "second" in fresh.stats()["docs"]


def test_model_execution_readiness_does_not_hide_test_set_checkpoint_selection():
    model = {"task":"emotion", "status":"registered", "file_uri":"mock://weights", "sha256":"a"*64,
             "modalities":["eeg"], "checkpoint_selection":"test_accuracy", "split_protocol":"cross_trial"}
    result = list_models(registry={"camer_deap_fixed_1s_fold0":model})["models"][0]
    assert result["execution_ready"] is True
    assert result["evaluation"] == model_evaluation(model)
    assert result["evaluation"]["status"] == "test_selected" and result["evaluation"]["independent_test"] is False
