"""Model discovery and routing decisions, separate from prediction execution."""

from __future__ import annotations

from eegagent.worker.registry import check_model_compat, load_registry
from eegagent.worker.recognition_windows import prediction_plan, video_coverage_error
from eegagent.worker.labels import label_definitions

# Add a model id here only when its inference executor is implemented.
EXECUTABLE_MODELS: frozenset[str] = frozenset({"eav_eegnet_emotion5_loso_fold0", "camer_eav_photo_fixed_fold0", "camer_deap_fixed_1s_fold0"})


def paired_video_context(video: dict | None, video_role: str = "unknown",
                         sync_offset_s: float | None = None) -> dict:
    """Resolve the pair's existing user-confirmed facts and explicit call context."""
    role = (video or {}).get("role") or {}
    sync = (video or {}).get("sync") or {}
    role_source = "tool_argument" if video_role != "unknown" else "unknown"
    sync_source = "tool_argument" if sync_offset_s is not None else "unknown"
    if video_role == "unknown" and role.get("source") == "user_confirmed":
        video_role, role_source = role["value"], role["source"]
    if sync_offset_s is None and sync.get("source") == "user_confirmed":
        sync_offset_s, sync_source = sync["start_offset_s"], sync["source"]
    return {"video_manifest_id": video["manifest_id"] if video else None,
            "video_role": video_role, "video_role_source": role_source,
            "sync_offset_s": sync_offset_s, "sync_offset_source": sync_source,
            "clock": "video time = original EEG time - sync_offset_s"}


def list_models(task: str | None = None, registry: dict | None = None) -> dict:
    registry = load_registry() if registry is None else registry
    rows = []
    for model_id, model in registry.items():
        if task and model.get("task") != task:
            continue
        blockers = []
        registered = model.get("status") == "registered" and model.get("file_uri") and model.get("sha256")
        if not registered:
            blockers.append("权重尚未登记")
        if model.get("input_contract_status") == "pending":
            blockers.append(model.get("input_contract_note", "需填写训练时的输入契约与标签顺序"))
        if model_id not in EXECUTABLE_MODELS:
            blockers.append("推理执行器尚未接入")
        availability = "ready" if not blockers else "weights_pending" if not registered else "adapter_pending"
        if model.get("status") == "research_only":
            availability = "research_only"
        rows.append({
            "model_id": model_id,
            "display_name": model.get("display_name", model_id),
            "task": model.get("task"), "dataset": model.get("dataset"),
            "modalities": model.get("modalities", []), "input": model.get("input", {}),
            "input_contract_status": model.get("input_contract_status"),
            "label_map": model.get("label_map", []), "split_protocol": model.get("split_protocol"),
            "label_definitions": label_definitions(model.get("label_map", [])),
            "model_version": model.get("model_version"), "limitations": model.get("limitations", []),
            "status": model.get("status"), "availability": availability,
            "inference_available": not blockers, "blockers": blockers,
        })
    return {"status": "ok", "models": rows}


