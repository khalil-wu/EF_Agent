"""Scientific graphics must preserve values and disclose what their summaries mean."""

from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import outputs, store, signals
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.processing import extract_features, preprocess, segment_signal
from eegagent.worker.sandbox import run_python


@pytest.fixture()
def scientific_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    root = store.ensure_layout()
    sr = 128
    t = np.arange(sr * 12) / sr
    data = np.array([(index + 1) * np.sin(2 * np.pi * 10 * t) for index in range(8)])
    path = root / "uploads" / "paper.npy"
    np.save(path, data)
    manifest = inspect_record(str(path), sample_rate_hz=sr,
                              channel_names=["Fp1", "F3", "C3", "P3", "O1", "F4", "P4", "O2"])
    return manifest, data


@pytest.fixture()
def captured_plot(monkeypatch):
    captured = {}

    def capture(fig, path):
        captured["fig"] = fig
        return {"png": {"uri": str(path), "media_type": "image/png"}}

    monkeypatch.setattr(outputs, "save_files", capture)
    return captured


def test_builtin_figure_has_native_vectors_and_exact_channel_selection(scientific_signal):
    manifest, original = scientific_signal
    result = outputs.plot("psd", manifest["manifest_id"], channels=["O2", "Fp1"])
    assert result["summary"]["channels_shown"] == ["O2", "Fp1"]
    assert result["summary"]["peak_hz"] == 10
    assert result["summary"]["title"] == "功率谱密度（PSD）"
    assert "频率" in result["summary"]["description"]
    record = store.load_artifact(result["artifact"]["artifact_id"])
    assert set(record["representations"]) == {"png", "svg", "pdf"}
    svg = Path(record["representations"]["svg"]["uri"]).read_text(encoding="utf-8")
    assert "<text" in svg and "<image" not in svg
    pdf = Path(record["representations"]["pdf"]["uri"]).read_bytes()
    assert pdf.startswith(b"%PDF")
    assert outputs.export("svg", record["artifact_id"])["artifact"]["media_type"] == "image/svg+xml"
    assert outputs.export("pdf", record["artifact_id"])["artifact"]["media_type"] == "application/pdf"
    for kind in ("png", "svg", "pdf"):
        exported = outputs.export(kind, record["artifact_id"])
        assert exported["artifact"]["time_range_s"] == [0.0, 12.0]
        assert exported["summary"]["time_range_s"] == [0.0, 12.0]
        assert exported["summary"]["analysis_interval"] == result["summary"]["analysis_interval"]
        assert exported["summary"]["title"] == result["summary"]["title"]
        assert exported["summary"]["description"] == result["summary"]["description"]
    np.testing.assert_array_equal(np.load(manifest["source_path"]), original)


def test_download_names_distinguish_real_processing_parameters_and_versions(scientific_signal):
    manifest, _ = scientific_signal
    base = {"kind": "plot", "summary": {"kind": "clean_asr"}, "source_manifest_ids": [manifest["manifest_id"]],
            "time_range_s": [0, 10]}
    first = {**base, "artifact_id": "art_123456789abc", "provenance": {"steps": ["asr_k20"]}}
    second = {**base, "artifact_id": "art_abcdef123456", "provenance": {"steps": ["asr_k30"]}}
    first_name, second_name = (outputs.download_filename(record, ".svg") for record in (first, second))
    assert first_name != second_name
    assert "ASR20" in first_name and "ASR30" in second_name
    assert "0-10秒" in first_name and first_name.endswith("123456789abc.svg")
    assert first_name.startswith("ASR清洗前后对照_")


