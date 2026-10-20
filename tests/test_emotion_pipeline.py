"""Exercise real routing/storage with synthetic remote replies, without accuracy claims."""

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from eegagent.worker import emotion_pipeline, remote_model, signals, store
from eegagent.worker.channel_orders import DEAP_MAHNOB_CHANNEL_ORDER, EAV_CHANNEL_ORDER
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.tools import TOOL_DEFINITIONS, run_tool


DEAP = "camer_deap_fixed_1s_fold0"
EAV = "eav_eegnet_emotion5_loso_fold0"
FACTS = {"sample_rate_hz": 128, "channel_names": DEAP_MAHNOB_CHANNEL_ORDER,
         "unit": "dataset_native", "reference": "average",
         "preprocessing": "deap_official_preprocessed", "baseline_duration_s": 3}


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    raw = np.random.default_rng(14).normal(size=(32, 8064))
    path = store.ensure_layout() / "uploads" / "anonymous.npy"
    np.save(path, raw)
    eeg = inspect_record(str(path))
    video_path = path.with_suffix(".avi")
    video_path.write_bytes(b"synthetic transport fixture; not a decodable video")
    video = {"manifest_id": "vid_pipeline", "modality": "video", "source_path": str(video_path),
             "attachment_sha256": "test-only", "duration_s": 60,
             "timing": {"status": "cfr_verified"}, "role": None, "sync": None}
    store.save_manifest(video)
    return eeg, video, raw


def synthetic_reply(monkeypatch, raw, expected_start=5, expected_end=15):
    def infer(request, response, model, *_):
        with np.load(request, allow_pickle=False) as archive:
            np.testing.assert_array_equal(archive["signal"], raw)
            metadata = json.loads(archive["metadata"].item())
        count = len(metadata["windows"])
        assert count == expected_end - expected_start
        assert metadata["windows"][0]["start_sample"] == expected_start * 128
        assert metadata["windows"][-1]["video_end_s"] == expected_end - 3
        # Later rows differ from both the old eight-row and the compact two-row previews.
        logits = np.tile([4, -2, 0, 1], (count, 1)).astype(np.float32)
        logits[-2:] = [-2, 0, 1, 5]
        received = {"model": {"model_id": DEAP, "model_version": model["model_version"],
                     "task": "emotion", "weight_sha256": model["sha256"],
                     "input_recipe": {"fixture": "explicit synthetic reply"}},
                    "output": {"kind": "classification", "labels": model["label_map"], "score_kind": "logits"},
                    "preparation": {}, "runtime": {"device": "synthetic, no GPU accuracy experiment"},
                    "video_preparation": {"frame_times_ms": [[0, 250, 500, 750]] * count}}
        np.savez(response, eeg_inputs=np.zeros((count, 32, 5), dtype=np.float32),
                 video_inputs=np.zeros((count, 4, 224, 224, 3), dtype=np.float32),
                 logits=logits, metadata=json.dumps(received))
    monkeypatch.setattr(remote_model, "_remote_infer", infer)


