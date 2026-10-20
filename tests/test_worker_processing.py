import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.outputs import export, plot, read_artifact
from eegagent.worker.processing import extract_features, preprocess, segment_signal


def test_channel_selection_orders_data_names_and_types_as_requested(manifest):
    from eegagent.worker.signals import resolve_signal

    original, rate, names, _, _ = resolve_signal(manifest['manifest_id'])
    result = preprocess(manifest['manifest_id'], [{'op': 'pick_channels', 'channels': ['O2', 'Fp1', 'O1']}])
    selected, selected_rate, selected_names, _, _ = resolve_signal(result['artifact']['artifact_id'])
    assert selected_names == ['O2', 'Fp1', 'O1'] and selected_rate == rate
    np.testing.assert_array_equal(selected, original[[names.index('O2'), names.index('Fp1'), names.index('O1')]].astype(np.float32))


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    from eegagent.worker import signals

    signals._CACHE.clear()
    return store.ensure_layout()


@pytest.fixture()
def manifest(storage: Path) -> dict:
    sr, secs = 250, 30
    t = np.arange(sr * secs) / sr
    rng = np.random.default_rng(3)
    data = rng.normal(0, 5, (8, len(t)))
    data[6] += 25 * np.sin(2 * np.pi * 10 * t)  # strong alpha on O1
    data[7] += 25 * np.sin(2 * np.pi * 10 * t)  # and O2
    data[0] += 8 * np.sin(2 * np.pi * 50 * t)   # line noise on Fp1
    path = storage / "uploads" / "synthetic.npy"
    np.save(path, data)
    return inspect_record(str(path), sample_rate_hz=sr, channel_names=["Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"])


def test_preprocess_records_steps_and_changes_rate(manifest):
    out = preprocess(manifest["manifest_id"], [{"op": "notch", "freq": 50}, {"op": "bandpass", "low": 0.5, "high": 45}, {"op": "resample", "target_hz": 125}])
    art = out["artifact"]
    assert art["kind"] == "normalized_signal"
    assert art["provenance"]["steps"][-3:] == ["notch_50", "bandpass_0.5_45", "resample_125"]
    assert out["summary"]["sample_rate_hz"] == 125
    assert Path(art["uri"]).exists()
    # content-addressed: same inputs -> same id
    again = preprocess(manifest["manifest_id"], [{"op": "notch", "freq": 50}, {"op": "bandpass", "low": 0.5, "high": 45}, {"op": "resample", "target_hz": 125}])
    assert again["artifact"]["artifact_id"] == art["artifact_id"]


def test_pipeline_alpha_dominates_on_occipital(manifest):
    pre = preprocess(manifest["manifest_id"], [{"op": "notch", "freq": 50}, {"op": "bandpass", "low": 0.5, "high": 50}])["artifact"]["artifact_id"]
    win = segment_signal(pre, window_s=4, stride_s=2)
    assert win["summary"]["n_windows"] == 14
    feats = extract_features(win["artifact"]["artifact_id"], ["psd", "bandpower", "de", "hjorth", "asymmetry", "spectral_entropy"])
    s = feats["summary"]
    assert s["n_windows"] == 14 and s["n_channels"] == 8
    assert max(s["relative_bandpower"], key=s["relative_bandpower"].get) == "alpha"
    assert {c["channel"] for c in s["alpha_by_channel_top5"][:2]} == {"O1", "O2"}
    assert "O1/O2" in s["alpha_asymmetry"]
    assert "O1/O2" not in s["frontal_alpha_asymmetry"]
    assert "C3/C4" not in s["frontal_alpha_asymmetry"]
    assert abs(s["alpha_asymmetry"]["O1/O2"]) < 0.3  # symmetric by construction
    assert "bandpass_0.5_50" in feats["artifact"]["provenance"]["steps"]


def test_psd_reader_selects_channel_and_pages_frequency_axis(manifest):
    from scipy.signal import welch

    windows = segment_signal(manifest["manifest_id"], 4, stride_s=4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["psd"])["artifact"]
    source = np.load(manifest["source_path"])
    expected = []
    for start in range(0, 250 * 28, 1000):
        frequencies, density = welch(source[6, start:start + 1000], fs=250, nperseg=500, window="hamming")
        expected.append(density)
    expected = np.mean(expected, axis=0)
    page = read_artifact(features["artifact_id"], part="psd_mean", channel="o1", start=17, limit=7)
    assert page["axis"] == "frequency_hz" and page["unit"] == "µV²/Hz"
    np.testing.assert_allclose([row["power_density"] for row in page["rows"]], expected[17:24], rtol=1e-10)
    assert all("window" not in row and "start_s" not in row for row in page["rows"])
    tail = read_artifact(features["artifact_id"], part="psd_freqs", start=249, limit=20)
    assert tail["total"] == 251 and len(tail["rows"]) == 2
    np.testing.assert_equal([row["frequency_hz"] for row in tail["rows"]], frequencies[249:])


