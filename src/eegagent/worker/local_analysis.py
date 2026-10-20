"""A complete local measurement pass, with no classification model or remote upload."""
from pathlib import Path

from eegagent.worker import script_jobs
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.measurements import quality_summary
from eegagent.worker.processing import segment_signal, extract_features
from eegagent.worker.outputs import plot, export
from eegagent.worker.emotion import save_emotion_assessment
from eegagent.worker.progress import report
from eegagent.worker.store import load_manifest, save_manifest


def analyze_signal(args, session_id):
    metadata = args.get("eeg_metadata",{})
    manifest = load_manifest(args["manifest_id"]) if args.get("manifest_id") else inspect_record(args["path"], sample_rate_hz=metadata.get("sample_rate_hz"), channel_names=metadata.get("channel_names"), trial_index=args.get("trial_index"))
    trial = args.get("trial_index")
    if metadata or (trial is not None and trial != manifest.get("trial_index")):
        manifest = update_record_metadata(manifest["manifest_id"], trial_index=trial, **metadata)["manifest"]
    source = manifest["manifest_id"]
    review = args.get("emotion_review", False)
    baseline = manifest.get("metadata_evidence", {}).get("baseline_duration_s", {})
    start = args.get("start_s", baseline.get("value", 0) if review and baseline.get("source") in ("file_header", "user_confirmed") else 0)
    end = args.get("end_s", manifest["duration_s"])
    job = script_jobs.ScriptJob()
    with script_jobs.lock:
        if session_id in script_jobs.active:
            raise ValueError("当前会话已有执行中的任务")
        script_jobs.active[session_id] = job
    def stage(name, label):
        if job.cancelled:
            raise InterruptedError("本地分析已停止")
        report(name, label, Path(manifest["source_path"]).name)
    try:
        stage("local_quality", "检查信号质量")
        quality = quality_summary(source, start, end)
        manifest["quality"] = quality;save_manifest(manifest)
        stage("local_features", "计算频谱与逐窗特征")
        windows = segment_signal(source, args.get("window_s",4), args.get("stride_s",2), start_s=start, end_s=end)
        features = extract_features(windows["artifact"]["artifact_id"], args.get("features",["bandpower","de","spectral_entropy","hjorth"]))
        stage("local_plots", "生成波形与频谱图")
        figures = [plot(kind,source,start,end)["artifact"] for kind in ["raw","psd"]]
        stage("local_export", "保存分析结果")
        table = export("csv",features["artifact"]["artifact_id"])
        artifacts = [windows["artifact"],features["artifact"],*figures,table["artifact"]]
        if review:
            powers = features["summary"].get("mean_bandpower",{})
            bands = {"delta":"δ", "theta":"θ", "alpha":"α", "beta":"β", "gamma":"γ"}
            rationale = f"已完成 {start:g}–{end:g} 秒的本地特征测量。"
            if powers:
                rationale += "平均频带功率（" + features["summary"]["power_unit"] + "）：" + "，".join(f"{bands[band]} {value:.3g}" for band,value in powers.items()) + "。"
            assessment = save_emotion_assessment(features["artifact"]["artifact_id"],[],None,rationale,assessment_status="unassessed")
            artifacts.append(assessment["artifact"])
        quality_brief = {key: quality[key] for key in ["scope", "sample_time_range_s", "sample_count", "amplitude_unit", "flat_channels", "line_noise_ratio", "line_noise_bands_hz", "noise_metric"]}
        feature_brief = {key: value for key, value in features["summary"].items() if key in ["n_windows", "n_channels", "features", "channel_names", "bands", "band_names", "mean_bandpower", "relative_bandpower", "relative_bandpower_basis", "power_unit", "amplitude_unit", "mean_de", "de_method", "de_unit", "de_bands", "de_filter", "de_log_base", "de_input_amplitude_unit", "de_variance_ddof", "mean_hjorth", "mean_spectral_entropy", "feature_component_units", "frontal_alpha_asymmetry", "asymmetry_note"]}
        return {"status":"ok", "manifest":manifest, "artifacts":artifacts,
                "summary":{"quality":quality_brief,"features":feature_brief,"time_range_s":[start,end],"n_windows":windows["summary"]["n_windows"],"emotion_review":review,"prediction_executed":False}}
    finally:
        with script_jobs.lock:
            script_jobs.active.pop(session_id)
        job.done.set()
