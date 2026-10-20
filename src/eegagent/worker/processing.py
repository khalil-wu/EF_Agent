"""Signal processing tools: preprocess, segment_signal, extract_features.

All functions are deterministic, take manifest/artifact ids, and write artifacts. Nothing here
interprets results; interpretation is the model's job with the skill's knowledge.
"""

from __future__ import annotations

import json
import math
from fractions import Fraction

import numpy as np
from scipy import signal as sps
from scipy import stats as spstats

from eegagent.core.segment import WindowPolicy, plan_windows, _uniform_indices
from eegagent.worker.signals import (amplitude_unit, amplitude_unit_source, analysis_context, analysis_from_geometry,
                                    events_for_source, resolve_channel_types, resolve_signal, signal_metadata, time_coordinates)
from eegagent.worker.store import artifact_payload_path, load_artifact, make_artifact_id, resolve_inside_storage, save_artifact

VERSION = "1.3"
FEATURE_VERSION = "1.6"

BANDS = {
    "standard": {"delta": (0.5, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30), "gamma": (30, 50)},
    "camer_5band": {"delta": (0.5, 4), "theta": (4, 8), "alpha": (8, 14), "beta": (14, 30), "gamma": (30, 50)},
    "torcheeg_4band": {"theta": (4, 8), "alpha": (8, 14), "beta": (14, 31), "gamma": (31, 49)},
}

ASYM_PAIRS = [("F3", "F4"), ("F7", "F8"), ("AF3", "AF4"), ("FC5", "FC6"), ("C3", "C4"), ("T7", "T8"), ("CP5", "CP6"),
              ("P3", "P4"), ("P7", "P8"), ("O1", "O2")]
FRONTAL_ASYM_PAIRS = {("F3", "F4"), ("F7", "F8"), ("AF3", "AF4")}


def _norm(name: str) -> str:
    return name.strip().replace("EEG ", "").replace("-REF", "").replace("-LE", "").upper()


def save_signal(art_id: str, data: np.ndarray, sr: float, names: list[str], types: list[str], *, source_manifest_ids: list[str],
                parents: list[str], steps: list[str], summary: dict, params: dict, created_by: str, time_offset_s: float = 0.0,
                signal_kind: str = "continuous", segments: list[dict] | None = None, events: list[dict] | None = None) -> dict:
    """Persist a normalized_signal artifact (µV float32 .npy + record). Shared by preprocess, clean_artifacts, eeg.save_raw."""
    path = artifact_payload_path(art_id, "npy")
    np.save(path, np.asarray(data, dtype=np.float32))
    duration = data.shape[1] / sr
    segments = segments if segments is not None else [{"start_sample": 0, "end_sample": data.shape[1],
                                                       "original_start_s": float(time_offset_s),
                                                       "original_end_s": float(time_offset_s) + duration}]
    signal = {"kind": signal_kind, "sample_rate_hz": sr, "channel_names": list(names), "channel_types": list(types),
              "amplitude_unit": amplitude_unit(steps), "amplitude_unit_source": amplitude_unit_source(steps), "segments": segments}
    if signal_kind == "continuous" and len(segments) == 1:
        signal["time_offset_s"] = float(segments[0]["original_start_s"])
    if events is not None:
        signal["events"] = events
    return save_artifact({
        "artifact_id": art_id,
        "kind": "normalized_signal",
        "uri": str(path),
        "media_type": "application/x-npy",
        "source_manifest_ids": source_manifest_ids,
        "parent_artifact_ids": parents,
        "time_range_s": [0.0, round(duration, 3)],
        "shape": list(data.shape),
        "signal": signal,
        "analysis": analysis_from_geometry(art_id, signal),
        "summary": {"n_channels": data.shape[0], "sample_rate_hz": sr, "duration_s": round(duration, 3),
                    "amplitude_unit": amplitude_unit(steps), "amplitude_unit_source": amplitude_unit_source(steps),
                    "amplitude_unit_estimated": amplitude_unit_source(steps) in ("inferred", "format_assumption"), **summary},
        "provenance": {"created_by": created_by, "params": params, "steps": steps},
    })


# ================================================================ preprocess