def test_temporal_features_have_physical_values_and_export_units(storage):
    import csv

    sr = 250
    t = np.arange(2500) / sr
    path = storage / "uploads" / "known_sine.npy"
    np.save(path, (5 * np.sin(2 * np.pi * 10 * t))[None, :])
    m = inspect_record(str(path), sample_rate_hz=sr, channel_names=["O1"])
    windows = segment_signal(m["manifest_id"], 2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["rms", "line_length", "zero_crossing_rate"])["artifact"]["artifact_id"]
    rms = read_artifact(features, part="rms", channel="O1")
    assert rms["components"] == ["rms"]
    assert rms["rows"][0]["values"][0] == pytest.approx(5 / np.sqrt(2), abs=1e-5)
    rate = read_artifact(features, part="zero_crossing_rate", channel="O1")
    assert rate["rows"][0]["values"][0] == pytest.approx(20, abs=.6)
    output = export("csv", features)
    with Path(output["artifact"]["uri"]).open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {row["unit"] for row in rows if row["feature"] == "rms"} == {"µV"}
    assert {row["unit"] for row in rows if row["feature"] == "line_length"} == {"µV/sample"}
    assert {row["unit"] for row in rows if row["feature"] == "zero_crossing_rate"} == {"Hz"}


def test_plots_and_export_and_read(manifest):
    pre = preprocess(manifest["manifest_id"], [{"op": "bandpass", "low": 0.5, "high": 50}])["artifact"]["artifact_id"]
    win = segment_signal(pre, window_s=4, stride_s=4)["artifact"]["artifact_id"]
    feats = extract_features(win, ["bandpower", "de"], bands="camer_5band")["artifact"]["artifact_id"]

    raw = plot("raw", manifest["manifest_id"], start_s=0, end_s=5)
    assert raw["artifact"]["media_type"] == "image/png" and Path(raw["artifact"]["uri"]).stat().st_size > 1000
    psd = plot("psd", feats)
    assert 8 <= psd["summary"]["peak_hz"] <= 12
    assert len(psd["summary"]["channel_peaks_shown"]) == 8
    assert psd["summary"]["power_unit"] == "µV²/Hz"
    tl = plot("bandpower_timeline", feats, band="alpha")
    assert tl["summary"]["bands"] == ["alpha"]

    exp = export("csv", feats)
    assert exp["artifact"]["kind"] == "table" and exp["summary"]["rows"] > 0
    assert set(exp["summary"]["features"]) == {"bandpower", "de"}
    header = Path(exp["artifact"]["uri"]).read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("window,start_s,end_s,channel,feature,component,value")

    r = read_artifact(feats, part="bandpower", start=0, limit=3, channel="O1")
    assert r["status"] == "ok" and len(r["rows"]) == 3 and r["components"] == ["delta", "theta", "alpha", "beta", "gamma"]
    with pytest.raises(ValueError):
        plot("topomap", feats)


def test_requires_windows_kind_is_enforced_by_worker_too(manifest):
    pre = preprocess(manifest["manifest_id"], [{"op": "bandpass", "low": 1, "high": 40}])["artifact"]["artifact_id"]
    with pytest.raises(ValueError):
        extract_features(pre, ["bandpower"])  # not a windows artifact


