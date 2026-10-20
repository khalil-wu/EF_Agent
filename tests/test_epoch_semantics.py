"""EEGLAB epoch timing and discontinuity regressions, with independent MNE/SciPy references."""

import csv
import re
from pathlib import Path

import mne
import numpy as np
import pytest
from scipy import signal as scipy_signal

from eegagent.worker import signals, store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.measurements import detect_transients, quality_summary
from eegagent.worker.mne_bridge import source_epochs, source_raw
from eegagent.worker.outputs import export, read_artifact
from eegagent.worker.processing import extract_features, preprocess, segment_signal
from eegagent.worker.sandbox_api import EEGApi
from eegagent.worker.sandbox import run_python
from eegagent.worker.model_prediction import save_model_prediction


@pytest.fixture()
def epoched(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    storage = store.ensure_layout()
    sr, count = 200, 400
    times = np.arange(count) / sr - .2
    data = np.zeros((2, 2, count))
    data[0, 0] = 20 * np.sin(2 * np.pi * 10 * times) + 100
    data[1, 0] = 200
    data[:, 1] = 30 * np.exp(-((times - .3) / .03) ** 2 / 2)
    epochs = mne.EpochsArray(data * 1e-6, mne.create_info(["O1", "Cz"], sr, "eeg"),
                             events=np.array([[1000, 0, 1], [2000, 0, 2]]),
                             event_id={"standard": 1, "target": 2}, tmin=-.2, baseline=None, verbose="ERROR")
    path = storage / "uploads" / "epoched.set"
    mne.export.export_epochs(path, epochs, fmt="eeglab", overwrite=True, verbose="ERROR")
    manifest = inspect_record(str(path))
    return manifest, data, sr, storage


def test_model_prediction_and_initial_quality_keep_distinct_epoch_coordinates(epoched):
    manifest, _, _, _ = epoched
    windows = segment_signal(manifest['manifest_id'], 1)['artifact']['artifact_id']
    result = save_model_prediction(windows, [[2, -1]] * 4,
        {'model_id': 'fixture', 'model_version': 'test-only', 'task': 'emotion'},
        {'kind': 'classification', 'labels': ['a', 'b'], 'score_kind': 'logits'}, run_id='epoch-fixture')
    coverage = result['summary']['coverage']
    assert 'original_intervals_s' not in coverage
    assert [r['epoch_index'] for r in coverage['original_time_ranges_s']] == [0, 1]
    assert [r['epoch_relative_time_range_s'] for r in coverage['original_time_ranges_s']] == [[-.2, 1.8], [-.2, 1.8]]
    row = read_artifact(result['artifact']['artifact_id'], part='predictions', start=2, limit=1)['rows'][0]
    assert row['epoch_index'] == 1 and row['epoch_relative_time_range_s'] == pytest.approx([-.2, .8])
    assert [r['epoch_index'] for r in manifest['quality']['sample_original_time_ranges_s']] == [0, 1]
    assert [r['epoch_index'] for r in manifest['quality']['line_noise_original_time_ranges_s']] == [0, 1]


def test_epoched_set_event_zero_and_erp_latency_are_preserved(epoched):
    manifest, original, sr, _ = epoched
    assert manifest["signal_kind"] == "epochs"
    assert [event["onset_s"] for event in manifest["events"]] == pytest.approx([.2, 2.2])
    assert [(s["start_sample"], s["end_sample"]) for s in manifest["segments"]] == [(0, 400), (400, 800)]
    epochs = source_epochs(manifest["manifest_id"], montage=None)
    assert epochs.tmin == pytest.approx(-.2)
    np.testing.assert_allclose(epochs.get_data() * 1e6, original, atol=1e-4)
    for epoch in epochs.get_data():
        assert epochs.times[np.argmax(epoch[1])] == pytest.approx(.3)
    assert set(epochs.event_id) == {"standard", "target"}
    raw, _ = source_raw(manifest["manifest_id"], montage=None)
    assert "EDGE boundary" in raw.annotations.description
    assert len(raw._raw_lengths) == 2


def test_filters_and_windows_never_use_adjacent_epoch_samples(epoched):
    manifest, original, sr, _ = epoched
    result = preprocess(manifest["manifest_id"], [{"op": "bandpass", "low": 2, "high": 40}])
    values = np.load(result["artifact"]["uri"])
    sos = scipy_signal.butter(4, [2, 40], btype="bandpass", fs=sr, output="sos")
    expected = np.concatenate([scipy_signal.sosfiltfilt(sos, epoch, axis=1) for epoch in original], axis=1)
    np.testing.assert_allclose(values, expected, atol=1e-4)
    assert np.max(np.abs(values[0, 400:])) < 1e-8  # a neighbouring sinusoid cannot leak into a flat epoch
    windows = segment_signal(result["artifact"]["artifact_id"], 1.5, stride_s=1)["artifact"]["artifact_id"]
    rows = read_artifact(windows, part="windows")["rows"]
    assert [(row["start_s"], row["end_s"]) for row in rows] == [(0, 1.5), (2, 3.5)]
    assert rows[0]["epoch_relative_time_range_s"] == pytest.approx([-.2, 1.3])
    assert rows[1]["epoch_index"] == 1


def test_baseline_resample_and_selection_keep_each_epoch_origin(epoched):
    manifest, original, sr, _ = epoched
    result = preprocess(manifest["manifest_id"], [{"op": "remove_baseline", "seconds": .2},
                                                {"op": "resample", "target_hz": 128},
                                                {"op": "pick_channels", "channels": ["Cz"]}])
    artifact = result["artifact"]
    expected = np.concatenate([scipy_signal.resample_poly((epoch - epoch[:, :40].mean(axis=1, keepdims=True))[:, 40:],
                                                          16, 25, axis=1)[1:2] for epoch in original], axis=1)
    np.testing.assert_allclose(np.load(artifact["uri"]), expected, atol=1e-4)
    assert artifact["signal"]["kind"] == "epochs" and "time_offset_s" not in artifact["signal"]
    assert artifact["signal"]["channel_names"] == ["Cz"]
    segments = read_artifact(artifact["artifact_id"], part="segments")["rows"]
    assert [s["original_start_s"] for s in segments] == pytest.approx([.2, 2.2])
    assert [s["epoch_tmin_s"] for s in segments] == pytest.approx([0, 0])
    assert [(s["start_sample"], s["end_sample"]) for s in segments] == [(0, 231), (231, 462)]
    epochs = source_epochs(artifact["artifact_id"], montage=None)
    assert epochs.get_data().shape == (2, 1, 231) and epochs.tmin == 0
    np.testing.assert_allclose(epochs.get_data()[:, 0, :], expected.reshape(2, 231) * 1e-6, atol=1e-10)
    windows = segment_signal(artifact["artifact_id"], .5, stride_s=.25)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    page = read_artifact(features, part="bandpower", channel="Cz")
    second = next(row for row in page["rows"] if row["epoch_index"] == 1)
    assert second["original_time_range_s"][0] == pytest.approx(2.2)
    assert second["epoch_relative_time_range_s"] == pytest.approx([0, .5])
    assert "original_recording_time_range_s" not in page["analysis"]


def test_mne_epoch_crop_selection_and_raw_crop_roundtrip_keep_coordinates(epoched):
    manifest, _, _, storage = epoched
    api = EEGApi("test", storage / "scratch", "run", "epoch-regression")
    epochs = api.to_epochs(manifest["manifest_id"], montage=None)[1:].copy().crop(-.1, .6)
    epochs.apply_baseline((None, 0))
    saved = api.save_epochs(epochs, steps=["epoch_baseline"])
    segment = read_artifact(saved, part="segments")["rows"][0]
    assert segment["epoch_index"] == 1
    assert segment["epoch_tmin_s"] == pytest.approx(-.1)
    assert segment["original_start_s"] == pytest.approx(2.1)
    rebuilt = api.to_epochs(saved, montage=None)
    np.testing.assert_allclose(rebuilt.get_data(), epochs.get_data(), atol=1e-10)
    assert rebuilt.tmin == pytest.approx(-.1)
    raw = api.to_mne(manifest["manifest_id"], montage=None).crop(2.1, 2.6).resample(100)
    raw_saved = api.save_raw(raw, steps=["crop", "resample_100"])
    raw_segment = read_artifact(raw_saved, part="segments")["rows"][0]
    assert raw_segment["epoch_index"] == 1
    assert raw_segment["original_start_s"] == pytest.approx(2.1)
    assert raw_segment["epoch_tmin_s"] == pytest.approx(-.1)
    assert raw_segment["end_sample"] == raw.n_times


def test_quality_psd_and_transients_do_not_treat_epoch_jumps_as_signal(epoched):
    manifest, _, _, _ = epoched
    quality = quality_summary(manifest["manifest_id"], channels=["O1"])
    assert quality["measurement_settings"]["welch"]["n_continuous_segments"] == 2
    assert quality["measurement_settings"]["rms"]["centering"] == "none"
    assert "original_recording_time_range_s" not in quality
    result = detect_transients(manifest["manifest_id"], channels=["O1"], min_amplitude=70)
    assert result["rows"] == []  # 100 → 200 µV epoch offset is not a transient
    raw, _ = source_raw(manifest["manifest_id"], montage=None)
    raw.filter(2, 40, method="iir", iir_params={"order": 4, "ftype": "butter"}, verbose="ERROR")
    assert np.max(np.abs(raw.get_data()[0, 400:])) < 1e-10


def test_epoched_export_carries_event_relative_coordinates(epoched):
    manifest, _, _, _ = epoched
    windows = segment_signal(manifest["manifest_id"], 1)["artifact"]["artifact_id"]
    features = extract_features(windows, ["rms"])["artifact"]["artifact_id"]
    result = export("csv", features)
    with Path(result["artifact"]["uri"]).open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    second = next(row for row in rows if row["epoch_index"] == "1")
    assert float(second["original_start_s"]) == pytest.approx(2)
    assert float(second["epoch_start_s"]) == pytest.approx(-.2)


def test_native_epoch_skill_recipe_runs_in_the_real_sandbox(epoched, commit_session):
    manifest, _, _, _ = epoched
    commit_session("epoch_recipe", [manifest])
    path = Path(__file__).resolve().parents[1] / "skills" / "eeg_toolboxes" / "references" / "api.md"
    blocks = re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), flags=re.S)
    recipe = next(block for block in blocks if "eeg.to_epochs" in block).replace("SOURCE_ID", manifest["manifest_id"])
    result = run_python(recipe, session_id="epoch_recipe", timeout_s=120)
    assert result["status"] == "ok", result.get("error")
    artifact = next(artifact for artifact in result["artifacts"] if artifact["kind"] == "normalized_signal")
    assert artifact["signal"]["kind"] == "epochs"
    assert artifact["signal"]["epoch_tmin_range_s"] == pytest.approx([-.2, -.2])