def preprocess(source_id: str, steps: list[dict]) -> dict:
    data, sr, names, prov, src_manifests = resolve_signal(source_id)
    types = resolve_channel_types(source_id, names)
    geometry = signal_metadata(source_id)
    segments = geometry["segments"]
    events = events_for_source(source_id)
    data = np.array(data, dtype=float, copy=True)
    applied: list[str] = []
    notes: list[str] = []
    effective: list[dict] = []

    for st in steps:
        op = st.get("op")
        setting = dict(st, input_sample_rate_hz=sr)
        if op == "pick_eeg":
            keep = [i for i, t in enumerate(types) if t == "eeg"]
            if not keep:
                raise ValueError("pick_eeg：没有 EEG 类型的通道")
            dropped = [f"{names[i]}({types[i]})" for i in range(len(names)) if i not in keep]
            data, names, types = data[keep], [names[i] for i in keep], [types[i] for i in keep]
            applied.append("pick_eeg")
            if dropped:
                notes.append(f"去除非 EEG 通道：{', '.join(dropped)}")
        elif op == "pick_channels":
            want = [_norm(c) for c in st.get("channels", [])]
            idx = [i for i, n in enumerate(names) if _norm(n) in want]
            idx.sort(key=lambda i: want.index(_norm(names[i])))
            if not idx:
                raise ValueError(f"pick_channels：没有匹配的通道 {st.get('channels')}")
            data, names, types = data[idx], [names[i] for i in idx], [types[i] for i in idx]
            applied.append("pick_channels")
        elif op == "rereference":
            method = {"average": "car", "avg": "car", "common_average": "car"}.get(st.get("method", "car"), st.get("method", "car"))
            if method not in ("car", "channel"):
                raise ValueError(f"未知重参考方法：{method}；可用 car（平均参考，也可写 average）或 channel")
            eeg = [i for i, t in enumerate(types) if t == "eeg"]
            if not eeg:
                raise ValueError("rereference：没有 EEG 类型的通道")
            if method == "car":
                data[eeg] = data[eeg] - data[eeg].mean(axis=0, keepdims=True)
                applied.append("rereference_car")
            else:
                ref = _norm(st.get("channel", ""))
                try:
                    j = [_norm(n) for n in names].index(ref)
                except ValueError as exc:
                    raise ValueError(f"rereference：参考通道 {st.get('channel')} 不存在") from exc
                data[eeg] = data[eeg] - data[j : j + 1]
                applied.append(f"rereference_{ref}")
        elif op == "notch":
            f0 = float(st.get("freq", 50))
            q = float(st.get("q", 30))
            if f0 >= sr / 2:
                raise ValueError(f"notch {f0} Hz 超过 Nyquist {sr / 2} Hz")
            b, a = sps.iirnotch(f0, q, fs=sr)
            volt = _volt_rows(types)
            for segment in segments:
                a0, b0 = segment["start_sample"], segment["end_sample"]
                data[volt, a0:b0] = sps.filtfilt(b, a, data[volt, a0:b0], axis=1)
            applied.append(f"notch_{int(f0)}")
            setting.update(freq_hz=f0, q=q, implementation="scipy filtfilt", boundary_policy="independent continuous segments")
        elif op == "bandpass":
            lo, hi = float(st.get("low", 0.5)), float(st.get("high", 50))
            order = int(st.get("order", 4))
            method = st.get("method", "iir")
            if method not in ("iir", "fir"):
                raise ValueError(f"未知滤波方法：{method}；可用 iir 或 fir")
            if hi >= sr / 2:
                hi = sr / 2 - 0.5
                notes.append(f"带通上限超过 Nyquist，已降到 {hi} Hz")
            if lo <= 0 or lo >= hi:
                raise ValueError(f"bandpass 频带无效：{lo}–{hi} Hz")
            volt = _volt_rows(types)
            if method == "fir":  # EEGLAB pop_eegfiltnew / MNE default: windowed-sinc FIR, zero phase
                import mne

                for segment in segments:
                    a, b = segment["start_sample"], segment["end_sample"]
                    data[volt, a:b] = mne.filter.filter_data(data[volt, a:b], sr, lo, hi, method="fir", phase="zero",
                                                            fir_window="hamming", fir_design="firwin", verbose="ERROR")
                applied.append(f"bandpass_{lo:g}_{hi:g}_fir")
            else:
                sos = sps.butter(order, [lo, hi], btype="bandpass", fs=sr, output="sos")
                for segment in segments:
                    a, b = segment["start_sample"], segment["end_sample"]
                    data[volt, a:b] = sps.sosfiltfilt(sos, data[volt, a:b], axis=1)
                applied.append(f"bandpass_{lo:g}_{hi:g}")
            setting.update(low_hz=lo, high_hz=hi, method=method, order=order if method == "iir" else None,
                           phase="zero", boundary_policy="independent continuous segments")
            if method == "fir":
                setting.update(fir_window="hamming", fir_design="firwin", filter_length="auto", pad="reflect_limited")
        elif op == "resample":
            target = float(st.get("target_hz"))
            if target <= 0:
                raise ValueError("resample 需要 target_hz")
            if abs(target - sr) > 1e-6:
                ratio = (Fraction(str(target)) / Fraction(str(sr))).limit_denominator(100000)
                up, down = ratio.numerator, ratio.denominator
                pieces, transformed, mapped_events, cursor = [], [], [], 0
                next_sr = sr * up / down
                for segment in segments:
                    a, b = segment["start_sample"], segment["end_sample"]
                    piece = sps.resample_poly(data[:, a:b], up, down, axis=1)
                    transformed.append(dict(segment, start_sample=cursor, end_sample=cursor + piece.shape[1],
                                            original_end_s=segment.get("original_end_s", segment["original_start_s"] + (b-a)/sr)))
                    pieces.append(piece)
                    for event in events:
                        if a/sr <= event["onset_s"] < b/sr:
                            mapped_events.append(dict(event, onset_s=cursor/next_sr + event["onset_s"] - a/sr))
                    cursor += piece.shape[1]
                data, segments, events = np.concatenate(pieces, axis=1), transformed, mapped_events
                sr = next_sr
            applied.append(f"resample_{target:g}")
            setting.update(output_sample_rate_hz=sr, implementation="scipy resample_poly", window=["kaiser", 5.0],
                           padtype="constant", boundary_policy="independent continuous segments")
        elif op == "remove_baseline":
            secs = float(st.get("seconds", 3))
            n = int(secs * sr)
            if n <= 0 or any(n >= segment["end_sample"] - segment["start_sample"] for segment in segments):
                raise ValueError("remove_baseline 的 seconds 超出某个连续段/epoch的长度")
            volt = _volt_rows(types)
            pieces, transformed, mapped_events, cursor = [], [], [], 0
            drop = bool(st.get("drop", True))
            for segment in segments:
                a, b = segment["start_sample"], segment["end_sample"]
                data[volt, a:b] -= data[volt, a:a+n].mean(axis=1, keepdims=True)
                piece = data[:, a+n:b] if drop else data[:, a:b]
                updated = dict(segment, start_sample=cursor, end_sample=cursor + piece.shape[1])
                if drop:
                    updated["original_start_s"] += n / sr
                    if "epoch_tmin_s" in updated:
                        updated["epoch_tmin_s"] += n / sr
                transformed.append(updated)
                pieces.append(piece)
                cut = n if drop else 0
                for event in events:
                    if (a+cut)/sr <= event["onset_s"] < b/sr:
                        mapped_events.append(dict(event, onset_s=(cursor-a-cut)/sr + event["onset_s"]))
                cursor += piece.shape[1]
            data, segments, events = np.concatenate(pieces, axis=1), transformed, mapped_events
            if drop:
                notes.append(f"每个连续段/epoch分别去除前 {n/sr:g} s baseline，保留各段原始时间关系")
            applied.append(f"remove_baseline_{secs:g}")
            setting.update(samples=n, actual_seconds=n/sr, drop=drop, boundary_policy="each continuous segment / epoch separately")
        elif op == "normalize":
            method = st.get("method", "zscore")
            if method not in ("zscore", "minmax"):
                raise ValueError(f"未知归一化方法：{method}；可用 zscore 或 minmax")
            volt = _volt_rows(types)
            x = data[volt]
            if method == "zscore":
                sd = x.std(axis=1, keepdims=True)
                sd[sd == 0] = 1.0
                data[volt] = (x - x.mean(axis=1, keepdims=True)) / sd
            else:
                mn, mx = x.min(axis=1, keepdims=True), x.max(axis=1, keepdims=True)
                rng = np.where(mx - mn == 0, 1.0, mx - mn)
                data[volt] = (x - mn) / rng
            applied.append(f"normalize_{method}")
        else:
            raise ValueError(f"未知预处理步骤 op={op}")
        effective.append(setting)

    art_id = make_artifact_id("preprocess", VERSION, {"source": source_id, "steps": steps})
    record = save_signal(art_id, data, sr, names, types, source_manifest_ids=src_manifests,
                         parents=[source_id] if source_id.startswith("art_") else [], steps=prov + applied,
                         summary={"steps": applied, "notes": notes, "processing_settings": effective}, params={"source": source_id, "steps": steps},
                         created_by=f"preprocess@{VERSION}", signal_kind=geometry["kind"], segments=segments, events=events)
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}


