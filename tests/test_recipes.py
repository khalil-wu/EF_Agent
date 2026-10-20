"""Replay the real native numerical chain instead of approximating its algorithms."""
import ast
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.io import savemat

from eegagent.worker import signals, store, workspaces
from eegagent.worker.cleaning import clean_artifacts
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.outputs import export
from eegagent.worker.processing import extract_features, preprocess, segment_signal
from eegagent.worker.sandbox import run_python
from eegagent.worker.sandbox_api import EEGApi
from eeg_synth import make_eeg


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    signals._CACHE.clear()
    store.ensure_layout()
    folder = tmp_path / "inputs"
    folder.mkdir()
    return folder, workspaces.mount(str(folder))


def test_native_recipe_replays_fir_de_hjorth_exactly_with_units_and_selected_times(workspace):
    folder, workspace_record = workspace
    sr = 256
    t = np.arange(sr * 10) / sr
    rng = np.random.default_rng(3)
    data = np.array([np.sin(2*np.pi*frequency*t) for frequency in (10, 12, 18, 6)]) * 20e-6
    data += rng.normal(0, 2e-6, data.shape)
    source = folder / "raw-volts.npy"
    np.save(source, data)
    original_bytes = source.read_bytes()
    manifest = inspect_record(str(source), sample_rate_hz=sr, channel_names=["P3", "P4", "C3", "C4"])
    manifest = update_record_metadata(manifest["manifest_id"], unit="V", reference="average")["manifest"]
    processed = preprocess(manifest["manifest_id"], [{"op": "rereference", "method": "car"},
        {"op": "bandpass", "low": 1, "high": 40, "method": "fir"}, {"op": "resample", "target_hz": 128}])["artifact"]
    windows = segment_signal(processed["artifact_id"], 2, 1, max_windows=4, start_s=.137, end_s=8.239)["artifact"]
    features = extract_features(windows["artifact_id"], ["bandpower", "de", "hjorth"], de_method="libeer_filtfilt")["artifact"]
    recipe = export("python", features["artifact_id"])
    script = Path(recipe["artifact"]["uri"]).read_text(encoding="utf-8")
    ast.parse(script)
    assert features["artifact_id"] not in script and processed["artifact_id"] not in script
    assert "scipy" not in script and "filter_data" not in script
    assert recipe["summary"]["native_steps"] == ["preprocess@1.3", "segment_signal@1.3", "extract_features@1.6"]
    script = script.replace("OUTPUT_PATH = None", "OUTPUT_PATH = 'results/replayed-features.csv'")
    result = run_python(script, "native-feature-replay", timeout_s=120, workspace_id=workspace_record["workspace_id"])
    assert result["status"] == "ok", result.get("error")
    replayed = next(item for item in result["artifacts"] if item["kind"] == "features")
    assert replayed["artifact_id"] != features["artifact_id"]
    with np.load(features["uri"]) as expected, np.load(replayed["uri"]) as actual:
        assert expected.files == actual.files
        for key in expected.files:
            np.testing.assert_array_equal(actual[key], expected[key])
    replay_windows = store.load_artifact(replayed["parent_artifact_ids"][0])
    expected_windows = json.loads(Path(windows["uri"]).read_text(encoding="utf-8"))["windows"]
    assert json.loads(Path(replay_windows["uri"]).read_text(encoding="utf-8"))["windows"] == expected_windows
    assert replayed["analysis"]["source_time_range_s"] == [.137, 8.239]
    assert result["workspace_files"][0]["path"] == "results/replayed-features.csv"
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("wrapper_version", ["3.2", "3.3"])
def test_native_recipe_replays_cleaning_selected_trial_and_signal_export(workspace, wrapper_version):
    folder, workspace_record = workspace
    data, sr, names, _ = make_eeg(sr=128, secs=20, blinks=False)
    data[names.index("T8")] = 0
    source = folder / "two-trials.mat"
    savemat(source, {"data": np.stack([data * 2, data]), "fs": sr, "ch_names": np.array(names, dtype=object), "unit": "uV"})
    manifest = inspect_record(str(source), trial_index=1)
    cleaned = clean_artifacts(manifest["manifest_id"], "bad_channels", action="drop", ransac=False, plot=False)["artifact"]
    assert "T8" in cleaned["summary"]["bad_channels"]
    processed = preprocess(cleaned["artifact_id"], [{"op": "remove_baseline", "seconds": .25, "drop": True},
                                                    {"op": "resample", "target_hz": 100}])["artifact"]
    signal_export = export("signal_csv", processed["artifact_id"])["artifact"]
    # Historical export wrappers carry the same native numerical source and format.
    wrapper = store.load_artifact(signal_export["artifact_id"])
    wrapper["provenance"]["created_by"] = f"export@{wrapper_version}"
    store.save_artifact(wrapper)
    recipe = export("python", signal_export["artifact_id"])
    from eegagent.worker.outputs import VERSION
    assert recipe["summary"]["tool_versions"]["export"] == VERSION
    script = Path(recipe["artifact"]["uri"]).read_text(encoding="utf-8")
    assert "'trial_index': 1" in script and "'method': 'bad_channels'" in script
    result = run_python(script, "native-signal-replay", timeout_s=120, workspace_id=workspace_record["workspace_id"])
    assert result["status"] == "ok", result.get("error")
    assert result["manifests"][0]["trial_index"] == 1
    replayed = next(item for item in reversed(result["artifacts"]) if item["kind"] == "normalized_signal"
                    and item["provenance"]["created_by"] == "preprocess@1.3")
    np.testing.assert_array_equal(np.load(replayed["uri"]), np.load(processed["uri"]))
    replayed_record = store.load_artifact(replayed["artifact_id"])
    assert replayed_record["signal"] == store.load_artifact(processed["artifact_id"])["signal"]
    api = EEGApi("unsupported-recipe", store.storage_dir() / "scratch", "run", "custom-recipe")
    custom = api.save_raw(api.to_mne(manifest["manifest_id"]), steps=["custom_script"])
    with pytest.raises(ValueError, match="run_python.*尚不支持"):
        export("python", custom)
