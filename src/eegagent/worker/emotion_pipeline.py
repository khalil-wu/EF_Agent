"""Complete an emotion recognition request with the existing scientific contracts."""

from __future__ import annotations

from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.models import route_model
from eegagent.worker.outputs import export
from eegagent.worker.remote_model import run_model
from eegagent.worker.store import load_manifest


def _artifact_view(record: dict) -> dict:
    """Keep citable identities; the saved artifact retains the complete input recipe."""
    view = {key: record[key] for key in ("artifact_id", "kind", "uri", "media_type",
            "source_manifest_ids", "parent_artifact_ids", "time_range_s", "shape") if key in record}
    view["provenance"] = record["provenance"]
    view["summary"] = dict(record["summary"])
    if "prediction_preview" in view["summary"]:
        view["summary"]["prediction_preview"] = view["summary"]["prediction_preview"][:2]
    return view


def recognize_emotion(manifest_id: str, video_manifest_id: str | None = None,
                      eeg_metadata: dict | None = None, video_role: str = "unknown",
                      sync_offset_s: float | None = None, start_s: float | None = None,
                      end_s: float | None = None, max_trials: int | None = None,
                      export_formats: list[str] | None = None, timeout_s: float = 300,
                      _session_id: str | None = None) -> dict:
    """Apply explicit collection facts, route, infer, and export in one tool call.

    eeg_metadata contains only facts supplied by the user or acquisition notes.
    Omitted metadata stays unchanged; updating facts never authenticates origin.
    All compatibility, whole-trial preparation and video checks remain in the
    shared router and executor used by the atomic tools.
    """
    formats = list(dict.fromkeys(["csv"] if export_formats is None else export_formats))
    if any(kind not in ("csv", "json") for kind in formats):
        raise ValueError("export_formats 仅支持 csv、json；空数组表示不导出")
    manifest = load_manifest(manifest_id)
    video = load_manifest(video_manifest_id) if video_manifest_id else None
    updated = []
    if eeg_metadata:
        manifest = update_record_metadata(manifest_id, **eeg_metadata)["manifest"]
        updated.append(manifest)
    if video is not None and (video_role != "unknown" or sync_offset_s is not None):
        metadata = {}
        if video_role != "unknown" and video.get("role") != {"value": video_role, "source": "user_confirmed"}:
            metadata["video_role"] = video_role
        if sync_offset_s is not None and video.get("sync") != {
                "clock": "eeg_original", "start_offset_s": sync_offset_s, "source": "user_confirmed"}:
            metadata["sync_offset_s"] = sync_offset_s
        if metadata:
            video = update_record_metadata(video_manifest_id, **metadata)["manifest"]
            updated.append(video)
    route = route_model("emotion", manifest, video, video_role, sync_offset_s,
                        start_s=start_s, end_s=end_s, max_trials=max_trials)
    context = {"manifest_id": manifest["manifest_id"],
               "video_manifest_id": video["manifest_id"] if video else None,
               "manifests": updated, "selected_model": route["selected_model"],
               "requested_range": route["requested_range"]}
    if route["selected_model"] is None:
        relevant = [row for row in route["candidates"] if row["inference_available"] and
                    (video is None or "face_video" in row["modalities"])]
        if not relevant:
            relevant = route["candidates"]
        status = "needs_input" if any(row["route_status"] in ("needs_context", "needs_preparation")
                                     for row in relevant) else "incompatible"
        return {"status": status, **context, "prediction_executed": False,
                "missing_context": list(dict.fromkeys(reason for row in relevant for reason in row["missing_context"])),
                "reasons": list(dict.fromkeys(reason for row in relevant for reason in row["reasons"])),
                "candidates": [{key: row[key] for key in ("model_id", "route_status", "compatibility",
                               "missing_channels", "missing_context", "reasons")} for row in relevant],
                "artifacts": []}
    result = run_model(manifest["manifest_id"], route["selected_model"], _session_id,
                       start_s, end_s, max_trials, timeout_s,
                       video["manifest_id"] if video else None, video_role, sync_offset_s)
    if result["status"] != "ok":
        return {**result, **context}
    prediction = _artifact_view(result["artifact"])
    exports = [_artifact_view(export(kind, prediction["artifact_id"])["artifact"]) for kind in formats]
    return {"status": "ok", **context, "prediction_executed": True,
            "artifact": prediction, "artifacts": exports,
            "summary": prediction["summary"], "input_artifact_id": result["input_artifact_id"]}
