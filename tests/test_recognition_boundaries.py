"""Input equivalence and scientific mismatch tests, independent of emotion accuracy."""
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.io import savemat

from eegagent.worker import remote_model, signals, store
from eegagent.worker.channel_orders import EAV_CHANNEL_ORDER, DEAP_MAHNOB_CHANNEL_ORDER
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.models import route_model
from eegagent.worker.registry import load_registry

EAV = "eav_eegnet_emotion5_loso_fold0"
DEAP = "camer_deap_fixed_1s_fold0"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    return store.ensure_layout() / "uploads"


def declared(folder, names=None, data=None, model=EAV):
    names = names if names is not None else EAV_CHANNEL_ORDER
    raw = data if data is not None else np.random.default_rng(31).normal(size=(len(names), 10000))
    path = folder / "signal.npz"
    np.savez(path, data=raw, fs=500 if model == EAV else 128, ch_names=names, unit="dataset_native")
    initial = inspect_record(str(path))
    manifest = update_record_metadata(initial["manifest_id"], preprocessing="eav_raw" if model == EAV else "deap_official_preprocessed",
                                      task_mode="speaking" if model == EAV else None)["manifest"]
    return manifest, raw


def video(duration=60, offset=3):
    return {"manifest_id": "vid_test", "modality": "video", "duration_s": duration,
            "timing": {"status": "cfr_verified"}, "role": {"value": "face", "source": "user_confirmed"},
            "sync": {"source": "user_confirmed", "start_offset_s": offset}}


@pytest.mark.parametrize("suffix", ["npy", "npz", "mat", "csv"])
def test_equal_waveform_containers_use_explicit_contract_without_rescaling(folder, suffix):
    raw = np.random.default_rng(23).normal(size=(32, 8064))
    path = folder / f"same.{suffix}"
    if suffix == "npy":
        np.save(path, raw)
    elif suffix == "npz":
        np.savez(path, data=raw)
    elif suffix == "mat":
        savemat(path, {"data": raw})
    else:
        np.savetxt(path, raw.T, delimiter=",", header=",".join(DEAP_MAHNOB_CHANNEL_ORDER), comments="", fmt="%.18e")
    initial = inspect_record(str(path))
    assert route_model("emotion", initial, video(), model_id=DEAP)["selected_model"] is None
    result = update_record_metadata(initial["manifest_id"], sample_rate_hz=128, channel_names=DEAP_MAHNOB_CHANNEL_ORDER,
        unit="dataset_native", reference="average", preprocessing="deap_official_preprocessed")["manifest"]
    assert result["format_profile"] is None
    assert route_model("emotion", result, video(), model_id=DEAP)["selected_model"] == DEAP
    np.testing.assert_array_equal(signals.load_signal(result["manifest_id"])[0], raw)
    assert result["metadata_evidence"]["origin"]["value"] == "unverified"


def test_generic_unlabelled_array_needs_actual_channel_mapping(folder):
    path = folder / "anonymous.npy"
    np.save(path, np.ones((32, 8064)))
    initial = inspect_record(str(path))
    result = update_record_metadata(initial["manifest_id"], sample_rate_hz=128, unit="dataset_native",
                                    preprocessing="deap_official_preprocessed")["manifest"]
    assert result["channel_names"] == [f"ch{i+1}" for i in range(32)]
    assert route_model("emotion", result, video(), model_id=DEAP)["selected_model"] is None


def test_extra_channels_and_old_names_are_uniquely_mapped_and_recorded(folder, monkeypatch):
    names = ["EOG", "Trigger", *reversed(EAV_CHANNEL_ORDER)]
    names = ["T3" if name == "T7" else name for name in names]
    manifest, raw = declared(folder, names)
    assert route_model("emotion", manifest, model_id=EAV)["selected_model"] == EAV
    def receive(source_id, data, sr, ordered_names, model, *_):
        assert sr == 500 and ordered_names == EAV_CHANNEL_ORDER
        np.testing.assert_array_equal(data, raw[2:][::-1])
        assert model["source_channel_order"] == names
        assert model["excluded_source_channels"] == ["EOG", "Trigger"]
        assert model["source_channel_mapping"] == list(reversed(range(2, 32)))
        return {"status": "ok"}
    monkeypatch.setattr(remote_model, "_run_model", receive)
    assert remote_model.run_model(manifest["manifest_id"], EAV)["status"] == "ok"