def test_signal_export_csv_and_edf_roundtrip(manifest):
    import csv

    import mne

    out = export("signal_csv", manifest["manifest_id"])
    assert out["artifact"]["kind"] == "normalized_signal" and out["summary"]["n_channels"] == 8
    with Path(out["artifact"]["uri"]).open(encoding="utf-8") as f:
        metadata = json.loads(next(f).removeprefix("# EFAgent metadata: "))
        rows = list(csv.reader(f))
    assert metadata["sample_rate_hz"] == 250 and metadata["unit"] == "uV"
    assert rows[0] == ["time_s", "Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"]
    assert len(rows) == 1 + 250 * 30 and float(rows[1][0]) == 0 and float(rows[-1][0]) == pytest.approx(29.996)
    edf = export("signal_edf", manifest["manifest_id"])
    raw = mne.io.read_raw_edf(edf["artifact"]["uri"], preload=True, verbose=False)
    assert raw.ch_names == ["Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"]
    assert raw.info["sfreq"] == 250
    original = np.load(manifest["source_path"]) if str(manifest["source_path"]).endswith(".npy") else None
    data = raw.get_data()
    assert data.shape[1] >= 250 * 30  # 末记录可零填充
    scale = 1e6  # EDF 物理量纲 uV → mne 内部 V
    np.testing.assert_allclose(data[:, :250] * scale, original[:, :250], atol=0.5)  # int16 量化误差内
    # 预处理产物也能导出（带 provenance 的整理信号）
    pre = preprocess(manifest["manifest_id"], [{"op": "bandpass", "low": 1, "high": 45}])["artifact"]["artifact_id"]
    out2 = export("signal_edf", pre)
    assert out2["summary"]["channel_names"][0] == "Fp1" and "bandpass_1_45" in out2["artifact"]["provenance"]["steps"]


