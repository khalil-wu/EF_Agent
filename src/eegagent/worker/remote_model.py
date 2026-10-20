"""Execute registered EEGNet or full CAMER through SSH and retain real input tensors."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import uuid

import numpy as np

from eegagent.worker import script_jobs
from eegagent.worker.files import resolve_readable, scratch_dir
from eegagent.worker.channel_orders import CHANNEL_ORDERS
from eegagent.worker.channels import normalize_channel
from eegagent.worker.model_prediction import save_model_prediction
from eegagent.worker.models import EXECUTABLE_MODELS, list_models, paired_video_context, model_evaluation
from eegagent.worker.processing import _public
from eegagent.worker.progress import report as progress
from eegagent.worker.recognition_windows import prediction_plan, video_coverage_error
from eegagent.worker.registry import check_model_compat, load_registry
from eegagent.worker.signals import analysis_context, resolve_signal, signal_metadata
from eegagent.worker.store import (artifact_payload_path, load_manifest, make_artifact_id,
                                   resolve_inside_storage, save_artifact)

VERSION = "1.0"


class _Cancelled(Exception):
    pass


def _command(command: list[str], job, deadline: float, log: Path) -> str:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with script_jobs.lock:
        if job.cancelled:
            raise _Cancelled
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", **options)
        job.process = process
    try:
        output, _ = process.communicate(timeout=max(.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        log.write_text(log.read_text(encoding="utf-8") + output, encoding="utf-8")
        raise
    with log.open("a", encoding="utf-8") as stream:
        stream.write(output)
    if job.cancelled:
        raise _Cancelled
    if process.returncode:
        raise RuntimeError(f"远程模型命令失败（exit {process.returncode}）：{output[-2000:]}")
    return output


def _remote_infer(request: Path, response: Path, model: dict, run_id: str, job,
                  deadline: float, log: Path) -> None:
    remote = model["remote"]
    directory = remote["root"].rstrip("/") + "/runs/" + run_id
    remote_request, remote_response = directory + "/request.npz", directory + "/response.npz"
    host = remote["host"]
    ssh = ["ssh", "-o", "BatchMode=yes"]
    scp = ["scp", "-o", "BatchMode=yes"]
    _command(ssh + [host, "mkdir -p " + shlex.quote(directory)], job, deadline, log)
    progress("upload_eeg", "上传 EEG 输入", f"{request.stat().st_size / 1024:.0f} KB · 发送到推理服务器")
    _command(scp + [str(request), host + ":" + remote_request], job, deadline, log)
    paired = "face_video" in model["modalities"]
    script = remote["root"].rstrip("/") + "/" + remote["script"]
    arguments = [remote["python"], script, "--request", remote_request, "--response", remote_response]
    if paired:
        video = model["paired_video"]
        remote_video = directory + "/video" + Path(video["source_path"]).suffix.lower()
        progress("upload_video", "上传配对视频", Path(video["source_path"]).name)
        _command(scp + [video["source_path"], host + ":" + remote_video], job, deadline, log)
        arguments += ["--video", remote_video]
    command = "exec env CUDA_VISIBLE_DEVICES=" + str(remote["gpu"]) + " OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 HF_HUB_OFFLINE=1 "
    command += " ".join(shlex.quote(value) for value in arguments)
    progress("inference", "远程推理中", "EEG 与配对视频" if paired else "EEG 输入")
    _command(ssh + ["-tt", host, command], job, deadline, log)
    progress("download", "取回推理结果")
    _command(scp + [host + ":" + remote_response, str(response)], job, deadline, log)


def _prediction_windows(source_id: str, model: dict, max_trials: int | None,
                        start_s: float | None, end_s: float | None, paired: dict | None = None) -> tuple[dict, dict, dict]:
    geometry = signal_metadata(source_id)
    sr = geometry["sample_rate_hz"]
    baseline_s = model["input"].get("baseline_s", 0)
    trial_s = model["input"]["trial_s"]
    plan = prediction_plan(geometry, model["input"], start_s, end_s, max_trials)
    rows = plan["windows"]
    if paired:
        error = video_coverage_error(rows, paired["duration_s"], paired["sync_offset_s"])
        if error:
            raise ValueError(error)
        for row in rows:
            row.update(video_manifest_id=paired["video_manifest_id"],
                       video_time_range_s=[value - paired["sync_offset_s"] for value in row["original_time_range_s"]])
    prediction_range = plan["prediction_range_s"]
    _, _, _, steps, sources = resolve_signal(source_id)
    params = {"source": source_id, "model_id": model["model_id"], "start_s": start_s, "end_s": end_s, "max_trials": max_trials}
    trial_id = make_artifact_id("model_trial_context", VERSION, params)
    trial_path = artifact_payload_path(trial_id, "json")
    trial_payload = {"source": source_id, "sample_rate_hz": sr, "channel_names": geometry["channel_names"], "windows": plan["trials"]}
    trial_path.write_text(json.dumps(trial_payload, ensure_ascii=False), encoding="utf-8")
    trial_record = save_artifact({"artifact_id": trial_id, "kind": "windows", "uri": str(trial_path),
        "media_type": "application/json", "source_manifest_ids": sources,
        "parent_artifact_ids": [source_id] if source_id.startswith("art_") else [],
        "time_range_s": plan["context_time_range_s"], "shape": [len(plan["trials"])], "signal": geometry,
        "analysis": analysis_context(source_id, *plan["context_time_range_s"]),
        "summary": {"source": source_id, "n_windows": len(plan["trials"]), "window_s": trial_s},
        "provenance": {"created_by": f"model_trial_context@{VERSION}", "params": params, "steps": steps + ["complete_model_trials"]}})
    support = {"window_s": model["input"]["window_s"], "stride_s": model["input"]["window_s"],
               "partial": "drop", "n_windows": len(rows), "n_trials": len(trial_payload["windows"]),
               "trial_s": model["input"]["trial_s"], "max_trials": max_trials,
               "baseline_excluded_s": baseline_s,
               "context_time_range_s": trial_record["time_range_s"],
               "boundary_policy": "complete trial context; stimulus windows never cross trial / epoch boundaries"}
    windows_id = make_artifact_id("run_model_windows", VERSION, {"trials_id": trial_record["artifact_id"],
                                                                "model_id": model["model_id"], "prediction_range_s": prediction_range,
                                                                **({"video_id": paired["video_manifest_id"], "offset": paired["sync_offset_s"]} if paired else {})})
    path = artifact_payload_path(windows_id, "json")
    payload = {"source": source_id, "sample_rate_hz": sr, "channel_names": geometry["channel_names"],
               "signal_kind": geometry["kind"], "window_support": support, "windows": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    sources = trial_record["source_manifest_ids"] + ([paired["video_manifest_id"]] if paired else [])
    record = save_artifact({"artifact_id": windows_id, "kind": "windows", "uri": str(path),
                            "media_type": "application/json", "source_manifest_ids": sources,
                            "parent_artifact_ids": [trial_record["artifact_id"]], "time_range_s": prediction_range,
                            "shape": [len(rows)], "signal": geometry,
                            "analysis": {**analysis_context(source_id, *prediction_range), "window_support": support},
                            "summary": {**support, "source": source_id},
                            "provenance": {"created_by": f"run_model_windows@{VERSION}",
                                           "params": {"trials_id": trial_record["artifact_id"]},
                                           "steps": trial_record["provenance"]["steps"] + ["model_trial_windows"]}})
    return trial_record, trial_payload, record


def run_model(source_id: str, model_id: str, session_id: str | None = None,
              start_s: float | None = None, end_s: float | None = None,
              max_trials: int | None = None, timeout_s: float = 300,
              video_manifest_id: str | None = None, video_role: str = "unknown",
              sync_offset_s: float | None = None) -> dict:
    registry = load_registry()
    if model_id not in EXECUTABLE_MODELS or model_id not in registry:
        raise ValueError(f"模型 {model_id} 尚无推理执行器")
    catalog = list_models(registry=registry)["models"]
    selected = next(row for row in catalog if row["model_id"] == model_id)
    if not selected["inference_available"]:
        raise ValueError("；".join(selected["blockers"]))
    if timeout_s < 1 or timeout_s > 900 or max_trials is not None and max_trials < 1:
        raise ValueError("timeout_s 需要在 1–900 秒，max_trials 需要为正数")
    data, sr, names, steps, manifests = resolve_signal(source_id)
    if len(manifests) != 1:
        raise ValueError("模型需要单一、有明确准备契约的源记录")
    manifest = load_manifest(manifests[0])
    mode = manifest.get("metadata_evidence", {}).get("task_mode", {}).get("value", "unknown")
    required_mode = registry[model_id]["input"].get("task_mode")
    if required_mode and mode != required_mode:
        raise ValueError("此权重仅验证 EAV speaking 模式；请先登记 task_mode=speaking 的真实记录上下文")
    compat = next(row for row in check_model_compat(manifest, registry) if row["model_id"] == model_id)
    if compat["status"] != "compatible":
        raise ValueError("；".join(compat["reasons"]))
    if any(not step.startswith(("unit_", "sample_rate_")) for step in steps):
        raise ValueError("请使用登记输入格式的原波形，避免叠加训练配方之外的滤波或归一化")
    model = {**registry[model_id], "model_id": model_id}
    if "face_video" in model["modalities"]:
        if video_manifest_id is None:
            raise ValueError("此识别路线需要配对被试视频：video_manifest_id")
        video = load_manifest(video_manifest_id)
        if video["modality"] != "video":
            raise ValueError("配对输入必须是视频附件")
        pair = paired_video_context(video, video_role, sync_offset_s)
        if pair["video_role"] != "face" or pair["sync_offset_s"] is None:
            raise ValueError("需要被试面部视频用途和同步偏移；按采集说明登记 video_role=face 与 sync_offset_s")
        timing = video.get("timing") or {}
        if timing.get("status") != "cfr_verified" or video.get("duration_s") is None:
            raise ValueError("；".join(timing.get("reasons") or ["视频帧时钟与真实视频流时长尚未核实；请重新读取录像"]))
        model["paired_video"] = {**pair, "source_path": str(resolve_readable(video["source_path"])),
                                  "attachment_sha256": video["attachment_sha256"], "duration_s": video["duration_s"]}
    expected = CHANNEL_ORDERS[model["input"]["channel_order"]]
    mapping = [list(map(normalize_channel, names)).index(normalize_channel(name)) for name in expected]
    model["source_channel_order"] = list(names)
    model["excluded_source_channels"] = [name for i, name in enumerate(names) if i not in mapping]
    data, names = data[mapping], list(expected)
    model["source_channel_mapping"] = mapping
    sid = session_id or "anon"
    job = script_jobs.ScriptJob()
    with script_jobs.lock:
        if sid in script_jobs.active:
            raise ValueError("本会话已有脚本或模型正在执行，请先停止或等待完成")
        script_jobs.active[sid] = job
    started = time.monotonic()
    try:
        return _run_model(source_id, data, sr, names, model, sid, job, started + timeout_s,
                          start_s, end_s, max_trials)
    except _Cancelled:
        return {"status": "error", "error": {"code": "Cancelled", "message": "用户已停止模型推理", "recoverable": True}}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": {"code": "Timeout", "message": f"远程模型超过 {timeout_s:g} 秒；可缩小时间范围或 max_trials", "recoverable": True}}
    finally:
        with script_jobs.lock:
            script_jobs.active.pop(sid)
        job.done.set()


def _run_model(source_id, data, sr, names, model, sid, job, deadline, start_s, end_s, max_trials):
    progress("prepare", "准备推理输入", "按已登记的模型输入与所选时间范围准备")
    trials, trial_payload, windows = _prediction_windows(source_id, model, max_trials, start_s, end_s, model.get("paired_video"))
    run_id = "model_" + uuid.uuid4().hex[:12]
    directory = scratch_dir(sid) / run_id
    directory.mkdir()
    request, response, log = directory / "request.npz", directory / "response.npz", directory / "transport.log"
    log.write_text("", encoding="utf-8")
    packed = np.concatenate([data[:, row["start_sample"]:row["end_sample"]] for row in trial_payload["windows"]], axis=1)
    sample_count = round(model["input"]["window_s"] * sr)
    window_payload = json.loads(resolve_inside_storage(windows["uri"]).read_text(encoding="utf-8"))
    metadata = {"model_id": model["model_id"], "sample_rate_hz": sr, "channel_names": names,
                "unit": "dataset_native", "input_stage": model["input"]["input_stage"],
                "windows": [{"index": row["index"], "start_sample": row["context_start_sample"],
                             "end_sample": row["context_start_sample"] + sample_count} for row in window_payload["windows"]]}
    if model["input"].get("task_mode"):
        metadata["task_mode"] = model["input"]["task_mode"]
    if "paired_video" in model:
        pair = model["paired_video"]
        metadata.update(video_manifest_id=pair["video_manifest_id"], video_role="face", sync_offset_s=pair["sync_offset_s"])
        for requested, saved in zip(metadata["windows"], window_payload["windows"]):
            requested.update(video_start_s=saved["video_time_range_s"][0], video_end_s=saved["video_time_range_s"][1])
    np.savez_compressed(request, signal=packed, metadata=json.dumps(metadata, ensure_ascii=False))
    _remote_infer(request, response, model, run_id, job, deadline, log)
    progress("save", "核对并保存结果")
    with np.load(response, allow_pickle=False) as archive:
        inputs = archive["eeg_inputs"] if "paired_video" in model else archive["inputs"]
        video_inputs = archive["video_inputs"] if "paired_video" in model else None
        logits = archive["logits"]
        received = json.loads(str(archive["metadata"].item()))
    identity, output = received["model"], received["output"]
    if (identity["model_id"] != model["model_id"] or identity["model_version"] != model["model_version"] or identity["task"] != model["task"] or
        identity["weight_sha256"] != model["sha256"] or output["labels"] != model["label_map"] or
        output["kind"] != "classification" or output["score_kind"] != "logits"):
        raise ValueError("远程返回的模型、权重或标签顺序与登记契约不一致")
    expected_shape = (windows["shape"][0], *model["input"]["model_input_shape"])
    if inputs.shape != expected_shape or not np.isfinite(inputs).all():
        raise ValueError(f"远程 EEG 张量不符合登记输入契约 {expected_shape}")
    if video_inputs is not None:
        video_shape = (windows["shape"][0], model["input"]["video"]["frames_per_window"], *model["input"]["video"]["size"], 3)
        if video_inputs.shape != video_shape or not np.isfinite(video_inputs).all():
            raise ValueError(f"远程视频张量不符合登记输入契约 {video_shape}")
    if job.cancelled:
        raise _Cancelled
    input_id = make_artifact_id("model_input", VERSION, {"run_id": run_id, "windows_id": windows["artifact_id"],
                                                        "weight_sha256": model["sha256"]})
    tensor_path = artifact_payload_path(input_id, "npz")
    shutil.copyfile(response, tensor_path)
    paired_summary = {}
    if "paired_video" in model:
        pad_names = [f"zero_pad_{i+1}" for i in range(inputs.shape[1] - len(names))]
        input_names = names + pad_names
        paired_summary = {"video_tensor_shape": list(video_inputs.shape), "video_preparation": received["video_preparation"],
                          "paired_video": {key: value for key, value in model["paired_video"].items() if key != "source_path"},
                          "channel_names": input_names,
                          "model_channel_order": input_names,
                          "bands": ["delta", "theta", "alpha", "beta", "gamma"],
                          "model_amplitude_unit": "DE-LDS log2 in original numerical scale"}
    tensor = save_artifact({"artifact_id": input_id, "kind": "table", "uri": str(tensor_path),
                            "media_type": "application/x-npz", "source_manifest_ids": windows["source_manifest_ids"],
                            "parent_artifact_ids": [windows["artifact_id"]], "time_range_s": windows["time_range_s"],
                            "shape": list(inputs.shape), "signal": windows["signal"], "analysis": windows["analysis"],
                            "summary": {"result_type": "model_input_tensor", "model_id": model["model_id"],
                                        "windows_id": windows["artifact_id"], "channel_names": names,
                                        "model_channel_order": names,
                                        "source_channel_order": model["source_channel_order"],
                                        "source_channel_mapping": model["source_channel_mapping"],
                                        "excluded_source_channels": model["excluded_source_channels"],
                                        "tensor_shape": list(inputs.shape), "tensor_dtype": str(inputs.dtype),
                                        "model_amplitude_unit": "a.u.", "model_sample_rate_hz": 128,
                                        "preparation": received["preparation"], "runtime": received["runtime"], **paired_summary},
                            "provenance": {"created_by": f"run_model@{VERSION}", "model_id": model["model_id"],
                                           "model_run_id": run_id, "weight_sha256": model["sha256"],
                                           "params": {"model": identity, "preparation": received["preparation"]},
                                           "steps": windows["provenance"]["steps"] + ["model_input_preparation"]}})
    identity = {**identity, "input_artifact_id": input_id, "preparation": received["preparation"],
                "evaluation": model_evaluation(model),
                "runtime": received["runtime"], "source_channel_mapping": model["source_channel_mapping"],
                "source_channel_order": model["source_channel_order"], "excluded_source_channels": model["excluded_source_channels"],
                "limitations": model.get("limitations", [])}
    identity["modalities"] = model["modalities"]
    if "paired_video" in model:
        identity.update(paired_video=paired_summary["paired_video"], video_preparation=received["video_preparation"])
    result = save_model_prediction(windows["artifact_id"], logits, identity, output, run_id=run_id)
    result.update(prediction_executed=True, artifacts=[_public(trials), _public(windows), _public(tensor)],
                  transport_log_path=str(log), input_artifact_id=input_id)
    return result
