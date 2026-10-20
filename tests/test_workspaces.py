import csv
import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import workspaces
from eegagent.worker.files import list_files
from eegagent.worker.tools import run_tool
from eegagent.worker import signals


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    signals._CACHE.clear()
    folder = tmp_path / "recordings"
    folder.mkdir()
    for name in ["a", "b", "c"]:
        t = np.arange(512) / 128
        np.savez(folder / (name+".npz"), data=np.array([np.sin(2*np.pi*10*t), np.cos(2*np.pi*8*t)]), fs=128, ch_names=["P3", "P4"], unit="uV")
    return folder, workspaces.mount(str(folder), "采集数据")


def test_mounted_root_exclusions_and_exact_pagination(mounted):
    folder, record = mounted
    identifier = record["workspace_id"]
    assert workspaces.mount(str(folder))["workspace_id"] == identifier
    workspaces.operations(identifier, "exclude", ["b.npz"])
    first = list_files(limit=1, workspace_id=identifier)
    second = list_files(limit=1, offset=first["next_offset"], workspace_id=identifier)
    assert [Path(row["path"]).name for row in first["files"]+second["files"]] == ["a.npz", "c.npz"]
    assert second["next_offset"] is None
    assert first["total"] == 2
    assert (folder / "b.npz").exists()
    with pytest.raises(ValueError):
        workspaces.operations(identifier, "move", ["a.npz"], "../outside")
    assert (folder / "a.npz").exists()


def test_mention_search_returns_exact_relative_paths_root_and_manual_exclusion(mounted):
    folder, record = mounted
    (folder/"被试 01").mkdir()
    (folder/"被试 01"/"a.npz").write_bytes((folder/"a.npz").read_bytes())
    (folder/".gitignore").write_text("*.tmp",encoding="utf-8")
    workspaces.operations(record["workspace_id"],"exclude",["b.npz"])
    root = workspaces.mentions(record["workspace_id"],".")["items"]
    assert root[0]["path"] == "." and root[0]["kind"] == "directory"
    assert any(item["path"]==".gitignore" for item in root)
    matches = workspaces.mentions(record["workspace_id"],"a.npz")["items"]
    assert {item["path"] for item in matches} == {"a.npz","被试 01/a.npz"}
    nested = workspaces.mentions(record["workspace_id"],"被试 01/")["items"]
    assert [item["path"] for item in nested] == ["被试 01/a.npz"]
    refs = workspaces.reference_items(record["workspace_id"],["b.npz","被试 01"])
    assert refs[0]["excluded"] is True and refs[1]["kind"] == "directory"
    with pytest.raises(ValueError):
        workspaces.reference_items(record["workspace_id"],["../outside.npz"])


def test_compressed_fif_is_listed_and_inspected_as_one_supported_record(mounted):
    import mne
    folder, record = mounted
    time = np.arange(512) / 128
    data = np.array([np.sin(2*np.pi*10*time), np.cos(2*np.pi*10*time)]) * 1e-6
    path = folder / "trial_raw.fif.gz"
    raw = mne.io.RawArray(data, mne.create_info(["P3", "P4"], 128, "eeg"), verbose="ERROR")
    raw.save(path, overwrite=True, verbose="ERROR")
    entries = run_tool("list_files", {"_workspace_id": record["workspace_id"], "kind": "eeg"})["files"]
    assert next(row for row in entries if row["name"] == path.name)["ext"] == ".fif.gz"
    inspected = run_tool("workspace_files", {"_workspace_id": record["workspace_id"], "action": "inspect", "path": path.name})["manifest"]
    assert inspected["n_channels"] == 2 and inspected["n_samples"] == 512 and inspected["sample_rate_hz"] == 128