def _volt_rows(types: list[str]) -> list[int]:
    """Rows that hold voltages (EEG/EOG/ECG/EMG); trigger and misc channels are never filtered or rescaled."""
    from eegagent.worker.channels import VOLT_TYPES

    rows = [i for i, t in enumerate(types) if t in VOLT_TYPES]
    return rows or list(range(len(types)))


# ================================================================ segment

def segment_signal(source_id: str, window_s: float, stride_s: float | None = None, partial: str = "drop",
                   max_windows: int | None = None, start_s: float | None = None, end_s: float | None = None) -> dict:
    data, sr, names, prov, src_manifests = resolve_signal(source_id)
    total = data.shape[1] / sr
    s0 = float(start_s or 0.0)
    s1 = float(end_s) if end_s is not None else total
    if s0 < 0 or s1 > total + 1e-9 or s0 >= s1:
        raise ValueError(f"时间范围 {s0}–{s1} s 超出信号时长 {total:.3f} s")
    stride = float(stride_s) if stride_s is not None else float(window_s)
    if window_s <= 0 or stride <= 0 or partial not in ("drop", "keep", "pad"):
        raise ValueError("window_s 和 stride_s 须为正数，partial 须为 drop / keep / pad")
    geometry = signal_metadata(source_id)
    rows = []
    for si, segment in enumerate(geometry["segments"]):
        seg_start = max(round(s0 * sr), segment["start_sample"])
        seg_end = min(round(s1 * sr), segment["end_sample"])
        if seg_end <= seg_start:
            continue
        wins = plan_windows((seg_end - seg_start) / sr, WindowPolicy(float(window_s), stride, partial=partial))
        for w in wins:
            a = seg_start + int(round(w.start_s * sr))
            b = min(seg_end, seg_start + int(round(w.end_s * sr)))
            rows.append({"start_s": a / sr, "end_s": b / sr, "start_sample": a, "end_sample": b,
                         "segment_index": si, "pad_right_samples": int(round(w.pad_right_s * sr)),
                         **time_coordinates(segment, a / sr, b / sr, sr)})
    rows = [dict(rows[i], index=j) for j, i in enumerate(_uniform_indices(len(rows), max_windows or 0))]
    support = {"window_s": float(window_s), "stride_s": stride, "partial": partial,
               "max_windows": max_windows, "n_windows": len(rows), "n_segments": len(geometry["segments"]),
               "boundary_policy": "windows never cross continuous-segment / epoch boundaries",
               "range_selection": "selected source range; actual support is recorded per window"}
    context = {**analysis_context(source_id, s0, s1), "window_support": support}
    art_id = make_artifact_id("segment_signal", VERSION, {"source": source_id, "window_s": window_s, "stride_s": stride,
                                                          "partial": partial, "max_windows": max_windows, "start_s": s0, "end_s": s1})
    path = artifact_payload_path(art_id, "json")
    path.write_text(json.dumps({"source": source_id, "sample_rate_hz": sr, "channel_names": names,
                               "signal_kind": geometry["kind"], "window_support": support, "windows": rows},
                               ensure_ascii=False), encoding="utf-8")
    record = save_artifact({
        "artifact_id": art_id, "kind": "windows", "uri": str(path), "media_type": "application/json",
        "source_manifest_ids": src_manifests, "parent_artifact_ids": [source_id] if source_id.startswith("art_") else [],
        "time_range_s": [round(s0, 3), round(s1, 3)], "shape": [len(rows)],
        "signal": geometry, "analysis": context,
        "summary": {"n_windows": len(rows), "window_s": window_s, "stride_s": stride, "covers_s": [round(s0, 3), round(s1, 3)],
                    "source": source_id, "boundary_policy": support["boundary_policy"]},
        "provenance": {"created_by": f"segment_signal@{VERSION}",
                       "params": {"source": source_id, "window_s": window_s, "stride_s": stride, "partial": partial, "max_windows": max_windows},
                       "steps": prov + [f"segment_{window_s:g}_{stride:g}"]},
    })
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}