def test_feature_exports_keep_real_electrode_pairs_and_json_numeric_types(manifest):
    import csv
    windows = segment_signal(manifest["manifest_id"], 4, stride_s=4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower", "asymmetry"])["artifact"]
    json_result = export("json", features["artifact_id"])["artifact"]
    payload = json.loads(Path(json_result["uri"]).read_text(encoding="utf-8"))
    rows = [dict(zip(payload["columns"], row)) for row in payload["rows"]]
    assert all(isinstance(row["window"], int) and isinstance(row["value"], (float, int)) for row in rows)
    pairs = [row for row in rows if row["feature"] == "asymmetry"]
    assert {row["component"] for row in pairs} == set(features["summary"]["feature_components"]["asymmetry"])
    source = np.load(features["uri"])
    assert pairs[0]["value"] == float(source["asymmetry"][0, 0])
    csv_result = export("csv", features["artifact_id"])["artifact"]
    with Path(csv_result["uri"]).open(encoding="utf-8") as stream:
        csv_pairs = [row for row in csv.DictReader(stream) if row["feature"] == "asymmetry"]
    assert [row["component"] for row in csv_pairs] == [row["component"] for row in pairs]
    assert [float(row["value"]) for row in csv_pairs] == [row["value"] for row in pairs]

def test_device_lookup_matches_openbci():
    from eegagent.worker.devices import device_lookup

    r = device_lookup(vendor="openbci", n_channels=8)
    assert r["matches"][0]["model"] == "Cyton"
    assert device_lookup(vendor="nonexistent")["matches"] == []


def test_fractional_rate_resampling_preserves_time_axis(storage):
    sr = 250.5
    path = storage / "uploads" / "fractional.npy"
    np.save(path, np.sin(2 * np.pi * 10 * np.arange(5010) / sr)[None, :])
    m = inspect_record(str(path), sample_rate_hz=sr, channel_names=["Cz"])
    out = preprocess(m["manifest_id"], [{"op": "resample", "target_hz": 128}])
    data = np.load(out["artifact"]["uri"])
    assert data.shape == (1, 2560)
    assert out["summary"]["duration_s"] == 20
    assert out["summary"]["sample_rate_hz"] == pytest.approx(128)


def test_normalized_signal_units_reach_features_plots_and_exports(manifest):
    normalized = preprocess(manifest["manifest_id"], [{"op": "normalize", "method": "zscore"}])["artifact"]["artifact_id"]
    windows = segment_signal(normalized, 4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower", "de"])
    assert features["summary"]["power_unit"] == "a.u.²"
    assert "mean_bandpower_uV2" not in features["summary"]
    image = plot("raw", normalized)
    assert image["summary"]["amplitude_unit"] == "a.u."
    assert "scale_uV_per_row" not in image["summary"]
    from eegagent.worker.mne_bridge import source_raw
    with pytest.raises(ValueError, match="物理单位"):
        source_raw(normalized)
    import csv
    result = export("csv", features["artifact"]["artifact_id"])
    with Path(result["artifact"]["uri"]).open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {r["unit"] for r in rows if r["feature"] == "bandpower"} == {"a.u.²"}


def test_low_sample_rate_does_not_report_truncated_gamma(manifest):
    low = preprocess(manifest["manifest_id"], [{"op": "resample", "target_hz": 80}])["artifact"]["artifact_id"]
    windows = segment_signal(low, 4)["artifact"]["artifact_id"]
    summary = extract_features(windows, ["bandpower", "de"])["summary"]
    assert summary["unavailable_bands"]["gamma"] == [30, 50]
    assert "gamma" not in summary["mean_de"]
    assert "gamma" not in summary["relative_bandpower"]
    json.dumps(summary, allow_nan=False)


def test_unknown_analysis_methods_do_not_silently_change_algorithm(manifest):
    with pytest.raises(ValueError, match="归一化"):
        preprocess(manifest["manifest_id"], [{"op": "normalize", "method": "typo"}])
    windows = segment_signal(manifest["manifest_id"], 4)["artifact"]["artifact_id"]
    with pytest.raises(ValueError, match="DE 方法"):
        extract_features(windows, ["de"], de_method="typo")


def test_bandpower_integrates_both_frequency_endpoints():
    from eegagent.worker.processing import _bandpower
    frequencies=np.arange(0,51,.5)
    assert _bandpower(frequencies,np.ones((1,len(frequencies))),(8,13))[0]==pytest.approx(5)


@pytest.mark.parametrize("method", ["libeer_filtfilt", "torcheeg_var"])
def test_differential_entropy_scale_law_uses_bits(method):
    from eegagent.worker.processing import _de_filtfilt, _de_lfilter

    calculate = _de_filtfilt if method == "libeer_filtfilt" else _de_lfilter
    segment = np.random.default_rng(8).normal(size=(2, 1024))
    # For differential entropy in bits, multiplying amplitude by 8 adds log2(8) = 3.
    np.testing.assert_allclose(calculate(8 * segment, 128, (8, 14)) - calculate(segment, 128, (8, 14)), 3, atol=1e-9)


@pytest.mark.parametrize("method, ddof", [("libeer_filtfilt", 1), ("torcheeg_var", 0)])
def test_feature_summary_preserves_de_convention_and_estimated_units(manifest, method, ddof):
    windows = segment_signal(manifest["manifest_id"], 4)["artifact"]["artifact_id"]
    result = extract_features(windows, ["de"], de_method=method)["summary"]
    assert result["de_log_base"] == 2 and result["de_unit"] == "bit"
    assert result["de_variance_ddof"] == ddof
    assert result["amplitude_unit_source"] == "inferred"
    assert result["amplitude_unit_estimated"] is True


def test_de_convention_does_not_change_other_requested_frequency_bands(manifest):
    windows = segment_signal(manifest["manifest_id"], 4)["artifact"]["artifact_id"]
    reference = extract_features(windows, ["bandpower"], de_method="libeer_filtfilt")
    other = extract_features(windows, ["bandpower"], de_method="torcheeg_var")
    mixed = extract_features(windows, ["bandpower", "de"], de_method="torcheeg_var")
    assert other["summary"]["bands"] == reference["summary"]["bands"]
    np.testing.assert_allclose(np.load(other["artifact"]["uri"])["bandpower"], np.load(reference["artifact"]["uri"])["bandpower"])
    assert mixed["summary"]["bands"]["alpha"] == [8, 13]
    assert mixed["summary"]["de_bands"]["alpha"] == [8, 14]
    assert mixed["summary"]["feature_components"]["de"] == ["theta", "alpha", "beta", "gamma"]
    assert len(read_artifact(mixed["artifact"]["artifact_id"], part="de")["rows"][0]["values"]) == 4


def test_readback_binds_values_to_source_coordinates_and_measurement_convention(storage):
    sr = 200
    time = np.arange(sr * 8) / sr
    path = storage / "uploads" / "calibrated.npz"
    wave = 5 * np.sin(2 * np.pi * 10 * time)
    np.savez(path, data=np.vstack([wave + 100, 2 * wave - 50]), fs=sr, unit="uV", ch_names=["F3", "F4"])
    manifest = inspect_record(str(path))
    pre = preprocess(manifest["manifest_id"], [{"op": "remove_baseline", "seconds": 1.003}])["artifact"]
    assert pre["signal"]["time_offset_s"] == 1  # actual 200 dropped samples, not a claimed 1.003 s
    windows = segment_signal(pre["artifact_id"], 2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["rms", "connectivity", "hjorth", "psd"])["artifact"]
    page = read_artifact(features["artifact_id"], part="rms", channel="F3")
    assert page["signal"]["channel_names"] == ["F3", "F4"] and page["signal"]["channel_types"] == ["eeg", "eeg"]
    assert page["analysis"]["original_recording_offset_s"] == 1
    assert page["rows"][0]["original_time_range_s"] == [1, 3]
    assert page["unit"] == "µV" and page["rows"][0]["values"][0] == pytest.approx(5 / np.sqrt(2), abs=1e-5)
    correlation = read_artifact(features["artifact_id"], part="connectivity", channel="F3")
    assert correlation["rows"][0]["values"] == pytest.approx([1, 1])
    settings = features["summary"]["measurement_settings"]
    assert settings["connectivity"]["method"] == "Pearson correlation"
    assert settings["hjorth"]["derivative"].startswith("successive sample differences")
    assert settings["welch"]["nperseg"] == 400 and settings["welch"]["noverlap"] == 200
    psd = read_artifact(features["artifact_id"], part="psd_mean", channel="F3", start=19, limit=3)
    assert psd["parent_artifact_ids"] == [windows] and psd["source_manifest_ids"] == [manifest["manifest_id"]]
    assert psd["analysis"]["window_support"]["window_s"] == 2


def test_fractional_rate_edf_and_exported_signal_are_readable_without_clock_drift(storage):
    from eegagent.worker.signals import resolve_signal

    sr = 250.5
    path = storage / "uploads" / "fractional_edf.npz"
    data = 4 * np.sin(2 * np.pi * 10 * np.arange(5010) / sr)[None, :]
    np.savez(path, data=data, fs=sr, unit="uV", ch_names=["Cz"])
    manifest = inspect_record(str(path))
    result = export("signal_edf", manifest["manifest_id"])
    loaded, rate, names, _, _ = resolve_signal(result["artifact"]["artifact_id"])
    assert loaded.shape == data.shape and rate == sr and names == ["Cz"]
    np.testing.assert_allclose(loaded, data, atol=.002)
    csv_result = export("signal_csv", manifest["manifest_id"])
    csv_data, csv_rate, _, _, _ = resolve_signal(csv_result["artifact"]["artifact_id"])
    assert csv_rate == sr
    np.testing.assert_allclose(csv_data, data, atol=5e-6)


def test_feature_aggregation_excludes_peripheral_signals_and_retains_frontal_pairs(storage):
    sr = 128
    path = storage / "uploads" / "mixed.npz"
    time = np.arange(4 * sr) / sr
    data = np.vstack([np.sin(2 * np.pi * 10 * time), 2 * np.sin(2 * np.pi * 10 * time),
                      10000 * np.sin(2 * np.pi * 6 * time)])
    np.savez(path, data=data, fs=sr, unit="uV", ch_names=["F3", "F4", "GSR"])
    record = inspect_record(str(path))
    windows = segment_signal(record["manifest_id"], 4)["artifact"]["artifact_id"]
    result = extract_features(windows, ["bandpower", "asymmetry"])["summary"]
    assert result["channel_names"] == ["F3", "F4"] and result["n_channels"] == 2
    assert result["excluded_non_eeg_channels"] == ["GSR"]
    assert result["relative_bandpower"]["alpha"] > 0.99
    assert result["frontal_alpha_asymmetry"]["F3/F4"] == pytest.approx(np.log(4), abs=1e-4)


def test_emotion_prediction_artifact_pages_keep_full_window_rows(storage):
    artifact_id = store.make_artifact_id("emotion", "1", {"test": "pagination"})
    path = store.artifact_payload_path(artifact_id, "json")
    predictions = [{"window": index, "start_s": index * 2, "end_s": index * 2 + 2,
                    "label": "positive" if index % 2 else "negative"} for index in range(45)]
    path.write_text(json.dumps({"predictions": predictions}), encoding="utf-8")
    store.save_artifact({"artifact_id": artifact_id, "kind": "emotion_assessment", "uri": str(path),
                         "summary": {"n_predictions": 45}})
    page = read_artifact(artifact_id, part="predictions", start=20, limit=20)
    assert page["part"] == "predictions" and page["start"] == 20 and page["total"] == 45
    assert page["rows"] == predictions[20:40]
    assert read_artifact(artifact_id, part="predictions", start=40, limit=20)["rows"] == predictions[40:]