@pytest.mark.parametrize("extra", ["EEG Fp1-REF", "T3"])
def test_duplicate_electrode_aliases_never_select_first_row(folder, extra):
    manifest, _ = declared(folder, [*EAV_CHANNEL_ORDER, extra])
    route = route_model("emotion", manifest, model_id=EAV)
    assert route["selected_model"] is None
    assert any("重复" in reason for reason in route["candidates"][0]["reasons"])
    with pytest.raises(ValueError, match="重复"):
        remote_model.run_model(manifest["manifest_id"], EAV)


def test_same_channel_count_cannot_replace_missing_electrode(folder):
    manifest, _ = declared(folder, [*EAV_CHANNEL_ORDER[:-1], "EOG"])
    route = route_model("emotion", manifest, model_id=EAV)
    assert route["selected_model"] is None
    assert route["candidates"][0]["compatibility"] == "incompatible"
    assert any("缺 1" in reason for reason in route["candidates"][0]["reasons"])


@pytest.mark.parametrize("change,reason", [({"sample_rate_hz": 250}, "采样率"),
    ({"reference": "average"}, "参考"), ({"unit": "uV"}, "数值尺度")])
def test_wrong_scientific_contract_is_not_automatically_repaired(folder, change, reason):
    manifest, _ = declared(folder)
    result = update_record_metadata(manifest["manifest_id"], **change)["manifest"]
    route = route_model("emotion", result, model_id=EAV)
    assert route["selected_model"] is None
    assert any(reason in item for item in route["candidates"][0]["reasons"])


def test_physical_mne_scale_cannot_be_relabelled_native(folder):
    manifest, _ = declared(folder)
    record = {**manifest, "source_format": "edf"}
    route = route_model("emotion", record, model_id=EAV)
    assert route["selected_model"] is None
    assert any("换算尚未核实" in item for item in route["candidates"][0]["reasons"])


def test_conflicting_baseline_is_not_ignored(folder):
    manifest, _ = declared(folder, DEAP_MAHNOB_CHANNEL_ORDER, np.ones((32, 8064)), DEAP)
    result = update_record_metadata(manifest["manifest_id"], baseline_duration_s=0)["manifest"]
    route = route_model("emotion", result, video(), model_id=DEAP)
    assert route["selected_model"] is None
    assert any("基线 0" in item for item in route["candidates"][0]["reasons"])


def test_partial_video_coverage_and_multi_trial_context_share_one_plan(folder):
    manifest, raw = declared(folder, DEAP_MAHNOB_CHANNEL_ORDER, np.ones((32, 8064*3)), DEAP)
    assert route_model("emotion", manifest, video(), model_id=DEAP)["selected_model"] is None
    # EEG second-trial stimulus66..67 maps to the one-second clip0..1.
    clip = video(1, 66)
    kwargs = {"start_s": 66, "end_s": 67, "max_trials": 1}
    assert route_model("emotion", manifest, clip, model_id=DEAP, **kwargs)["selected_model"] == DEAP
    config = {**load_registry()[DEAP], "model_id": DEAP}
    trial, payload, windows = remote_model._prediction_windows(manifest["manifest_id"], config, 1, 66, 67,
        {"video_manifest_id": clip["manifest_id"], "duration_s": 1, "sync_offset_s": 66})
    assert payload["windows"][0]["start_sample"] == 8064
    assert trial["time_range_s"] == [63, 126]
    rows = json.loads(Path(windows["uri"]).read_text(encoding="utf-8"))["windows"]
    assert len(rows) == 1 and rows[0]["context_start_sample"] == 384
    assert rows[0]["video_time_range_s"] == [0, 1]
    assert rows[0]["original_time_range_s"] == [66, 67]
    np.testing.assert_array_equal(raw[:, payload["windows"][0]["start_sample"]:payload["windows"][0]["end_sample"]], raw[:, 8064:16128])


@pytest.mark.parametrize("timing", [None, {"status": "unknown"},
    {"status": "unsupported", "reasons": ["变帧率视频不能使用当前训练抽帧配方"]}])