def load_windows(windows_id: str):
    rec = load_artifact(windows_id)
    if rec.get("kind") != "windows":
        raise ValueError(f"{windows_id} is kind={rec.get('kind')}, expected windows")
    payload = json.loads(resolve_inside_storage(rec["uri"]).read_text(encoding="utf-8"))
    data, sr, names, prov, src = resolve_signal(payload["source"])
    return rec, payload, data, sr, names, prov, src


# ================================================================ features

def _welch(seg: np.ndarray, sr: float, nper: int | None = None):
    nper = nper or int(min(seg.shape[1], max(64, 2 * sr)))
    freqs, psd = sps.welch(seg, fs=sr, nperseg=nper, noverlap=nper // 2, nfft=nper, axis=1,
                          window="hamming", detrend="constant", scaling="density", average="mean")
    return freqs, psd


def welch_by_segments(data: np.ndarray, sr: float, segments: list[dict], start_sample: int = 0,
                      end_sample: int | None = None):
    """Pool actual Welch subwindows while preventing a subwindow from crossing a discontinuity."""
    end_sample = data.shape[1] if end_sample is None else end_sample
    bounds = [(max(start_sample, s["start_sample"]), min(end_sample, s["end_sample"])) for s in segments]
    bounds = [(a, b) for a, b in bounds if b > a]
    nper = int(min(max(64, 2 * sr), min(b-a for a, b in bounds)))
    if nper < 2:
        raise ValueError("所选连续段/epoch不足两个采样点，无法计算 Welch PSD")
    weight_sum, pooled = 0, None
    for a, b in bounds:
        frequencies, density = _welch(data[:, a:b], sr, nper)
        count = 1 + (b-a-nper) // (nper - nper // 2)
        pooled = density * count if pooled is None else pooled + density * count
        weight_sum += count
    settings = {"window": "hamming", "nperseg": nper, "segment_s": nper/sr, "noverlap": nper//2,
                "nfft": nper, "detrend": "constant", "scaling": "density", "average": "mean",
                "boundary_policy": "Welch subwindows never cross continuous-segment / epoch boundaries",
                "aggregation": "equal weight over actual Welch subwindows", "n_welch_segments": weight_sum,
                "n_continuous_segments": len(bounds)}
    return frequencies, pooled / weight_sum, settings


