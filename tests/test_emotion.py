import json
import pickle

import numpy as np
import pytest

from eegagent.worker import signals, store
from eegagent.worker.emotion import save_emotion_assessment
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.outputs import read_artifact
from eegagent.worker.processing import extract_features, preprocess, segment_signal
from eegagent.worker.tools import TOOL_DEFINITIONS, run_tool


@pytest.fixture()
def manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("EEGAGENT_STORAGE_DIR", str(tmp_path))
    signals._CACHE.clear()
    root = store.ensure_layout()
    rate = 128
    t = np.arange(rate * 60) / rate
    rng = np.random.default_rng(7)
    data = rng.normal(0, 2, (4, len(t)))
    data += 8 * np.sin(2 * np.pi * 10 * t)
    path = root / "uploads" / "eeg.npy"
    np.save(path, data)
    return inspect_record(str(path), sample_rate_hz=rate, channel_names=["F3", "F4", "O1", "O2"])


def _features(manifest, **segment_args):
    windows = segment_signal(manifest["manifest_id"], window_s=10, **segment_args)["artifact"]
    features = extract_features(windows["artifact_id"], ["bandpower", "hjorth", "asymmetry"])["artifact"]
    return windows, features


def test_emotion_tool_saves_real_feature_lineage_and_segment_times(manifest):
    windows, features = _features(manifest)
    args = {
        "features_id": features["artifact_id"],
        "labels": ["高效价高唤醒", "低效价低唤醒"],
        "label": "高效价高唤醒",
        "assessment_status": "hypothesis",
        "rationale": "综合逐窗频带功率与额叶左右不对称进行工具推断。",
        "predictions": [
            {"window_start": 0, "window_end": 2, "label": "低效价低唤醒", "rationale": "前半段特征判断。"},
            {"window_start": 3, "window_end": 5, "label": "高效价高唤醒", "rationale": "后半段特征判断。"},
        ],
    }
    result = run_tool("save_emotion_assessment", args)
    artifact = result["artifact"]
    record = store.load_artifact(artifact["artifact_id"])
    payload = json.loads(store.resolve_inside_storage(record["uri"]).read_text(encoding="utf-8"))
    assert record["kind"] == "emotion_assessment"
    assert record["parent_artifact_ids"] == [features["artifact_id"]]
    assert record["source_manifest_ids"] == [manifest["manifest_id"]]
    assert result["summary"]["windows_id"] == windows["artifact_id"]
    assert result["summary"]["method"] == "llm_tool_reasoning"
    assert result["summary"]["assessment_status"] == "hypothesis"
    assert result["summary"]["coverage"] == {
        "intervals_s": [[0.0, 60.0]], "original_intervals_s": [[0.0, 60.0]],
        "original_time_ranges_s": [{"source_time_range_s": [0.0, 60.0], "original_time_range_s": [0.0, 60.0]}],
        "covered_duration_s": 60.0, "span_s": [0.0, 60.0],
        "requested_span_s": [0.0, 60.0], "complete": True, "n_windows": 6,
    }
    assert [(row["start_s"], row["end_s"]) for row in payload["predictions"]] == [(0.0, 30.0), (30.0, 60.0)]
    assert "probabilities" not in payload and "confidence" not in payload
    assert "probabilities" not in payload["predictions"][0]
    assert run_tool("save_emotion_assessment", args)["artifact"]["artifact_id"] == artifact["artifact_id"]
    assert read_artifact(artifact["artifact_id"])["artifact"]["summary"]["label"] == args["label"]
    page = read_artifact(artifact["artifact_id"], part="predictions", start=1, limit=1)
    assert page["total"] == 2 and page["rows"][0]["start_s"] == 30.0


def test_sampled_features_preserve_observed_gaps(manifest):
    _, features = _features(manifest, max_windows=2)
    result = save_emotion_assessment(features["artifact_id"], ["平静", "激动"], "平静", "比较两处实际测量窗口。",
                                     [{"window_start": 0, "window_end": 1, "label": "平静"}])
    coverage = result["summary"]["coverage"]
    assert coverage["span_s"] == [0.0, 60.0]
    assert coverage["intervals_s"] == [[0.0, 10.0], [50.0, 60.0]]
    assert coverage["covered_duration_s"] == 20.0 and coverage["complete"] is False
    assert result["summary"]["prediction_preview"][0]["observed_intervals_s"] == coverage["intervals_s"]


def test_assessment_does_not_claim_dropped_tail(manifest):
    _, features = _features(manifest, start_s=20, end_s=55, partial="drop")
    result = save_emotion_assessment(features["artifact_id"], ["积极", "消极"], "积极", "根据实际窗口特征推断。")
    assert result["artifact"]["time_range_s"] == [20.0, 50.0]
    assert result["summary"]["coverage"]["requested_span_s"] == [20.0, 55.0]
    assert result["summary"]["coverage"]["complete"] is False
    assert result["summary"]["prediction_count"] == 0


def test_emotion_inputs_require_real_features_and_consistent_labels(manifest):
    windows, features = _features(manifest)
    with pytest.raises(ValueError, match="expected features"):
        save_emotion_assessment(windows["artifact_id"], ["积极", "消极"], "积极", "依据")
    with pytest.raises(ValueError, match="label 必须属于 labels"):
        save_emotion_assessment(features["artifact_id"], ["积极", "消极"], "焦虑", "依据")
    with pytest.raises(ValueError, match="超出"):
        save_emotion_assessment(features["artifact_id"], ["积极", "消极"], "积极", "依据",
                                [{"window_start": 6, "label": "积极"}])
    definition = next(item for item in TOOL_DEFINITIONS if item["name"] == "save_emotion_assessment")
    assert definition["requires"] == {"artifact_kind": {"features_id": "features"}}
    assert definition["input_schema"]["additionalProperties"] is False
    assert "probabilities" not in definition["input_schema"]["properties"]
    assert "assessment_status" in definition["input_schema"]["required"]
    assert "labels" not in definition["input_schema"]["required"]


