"""inspect_record: read a file header (and the first seconds of data) and build a SignalManifest.

Deterministic. Never puts raw data into the result. Unit and channel layout are reported as
Inferred values with a source, so the prompt can tell facts from guesses.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from eegagent.worker.channels import eeg_indices
from eegagent.worker.files import resolve_inside_roots
from eegagent.worker.readers import SUPPORTED, content_sha, file_ext, inferred, infer_unit, read_record, record_files, resolve_main_file, source_signature  # noqa: F401
from eegagent.worker.registry import check_model_compat, known_ten_twenty
from eegagent.worker.store import save_manifest

SAMPLE_SECONDS = 10
_inferred = inferred  # old name, kept for callers


def infer_layout(channel_names: list[str], channel_types: list[str] | None = None) -> dict:
    """10-20 layout is 'known' when at least half of the EEG channels carry 10-20 / 10-10 names."""
    names = [n for n, t in zip(channel_names, channel_types) if t == "eeg"] if channel_types else list(channel_names)
    if not names:
        return inferred(None, "unknown")
    known = known_ten_twenty(names)
    ratio = len(known) / len(names)
    if ratio >= 0.5:
        return inferred({c: c.strip() for c in known}, "file_header", round(ratio, 2))
    return inferred(None, "unknown", round(ratio, 2))


def _quality(sample: np.ndarray, sr: float | None, names: list[str], types: list[str], segments: list[dict] | None = None) -> dict:
    """Cheap quality summary on the sampled block (EEG channels only): flat channels and 50/60 Hz share."""
    idx = eeg_indices(types)
    if sample.ndim != 2 or sample.shape[1] < 16 or not idx:
        return {}
    x = sample[idx]
    std = np.nanstd(x, axis=1)
    flat = [idx[int(i)] for i in np.where(std < 1e-9)[0]]
    out: dict = {"scope": "initial_sample", "flat_channel_indices": flat, "flat_channels": [names[i] for i in flat],
                 "sample_count": x.shape[1]}
    if sr:
        out["sample_time_range_s"] = [0.0, x.shape[1] / sr]
        from eegagent.worker.signals import analysis_from_geometry

        bounds = segments or [{"start_sample": 0, "end_sample": sample.shape[1], "original_start_s": 0.0}]
        geometry = {"kind": "epochs" if any("epoch_index" in segment for segment in bounds) else "continuous",
                    "sample_rate_hz": sr, "segments": bounds,
                    "time_offset_s": bounds[0]["original_start_s"] - bounds[0]["start_sample"] / sr}
        context = analysis_from_geometry("", geometry, 0, x.shape[1] / sr)
        out.update(time_axis=context["time_axis"], sample_original_time_ranges_s=context["original_time_ranges_s"])
    if sr and x.shape[1] >= round(sr):
        seg = x[:, : min(x.shape[1], round(4 * sr))]
        from eegagent.worker.processing import welch_by_segments

        seg = np.nan_to_num(seg)
        freqs, spec, settings = welch_by_segments(seg, sr, bounds, end_sample=seg.shape[1])
        out["line_noise_settings"] = settings
        total = spec.sum()
        if total > 0:
            band = ((freqs > 48) & (freqs < 52)) | ((freqs > 58) & (freqs < 62))
            out["line_noise_ratio"] = round(float(spec[:, band].sum() / total), 4)
            out["line_noise_bands_hz"] = [[48, 52], [58, 62]]
            out["line_noise_time_range_s"] = [0.0, seg.shape[1] / sr]
            out["line_noise_original_time_ranges_s"] = analysis_from_geometry("", geometry, 0, seg.shape[1] / sr)["original_time_ranges_s"]
    p99 = float(np.nanpercentile(np.abs(x - np.nanmedian(x, axis=1, keepdims=True)), 99)) if x.size else 0.0
    out["amplitude_p99"] = round(p99, 3)
    out["amplitude_p99_centering"] = "channel median over initial sampled range"
    return out


def inspect_record(path: str, sample_rate_hz: float | None = None, channel_names: list[str] | None = None,
                   trial_index: int | None = None) -> dict:
    file_path = resolve_main_file(resolve_inside_roots(path))
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))
    ext = file_ext(file_path)
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported format {ext}; supported: {sorted(SUPPORTED)}")

    rec = read_record(file_path, sample_rate_hz=sample_rate_hz, channel_names=channel_names, trial_index=trial_index,
                      max_seconds=SAMPLE_SECONDS)
    names, types, sr = rec.channel_names, rec.channel_types, rec.sample_rate_hz
    warnings = list(rec.warnings)
    layout = infer_layout(names, types)
    names_source = rec.metadata_evidence.get("channel_names", {}).get("source")
    if names_source == "user_confirmed" and layout["source"] != "unknown":
        layout["source"] = names_source
    if names_source == "format_assumption" and layout["source"] != "unknown":
        layout["source"] = "format_assumption"
    if layout["source"] == "unknown":
        warnings.append("通道名不是 10-20 名称；需要设备型号或用户提供映射，否则需要通道名的模型不可用")
    non_eeg = [f"{n}({t})" for n, t in zip(names, types) if t != "eeg"]
    if non_eeg:
        warnings.append(f"非 EEG 通道 {len(non_eeg)} 个：{', '.join(non_eeg[:10])}{'…' if len(non_eeg) > 10 else ''}；分析前用 preprocess 的 pick_eeg 去掉")
    quality = _quality(np.asarray(rec.data, dtype=float), sr, names, types, rec.segments)
    unit = rec.unit
    if unit.get("value") == "uV" and quality.get("amplitude_p99", 0) > 5e3:
        if ext == ".bdf":
            warnings.append(f"EEG 幅值 p99 ≈ {quality['amplitude_p99']:.0f} µV：先核对设备与既有参考。BioSemi 原始 BDF "
                            "可保留相对 CMS 的共模干扰与电极偏移，先按采集协议重参考并高通，再判断剩余幅值与质量")
        else:
            warnings.append(f"EEG 幅值 p99 ≈ {quality['amplitude_p99']:.0f} µV，远超头皮 EEG 常见范围；文件头的物理单位可能不对，或含大量伪迹")

    evidence = dict(rec.metadata_evidence)
    evidence.setdefault("origin", inferred("unverified", "unknown"))
    evidence.setdefault("sample_rate_hz", inferred(sr, "file_header" if sr else "unknown"))
    evidence.setdefault("channel_names", inferred(names, "file_header" if layout["source"] != "unknown" else "unknown"))
    manifest = {
        "manifest_id": f"sig_{uuid.uuid4().hex[:8]}",
        "modality": "eeg",
        "attachment_sha256": content_sha(file_path, rec.source_files),
        "source_signature": source_signature(file_path, rec.source_files),
        "source_path": str(file_path),
        "original_name": file_path.name,
        "source_files": [str(file) for file in record_files(file_path, rec.source_files)],
        "source_format": rec.fmt,
        "dataset": rec.dataset,
        "format_profile": rec.format_profile,
        "metadata_evidence": evidence,
        "subject_id": None,
        "trial_id": rec.trial_id,
        "trial_index": rec.trial_index,
        "trial_count": rec.trial_count,
        "trial_selection": "explicit" if trial_index is not None else "default_first" if rec.trial_count > 1 else "single",
        "sample_rate_hz": sr,
        "n_channels": len(names),
        "n_samples": int(rec.n_samples),
        "duration_s": round(rec.n_samples / sr, 3) if sr else None,
        "channel_names": names,
        "channel_types": types,
        "signal_kind": rec.signal_kind,
        "segments": rec.segments,
        "channel_layout": layout,
        "unit": unit,
        "reference": evidence["reference"]["value"] if evidence.get("reference", {}).get("source") == "file_header" else "unknown",
        "events": rec.events,
        "labels": rec.labels,
        "quality": quality,
        "sync": None,
        "compat": [],
        "warnings": warnings,
    }
    manifest["compat"] = check_model_compat(manifest)
    save_manifest(manifest)
    return manifest


def is_supported(path: str | Path) -> bool:
    return file_ext(Path(path)) in SUPPORTED