def route_model(task: str, manifest: dict, video: dict | None = None,
                video_role: str = "unknown", sync_offset_s: float | None = None,
                model_id: str | None = None, registry: dict | None = None,
                start_s: float | None = None, end_s: float | None = None, max_trials: int | None = None) -> dict:
    """Rank models for one EEG recording and return the requirements before inference.

    A selected model means its data and executor are ready. This function never
    produces predictions or silently substitutes a different task or modality.
    """
    registry = load_registry() if registry is None else registry
    if manifest["modality"] != "eeg":
        raise ValueError("模型路由需要 EEG 数据；视频作为可选配对输入")
    if video is not None and video["modality"] != "video":
        raise ValueError("配对输入必须是视频附件")
    pair = paired_video_context(video, video_role, sync_offset_s)
    video_role, sync_offset_s = pair["video_role"], pair["sync_offset_s"]
    if model_id is not None and model_id not in registry:
        raise ValueError(f"模型未登记：{model_id}")
    if model_id is not None and registry[model_id].get("task") != task:
        raise ValueError(f"模型 {model_id} 不支持任务 {task}")
    catalog = list_models(task, registry)["models"]
    compatibility = {row["model_id"]: row for row in check_model_compat(manifest, registry)}
    candidates = []
    for row in catalog:
        if row["status"] == "research_only" or "eeg" not in row["modalities"] or model_id is not None and row["model_id"] != model_id:
            continue
        compat = compatibility[row["model_id"]]
        reasons = list(dict.fromkeys(row["blockers"] + compat["reasons"]))
        missing_context = []
        input_errors = []
        plan = None
        if compat["status"] == "compatible":
            geometry = {"sample_rate_hz": manifest["sample_rate_hz"],
                        "segments": manifest.get("segments") or [{"start_sample": 0,
                            "end_sample": manifest.get("n_samples") or round(manifest["duration_s"] * manifest["sample_rate_hz"]),
                            "original_start_s": 0.0}]}
            try:
                plan = prediction_plan(geometry, row["input"], start_s, end_s, max_trials)
            except ValueError as error:
                input_errors.append(str(error))
        if row["input"].get("task_mode"):
            mode = manifest.get("metadata_evidence", {}).get("task_mode", {}).get("value", "unknown")
            if mode == "unknown":
                missing_context.append(f"需要确认记录属于 {row['input']['task_mode']} 任务模式")
        if "face_video" in row["modalities"]:
            if video is None:
                missing_context.append("需要配对的面部视频")
            elif video_role == "unknown":
                missing_context.append("需要确认视频为被试面部记录；刺激视频不能替代面部输入")
            elif video_role != "face":
                input_errors.append("已声明的视频用途不是被试面部；刺激视频不能替代面部输入")
            if row["input"].get("sync_required") and sync_offset_s is None:
                missing_context.append("需要确认 EEG 与视频的时间偏移（秒）")
            if video is not None:
                timing = video.get("timing") or {}
                if timing.get("status") == "unsupported":
                    input_errors.extend(timing["reasons"])
                elif timing.get("status") != "cfr_verified" or video.get("duration_s") is None:
                    missing_context.append("视频帧时钟与真实视频流时长尚未核实；需要重新读取录像")
                if plan is not None and sync_offset_s is not None and video.get("duration_s") is not None:
                    error = video_coverage_error(plan["windows"], video["duration_s"], sync_offset_s)
                    if error:
                        input_errors.append(error)
        data_ready = compat["status"] == "compatible" and not missing_context and not input_errors
        ready = row["inference_available"] and data_ready
        candidates.append({
            **row, "compatibility": compat["status"],
            "missing_channels": compat["missing_channels"],
            "route_status": "ready" if ready else "unavailable" if not row["inference_available"] else "incompatible" if compat["status"] == "incompatible" or input_errors else "needs_context" if missing_context else "needs_preparation",
            "reasons": list(dict.fromkeys(reasons + input_errors + missing_context)),
            "missing_context": missing_context,
            "prediction_window_count": len(plan["windows"]) if plan else None,
        })
    rank = {"ready": 0, "needs_preparation": 1, "needs_context": 2, "unavailable": 3, "incompatible": 4}
    candidates.sort(key=lambda row: (rank[row["route_status"]], len(row["missing_context"]), row["model_id"]))
    selected = next((row["model_id"] for row in candidates if row["route_status"] == "ready" and
                     (video is None or model_id is not None or "face_video" in row["modalities"])), None)
    return {
        "status": "ok", "task": task, "manifest_id": manifest["manifest_id"],
        "video_manifest_id": video["manifest_id"] if video else None,
        "sync_offset_s": sync_offset_s, "selected_model": selected,
        "video_alignment": pair if video is not None else None,
        "requested_range": {"start_s": start_s, "end_s": end_s, "max_trials": max_trials},
        "candidates": candidates, "prediction_executed": False,
        "analysis_available": ["signal_quality", "spectral_features", "feature_timeline"],
        "note": "这是输入契约与所选范围的路由结果，未运行分类推理；远端仍需实际解码选中帧并完成相应画面准备。",
    }