def _bandpower(freqs, psd, band):
    lo, hi = band
    m = (freqs >= lo) & (freqs <= hi)
    if np.count_nonzero(m) < 2:
        return np.full(psd.shape[0], np.nan)
    integrate = getattr(np, "trapezoid", None) or np.trapz
    return integrate(psd[:, m], freqs[m], axis=1)


def _de_filtfilt(seg: np.ndarray, sr: float, band, order=3):
    lo, hi = band
    sos = sps.butter(order, [lo, hi], btype="bandpass", fs=sr, output="sos")
    x = sps.sosfiltfilt(sos, seg, axis=1)
    var = x.var(axis=1, ddof=1)
    return 0.5 * np.log2(2 * math.pi * math.e * np.maximum(var, 1e-12))


def _de_lfilter(seg: np.ndarray, sr: float, band, order=5):
    lo, hi = band
    b, a = sps.butter(order, [lo, hi], btype="bandpass", fs=sr)
    x = sps.lfilter(b, a, seg, axis=1)
    var = x.var(axis=1, ddof=0)
    return 0.5 * np.log2(2 * math.pi * math.e * np.maximum(var, 1e-12))


def _hjorth(seg: np.ndarray):
    d1 = np.diff(seg, axis=1)
    d2 = np.diff(d1, axis=1)
    act = seg.var(axis=1)
    mob = np.sqrt(d1.var(axis=1) / np.maximum(act, 1e-12))
    comp = np.sqrt(d2.var(axis=1) / np.maximum(d1.var(axis=1), 1e-12)) / np.maximum(mob, 1e-12)
    return np.stack([act, mob, comp], axis=1)