@pytest.mark.parametrize("width, mm", [("wide", 183), ("single", 89)])
def test_publication_overview_has_exact_final_size_and_readable_embedded_text(scientific_signal, width, mm):
    import pymupdf

    manifest, original = scientific_signal
    windows = segment_signal(manifest["manifest_id"], 4, stride_s=2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    result = outputs.plot("overview", features, channels=["O1", "O2"], profile="publication", width=width)
    record = store.load_artifact(result["artifact"]["artifact_id"])
    page = pymupdf.open(record["representations"]["pdf"]["uri"])[0]
    assert page.rect.width / 72 * 25.4 == pytest.approx(mm, abs=.01)
    spans = [span for block in page.get_text("dict")["blocks"] if "lines" in block
             for line in block["lines"] for span in line["spans"] if span["text"].strip()]
    assert {span["text"] for span in spans} >= {"a", "b", "c"}
    for span in spans:
        assert 5 <= span["size"] <= 8.01
        if span["text"] not in ("a", "b", "c"):
            assert span["size"] <= 7.01
    assert result["summary"]["panels"] == ["waveform", "psd", "bandpower_timeline"]
    assert result["summary"]["channels_shown"] == ["O1", "O2"]
    np.testing.assert_array_equal(np.load(manifest["source_path"]), original)


def test_timeline_does_not_join_unobserved_internal_gaps(scientific_signal, captured_plot):
    manifest, _ = scientific_signal
    windows = segment_signal(manifest["manifest_id"], 1, stride_s=4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    result = outputs.plot("bandpower_timeline", features, band="alpha")
    curve = captured_plot["fig"].axes[0].lines[0]
    assert np.count_nonzero(np.isnan(curve.get_ydata())) == 2
    assert result["summary"]["unrepresented_time_ranges_s"] == [[1, 4], [5, 8], [9, 12]]


def test_default_psd_includes_the_recording_tail_and_explicit_range_is_respected(scientific_signal):
    sr = 128
    t = np.arange(sr * 63) / sr
    data = np.sin(2 * np.pi * 10 * t)
    tail = t >= 60
    data[tail] = 100 * np.sin(2 * np.pi * 20 * t[tail])
    path = store.ensure_layout() / "uploads" / "recording_with_tail.npy"
    np.save(path, data[None, :])
    manifest = inspect_record(str(path), sample_rate_hz=sr, channel_names=["Fp1"])

    full = outputs.plot("psd", manifest["manifest_id"])
    first_minute = outputs.plot("psd", manifest["manifest_id"], start_s=0, end_s=60)

    assert full["summary"]["peak_hz"] == 20
    assert first_minute["summary"]["peak_hz"] == 10
    assert full["artifact"]["time_range_s"] == [0.0, 63.0]
    assert first_minute["artifact"]["time_range_s"] == [0.0, 60.0]
    assert full["summary"]["analysis_interval"] == "0–63 秒"
    assert first_minute["summary"]["analysis_interval"] == "0–60 秒"


def test_psd_iqr_and_mean_are_descriptive_and_unclipped(scientific_signal, monkeypatch):
    manifest, data = scientific_signal
    captured = {}

    def capture(fig, kind, inputs, source_manifests, parents, time_range, summary, steps):
        captured.update(fig=fig, summary=summary)
        return {"summary": summary}

    monkeypatch.setattr(outputs, "_save_plot", capture)
    outputs.plot("psd", manifest["manifest_id"])
    assert captured["summary"]["shading"] == "channel IQR, not confidence interval"
    from scipy.signal import welch
    f, p = welch(data, fs=128, nperseg=256, window="hamming", axis=1)
    m = (f >= .5) & (f <= 60)
    ax = captured["fig"].axes[0]
    np.testing.assert_allclose(ax.lines[8].get_ydata(), p[:, m].mean(axis=0), atol=1e-12)
    assert ax.get_ylim()[0] <= np.min(p[:, m][p[:, m] > 0])
    assert ax.get_ylim()[1] >= np.max(p[:, m])


def test_raw_preserves_large_excursions_and_discloses_inferred_units(scientific_signal, monkeypatch):
    manifest, original = scientific_signal
    original[0, 128] = 10_000
    np.save(manifest["source_path"], original)
    manifest = inspect_record(manifest["source_path"], sample_rate_hz=128, channel_names=manifest["channel_names"])
    manifest["unit"] = {"value": "uV", "source": "inferred", "confidence": .5}
    store.save_manifest(manifest)
    captured = {}

    def capture(fig, kind, inputs, source_manifests, parents, time_range, summary, steps):
        captured.update(fig=fig, summary=summary)
        return {"summary": summary}

    monkeypatch.setattr(outputs, "_save_plot", capture)
    outputs.plot("raw", manifest["manifest_id"], channels=["Fp1"], end_s=3)
    summary = captured["summary"]
    assert summary["unit_estimated"] and "估计" in summary["unit_note"]
    ax = captured["fig"].axes[0]
    trace = ax.lines[1].get_ydata()
    assert ax.get_ylim()[0] <= np.min(trace) and ax.get_ylim()[1] >= np.max(trace)
    np.testing.assert_allclose(trace * summary["scale_per_row"], original[0, :384] - original[0, :384].mean(), atol=.02)


def test_invalid_plot_filters_cannot_silently_change_the_scientific_question(scientific_signal):
    manifest, _ = scientific_signal
    with pytest.raises(ValueError, match="通道"):
        outputs.plot("psd", manifest["manifest_id"], channels=["O1", "missing"])
    with pytest.raises(ValueError, match="时间范围"):
        outputs.plot("psd", manifest["manifest_id"], start_s=-1)
    windows = segment_signal(manifest["manifest_id"], 4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    with pytest.raises(ValueError, match="频带"):
        outputs.plot("bandpower_timeline", features, band="alhpa")
    with pytest.raises(ValueError, match="更细窗口"):
        outputs.plot("bandpower_timeline", features, start_s=1, end_s=2)


def test_timeline_does_not_classify_power_excursions_as_artifacts(scientific_signal):
    manifest, data = scientific_signal
    data[:, 512:768] *= 1000
    np.save(manifest["source_path"], data)
    manifest = inspect_record(manifest["source_path"], sample_rate_hz=128, channel_names=manifest["channel_names"])
    windows = segment_signal(manifest["manifest_id"], 2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    result = outputs.plot("bandpower_timeline", features, band="alpha")
    assert "outlier_windows" not in result["summary"]
    values = np.load(store.load_artifact(features)["uri"])["bandpower"]
    assert values[2, :, 2].mean() > 100_000 * values[0, :, 2].mean()
    assert result["summary"]["yscale"]["alpha"] == "log"


def test_normalized_units_and_unavailable_bands_remain_explicit(scientific_signal):
    manifest, _ = scientific_signal
    normalized = preprocess(manifest["manifest_id"], [{"op": "normalize", "method": "zscore"},
                                                    {"op": "resample", "target_hz": 80}])["artifact"]["artifact_id"]
    windows = segment_signal(normalized, 4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    image = outputs.plot("bandpower_timeline", features)
    assert image["summary"]["unavailable_bands"] == ["gamma"]
    assert "归一化数值（任意单位）" in image["summary"]["unit_note"]


def test_generated_script_uses_same_figure_api_and_vector_export(scientific_signal, commit_session):
    manifest, _ = scientific_signal
    commit_session("ses_scientific_figure", [manifest])
    result = run_python(f'''
data, sr, channels = eeg.load_signal("{manifest["manifest_id"]}")
steps = eeg.signal_steps("{manifest["manifest_id"]}")
fig, ax = eeg.figure(width="single", height=2.5)
ax.plot(data[0, :128], color=eeg.colors[0])
ax.set(xlabel="采样点", ylabel="振幅（" + eeg.amplitude_unit(steps) + "）")
ax.set_title("首通道采样波形")
import matplotlib.pyplot as plt
print("FONT", plt.rcParams["font.family"][0])
eeg.save_figure(fig, {{"method": "direct trace", "unit_note": eeg.unit_note(steps),
                      "sample_rate_note": eeg.sampling_note(steps, sr)}}, name="script_trace")
eeg.save_figure(fig, {{"method": "direct trace"}}, name="explicit_trace",
                title="首通道波形", description="展示首个通道前 128 个采样点的振幅变化。")
eeg.save_table([{{"channel": channels[0], "samples": 128}}], name="coverage",
               title="波形展示范围", description="记录图中通道和展示的采样点数。")
''', "ses_scientific_figure", timeout_s=60)
    assert result["status"] == "ok", result.get("error")
    artifact = result["artifacts"][0]
    assert artifact["summary"]["available_formats"] == ["png", "svg", "pdf"]
    assert artifact["summary"]["title"] == "首通道采样波形"
    assert "采样点" in artifact["summary"]["description"]
    assert any(font in result["stdout"] for font in ("Microsoft YaHei", "Noto Sans SC", "Noto Sans CJK SC", "SimHei"))
    assert result["artifacts"][1]["summary"]["title"] == "首通道波形"
    assert result["artifacts"][1]["summary"]["description"] == "展示首个通道前 128 个采样点的振幅变化。"
    table = result["artifacts"][2]
    exported = outputs.export("json", table["artifact_id"])
    assert exported["summary"]["title"] == "波形展示范围"
    assert exported["summary"]["description"] == "记录图中通道和展示的采样点数。"
    record = store.load_artifact(artifact["artifact_id"])
    assert Path(record["representations"]["svg"]["uri"]).exists()


def test_format_assumptions_are_visible_on_amplitude_and_time_axes(scientific_signal, monkeypatch):
    manifest, _ = scientific_signal
    manifest["unit"] = {"value": "uV", "source": "format_assumption"}
    manifest["metadata_evidence"] = {"sample_rate_hz": {"source": "format_assumption"}}
    store.save_manifest(manifest)
    captured = {}

    def capture(fig, kind, inputs, source_manifests, parents, time_range, summary, steps):
        captured.update(fig=fig, summary=summary)
        return {"summary": summary}

    monkeypatch.setattr(outputs, "_save_plot", capture)
    result = outputs.plot("raw", manifest["manifest_id"], channels=["Fp1"])
    assert result["summary"]["amplitude_unit_source"] == "format_assumption"
    assert result["summary"]["unit_assumed"]
    assert "按格式约定使用 µV" in captured["fig"]._supxlabel.get_text()
    assert "按格式约定采样率：128 Hz" in captured["fig"]._supxlabel.get_text()


def test_window_figures_keep_source_offset_and_actual_welch_method(scientific_signal, monkeypatch):
    manifest, _ = scientific_signal
    source = preprocess(manifest["manifest_id"], [{"op": "remove_baseline", "seconds": 3}])["artifact"]["artifact_id"]
    windows = segment_signal(source, 4, stride_s=2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    monkeypatch.setattr(outputs, "_save_plot", lambda fig, kind, inputs, sources, parents, tr, summary, steps: {"summary": summary})
    psd = outputs.plot("psd", features)["summary"]
    assert psd["original_recording_offset_s"] == 3
    assert psd["method"] == "汉明窗 2 秒 · 重叠 50%"
    assert "3 个窗口的平均功率谱" in psd["analysis_interval"]
    timeline = outputs.plot("timeline", features)["summary"]
    assert timeline["original_recording_offset_s"] == 3
    assert timeline["window_s"] == 4 and timeline["stride_s"] == 2


def test_plot_excludes_peripheral_channels_from_voltage_axes(scientific_signal, monkeypatch):
    manifest, _ = scientific_signal
    manifest["channel_names"][-1] = "GSR"
    manifest["channel_types"] = ["eeg"] * 7 + ["gsr"]
    store.save_manifest(manifest)
    monkeypatch.setattr(outputs, "_save_plot", lambda fig, kind, inputs, sources, parents, tr, summary, steps: {"summary": summary})
    summary = outputs.plot("psd", manifest["manifest_id"])["summary"]
    assert summary["n_channels"] == 7 and "GSR" not in summary["channels_shown"]
    with pytest.raises(ValueError, match="非电压"):
        outputs.plot("raw", manifest["manifest_id"], channels=["Fp1", "GSR"])


def test_zero_power_is_shown_without_inventing_a_peak(scientific_signal, monkeypatch):
    manifest, data = scientific_signal
    np.save(manifest["source_path"], np.zeros_like(data))
    manifest = inspect_record(manifest["source_path"], sample_rate_hz=128, channel_names=manifest["channel_names"])
    captured = {}

    def capture(fig, kind, inputs, sources, parents, tr, summary, steps):
        captured.update(fig=fig, summary=summary)
        return {"summary": summary}

    monkeypatch.setattr(outputs, "_save_plot", capture)
    summary = outputs.plot("psd", manifest["manifest_id"], channels=["Fp1"])["summary"]
    assert summary["peak_hz"] is None and summary["shading"] is None
    assert captured["fig"].axes[0].get_yscale() == "linear"


@pytest.mark.parametrize("requested, expected", [
    ({}, [0.0, 63.0]),
    ({"end_s": 60}, [0.0, 60.0]),
    ({"start_s": 30, "end_s": 60}, [30.0, 60.0]),
    ({"start_s": 30}, [30.0, 63.0]),
])
def test_raw_full_recording_and_selected_seconds_share_exact_range_metadata(scientific_signal, captured_plot, requested, expected):
    sr = 128
    t = np.arange(sr * 63) / sr
    data = np.sin(2 * np.pi * 10 * t)[None, :]
    path = store.ensure_layout() / "uploads" / "full_recording.npy"
    np.save(path, data)
    manifest = inspect_record(str(path), sample_rate_hz=sr, channel_names=["Fp1"])
    result = outputs.plot("raw", manifest["manifest_id"], **requested)
    record = store.load_artifact(result["artifact"]["artifact_id"])
    ax = captured_plot["fig"].axes[0]
    assert list(ax.get_xlim()) == expected
    assert record["time_range_s"] == result["summary"]["time_range_s"] == expected
    params = record["provenance"]["params"]
    assert [params["start_s"], params["end_s"]] == expected
    x = ax.lines[1].get_xdata()
    assert x[0] == expected[0] and x[-1] == expected[1] - 1 / sr
    a, b = (round(value * sr) for value in expected)
    sample_indices = np.rint((x - expected[0]) * sr).astype(int)
    np.testing.assert_allclose(ax.lines[1].get_ydata() * result["summary"]["scale_per_row"],
                               data[0, a:b][sample_indices] - data[0, a:b].mean())
    np.testing.assert_array_equal(np.load(path), data)


def test_full_overview_retains_brief_extrema_and_the_recording_tail(scientific_signal, captured_plot):
    sr = 128
    data = np.zeros((1, sr * 120))
    data[0, 8123] = 10_000
    data[0, 15_123] = -8000
    data[0, -1] = 2500
    path = store.ensure_layout() / "uploads" / "transient_tail.npy"
    np.save(path, data)
    manifest = inspect_record(str(path), sample_rate_hz=sr, channel_names=["Fp1"])
    result = outputs.plot("raw", manifest["manifest_id"])
    summary = result["summary"]
    ax = captured_plot["fig"].axes[0]
    trace = ax.lines[1]
    recovered = trace.get_ydata() * summary["scale_per_row"] + data.mean()
    assert summary["display_sampling"] == "block min/max; endpoints retained"
    assert len(recovered) < 6005 < data.shape[1]
    assert recovered.max() == pytest.approx(10_000) and recovered.min() == pytest.approx(-8000)
    assert recovered[-1] == pytest.approx(2500)
    assert trace.get_xdata()[-1] == 120 - 1 / sr
    assert ax.get_ylim()[0] <= trace.get_ydata().min() and ax.get_ylim()[1] >= trace.get_ydata().max()
    np.testing.assert_array_equal(np.load(path), data)


def test_timeline_range_keeps_complete_windows_and_discloses_unrepresented_edges(scientific_signal, captured_plot):
    manifest, _ = scientific_signal
    windows = segment_signal(manifest["manifest_id"], 4)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    result = outputs.plot("bandpower_timeline", features, start_s=3, end_s=10, band="alpha")
    summary = result["summary"]
    record = store.load_artifact(result["artifact"]["artifact_id"])
    assert list(captured_plot["fig"].axes[0].get_xlim()) == [3, 10]
    np.testing.assert_array_equal(captured_plot["fig"].axes[0].lines[0].get_xdata(), [6])
    assert record["time_range_s"] == summary["time_range_s"] == [3.0, 10.0]
    assert summary["window_evidence_time_range_s"] == [4.0, 8.0]
    assert summary["unrepresented_time_ranges_s"] == [[3.0, 4.0], [8.0, 10.0]]
    assert summary["n_windows"] == 1
    assert [record["provenance"]["params"][key] for key in ("start_s", "end_s")] == [3.0, 10.0]


def test_feature_psd_selected_seconds_recompute_signal_instead_of_relabeling_mean(scientific_signal, captured_plot):
    manifest, data = scientific_signal
    t = np.arange(data.shape[1]) / 128
    data[:, t >= 6] = 50 * np.sin(2 * np.pi * 20 * t[t >= 6])
    np.save(manifest["source_path"], data)
    manifest = inspect_record(manifest["source_path"], sample_rate_hz=128, channel_names=manifest["channel_names"])
    windows = segment_signal(manifest["manifest_id"], 2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    full = outputs.plot("psd", features)
    selected = outputs.plot("psd", features, start_s=0, end_s=6)
    assert full["summary"]["peak_hz"] == 20
    assert selected["summary"]["peak_hz"] == 10
    assert selected["summary"]["aggregation"] == "equal weight over actual Welch subwindows"
    settings = selected["summary"]["measurement_settings"]["welch"]
    assert settings["n_continuous_segments"] == 1 and settings["n_welch_segments"] == 5
    from scipy.signal import welch
    frequencies, density = welch(data[:, :6 * 128], fs=128, nperseg=256, noverlap=128, window="hamming", axis=1)
    mask = (frequencies >= .5) & (frequencies <= 60)
    np.testing.assert_allclose(captured_plot["fig"].axes[0].lines[8].get_ydata(), density[:, mask].mean(axis=0), atol=1e-12)
    assert selected["artifact"]["time_range_s"] == selected["summary"]["time_range_s"] == [0.0, 6.0]
    record = store.load_artifact(selected["artifact"]["artifact_id"])
    assert record["provenance"]["params"]["signal_source"] == manifest["manifest_id"]


def test_processed_plot_ranges_remain_source_seconds_and_retain_original_offset(scientific_signal, captured_plot):
    manifest, _ = scientific_signal
    source = preprocess(manifest["manifest_id"], [{"op": "remove_baseline", "seconds": 3}])["artifact"]["artifact_id"]
    windows = segment_signal(source, 2)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    for kind, source_id in (("raw", source), ("psd", features), ("bandpower_timeline", features)):
        result = outputs.plot(kind, source_id, start_s=2, end_s=6)
        assert result["artifact"]["time_range_s"] == result["summary"]["time_range_s"] == [2.0, 6.0]
        assert result["summary"]["original_recording_offset_s"] == 3
        assert result["summary"]["original_recording_time_range_s"] == [5.0, 9.0]
        assert "source start" in result["summary"]["time_axis"]
        if kind != "psd":
            assert list(captured_plot["fig"].axes[0].get_xlim()) == [2.0, 6.0]