def test_original_file_rename_move_trash_restore_and_record_paths(mounted, monkeypatch):
    folder, record = mounted
    identifier = record["workspace_id"]
    original = (folder / "a.npz").read_bytes()
    workspaces.operations(identifier, "exclude", ["a.npz"])
    workspaces.operations(identifier, "rename", ["a.npz"], "renamed.npz")
    assert workspaces.load(identifier)["excluded"] == ["renamed.npz"]
    workspaces.operations(identifier, "mkdir", destination="reviewed")
    workspaces.operations(identifier, "move", ["renamed.npz"], "reviewed")
    result = workspaces.operations(identifier, "trash", ["reviewed"])
    recovery = result["workspace"]["trash"][0]
    assert not (folder / "reviewed").exists()
    workspaces.operations(identifier, "restore", trash_id=recovery["id"])
    assert (folder / "reviewed/renamed.npz").read_bytes() == original
    assert workspaces.load(identifier)["excluded"] == ["reviewed/renamed.npz"]


def test_include_one_file_inside_an_excluded_folder(mounted):
    folder, record = mounted
    identifier = record["workspace_id"]
    workspaces.operations(identifier, "mkdir", destination="subject")
    workspaces.operations(identifier, "move", ["a.npz", "b.npz"], "subject")
    workspaces.operations(identifier, "exclude", ["subject"])
    workspaces.operations(identifier, "include", ["subject/a.npz"])
    assert {Path(row["path"]).name for row in list_files(workspace_id=identifier)["files"]} == {"a.npz", "c.npz"}


def test_multifile_record_moves_and_restores_its_referenced_sidecars(mounted):
    folder, record = mounted
    (folder / "record.vhdr").write_text("Brain Vision Data Exchange Header File Version 1.0\nDataFile=signal.eeg\nMarkerFile=events.vmrk\n", encoding="utf-8")
    (folder / "signal.eeg").write_bytes(b"synthetic")
    (folder / "events.vmrk").write_text("fixture", encoding="utf-8")
    workspaces.operations(record["workspace_id"], "mkdir", destination="records")
    workspaces.operations(record["workspace_id"], "move", ["signal.eeg", "record.vhdr"], "records")
    assert all((folder / "records" / name).exists() for name in ["record.vhdr", "signal.eeg", "events.vmrk"])
    trashed = workspaces.operations(record["workspace_id"], "trash", ["records/record.vhdr"])
    workspaces.operations(record["workspace_id"], "restore", trash_id=trashed["workspace"]["trash"][0]["id"])
    assert all((folder / "records" / name).exists() for name in ["record.vhdr", "signal.eeg", "events.vmrk"])


def test_single_batch_quality_reports_all_included_records_and_real_csv(mounted):
    folder, record = mounted
    workspaces.operations(record["workspace_id"], "exclude", ["b.npz"])
    result = run_tool("workspace_quality", {"_workspace_id":record["workspace_id"], "_session_id":"quality_fixture"})
    assert result["summary"]["file_count"] == 2
    assert result["summary"]["measured"] == 2
    assert result["summary"]["not_measured"] == 0
    from eegagent.worker.store import load_artifact
    artifact = load_artifact(result["artifact"]["artifact_id"])
    with Path(artifact["uri"]).open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["file"] for row in rows] == ["a.npz", "c.npz"]
    assert all(float(row["end_s"])==4 for row in rows)
    assert all((folder / (name+".npz")).exists() for name in ["a", "b", "c"])


def test_batch_signal_set_matches_upload_content_routing_with_explicit_clock(mounted):
    folder, record = mounted
    inputs = folder / "batch-inputs"
    inputs.mkdir()
    time = np.arange(512) / 128
    np.savetxt(inputs / "signal.csv", np.array([np.sin(2*np.pi*10*time), np.cos(2*np.pi*10*time)]).T,
               delimiter=",", header="P3,P4", comments="")
    (inputs / "events.tsv").write_text("onset\tduration\ttrial_type\n" + "\n".join(f"{i}\t0.1\tstim" for i in range(300)), encoding="utf-8")
    (inputs / "采集说明.txt").write_text("采样率128 Hz，通道P3和P4，按原始顺序分析。", encoding="utf-8")
    listed = run_tool("list_files", {"_workspace_id": record["workspace_id"], "kind": "eeg", "root": "batch-inputs"})["files"]
    assert [row["name"] for row in listed] == ["signal.csv"]
    result = run_tool("workspace_quality", {"_workspace_id": record["workspace_id"], "_session_id": "quality_content",
                                             "path": "batch-inputs", "sample_rate_hz": 128})
    assert result["summary"]["file_count"] == result["summary"]["measured"] == 1
    assert result["summary"]["not_measured"] == 0
    from eegagent.worker.store import load_artifact
    artifact = load_artifact(result["artifact"]["artifact_id"])
    with Path(artifact["uri"]).open(encoding="utf-8-sig") as stream:
        assert [row["file"] for row in csv.DictReader(stream)] == ["batch-inputs/signal.csv"]