def extract_features(windows_id: str, features: list[str], bands: str = "standard", de_method: str = "libeer_filtfilt") -> dict:
    rec_w, payload, data, sr, names, prov, src = load_windows(windows_id)
    types = resolve_channel_types(payload["source"], names)
    eeg_indices = [i for i, kind in enumerate(types) if kind == "eeg"]
    excluded = [name for name, kind in zip(names, types) if kind != "eeg"]
    if not eeg_indices:
        raise ValueError("记录没有 EEG 通道，无法提取 EEG 特征")
    data, names = data[eeg_indices], [names[i] for i in eeg_indices]
    if excluded:
        prov = prov + ["pick_eeg"]
    if "de" in features and de_method not in ("libeer_filtfilt", "torcheeg_var"):
        raise ValueError(f"未知 DE 方法：{de_method}")
    if bands not in BANDS:
        raise ValueError(f"未知频带定义：{bands}")
    if not features:
        raise ValueError("至少选择一种特征")
    band_map = BANDS[bands]
    unavailable_bands = {name: list(band) for name, band in band_map.items() if band[1] >= sr / 2}
    band_map = {name: band for name, band in band_map.items() if name not in unavailable_bands}
    if not band_map and set(features) & {"bandpower", "band_stats", "asymmetry"}:
        raise ValueError(f"采样率 {sr:g} Hz 无法完整覆盖所选频带")
    band_names = list(band_map)
    de_bands = BANDS["torcheeg_4band"] if de_method == "torcheeg_var" else BANDS[bands]
    unavailable_de_bands = {name: list(band) for name, band in de_bands.items() if band[1] >= sr / 2} if "de" in features else {}
    de_bands = {name: band for name, band in de_bands.items() if band[1] < sr / 2}
    de_band_names = list(de_bands)
    if "de" in features and not de_bands:
        raise ValueError(f"采样率 {sr:g} Hz 无法完整覆盖 DE 频带")
    wins = payload["windows"]
    if not wins:
        raise ValueError("窗口表为空")
    unknown = [f for f in features if f not in ("psd", "bandpower", "de", "hjorth", "band_stats", "spectral_entropy", "asymmetry", "connectivity", "rms", "line_length", "zero_crossing_rate")]
    if unknown:
        raise ValueError(f"未知特征 {unknown}")

    out: dict[str, np.ndarray] = {}
    psd_sum = None
    freqs_ref = None
    W, C = len(wins), data.shape[0]
    nper = int(min(max(64, 2 * sr), min(w["end_sample"] - w["start_sample"] + w.get("pad_right_samples", 0) for w in wins)))
    need_psd = any(f in features for f in ("psd", "bandpower", "spectral_entropy", "asymmetry"))
    if need_psd:
        out["bandpower"] = np.full((W, C, len(band_names)), np.nan)
        if "spectral_entropy" in features:
            out["spectral_entropy"] = np.full((W, C, 1), np.nan)
    if "de" in features:
        out["de"] = np.full((W, C, len(de_band_names)), np.nan)
    if "hjorth" in features:
        out["hjorth"] = np.full((W, C, 3), np.nan)
    if "band_stats" in features:
        out["band_stats"] = np.full((W, C, len(band_names) * 3), np.nan)
    if "connectivity" in features:
        out["connectivity"] = np.full((W, C, C), np.nan)
    for name in ("rms", "line_length", "zero_crossing_rate"):
        if name in features:
            out[name] = np.empty((W, C, 1))

    for wi, w in enumerate(wins):
        seg = data[:, w["start_sample"]:w["end_sample"]]
        if seg.shape[1] < 8:
            raise ValueError(f"窗口 {wi} 只有 {seg.shape[1]} 个采样点，无法计算所选特征；请增加窗口长度")
        seg = seg - seg.mean(axis=1, keepdims=True)
        if w.get("pad_right_samples"):
            seg = np.pad(seg, ((0, 0), (0, w["pad_right_samples"])))
        if need_psd:
            freqs, psd = _welch(seg, sr, nper)
            if psd_sum is None:
                psd_sum, freqs_ref = np.zeros_like(psd), freqs
            psd_sum += psd
            for bi, b in enumerate(band_names):
                out["bandpower"][wi, :, bi] = _bandpower(freqs, psd, band_map[b])
            if "spectral_entropy" in features:
                m = (freqs >= 0.5) & (freqs <= min(50, sr / 2))
                p = psd[:, m] / np.maximum(psd[:, m].sum(axis=1, keepdims=True), 1e-12)
                out["spectral_entropy"][wi, :, 0] = -(p * np.log2(np.maximum(p, 1e-12))).sum(axis=1) / math.log2(max(p.shape[1], 2))
        if "de" in features:
            fn = _de_lfilter if de_method == "torcheeg_var" else _de_filtfilt
            for bi, b in enumerate(de_band_names):
                out["de"][wi, :, bi] = fn(seg, sr, de_bands[b])
        if "hjorth" in features:
            out["hjorth"][wi] = _hjorth(seg)
        if "band_stats" in features:
            for bi, b in enumerate(band_names):
                lo, hi = band_map[b]
                sos = sps.butter(3, [lo, hi], btype="bandpass", fs=sr, output="sos")
                x = sps.sosfiltfilt(sos, seg, axis=1)
                out["band_stats"][wi, :, bi * 3 + 0] = np.mean(np.abs(x - x.mean(axis=1, keepdims=True)), axis=1)
                out["band_stats"][wi, :, bi * 3 + 1] = spstats.kurtosis(x, axis=1)
                out["band_stats"][wi, :, bi * 3 + 2] = spstats.skew(x, axis=1)
        if "connectivity" in features:
            out["connectivity"][wi] = np.corrcoef(seg)
        if "rms" in out:
            out["rms"][wi, :, 0] = np.sqrt(np.mean(seg ** 2, axis=1))
        if "line_length" in out:
            out["line_length"][wi, :, 0] = np.mean(np.abs(np.diff(seg, axis=1)), axis=1)
        if "zero_crossing_rate" in out:
            out["zero_crossing_rate"][wi, :, 0] = np.sum(np.diff(np.signbit(seg), axis=1), axis=1) / (seg.shape[1] / sr)

    # asymmetry from alpha bandpower
    asym = None
    asym_pairs_found: list[str] = []
    if "asymmetry" in features:
        nn = [_norm(n) for n in names]
        alpha_i = band_names.index("alpha") if "alpha" in band_names else None
        vals = []
        for l, r in ASYM_PAIRS:
            if l in nn and r in nn and alpha_i is not None:
                li, ri = nn.index(l), nn.index(r)
                v = np.log(np.maximum(out["bandpower"][:, ri, alpha_i], 1e-12)) - np.log(np.maximum(out["bandpower"][:, li, alpha_i], 1e-12))
                vals.append(v)
                asym_pairs_found.append(f"{l}/{r}")
        if vals:
            asym = np.stack(vals, axis=1)  # [W, pairs]

    # ---- persist
    art_id = make_artifact_id("extract_features", FEATURE_VERSION, {"windows": windows_id, "features": sorted(features), "bands": bands, "de_method": de_method})
    path = artifact_payload_path(art_id, "npz")
    arrays = {k: v for k, v in out.items()}
    if asym is not None:
        arrays["asymmetry"] = asym
    if psd_sum is not None:
        arrays["psd_mean"] = psd_sum / max(W, 1)
        arrays["psd_freqs"] = freqs_ref
    np.savez_compressed(path, **arrays)

    # ---- summary (prompt-safe)
    component_names = {"bandpower": band_names, "de": de_band_names, "hjorth": ["activity", "mobility", "complexity"],
                       "band_stats": [f"{band}_{stat}" for band in band_names for stat in ("mean_abs_deviation", "kurtosis", "skewness")],
                       "connectivity": names, "asymmetry": asym_pairs_found,
                       "spectral_entropy": ["spectral_entropy"], "rms": ["rms"],
                       "line_length": ["line_length"], "zero_crossing_rate": ["zero_crossing_rate"]}
    summary: dict = {"n_windows": W, "n_channels": C, "bands": {b: list(band_map[b]) for b in band_names},
                     "band_names": band_names, "feature_components": {k: v for k, v in component_names.items() if k in arrays},
                     "feature_units": {"rms": amplitude_unit(prov), "line_length": amplitude_unit(prov) + "/sample", "zero_crossing_rate": "Hz"},
                     "features": list(arrays.keys()), "channel_names": names, "windows_id": windows_id,
                     "amplitude_unit": amplitude_unit(prov), "power_unit": amplitude_unit(prov) + "²",
                     "amplitude_unit_source": amplitude_unit_source(prov),
                     "amplitude_unit_estimated": amplitude_unit_source(prov) in ("inferred", "format_assumption")}
    summary["feature_units"].update(bandpower=amplitude_unit(prov) + "²", de="bit", connectivity="1",
                                    spectral_entropy="1", asymmetry="1")
    summary["feature_component_units"] = {
        "hjorth": [amplitude_unit(prov) + "²", "per_sample", "1"],
        "band_stats": [unit for _ in band_names for unit in (amplitude_unit(prov), "1", "1")]}
    settings = {"input_centering": "subtract each channel's arithmetic mean within each observed window; zero padding afterwards",
                "rms": {"centering": "window mean", "definition": "sqrt(mean(centered samples squared))"},
                "hjorth": {"derivative": "successive sample differences, without multiplying by sample_rate_hz",
                           "variance_ddof": 0, "mobility_unit": "per_sample", "complexity_unit": "1"},
                "connectivity": {"method": "Pearson correlation", "axis": "channels within each window"},
                "bandpower": {"method": "trapezoidal integration", "frequency_endpoints": "both inclusive"},
                "outer_windows": payload.get("window_support", rec_w.get("summary", {}))}
    settings = {key: value for key, value in settings.items() if key in out or key in ("input_centering", "outer_windows")}
    if need_psd:
        settings["welch"] = {"window": "hamming", "nperseg": nper, "segment_s": nper / sr,
                              "noverlap": nper // 2, "overlap_s": (nper // 2) / sr, "nfft": nper,
                              "detrend": "constant", "scaling": "density", "average": "mean", "one_sided": True}
    summary["measurement_settings"] = settings
    if excluded:
        summary["excluded_non_eeg_channels"] = excluded
    if need_psd:
        summary["psd_resolution_hz"] = sr / nper
    if unavailable_bands:
        summary["unavailable_bands"] = unavailable_bands
        summary["notes"] = [f"采样率 {sr:g} Hz 下未计算的频带：{', '.join(unavailable_bands)}"]
    if "bandpower" in out:
        mean_bp = np.nanmean(out["bandpower"], axis=(0, 1))
        tot = np.nansum(mean_bp)
        summary["mean_bandpower"] = {b: _summary_number(mean_bp[i]) for i, b in enumerate(band_names)}
        if amplitude_unit(prov) == "µV":
            summary["mean_bandpower_uV2"] = summary["mean_bandpower"]
        summary["relative_bandpower"] = {b: _summary_number(mean_bp[i] / tot) if tot > 0 else None for i, b in enumerate(band_names)}
        summary["relative_bandpower_basis"] = [b for i,b in enumerate(band_names) if np.isfinite(mean_bp[i])]
        if "alpha" in band_names and np.isfinite(out["bandpower"][:, :, band_names.index("alpha")]).any():
            ai = band_names.index("alpha")
            per_w = np.nanmean(out["bandpower"][:, :, ai], axis=1)
            top = np.argsort(-per_w)[:3]
            summary["top_alpha_windows"] = [{"index": int(i), **{k: v for k, v in wins[i].items()
                                                 if k in ("start_s", "end_s", "original_time_range_s", "epoch_index", "epoch_relative_time_range_s")},
                                             "alpha_power": round(float(per_w[i]), 4)} for i in top]
            per_c = np.nanmean(out["bandpower"][:, :, ai], axis=0)
            summary["alpha_by_channel_top5"] = [{"channel": names[i], "alpha_power": round(float(per_c[i]), 4)} for i in np.argsort(-per_c)[:5]]
    if "de" in out:
        summary["mean_de"] = {b: _summary_number(np.nanmean(out["de"][:, :, i])) for i, b in enumerate(de_band_names)}
        summary["de_bands"] = {b: list(de_bands[b]) for b in de_band_names}
        summary["de_band_names"] = de_band_names
        if unavailable_de_bands:
            summary["unavailable_de_bands"] = unavailable_de_bands
        summary["de_method"] = de_method
        summary["de_log_base"] = 2
        summary["de_unit"] = "bit"
        summary["de_variance_ddof"] = 0 if de_method == "torcheeg_var" else 1
        summary["de_input_amplitude_unit"] = amplitude_unit(prov)
        summary["de_scale_note"] = "差分熵采用当前信号的幅值单位"
        summary["de_filter"] = {"family": "butterworth", "order": 5 if de_method == "torcheeg_var" else 3,
                                "implementation": "lfilter" if de_method == "torcheeg_var" else "sosfiltfilt",
                                "scope": "within each observed window", "input_centering": "window mean"}
    if "hjorth" in out:
        h = np.nanmean(out["hjorth"], axis=(0, 1))
        summary["mean_hjorth"] = {"activity": round(float(h[0]), 4), "mobility": round(float(h[1]), 4), "complexity": round(float(h[2]), 4)}
    if "spectral_entropy" in out:
        summary["mean_spectral_entropy"] = round(float(np.nanmean(out["spectral_entropy"])), 4)
    if "asymmetry" in features:
        if asym is not None:
            summary["alpha_asymmetry"] = {p: _summary_number(np.nanmean(asym[:, i])) for i, p in enumerate(asym_pairs_found)}
            summary["frontal_alpha_asymmetry"] = {p: v for p, v in summary["alpha_asymmetry"].items()
                                                   if tuple(p.split("/")) in FRONTAL_ASYM_PAIRS}
            summary["asymmetry_note"] = (
                "ln(右 alpha) − ln(左 alpha)。alpha 功率与皮层活动反向：正值=右侧 alpha 更高=左侧相对更活跃"
                "（Davidson 框架中与趋近动机相关）；负值=左侧 alpha 更高=右侧相对更活跃（与回避/退缩相关）。")
            if not summary["frontal_alpha_asymmetry"]:
                summary["asymmetry_note"] += ("本记录没有额叶左右电极对（F3/F4、F7/F8、AF3/AF4），列出的是"
                                              f"{'、'.join(asym_pairs_found)} 的半球差，不是额叶 alpha 不对称，不用于趋近/回避解释。")
        else:
            summary["asymmetry_note"] = "没有可用的左右对称通道对（需要如 F3/F4、AF3/AF4 的 10-20 名称）"
    if "connectivity" in out:
        summary["connectivity_method"] = "Pearson correlation"
        cm = np.nanmean(out["connectivity"], axis=0)
        iu = np.triu_indices(C, 1)
        summary["mean_abs_connectivity"] = _summary_number(np.nanmean(np.abs(cm[iu]))) if C > 1 else None

    geometry = signal_metadata(payload["source"])
    geometry.update(channel_names=names, channel_types=["eeg"] * len(names))
    context = {**analysis_context(payload["source"], *rec_w["time_range_s"]),
               "window_support": payload.get("window_support", rec_w["summary"]), "settings": settings,
               "aggregation": {"summary": "arithmetic mean with equal weight over selected windows and EEG channels",
                               "psd_mean": "arithmetic mean with equal weight over selected windows, separately per channel",
                               "relative_bandpower": "ratio of mean absolute bandpower to the sum over available requested bands"}}
    record = save_artifact({
        "artifact_id": art_id, "kind": "features", "uri": str(path), "media_type": "application/x-npz",
        "source_manifest_ids": src, "parent_artifact_ids": [windows_id],
        "time_range_s": rec_w.get("time_range_s"), "shape": [W, C],
        "signal": geometry, "analysis": context,
        "summary": summary,
        "provenance": {"created_by": f"extract_features@{FEATURE_VERSION}",
                       "params": {"windows": windows_id, "features": features, "bands": bands, "de_method": de_method},
                       "steps": prov + ["features_" + "+".join(sorted(features))]},
    })
    return {"status": "ok", "artifact": _public(record), "summary": summary}


