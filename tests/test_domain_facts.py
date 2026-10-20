"""Scientific regressions from the platform audit; all data and outputs are isolated."""
import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import cleaning, signals, store
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.local_analysis import analyze_signal
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.outputs import export
from eegagent.worker.processing import preprocess


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path / "storage"))
    signals._CACHE.clear()
    return store.ensure_layout()


def record(storage, *, unit="uV", unit_source="confirmed", **metadata):
    rate = 128
    data = np.random.default_rng(31).normal(0, 20, (8, rate * 8))
    path = storage / "uploads" / "record.npz"
    np.savez(path, data=data, sample_rate_hz=rate,
             channel_names=["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4"],
             unit=unit, unit_source=unit_source, **metadata)
    return inspect_record(str(path))


@pytest.mark.parametrize("unit,source", [("V", "confirmed"), ("mV", "confirmed"), ("uV", "confirmed"),
                                         ("uV", "inferred"), ("uV", "format_assumption"),
                                         ("dataset_native", "dataset_native"), ("a.u.", "normalization"), ("unknown", "unknown")])
def test_signal_csv_preserves_numbers_and_unit_evidence_on_reimport(storage, unit, source):
    original = record(storage, unit=unit, unit_source=source)
    expected, rate, names, original_steps, _ = signals.resolve_signal(original["manifest_id"])
    exported = export("signal_csv", original["manifest_id"])["artifact"]
    loaded = inspect_record(exported["uri"])
    actual, actual_rate, actual_names, steps, _ = signals.resolve_signal(loaded["manifest_id"])
    np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=1e-8)
    assert actual_rate == rate and actual_names == names
    assert signals.amplitude_unit(steps) == signals.amplitude_unit(original_steps)
    assert signals.amplitude_unit_source(steps) == signals.amplitude_unit_source(original_steps)
    if source in ("inferred", "format_assumption"):
        assert loaded["unit"]["source"] == source


def test_signal_csv_keeps_epoch_segments_and_true_upstream_processing(storage):
    source = record(storage)
    rate = int(source["sample_rate_hz"])
    source["signal_kind"] = "epochs"
    source["segments"] = [
        {"start_sample": 0, "end_sample": 4 * rate, "original_start_s": 10., "original_end_s": 14.,
         "epoch_index": 5, "epoch_tmin_s": -.2, "original_epoch_tmin_s": -.2, "event_code": 1, "event_label": "任务"},
        {"start_sample": 4 * rate, "end_sample": 8 * rate, "original_start_s": 20., "original_end_s": 24.,
         "epoch_index": 7, "epoch_tmin_s": -.2, "original_epoch_tmin_s": -.2, "event_code": 1, "event_label": "任务"},
    ]
    store.save_manifest(source)
    processed = preprocess(source["manifest_id"], [{"op": "bandpass", "low": 1, "high": 40}])["artifact"]
    exported = export("signal_csv", processed["artifact_id"])["artifact"]
    loaded = inspect_record(exported["uri"])
    geometry = signals.signal_metadata(loaded["manifest_id"])
    assert geometry["kind"] == "epochs" and geometry["segments"] == source["segments"]
    assert signals.filter_bounds([], geometry["source_processing"]) == (1., 40.)
    assert "bandpass_1_40" in geometry["source_processing"]["value"]["processing_steps"]


def test_csv_declared_channel_names_must_match_the_actual_columns(storage):
    path = storage / "uploads" / "bad_header.csv"
    path.write_text('# EEGAgent metadata: {"sample_rate_hz":128,"channel_names":["F3","F4"],"unit":"uV"}\n'
                    'time_s,P3,P4\n0,1,2\n0.0078125,2,3\n', encoding="utf-8")
    with pytest.raises(ValueError, match="channel_names"):
        inspect_record(str(path))


@pytest.mark.parametrize("brand", ["EFAgent", "EEGAgent"])
def test_current_and_legacy_csv_metadata_headers_preserve_short_signal_identity(storage, brand):
    from eegagent.worker.tools import run_tool
    path = storage / "uploads" / (brand + "-signal.csv")
    path.write_text(f'# {brand} metadata: {{"sample_rate_hz":128,"channel_names":["F3","F4"],"unit":"uV","unit_source":"confirmed"}}\n'
                    'time_s,F3,F4\n0,1,2\n0.0078125,2,3\n0.015625,3,4\n', encoding="utf-8")
    manifest = run_tool("register_upload", {"path": str(path)})["manifest"]
    assert manifest["modality"] == "eeg" and manifest["sample_rate_hz"] == 128
    assert manifest["unit"] == {"value": "uV", "source": "file_header"}
    exported = export("signal_csv", manifest["manifest_id"])["artifact"]
    assert Path(exported["uri"]).read_text(encoding="utf-8").startswith("# EFAgent metadata: ")


