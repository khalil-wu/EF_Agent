"""Deterministic full-range quality measurements and transient candidates."""

import json

import numpy as np
from scipy.signal import find_peaks

from eegagent.worker.channels import VOLT_TYPES
from eegagent.worker.processing import _public, welch_by_segments
from eegagent.worker.signals import amplitude_unit, analysis_context, resolve_channel_types, resolve_signal, signal_metadata, time_coordinates
from eegagent.worker.store import artifact_payload_path, make_artifact_id, save_artifact


def _selection(source_id, start_s, end_s, channels):
    data, sr, names, steps, manifests = resolve_signal(source_id)
    types = resolve_channel_types(source_id, names)
    indices = ([names.index(name) for name in channels] if channels is not None
               else [i for i, kind in enumerate(types) if kind in VOLT_TYPES])
    if not indices or any(types[index] not in VOLT_TYPES for index in indices):
        raise ValueError("请选择可测量的电压通道；其他物理量使用其自身单位分别分析")
    start = 0.0 if start_s is None else float(start_s)
    end = data.shape[1] / sr if end_s is None else float(end_s)
    if not 0 <= start < end <= data.shape[1] / sr:
        raise ValueError("检查范围超出源信号的时间范围")
    a, b = round(start * sr), round(end * sr)
    if b <= a:
        raise ValueError("检查范围不足一个采样点")
    return data[indices, a:b], sr, [names[i] for i in indices], indices, steps, manifests, a / sr, b / sr


def quality_summary(source_id, start_s=None, end_s=None, channels=None):
    data, sr, names, indices, steps, _, start, end = _selection(source_id, start_s, end_s, channels)
    unit = amplitude_unit(steps)
    geometry = signal_metadata(source_id)
    a, b = round(start * sr), round(end * sr)
    segments = [dict(s, start_sample=max(a, s["start_sample"])-a, end_sample=min(b, s["end_sample"])-a)
                for s in geometry["segments"] if min(b, s["end_sample"]) > max(a, s["start_sample"])]
    frequencies, densities, psd_settings = welch_by_segments(data, sr, segments)
    rows, noise_energy, total_energy = [], 0.0, 0.0
    for name, signal, psd in zip(names, data, densities):
        total = float(psd.sum())
        line = ((frequencies > 48) & (frequencies < 52)) | ((frequencies > 58) & (frequencies < 62))
        noise = float(psd[line].sum())
        differences = np.concatenate([np.diff(signal[s["start_sample"]:s["end_sample"]]) for s in segments])
        rows.append({"channel": name, "rms": float(np.sqrt(np.mean(signal ** 2))),
                     "amplitude_p99": float(np.percentile(np.abs(signal - np.median(signal)), 99)),
                     "peak_to_peak": float(np.ptp(signal)), "flat": bool(np.ptp(signal) == 0),
                     "flat_line_ratio": float(np.mean(differences == 0)),
                     "extrema_repetition_ratio": float(np.mean((signal == signal.min()) | (signal == signal.max()))),
                     "line_noise_ratio": noise / total if total > 0 else None,
                     "hf_noise_ratio": float(psd[frequencies > 40].sum()) / total if total > 0 else None})
        noise_energy += noise
        total_energy += total
    centred = data - np.median(data, axis=1, keepdims=True)
    return {"scope": "selected_signal_range", "sample_time_range_s": [start, end], "sample_count": data.shape[1],
            "amplitude_unit": unit, "channels": rows,
            "flat_channels": [row["channel"] for row in rows if row["flat"]],
            "flat_channel_indices": [index for index, row in zip(indices, rows) if row["flat"]],
            "amplitude_p99": float(np.percentile(np.abs(centred), 99)),
            "line_noise_ratio": noise_energy / total_energy if total_energy > 0 else None,
            "line_noise_time_range_s": [start, end], "line_noise_bands_hz": [[48, 52], [58, 62]],
            "noise_metric": "fraction of PSD energy in the stated bands", "psd_method": "Welch, Hamming segments",
            "measurement_settings": {"welch": psd_settings, "rms": {"centering": "none", "definition": "sqrt(mean(raw samples squared))"},
                                     "amplitude_p99": {"centering": "channel median over selected range"},
                                     "flat_line_ratio": "equal adjacent samples within segments; boundary pairs excluded"},
            **_public({"analysis": analysis_context(source_id, start, end)})["analysis"]}


def detect_transients(source_id, start_s=None, end_s=None, channels=None, min_amplitude=70,
                      min_width_s=.02, max_width_s=.2, min_distance_s=.2, max_candidates_per_channel=50):
    data, sr, names, _, steps, manifests, start, end = _selection(source_id, start_s, end_s, channels)
    geometry = signal_metadata(source_id)
    channel_type = dict(zip(geometry["channel_names"], geometry["channel_types"]))
    rows, total = [], 0
    for name, signal in zip(names, data):
        candidates = []
        for si, segment in enumerate(geometry["segments"]):
            seg_start = max(round(start * sr), segment["start_sample"])
            seg_end = min(round(end * sr), segment["end_sample"])
            if seg_end <= seg_start:
                continue
            piece = signal[seg_start-round(start*sr):seg_end-round(start*sr)]
            centred = piece - np.median(piece)
            peaks, properties = find_peaks(np.abs(centred), height=min_amplitude,
                                          width=(min_width_s * sr, max_width_s * sr),
                                          distance=max(1, round(min_distance_s * sr)))
            total += len(peaks)
            for index in range(len(peaks)):
                s0, s1 = (seg_start + properties["left_ips"][index]) / sr, (seg_start + properties["right_ips"][index]) / sr
                candidates.append({"channel": name, "peak_s": float((seg_start + peaks[index]) / sr),
                                   "start_s": float(s0), "end_s": float(s1), "segment_index": si,
                                   "amplitude": float(centred[peaks[index]]), "width_s": float(properties["widths"][index] / sr),
                                   **time_coordinates(segment, s0, s1, sr)})
        rows.extend(sorted(candidates, key=lambda row: -abs(row["amplitude"]))[:max_candidates_per_channel])
    rows.sort(key=lambda row: (row["peak_s"], row["channel"]))
    params = {"source": source_id, "start_s": start, "end_s": end, "channels": names,
              "min_amplitude": min_amplitude, "min_width_s": min_width_s, "max_width_s": max_width_s,
              "min_distance_s": min_distance_s, "max_candidates_per_channel": max_candidates_per_channel}
    artifact_id = make_artifact_id("detect_transients", "1.1", params)
    path = artifact_payload_path(artifact_id, "json")
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    record = save_artifact({"artifact_id": artifact_id, "kind": "table", "uri": str(path), "media_type": "application/json",
                            "source_manifest_ids": manifests, "parent_artifact_ids": [source_id] if source_id.startswith("art_") else [],
                            "time_range_s": [start, end],
                            "signal": dict(geometry, channel_names=names, channel_types=[channel_type[name] for name in names]),
                            "analysis": {**analysis_context(source_id, start, end),
                                         "settings": {"method": "absolute median-centered peaks within each continuous segment",
                                                      "width": "half-height width; no boundary-crossing candidates"}},
                            "summary": {"rows": len(rows), "total_detected": total, "amplitude_unit": amplitude_unit(steps),
                                        "channels": names, "result_type": "transient_candidates"},
                            "provenance": {"created_by": "detect_transients@1.1", "params": params, "steps": steps}})
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"], "rows": rows[:20]}