def _summary_number(value) -> float | None:
    """A feature undefined at this resolution is JSON null, never a fabricated zero or NaN token."""
    return round(float(value), 4) if np.isfinite(value) else None


def _public(record: dict) -> dict:
    """The compact artifact view returned to the model and the UI."""
    out = {k: record.get(k) for k in ("artifact_id", "kind", "uri", "media_type", "source_manifest_ids",
                                     "parent_artifact_ids", "time_range_s", "shape", "summary", "provenance")}
    if record.get("kind") == "prediction":
        provenance = record["provenance"]
        out["provenance"] = {**provenance,
                             "params": {key: value for key, value in provenance["params"].items() if key != "model"},
                             "params_omitted": "model" in provenance["params"]}
    if record.get("signal"):
        sig = record["signal"]
        out["signal"] = {k: v for k, v in sig.items() if k not in ("segments", "events")}
        segments = sig.get("segments", [])
        out["signal"]["n_segments"] = len(segments)
        if sig.get("kind") == "epochs":
            out["signal"]["time_axis"] = "packed epochs; boundaries are discontinuous"
            out["signal"]["epoch_tmin_range_s"] = [min(s["epoch_tmin_s"] for s in segments), max(s["epoch_tmin_s"] for s in segments)]
    if record.get("analysis"):
        out["analysis"] = dict(record["analysis"])
        ranges = out["analysis"].get("original_time_ranges_s", [])
        if len(ranges) > 8:
            out["analysis"].pop("original_time_ranges_s")
            out["analysis"].update(n_time_ranges=len(ranges), original_time_ranges_s_preview=ranges[:4] + ranges[-4:])
    return out
