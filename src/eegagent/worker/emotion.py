"""Persist a feature review or an explicitly requested emotion hypothesis.

The LLM supplies labels and reasoning; this worker records the result and derives
its observed time coverage from the real feature/window artifacts.
"""

from __future__ import annotations

import json

import numpy as np

from eegagent.worker.processing import _public
from eegagent.worker.labels import label_definitions
from eegagent.worker.signals import analysis_context
from eegagent.worker.store import artifact_payload_path, load_artifact, make_artifact_id, resolve_inside_storage, save_artifact

VERSION = "1.3"
METHOD = "llm_tool_reasoning"


def _observed_intervals(windows: list[dict]) -> list[list[float]]:
    intervals: list[list[float]] = []
    for window in windows:
        start, end = window["start_s"], window["end_s"]
        if intervals and start <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([start, end])
    return intervals


def window_observation(window_record: dict, window_payload: dict) -> tuple[dict, dict]:
    """Observed coverage with original and event-relative coordinates for each segment."""
    windows = window_payload["windows"]
    intervals = _observed_intervals(windows)
    span = [intervals[0][0], intervals[-1][1]]
    context = analysis_context(window_payload["source"], *span)
    original_ranges = [coordinate for interval in intervals
                       for coordinate in analysis_context(window_payload["source"], *interval)["original_time_ranges_s"]]
    requested_span = window_record["time_range_s"]
    coverage = {
        "intervals_s": intervals, "original_time_ranges_s": original_ranges,
        "covered_duration_s": round(sum(end - start for start, end in intervals), 4),
        "span_s": span, "requested_span_s": requested_span,
        "complete": len(intervals) == 1 and span[0] <= requested_span[0] + .001 and span[1] >= requested_span[1] - .001,
        "n_windows": len(windows),
    }
    if "original_recording_time_range_s" in context:
        coverage["original_intervals_s"] = [coordinate["original_time_range_s"] for coordinate in original_ranges]
    return context, coverage


def save_emotion_assessment(features_id: str, labels: list[str], label: str | None, rationale: str,
                            predictions: list[dict] | None = None, assessment_status: str = "hypothesis") -> dict:
    """Save measurements and hypothesis status without claiming model validation."""
    feature_record = load_artifact(features_id)
    if feature_record["kind"] != "features":
        raise ValueError(f"{features_id} is kind={feature_record['kind']}, expected features")
    if any(not item.strip() for item in labels) or len(set(labels)) != len(labels):
        raise ValueError("labels 必须是无空值、无重复的情绪标签列表")
    if assessment_status not in ("unassessed", "hypothesis"):
        raise ValueError("assessment_status 必须为 unassessed 或 hypothesis")
    if assessment_status == "hypothesis" and (not labels or label not in labels):
        raise ValueError("候选假设的 label 必须属于 labels")
    if assessment_status == "unassessed" and label is not None:
        raise ValueError("unassessed 的 label 必须为 null；测量发现写入 rationale")
    if not rationale.strip():
        raise ValueError("rationale 需要说明实际特征依据")

    windows_id = feature_record["summary"]["windows_id"]
    window_record = load_artifact(windows_id)
    window_payload = json.loads(resolve_inside_storage(window_record["uri"]).read_text(encoding="utf-8"))
    windows = window_payload["windows"]
    with np.load(resolve_inside_storage(feature_record["uri"])) as arrays:
        measured_features = [name for name in arrays.files if name not in ("psd_mean", "psd_freqs")]
        n_windows = arrays[measured_features[0]].shape[0]
    if n_windows != len(windows):
        raise ValueError("特征窗口数与窗口表不一致")

    context, coverage = window_observation(window_record, window_payload)
    span = coverage["span_s"]
    rows = []
    for prediction in predictions or []:
        first = prediction["window_start"]
        last = prediction.get("window_end", first)
        if not 0 <= first <= last < n_windows:
            raise ValueError(f"预测窗口范围 {first}–{last} 超出 0–{n_windows - 1}")
        row_status = prediction.get("assessment_status", assessment_status)
        row_label = prediction.get("label")
        if row_status not in ("unassessed", "hypothesis"):
            raise ValueError("逐段 assessment_status 必须为 unassessed 或 hypothesis")
        if row_status == "hypothesis" and row_label not in labels:
            raise ValueError("逐段候选的 label 必须属于 labels")
        if row_status == "unassessed" and row_label is not None:
            raise ValueError("逐段 unassessed 的 label 必须为 null")
        selected = windows[first:last + 1]
        row_context = analysis_context(window_payload["source"], selected[0]["start_s"], selected[-1]["end_s"])
        row = {
            "window_start": first,
            "window_end": last,
            "start_s": selected[0]["start_s"],
            "end_s": selected[-1]["end_s"],
            "observed_intervals_s": _observed_intervals(selected),
            "assessment_status": row_status,
            "label": row_label,
            "rationale": prediction.get("rationale", ""),
        }
        coordinates = row_context["original_time_ranges_s"]
        if len(coordinates) == 1:
            row.update({key: value for key, value in coordinates[0].items()
                        if key in ("original_time_range_s", "epoch_index", "epoch_relative_time_range_s")})
        else:
            row["original_time_ranges_s"] = coordinates
        row.update({key: value for key, value in row_context.items()
                    if key in ("original_recording_offset_s", "original_recording_time_range_s")})
        rows.append(row)

    summary = {
        "method": METHOD,
        "assessment_status": assessment_status,
        "label": label,
        "labels": labels,
        "label_definitions": label_definitions(labels),
        "rationale": rationale,
        "features_id": features_id,
        "windows_id": windows_id,
        "source_id": window_payload["source"],
        "measured_features": measured_features,
        "coverage": coverage,
        "time_axis": context["time_axis"],
        "prediction_count": len(rows),
        "prediction_preview": rows[:8],
    }
    summary.update({key: value for key, value in context.items()
                    if key in ("original_recording_offset_s", "original_recording_time_range_s")})
    inputs = {"features_id": features_id, "labels": labels, "label": label, "rationale": rationale,
              "assessment_status": assessment_status, "predictions": rows}
    artifact_id = make_artifact_id("save_emotion_assessment", VERSION, inputs)
    path = artifact_payload_path(artifact_id, "json")
    path.write_text(json.dumps({**summary, "predictions": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    record = save_artifact({
        "artifact_id": artifact_id,
        "kind": "emotion_assessment",
        "uri": str(path),
        "media_type": "application/json",
        "source_manifest_ids": feature_record["source_manifest_ids"],
        "parent_artifact_ids": [features_id],
        "time_range_s": span,
        "shape": [len(rows)],
        "signal": window_record.get("signal"),
        "analysis": {**context, "window_support": window_record.get("analysis", {}).get("window_support", {})},
        "summary": summary,
        "provenance": {
            "created_by": f"save_emotion_assessment@{VERSION}",
            "params": {"features_id": features_id, "labels": labels, "method": METHOD,
                       "assessment_status": assessment_status},
            "steps": feature_record["provenance"]["steps"] + [METHOD],
        },
    })
    return {"status": "ok", "artifact": _public(record), "summary": summary}