def forbid_inference_and_export(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A blocked recognition request must not infer or export")
    monkeypatch.setattr(emotion_pipeline, "run_model", forbidden)
    monkeypatch.setattr(emotion_pipeline, "export", forbidden)


def test_one_call_preserves_complete_trial_sources_selected_windows_and_csv_json(pair, monkeypatch, commit_session):
    eeg, video, raw = pair
    commit_session("recognition_fixture", [eeg, video])
    synthetic_reply(monkeypatch, raw)
    calls = []
    executor = emotion_pipeline.run_model

    def tracked(*args):
        calls.append(args)
        return executor(*args)
    monkeypatch.setattr(emotion_pipeline, "run_model", tracked)
    result = run_tool("recognize_emotion", {"manifest_id": eeg["manifest_id"], "video_manifest_id": video["manifest_id"],
                      "eeg_metadata": FACTS, "video_role": "face", "sync_offset_s": 3,
                      "start_s": 5, "end_s": 15, "max_trials": 1, "export_formats": ["csv", "json"],
                      "timeout_s": 80, "_session_id": "recognition_fixture"})
    assert result["status"] == "ok" and result["prediction_executed"]
    assert result["selected_model"] == DEAP
    assert calls[0] == (result["manifest_id"], DEAP, "recognition_fixture", 5, 15, 1, 80,
                        result["video_manifest_id"], "face", 3)
    updated_eeg, updated_video = result["manifests"]
    assert updated_eeg["manifest_id"] != eeg["manifest_id"]
    assert updated_eeg["metadata_evidence"]["origin"] == {"value": "unverified", "source": "unknown"}
    assert updated_eeg["metadata_evidence"]["preprocessing"]["source"] == "user_confirmed"
    assert "labels" not in updated_eeg
    assert updated_video["sync"]["start_offset_s"] == 3 and updated_video["role"]["value"] == "face"
    assert store.load_manifest(eeg["manifest_id"])["sample_rate_hz"] is None
    assert store.load_manifest(video["manifest_id"])["role"] is None
    summary = result["summary"]
    assert summary["label_counts"] == {"LVLA": 8, "LVHA": 0, "HVLA": 0, "HVHA": 2}
    assert summary["prediction_count"] == 10 and len(summary["prediction_preview"]) == 2
    assert summary["score_kind"] == "logits" and summary["prediction_preview"][0]["scores"] == [4, -2, 0, 1]
    assert summary["coverage"]["span_s"] == [5, 15] and summary["coverage"]["complete"]
    prediction = store.load_artifact(result["artifact"]["artifact_id"])
    assert prediction["source_manifest_ids"] == [result["manifest_id"], result["video_manifest_id"]]
    assert prediction["provenance"]["params"]["model"]["weight_sha256"]
    assert "model" not in result["artifact"]["provenance"]["params"]
    assert result["artifact"]["provenance"]["params_omitted"]
    payload = json.loads(Path(prediction["uri"]).read_text(encoding="utf-8"))
    assert len(payload["predictions"]) == 10 and payload["predictions"][-1]["label"] == "HVHA"
    assert payload["predictions"][0]["original_time_range_s"] == [5, 6]
    assert payload["predictions"][-1]["video_time_range_s"] == [11, 12]
    assert store.load_artifact(result["input_artifact_id"])["source_manifest_ids"] == prediction["source_manifest_ids"]
    csv_art, json_art = result["artifacts"]
    assert [csv_art["summary"]["format"], json_art["summary"]["format"]] == ["csv", "json"]
    assert csv_art["source_manifest_ids"] == prediction["source_manifest_ids"]
    with Path(csv_art["uri"]).open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 10 and rows[-1]["label"] == "HVHA"
    assert rows[0]["video_manifest_id"] == result["video_manifest_id"]
    assert rows[0]["video_start_s"] == "2.0" and rows[0]["sync_offset_s"] == "3"
    assert rows[0]["score:LVHA"] == "-2.0" and rows[0]["score_kind"] == "logits"
    assert json.loads(Path(json_art["uri"]).read_text(encoding="utf-8")) == payload


def test_missing_collection_facts_return_specific_requirements_without_inference(pair, monkeypatch):
    eeg, video, _ = pair
    forbid_inference_and_export(monkeypatch)
    result = run_tool("recognize_emotion", {"manifest_id": eeg["manifest_id"], "video_manifest_id": video["manifest_id"]})
    assert result["status"] == "incompatible" and not result["prediction_executed"]
    assert result["manifests"] == [] and result["artifacts"] == []
    assert any("采样率未知" in reason for reason in result["reasons"])
    assert any("波形处理契约" in reason for reason in result["reasons"])
    assert all("input" not in row and "label_map" not in row for row in result["candidates"])
    assert set(row["model_id"] for row in result["candidates"]) == {DEAP, "camer_eav_photo_fixed_fold0"}


def test_missing_video_facts_are_needs_input_even_when_eeg_facts_are_applied(pair, monkeypatch):
    eeg, video, _ = pair
    forbid_inference_and_export(monkeypatch)
    result = run_tool("recognize_emotion", {"manifest_id": eeg["manifest_id"], "video_manifest_id": video["manifest_id"],
                      "eeg_metadata": FACTS})
    assert result["status"] == "needs_input" and not result["prediction_executed"]
    assert len(result["manifests"]) == 1 and result["artifacts"] == []
    assert any("确认视频" in reason for reason in result["missing_context"])
    assert any("时间偏移" in reason for reason in result["missing_context"])


@pytest.mark.parametrize("facts,args,reason", [
    ({"baseline_duration_s": 0}, {}, "基线 0"),
    ({"reference": "mastoids"}, {}, "参考"),
    ({"unit": "uV"}, {}, "数值尺度"),
    ({"sample_rate_hz": 256}, {}, "训练采样率"),
    ({"channel_names": [*DEAP_MAHNOB_CHANNEL_ORDER[:-1], "EOG"]}, {}, "缺 1"),
    ({"channel_names": [*DEAP_MAHNOB_CHANNEL_ORDER[:-1], "EEG Fp1-REF"]}, {}, "重复"),
    ({}, {"video_role": "stimulus"}, "刺激视频"),
    ({}, {"sync_offset_s": 0}, "视频"),
    ({}, {"start_s": 0, "end_s": 2}, "基线"),
])
def test_pipeline_cannot_bypass_atomic_channel_scale_baseline_or_video_boundaries(pair, monkeypatch, facts, args, reason):
    eeg, video, _ = pair
    forbid_inference_and_export(monkeypatch)
    result = run_tool("recognize_emotion", {"manifest_id": eeg["manifest_id"], "video_manifest_id": video["manifest_id"],
                      "eeg_metadata": {**FACTS, **facts}, "video_role": "face", "sync_offset_s": 3, **args})
    assert result["status"] == "incompatible" and result["selected_model"] is None
    assert not result["prediction_executed"] and result["artifacts"] == []
    assert any(reason in item for item in result["reasons"])


def test_existing_facts_are_reused_without_rewriting_and_empty_formats_skip_export(pair, monkeypatch):
    eeg, video, raw = pair
    eeg = update_record_metadata(eeg["manifest_id"], **FACTS)["manifest"]
    video = update_record_metadata(video["manifest_id"], video_role="face", sync_offset_s=3)["manifest"]
    synthetic_reply(monkeypatch, raw)

    def forbidden(*args, **kwargs):
        raise AssertionError("Existing facts and empty export request require neither update nor export")
    monkeypatch.setattr(emotion_pipeline, "update_record_metadata", forbidden)
    monkeypatch.setattr(emotion_pipeline, "export", forbidden)
    result = run_tool("recognize_emotion", {"manifest_id": eeg["manifest_id"], "video_manifest_id": video["manifest_id"],
                      "video_role": "face", "sync_offset_s": 3, "start_s": 5, "end_s": 15, "export_formats": []})
    assert result["status"] == "ok" and result["manifests"] == [] and result["artifacts"] == []
    assert result["manifest_id"] == eeg["manifest_id"] and result["video_manifest_id"] == video["manifest_id"]
    assert store.load_manifest(eeg["manifest_id"]) == eeg and store.load_manifest(video["manifest_id"]) == video


def test_eeg_only_request_selects_applicable_executor_and_exports_csv_by_default(pair, monkeypatch):
    eeg, video, raw = pair
    path = Path(eeg["source_path"]).with_name("eav.npz")
    np.savez(path, data=np.random.default_rng(19).normal(size=(30, 10000)),
             fs=500, ch_names=EAV_CHANNEL_ORDER, unit="dataset_native", dataset="EAV",
             reference="mastoids", preprocessing="eav_raw", input_stage="raw_eav_trial", task_mode="speaking")
    eeg = inspect_record(str(path))

    def infer(request, response, model, *_):
        assert model["model_id"] == EAV
        with np.load(request, allow_pickle=False) as archive:
            metadata = json.loads(archive["metadata"].item())
        assert "video_manifest_id" not in metadata
        output = {"model": {"model_id": EAV, "model_version": model["model_version"], "task": "emotion",
                             "weight_sha256": model["sha256"], "input_recipe": {}},
                  "output": {"kind": "classification", "labels": model["label_map"], "score_kind": "logits"},
                  "preparation": {}, "runtime": {"device": "synthetic"}}
        np.savez(response, inputs=np.zeros((4, 1, 30, 640), dtype=np.float32),
                 logits=np.tile([0, 1, 2, 3, 4], (4, 1)), metadata=json.dumps(output))
    monkeypatch.setattr(remote_model, "_remote_infer", infer)
    result = run_tool("recognize_emotion", {"manifest_id": eeg["manifest_id"]})
    assert result["status"] == "ok" and result["selected_model"] == EAV
    assert result["video_manifest_id"] is None and result["manifests"] == []
    assert result["summary"]["label_counts"]["calm"] == 4
    assert result["summary"]["coverage"]["span_s"] == [0, 20]
    assert len(result["artifacts"]) == 1 and result["artifacts"][0]["summary"]["format"] == "csv"


def test_recognition_metadata_schema_reuses_atomic_fields_and_has_no_algorithm_parameter():
    definitions = {row["name"]: row for row in TOOL_DEFINITIONS}
    properties = definitions["recognize_emotion"]["input_schema"]["properties"]
    metadata = definitions["update_record_metadata"]["input_schema"]["properties"]
    assert properties["eeg_metadata"]["properties"] == {
        key: value for key, value in metadata.items() if key not in ("manifest_id", "video_role", "sync_offset_s")}
    assert properties["eeg_metadata"]["additionalProperties"] is False
    assert "model_id" not in properties and properties["export_formats"]["default"] == ["csv"]


# Inference is mocked; use isolated registered contracts rather than local weights.
pytestmark = pytest.mark.usefixtures("registered_model_registry")