def test_workspace_inspection_returns_real_record_without_copying(mounted):
    folder, record = mounted
    result = run_tool("workspace_files", {"action":"inspect", "path":"a.npz", "_workspace_id":record["workspace_id"], "_session_id":"inspect_fixture"})
    manifest = result["manifest"]
    assert Path(manifest["source_path"]) == folder / "a.npz"
    assert manifest["original_name"] == "a.npz"
    assert manifest["sample_rate_hz"] == 128
    assert manifest["channel_names"] == ["P3", "P4"]


def test_local_analysis_finishes_measurements_with_no_classifier_or_upload(mounted):
    folder, record = mounted
    from eegagent.worker.progress import channel
    stages=[]
    with channel(stages.append):
        result=run_tool("analyze_signal",{"path":str(folder/"a.npz"),"emotion_review":True,"window_s":2,"stride_s":1,"_session_id":"local_fixture","_workspace_id":record["workspace_id"]})
    assert result["summary"]["prediction_executed"] is False
    assert result["summary"]["n_windows"] == 3
    assert result["summary"]["features"]["mean_bandpower"]["alpha"] > 0
    assert result["summary"]["features"]["power_unit"]
    assert "channels" not in result["summary"]["quality"]
    assert result["summary"]["time_range_s"] == [0,4]
    assert {item["kind"] for item in result["artifacts"]} >= {"plot","features","table","emotion_assessment"}
    assert all(not item["stage"].startswith("upload") for item in stages)
    assert result["artifacts"][-1]["summary"]["assessment_status"] == "unassessed"


def test_local_analysis_selects_requested_trial_on_an_existing_record(mounted, commit_session):
    from scipy.io import savemat
    from eegagent.worker.inspect_record import inspect_record
    from eegagent.worker.store import load_manifest
    folder, record = mounted
    time = np.arange(512) / 128
    trial = np.array([np.sin(2*np.pi*10*time), np.cos(2*np.pi*10*time)])
    path = folder / "trials.mat"
    savemat(path, {"data": np.stack([trial, 10*trial]), "fs": 128, "ch_names": ["P3", "P4"], "unit": "uV"})
    first = inspect_record(str(path), trial_index=0)
    commit_session("selected_trial_fixture", [first], workspace_id=record["workspace_id"])
    result = run_tool("analyze_signal", {"manifest_id": first["manifest_id"], "trial_index": 1,
                                       "window_s": 2, "stride_s": 2, "_session_id": "selected_trial_fixture"})
    assert result["manifest"]["trial_index"] == 1
    assert result["summary"]["features"]["mean_bandpower"]["alpha"] > 40
    assert result["summary"]["features"]["de_filter"]
    assert result["summary"]["features"]["de_log_base"] == 2
    assert load_manifest(first["manifest_id"])["trial_index"] == 0


def test_local_csv_analysis_uses_user_metadata_before_reading_and_applies_baseline(mounted):
    folder, record = mounted
    with np.load(folder/"a.npz") as source:
        np.savetxt(folder/"plain.csv", source["data"].T, delimiter=",", header="P3,P4", comments="")
    result = run_tool("analyze_signal", {"path":"plain.csv", "eeg_metadata":{"sample_rate_hz":128, "channel_names":["P3","P4"], "unit":"uV", "baseline_duration_s":1}, "emotion_review":True, "window_s":2, "stride_s":1, "_workspace_id":record["workspace_id"], "_session_id":"csv_fixture"})
    assert result["summary"]["time_range_s"] == [1,4]
    assert result["summary"]["n_windows"] == 2
    assert result["manifest"]["sample_rate_hz"] == 128
    assert result["summary"]["features"]["mean_bandpower"]["alpha"] > 0
    assert result["summary"]["prediction_executed"] is False
