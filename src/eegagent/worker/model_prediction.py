"""Persist model outputs after inference; this function does not execute a model.

Model executors call this with their received outputs and known output contract.
It is not an LLM tool. Rows inherit timing from the input window artifact.
"""
from __future__ import annotations

import json
from collections import Counter
import numpy as np

from eegagent.worker.emotion import window_observation
from eegagent.worker.labels import label_definitions
from eegagent.worker.processing import _public
from eegagent.worker.store import artifact_payload_path, load_artifact, make_artifact_id, resolve_inside_storage, save_artifact

VERSION = "1.1"


def _decision_margin(rows: list[dict], values: np.ndarray, names: list[str], closest: int = 3) -> dict:
    """Top-1 minus top-2 score per window, in the model's own score units, and the windows decided by the smallest gap."""
    order = np.argsort(-values, axis=1, kind="stable")
    margins = values[np.arange(len(values)), order[:, 0]] - values[np.arange(len(values)), order[:, 1]]
    picks = np.argsort(margins)[:closest]
    return {"unit": "same as scores", "median": round(float(np.median(margins)), 4),
            "min": round(float(margins.min()), 4), "max": round(float(margins.max()), 4),
            "closest_windows": [{"window_index": rows[i]["window_index"],
                                 "time_range_s": rows[i].get("original_time_range_s") or [rows[i].get("start_s"), rows[i].get("end_s")],
                                 "label": names[order[i, 0]], "runner_up": names[order[i, 1]],
                                 "margin": round(float(margins[i]), 4)} for i in picks]}


def save_model_prediction(windows_id: str, outputs, model: dict, output: dict, *, run_id: str,
                          aggregation: dict | None = None) -> dict:
    """Save single-label classification or named regression dimensions, one row per window.

    model: model_id, model_version, task; optional weight_sha256/input_recipe/input_artifact_id.
    output: kind, labels+score_kind or dimensions[{name,unit}], optional calibration.
    aggregation is the executor's actual aggregation record; none means per-window only.
    """
    record_w = load_artifact(windows_id)
    if record_w["kind"] != "windows":
        raise ValueError("模型预测需要真实 windows 产物")
    payload_w = json.loads(resolve_inside_storage(record_w["uri"]).read_text(encoding="utf-8"))
    windows = payload_w["windows"]
    kind = output["kind"]
    if kind not in ("classification", "regression"):
        raise ValueError("模型输出必须是 classification 或 regression")
    dimensions = [{"name": d["name"], "unit": d["unit"]} for d in output["dimensions"]] if kind == "regression" else []
    names = output["labels"] if kind == "classification" else [d["name"] for d in dimensions]
    values = np.asarray(outputs, dtype=float)
    if not names or len(set(names)) != len(names) or values.shape != (len(windows), len(names)) or not np.isfinite(values).all():
        raise ValueError("模型输出须为完整的窗口×类别/维度矩阵，名称唯一且数值有限")
    if kind == "classification":
        if output["score_kind"] not in ("logits", "probabilities"):
            raise ValueError("分类分数必须注明 logits 或 probabilities")
        if output["score_kind"] == "probabilities" and (
            (values < 0).any() or (values > 1).any() or not np.allclose(values.sum(axis=1), 1, atol=1e-6)
        ):
            raise ValueError("单标签分类概率每行须在0到1之间且合计为1")

    rows = []
    definitions = label_definitions(names) if kind == "classification" else []
    for window, vector in zip(windows, values):
        row = {"window_index": window["index"], **{key: window[key] for key in (
            "start_s", "end_s", "segment_index", "pad_right_samples", "original_time_range_s",
            "epoch_index", "epoch_relative_time_range_s", "video_manifest_id", "video_time_range_s") if key in window}}
        if kind == "classification":
            row.update(**definitions[int(vector.argmax())], scores=vector.tolist())
        else:
            row["values"] = vector.tolist()
        rows.append(row)

    context, coverage = window_observation(record_w, payload_w)
    summary = {
        "method": "model_inference", "model_id": model["model_id"], "model_version": model["model_version"],
        "run_id": run_id, "task": model["task"], "output_kind": kind,
        "model_evaluation": model.get("evaluation", {"status": "not_recorded"}),
        "source_id": payload_w["source"], "windows_id": windows_id, "time_axis": context["time_axis"],
        "coverage": coverage, "calibration": output.get("calibration", {"status": "unknown"}),
        "aggregation": aggregation, "prediction_count": len(rows), "prediction_preview": rows[:8],
    }
    if kind == "classification":
        counts = Counter(row["label"] for row in rows)
        summary.update(labels=names, label_definitions=definitions, score_kind=output["score_kind"],
                       label_counts={label: counts[label] for label in names})
        if len(names) > 1:
            summary["decision_margin"] = _decision_margin(rows, values, names)
    else:
        summary["dimensions"] = dimensions
    summary.update({key: context[key] for key in ("original_recording_offset_s", "original_recording_time_range_s") if key in context})
    if model.get("paired_video"):
        summary.update(modalities=model["modalities"], paired_video=model["paired_video"])
    inputs = {"windows_id": windows_id, "model": model, "output": output,
              "run_id": run_id, "aggregation": aggregation, "outputs": values.tolist()}
    artifact_id = make_artifact_id("model_prediction", VERSION, inputs)
    path = artifact_payload_path(artifact_id, "json")
    path.write_text(json.dumps({**summary, "model": model, "predictions": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    parents = [windows_id]
    if model.get("input_artifact_id"):
        parents.append(model["input_artifact_id"])
    record = save_artifact({
        "artifact_id": artifact_id, "kind": "prediction", "uri": str(path), "media_type": "application/json",
        "source_manifest_ids": record_w["source_manifest_ids"], "parent_artifact_ids": parents,
        "time_range_s": coverage["span_s"], "shape": list(values.shape),
        "signal": record_w["signal"], "analysis": {**context, "aggregation": aggregation}, "summary": summary,
        "provenance": {
            "created_by": f"model_prediction@{VERSION}", "model_id": model["model_id"],
            "model_run_id": run_id, "weight_sha256": model.get("weight_sha256"),
            "params": {"windows_id": windows_id, "model": model, "output": output, "aggregation": aggregation},
            "steps": record_w["provenance"]["steps"] + ["model_inference"],
        },
    })
    return {"status": "ok", "artifact": _public(record), "summary": summary}