def test_unverified_or_vfr_video_cannot_route_ready(folder, timing):
    manifest, _ = declared(folder, DEAP_MAHNOB_CHANNEL_ORDER, np.ones((32, 8064)), DEAP)
    clip = video()
    clip["timing"] = timing
    assert route_model("emotion", manifest, clip, model_id=DEAP)["selected_model"] is None


def test_profile_npz_can_keep_additional_named_channels(folder):
    names = [*DEAP_MAHNOB_CHANNEL_ORDER, "EOG"]
    path = folder / "extra.npz"
    np.savez(path, data=np.ones((33, 8064)), fs=128, ch_names=names, unit="dataset_native", dataset="DEAP",
        format_profile="deap_preprocessed_trial", reference="average", preprocessing="deap_official_preprocessed")
    manifest = inspect_record(str(path))
    assert manifest["n_channels"] == 33
    assert route_model("emotion", manifest, video(), model_id=DEAP)["selected_model"] == DEAP


def test_profile_does_not_overwrite_explicit_baseline_or_processing_conflicts(folder):
    path = folder / "baseline_conflict.npz"
    np.savez(path, data=np.ones((32, 8064)), fs=128, ch_names=DEAP_MAHNOB_CHANNEL_ORDER,
        unit="dataset_native", dataset="DEAP", reference="average", format_profile="deap_preprocessed_trial",
        preprocessing="deap_official_preprocessed", baseline_duration_s=0)
    manifest = inspect_record(str(path))
    assert manifest["metadata_evidence"]["baseline_duration_s"] == {"value": 0, "source": "file_header"}
    assert route_model("emotion", manifest, video(), model_id=DEAP)["selected_model"] is None
    path = folder / "processing_conflict.npz"
    np.savez(path, data=np.ones((30, 10000)), fs=500, ch_names=EAV_CHANNEL_ORDER,
        unit="dataset_native", dataset="EAV", reference="mastoids", format_profile="eav_raw_trial",
        preprocessing="normalized", task_mode="speaking")
    manifest = inspect_record(str(path))
    assert route_model("emotion", manifest, model_id=EAV)["selected_model"] is None
    path = folder / "stage_conflict.npz"
    np.savez(path, data=np.ones((30, 10000)), fs=500, ch_names=EAV_CHANNEL_ORDER,
        unit="dataset_native", dataset="EAV", reference="mastoids", format_profile="eav_raw_trial",
        input_stage="deap_preprocessed_trial", task_mode="speaking")
    manifest = inspect_record(str(path))
    assert manifest["metadata_evidence"]["input_stage"]["value"] == "deap_preprocessed_trial"
    assert route_model("emotion", manifest, model_id=EAV)["selected_model"] is None


def test_container_independent_header_contract_is_retained(folder):
    path = folder / "header.npz"
    np.savez(path, data=np.ones((32, 8064)), fs=128, ch_names=DEAP_MAHNOB_CHANNEL_ORDER,
        unit="dataset_native", dataset="DEAP", reference="average", input_stage="deap_preprocessed_trial",
        preprocessing="deap_official_preprocessed", baseline_duration_s=3)
    manifest = inspect_record(str(path))
    assert manifest["format_profile"] is None and manifest["reference"] == "average"
    assert manifest["metadata_evidence"]["input_stage"]["source"] == "file_header"
    assert route_model("emotion", manifest, video(), model_id=DEAP)["selected_model"] == DEAP


def test_ragged_csv_does_not_silently_drop_eeg_samples(folder):
    path = folder / "ragged.csv"
    path.write_text("Fp1,Fp2\n1,2\n3,4,5\n6,7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        inspect_record(str(path), sample_rate_hz=128)


def test_csv_timestamp_gap_does_not_become_a_uniform_model_clock(folder):
    path = folder / "gap.csv"
    timestamps = np.arange(1280) / 128
    timestamps[100:] += 1
    np.savetxt(path, np.column_stack([timestamps, np.ones((1280, 2))]), delimiter=",", header="time,Fp1,Fp2", comments="")
    with pytest.raises(ValueError, match="时间列"):
        inspect_record(str(path))


# Inference is mocked; use isolated registered contracts rather than local weights.
pytestmark = pytest.mark.usefixtures("registered_model_registry")
