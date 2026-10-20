"""Synthetic transport responses exercise integration and storage, not emotion accuracy."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from threading import Event

import numpy as np
import pytest
from scipy.io import savemat

from eegagent.worker import remote_model, script_jobs, signals, store
from eegagent.worker.channel_orders import EAV_CHANNEL_ORDER
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.models import route_model
from eegagent.worker.outputs import export, read_artifact
from eegagent.worker.registry import load_registry
from eegagent.worker.tools import TOOL_DEFINITIONS, run_tool

MODEL_ID = "eav_eegnet_emotion5_loso_fold0"


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    monkeypatch.delenv("EEGAGENT_DATA_DIRS", raising=False)
    signals._CACHE.clear()
    path = store.ensure_layout() / "uploads" / "trial.npz"
    original = np.random.default_rng(12).normal(size=(30, 10000)) * 0.001
    np.savez(path, data=original[::-1], fs=500, ch_names=list(reversed([n.upper() for n in EAV_CHANNEL_ORDER])),
             unit="dataset_native", dataset="EAV", format_profile="eav_raw_trial", reference="mastoids",
             task_mode="speaking", source_provenance=json.dumps({"trial_index": 1, "origin": "test fixture"}))
    manifest = inspect_record(str(path))
    return manifest, original


def test_native_reader_preserves_scale_and_route_requires_speaking(native):
    manifest, original = native
    data, rate, names, steps, _ = signals.resolve_signal(manifest["manifest_id"])
    assert data.dtype == np.float64 and np.array_equal(data, original[::-1])
    assert rate == 500 and "unit_dataset_native" in steps
    assert signals.signal_metadata(manifest["manifest_id"])["amplitude_unit"] == "dataset_native"
    assert manifest["metadata_evidence"]["origin"]["value"] == "unverified"
    assert route_model("emotion", manifest, model_id=MODEL_ID)["selected_model"] == MODEL_ID
    unknown = update_record_metadata(manifest["manifest_id"], task_mode="unknown")["manifest"]
    assert route_model("emotion", unknown, model_id=MODEL_ID)["candidates"][0]["route_status"] == "needs_context"
    listening = update_record_metadata(manifest["manifest_id"], task_mode="listening")["manifest"]
    assert route_model("emotion", listening, model_id=MODEL_ID)["selected_model"] is None
    with pytest.raises(ValueError, match="speaking"):
        run_tool("run_model", {"source_id": listening["manifest_id"], "model_id": MODEL_ID})


def test_mat_trials_use_sample_channel_trial_axes_and_sidecar_mode(native):
    manifest, original = native
    directory = Path(manifest["source_path"]).parent
    path = directory / "subject1_eeg.mat"
    values = np.stack([original.T, (original + 1).T], axis=2)
    savemat(path, {"seg": values})
    labels = np.zeros((10, 2))
    labels[0, 0], labels[1, 1] = 1, 1
    savemat(directory / "subject1_eeg_label.mat", {"label": labels})
    record = inspect_record(str(path), trial_index=1)
    data, rate, names = signals.load_signal(record["manifest_id"])
    assert np.array_equal(data, original + 1) and rate == 500 and names == EAV_CHANNEL_ORDER
    assert record["duration_s"] == 20 and record["n_channels"] == 30
    assert record["metadata_evidence"]["task_mode"] == {"value": "speaking", "source": "file_content"}
    assert record["unit"] == {"value": "dataset_native", "source": "file_content"}
    assert record["channel_layout"]["source"] == "format_assumption"
    assert record["reference"] == "unknown" and record["metadata_evidence"]["reference"]["source"] == "format_assumption"
    assert record["segments"][0]["epoch_index"] == 1
    assert route_model("emotion", record, model_id=MODEL_ID)["selected_model"] is None
    record = update_record_metadata(record["manifest_id"], preprocessing="eav_raw")["manifest"]
    assert route_model("emotion", record, model_id=MODEL_ID)["selected_model"] == MODEL_ID
    trial_record, _, windows = remote_model._prediction_windows(record["manifest_id"],
        {**load_registry()[MODEL_ID], "model_id": MODEL_ID}, None, None, None)
    rows = read_artifact(windows["artifact_id"], "windows")["rows"]
    assert [row["epoch_relative_time_range_s"] for row in rows] == [[0, 5], [5, 10], [10, 15], [15, 20]]
    assert rows[0]["original_time_range_s"] == [20, 25]
    assert trial_record["shape"] == [1]


def test_run_model_stores_real_contract_outputs_and_exact_input_tensor(native, monkeypatch):
    manifest, original = native
    returned_inputs = np.random.default_rng(4).normal(size=(4, 1, 30, 640)).astype(np.float32)
    returned_logits = np.array([[1, 2, 3, 4, 5], [5, 1, 2, 3, 4], [1, 5, 2, 3, 4], [1, 2, 5, 3, 4]], dtype=np.float32)

    def infer(request, response, model, run_id, job, deadline, log):
        with np.load(request, allow_pickle=False) as archive:
            assert np.array_equal(archive["signal"], original)
            metadata = json.loads(archive["metadata"].item())
        assert metadata["channel_names"] == EAV_CHANNEL_ORDER and metadata["unit"] == "dataset_native"
        assert metadata["task_mode"] == "speaking" and "labels" not in metadata
        assert metadata["windows"][-1]["start_sample"] == 7500
        received = {"model": {"model_id": MODEL_ID, "model_version": model["model_version"], "task": "emotion",
                               "weight_sha256": model["sha256"], "input_recipe": {"test": "synthetic response"}},
                    "output": {"kind": "classification", "labels": model["label_map"], "score_kind": "logits",
                                "calibration": {"status": "unknown"}},
                    "preparation": {"input_stage": metadata["input_stage"], "windows": metadata["windows"]},
                    "runtime": {"test": "synthetic"}}
        np.savez(response, inputs=returned_inputs, logits=returned_logits, metadata=json.dumps(received))

    monkeypatch.setattr(remote_model, "_remote_infer", infer)
    result = run_tool("run_model", {"source_id": manifest["manifest_id"], "model_id": MODEL_ID,
                                    "_session_id": "ses_model_test", "max_trials": 1})
    assert result["prediction_executed"] and result["artifact"]["kind"] == "prediction"
    assert result["summary"]["score_kind"] == "logits" and result["summary"]["prediction_count"] == 4
    assert result["summary"]["coverage"]["covered_duration_s"] == 20
    rows = read_artifact(result["artifact"]["artifact_id"], "predictions")["rows"]
    assert [row["label"] for row in rows] == ["calm", "neutral", "sad", "anger"]
    tensor = store.load_artifact(result["input_artifact_id"])
    with np.load(tensor["uri"], allow_pickle=False) as archive:
        assert np.array_equal(archive["inputs"], returned_inputs)
        assert np.array_equal(archive["logits"], returned_logits)
    assert result["input_artifact_id"] in result["artifact"]["parent_artifact_ids"]
    assert tensor["summary"]["model_channel_order"] == EAV_CHANNEL_ORDER
    page = read_artifact(tensor["artifact_id"], "inputs", start=2, limit=1, channel="F3")
    assert page["total"] == 4 and len(page["rows"][0]["samples"]) == 640
    exported = export("json", result["artifact"]["artifact_id"])
    saved = json.loads(Path(exported["artifact"]["uri"]).read_text(encoding="utf-8"))
    assert saved["model"]["source_channel_mapping"] == list(reversed(range(30)))
    assert saved["predictions"][2]["scores"] == returned_logits[2].tolist()
    definition = next(row for row in TOOL_DEFINITIONS if row["name"] == "run_model")
    assert definition["permission_mode_min"] == "readonly"


def test_selected_range_cannot_shift_original_trial_boundary(native):
    manifest, _ = native
    with pytest.raises(ValueError, match="trial 边界"):
        run_tool("run_model", {"source_id": manifest["manifest_id"], "model_id": MODEL_ID, "start_s": 5, "end_s": 20})


def test_stop_terminates_model_transport_process(native, monkeypatch):
    manifest, _ = native
    started = Event()

    def infer(request, response, model, run_id, job, deadline, log):
        started.set()
        remote_model._command([sys.executable, "-c", "import time; time.sleep(20)"], job, deadline, log)

    monkeypatch.setattr(remote_model, "_remote_infer", infer)
    sid = "ses_cancel_model"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_tool, "run_model", {"source_id": manifest["manifest_id"], "model_id": MODEL_ID,
                                                    "_session_id": sid})
        assert started.wait(10)
        deadline = time.monotonic() + 5
        while script_jobs.active[sid].process is None and time.monotonic() < deadline:
            time.sleep(.01)
        process = script_jobs.active[sid].process
        assert process is not None and process.poll() is None
        assert script_jobs.stop_script(sid)["status"] == "stopped"
        result = future.result(timeout=5)
        assert process.poll() is not None
    assert result["error"]["code"] == "Cancelled" and sid not in script_jobs.active


# Inference is mocked; use isolated registered contracts rather than local weights.
pytestmark = pytest.mark.usefixtures("registered_model_registry")
