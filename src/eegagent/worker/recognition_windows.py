"""Shared, artifact-free trial and prediction planning for routing and execution."""

from eegagent.core.segment import _uniform_indices
from eegagent.worker.signals import time_coordinates


def prediction_plan(geometry: dict, inp: dict, start_s=None, end_s=None, max_trials=None) -> dict:
    sr = geometry["sample_rate_hz"]
    segments = geometry["segments"]
    trial_s = inp.get("trial_s", inp["window_s"])
    baseline_s = inp.get("baseline_s", 0)
    grid_s = inp["window_s"] if baseline_s else trial_s
    start = float(start_s or 0)
    end = float(end_s) if end_s is not None else segments[-1]["end_sample"] / sr
    if start < 0 or end <= start or end > segments[-1]["end_sample"] / sr + 1e-9:
        raise ValueError("预测范围结束时间须在信号范围内且晚于开始时间")
    if max_trials is not None and max_trials < 1:
        raise ValueError("max_trials 需要为正数")
    containing = next((seg for seg in segments if seg["start_sample"] / sr <= start < seg["end_sample"] / sr), None)
    if containing is not None and start >= containing["start_sample"] / sr + baseline_s:
        position = (start - containing["start_sample"] / sr - baseline_s) / grid_s
        if abs(position - round(position)) > 1e-9:
            raise ValueError(f"start_s需要对齐原trial 边界后的 {grid_s:g} 秒模型网格")
    trials = []
    trial_samples = round(trial_s * sr)
    window_samples = round(inp["window_s"] * sr)
    for si, segment in enumerate(segments):
        for a in range(segment["start_sample"], segment["end_sample"] - trial_samples + 1, trial_samples):
            b = a + trial_samples
            if not baseline_s and (a / sr < start or b / sr > end):
                continue
            windows = []
            for p in range(a + round(baseline_s * sr), b, window_samples):
                q = p + window_samples
                if p / sr < start or q / sr > end:
                    continue
                windows.append({"start_sample": p, "end_sample": q, "start_s": p / sr, "end_s": q / sr,
                                "segment_index": si, "pad_right_samples": 0,
                                **time_coordinates(segment, p / sr, q / sr, sr)})
            if windows:
                trials.append({"start_sample": a, "end_sample": b, "start_s": a / sr, "end_s": b / sr,
                               "segment_index": si, "pad_right_samples": 0,
                               **time_coordinates(segment, a / sr, b / sr, sr), "prediction_windows": windows})
    if not trials:
        raise ValueError(f"所选范围没有完整预测窗；需要完整 {trial_s:g} 秒trial上下文，基线不输出情绪预测")
    # Select only among trials that actually supply the requested predictions.
    trials = [trials[i] for i in _uniform_indices(len(trials), max_trials or 0)]
    rows = []
    for ti, trial in enumerate(trials):
        for row in trial.pop("prediction_windows"):
            rows.append({**row, "index": len(rows), "context_trial_index": ti,
                         "context_start_sample": ti * trial_samples + row["start_sample"] - trial["start_sample"]})
        trial["index"] = ti
    return {"trials": trials, "windows": rows,
            "context_time_range_s": [trials[0]["start_s"], trials[-1]["end_s"]],
            "prediction_range_s": [max(start, rows[0]["start_s"]), end] if baseline_s else [start, end]}


def video_coverage_error(rows: list[dict], duration_s: float, offset_s: float) -> str | None:
    for row in rows:
        a, b = [value - offset_s for value in row["original_time_range_s"]]
        if a < -1e-9 or b > duration_s + 1e-6:
            return f"视频没有覆盖原 EEG 窗口 {row['original_time_range_s']}；对应视频 {a:g}–{b:g} 秒，重叠时长不足"
    return None