def test_unassessed_review_saves_findings_without_manufacturing_emotion_labels(manifest):
    _, features = _features(manifest)
    result = run_tool("save_emotion_assessment", {
        "features_id": features["artifact_id"], "assessment_status": "unassessed",
        "rationale": "O1/O2 在 10 Hz 有主峰；现有特征不能区分效价。",
        "predictions": [{"window_start": 0, "window_end": 5}],
    })
    summary = result["summary"]
    assert summary["assessment_status"] == "unassessed"
    assert summary["label"] is None and summary["labels"] == []
    assert summary["coverage"]["complete"] is True
    row = read_artifact(result["artifact"]["artifact_id"], part="predictions")["rows"][0]
    assert row["label"] is None and row["assessment_status"] == "unassessed"
    assert row["observed_intervals_s"] == [[0, 60]]
    with pytest.raises(ValueError, match="null"):
        save_emotion_assessment(features["artifact_id"], ["positive"], "positive", "依据", assessment_status="unassessed")
    with pytest.raises(ValueError, match="assessment_status"):
        save_emotion_assessment(features["artifact_id"], ["positive"], "positive", "依据", assessment_status="validated")


def test_emotion_assessment_preserves_original_recording_time_after_baseline_drop(manifest):
    source = preprocess(manifest["manifest_id"], [{"op": "remove_baseline", "seconds": 3, "drop": True}])["artifact"]["artifact_id"]
    windows = segment_signal(source, window_s=10, start_s=10, end_s=30)["artifact"]["artifact_id"]
    features = extract_features(windows, ["bandpower"])["artifact"]["artifact_id"]
    result = save_emotion_assessment(features, ["积极", "消极"], "积极", "基于实际窗口特征推断。",
                                     [{"window_start": 1, "label": "积极"}])
    summary = result["summary"]
    assert result["artifact"]["time_range_s"] == [10.0, 30.0]
    assert summary["original_recording_offset_s"] == 3.0
    assert summary["original_recording_time_range_s"] == [13.0, 33.0]
    assert summary["coverage"]["original_intervals_s"] == [[13.0, 33.0]]
    prediction = read_artifact(result["artifact"]["artifact_id"], part="predictions")["rows"][0]
    assert [prediction["start_s"], prediction["end_s"]] == [20.0, 30.0]
    assert prediction["original_recording_offset_s"] == 3.0
    assert prediction["original_recording_time_range_s"] == [23.0, 33.0]


def test_emotion_assessment_preserves_epoch_axes_without_claiming_continuous_time(manifest):
    saved = store.load_manifest(manifest["manifest_id"])
    saved["signal"] = {"kind": "epochs", "segments": [
        {"start_sample": 0, "end_sample": 3840, "original_start_s": 0, "original_end_s": 30,
         "epoch_index": 0, "epoch_tmin_s": -.5},
        {"start_sample": 3840, "end_sample": 7680, "original_start_s": 30, "original_end_s": 60,
         "epoch_index": 1, "epoch_tmin_s": -.5},
    ]}
    store.save_manifest(saved)
    _, features = _features(manifest)
    result = save_emotion_assessment(features["artifact_id"], [], None, "逐 epoch 审阅特征。",
                                     [{"window_start": 0, "window_end": 1}, {"window_start": 2, "window_end": 3}],
                                     assessment_status="unassessed")
    assert result["artifact"]["signal"]["kind"] == "epochs"
    assert "discontinuous" in result["artifact"]["analysis"]["time_axis"]
    assert result["summary"]["source_id"] == manifest["manifest_id"]
    assert "original_recording_offset_s" not in result["summary"]
    rows = read_artifact(result["artifact"]["artifact_id"], part="predictions")["rows"]
    assert rows[0]["epoch_index"] == 0
    assert rows[0]["epoch_relative_time_range_s"] == [-.5, 19.5]
    assert [coordinate["epoch_index"] for coordinate in rows[1]["original_time_ranges_s"]] == [0, 1]
    assert "original_recording_time_range_s" not in rows[1]


def test_default_tool_inspection_and_metadata_hide_self_ratings_without_deleting_them(manifest):
    path = store.ensure_layout() / "uploads" / "rated.dat"
    content = {
        "data": np.random.default_rng(4).normal(0, 5, (1, 40, 8064)).astype(np.float32),
        "labels": np.array([[8, 3, 5, 6]]), "fs": 128,
        "label_names": ["valence", "arousal", "dominance", "liking"],
    }
    with path.open("wb") as file:
        pickle.dump(content, file, protocol=2)
    inspected = run_tool("inspect_record", {"path": str(path)})["manifest"]
    assert inspected["labels_available"] is True and "labels" not in inspected
    assert store.load_manifest(inspected["manifest_id"])["labels"]["values"] == [8, 3, 5, 6]
    explicit = run_tool("inspect_record", {"path": str(path), "include_labels": True})["manifest"]
    assert explicit["labels"]["values"] == [8, 3, 5, 6]
    updated = run_tool("update_record_metadata", {
        "manifest_id": inspected["manifest_id"], "baseline_duration_s": 3, "dataset": "study",
        "label_names": ["valence", "arousal", "dominance", "liking"],
    })["manifest"]
    assert updated["labels_available"] is True and "labels" not in updated
    saved = store.load_manifest(updated["manifest_id"])
    assert saved["labels"]["values"] == [8, 3, 5, 6]
    assert saved["metadata_evidence"]["baseline_duration_s"]["value"] == 3
    assert saved["dataset"] == "study"