def test_file_header_baseline_selects_the_stimulus_range(storage):
    source = record(storage, baseline_duration_s=3.)
    assert source["metadata_evidence"]["baseline_duration_s"]["source"] == "file_header"
    result = analyze_signal({"manifest_id": source["manifest_id"], "emotion_review": True}, "ses_baseline")
    assert result["summary"]["time_range_s"] == [3., 8.]
    windows = next(row for row in result["artifacts"] if row["kind"] == "windows")
    payload = json.loads(Path(windows["uri"]).read_text(encoding="utf-8"))
    assert payload["windows"][0]["start_s"] == 3.
    override = analyze_signal({"manifest_id": source["manifest_id"], "emotion_review": True, "start_s": 0.}, "ses_baseline_override")
    assert override["summary"]["time_range_s"] == [0., 8.]


def test_generic_preprocessing_is_a_fact_and_not_an_eav_input_contract(storage):
    source = record(storage)
    updated = update_record_metadata(source["manifest_id"], preprocessing={"highpass_hz": 1., "lowpass_hz": 40.})["manifest"]
    assert "input_stage" not in updated["metadata_evidence"]
    geometry = signals.signal_metadata(updated["manifest_id"])
    assert signals.filter_bounds([], geometry["source_processing"]) == (1., 40.)
    assert signals.resolve_signal(updated["manifest_id"])[3] == ["unit_uV"]


@pytest.mark.parametrize("unit,source,physical", [("uV", "confirmed", True), ("uV", "inferred", False),
                                                 ("dataset_native", "dataset_native", False), ("unknown", "unknown", False)])
def test_asr_uses_known_filter_facts_and_only_reports_physical_thresholds_when_known(storage, monkeypatch, unit, source, physical):
    import asrpy

    manifest = record(storage, unit=unit, unit_source=source, preprocessing="bandpass_4_45")
    before = signals.resolve_signal(manifest["manifest_id"])[0]
    class ScaleASR:
        def __init__(self, **kwargs):
            pass
        def fit(self, raw):
            np.testing.assert_allclose(raw.get_data() * 1e6, before)
        def transform(self, raw):
            result = raw.copy()
            result._data *= .9
            return result
    monkeypatch.setattr(asrpy, "ASR", ScaleASR)
    result = cleaning.clean_artifacts(manifest["manifest_id"], "asr", plot=False)
    assert "highpass_0.5" not in result["artifact"]["provenance"]["steps"]
    assert result["summary"]["relative_rms_change"] == pytest.approx(.1)
    assert result["summary"]["change_metric"]["absolute_threshold"] == (1. if physical else None)
    assert (result["summary"]["changed_fraction"] is not None) == physical


def test_iclabel_requires_its_training_voltage_scale_but_relative_analysis_remains_available(storage):
    manifest = record(storage, unit_source="inferred")
    assert signals.amplitude_unit_source(signals.resolve_signal(manifest["manifest_id"])[3]) == "inferred"
    with pytest.raises(ValueError, match="ICLabel.*电压尺度"):
        cleaning.clean_artifacts(manifest["manifest_id"], "ica_iclabel", plot=False)


def test_ica_defaults_to_the_actual_rank_after_interpolation_and_average_reference(storage, monkeypatch):
    import mne
    import mne_icalabel
    from eegagent.worker.channel_orders import DEAP_MAHNOB_CHANNEL_ORDER

    values = np.random.default_rng(17).normal(0, 10, (31, 128 * 16))
    data = np.vstack([values, (values[0] + values[1]) / 2])
    path = storage / "uploads" / "interpolated.npz"
    np.savez(path, data=data, sample_rate_hz=128., channel_names=DEAP_MAHNOB_CHANNEL_ORDER, unit="uV")
    manifest = inspect_record(str(path))
    chosen = []
    class RankICA:
        def __init__(self, *, n_components, **kwargs):
            self.n_components = n_components
            chosen.append(n_components)
        def fit(self, raw, **kwargs):
            return self
        def apply(self, raw, **kwargs):
            return raw
        def save(self, path, **kwargs):
            Path(path).write_text("rank-selection fixture", encoding="utf-8")
    monkeypatch.setattr(mne.preprocessing, "ICA", RankICA)
    monkeypatch.setattr(mne_icalabel, "label_components", lambda raw, ica, **kwargs: {
        "labels": ["brain"] * ica.n_components, "y_pred_proba": np.ones(ica.n_components)})
    result = cleaning.clean_artifacts(manifest["manifest_id"], "ica_iclabel", plot=False)
    assert chosen == [30] and result["summary"]["effective_rank"] == 30
    with pytest.raises(ValueError, match="实际有效秩 30"):
        cleaning.clean_artifacts(manifest["manifest_id"], "ica_iclabel", n_components=31, plot=False)
