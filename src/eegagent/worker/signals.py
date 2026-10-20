"""Load full signals ([channels, samples]) from a manifest or a normalized_signal artifact.

Physical voltage records use µV; declared dataset_native records keep their stored numerical scale.

Tools take a ``source_id`` that is either a manifest id (``sig_…``) or an artifact id (``art_…``).
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np
import re

from eegagent.worker.channels import VOLT_TYPES, guess_channel_types
from eegagent.worker.files import resolve_inside_roots
from eegagent.worker.store import load_artifact, load_manifest, resolve_inside_storage

_CACHE: "OrderedDict[str, tuple[np.ndarray, float, list[str]]]" = OrderedDict()
_CACHE_MAX = 8


def amplitude_unit(steps: list[str]) -> str:
    """Normalization removes physical amplitude units throughout the derived artifact chain."""
    if "unit_arbitrary" in steps or any(step.startswith("normalize_") for step in steps):
        return "a.u."
    if "unit_dataset_native" in steps:
        return "dataset_native"
    return "未知单位" if "unit_unknown" in steps else "µV"


def amplitude_unit_source(steps: list[str]) -> str:
    """Unit evidence is retained through processing; numerical conversion is not calibration."""
    if "unit_arbitrary" in steps or any(step.startswith("normalize_") for step in steps):
        return "normalization"
    if "unit_dataset_native" in steps:
        return "dataset_native"
    if "unit_unknown" in steps or "unit_source_unknown" in steps:
        return "unknown"
    if "unit_format_assumption" in steps:
        return "format_assumption"
    return "inferred" if "unit_inferred" in steps else "confirmed"


def _remember(key: str, value):
    _CACHE[key] = value
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return value


def _trial_index(m: dict) -> int | None:
    if m.get("trial_index") is not None:
        return int(m["trial_index"])
    tid = m.get("trial_id") or ""
    if tid.startswith("trial_"):
        return int(tid.split("_")[-1]) - 1
    return None


def load_signal(manifest_id: str) -> tuple[np.ndarray, float, list[str]]:
    """Return (data [C, N], sample_rate_hz, names), preserving explicitly declared native dataset scale."""
    if manifest_id in _CACHE:
        return _CACHE[manifest_id]
    from eegagent.worker.readers import read_record
    from eegagent.worker.readers import MNE_EXTS, file_ext

    m = load_manifest(manifest_id)
    if m.get("modality") != "eeg":
        raise ValueError(f"{manifest_id} is not an EEG manifest")
    sr = m.get("sample_rate_hz")
    if not sr:
        raise ValueError("采样率未知：请先让用户提供 sample_rate_hz 并重新 inspect_record")
    rec = read_record(resolve_inside_roots(m["source_path"]), sample_rate_hz=sr, trial_index=_trial_index(m))
    data = np.asarray(rec.data, dtype=float)
    unit = (m.get("unit") or {}).get("value")
    if file_ext(resolve_inside_roots(m["source_path"])) not in MNE_EXTS:
        # Native arrays remain in stored units, including explicit unit headers; MNE readers already return µV.
        factor = {"V": 1e6, "mV": 1e3}.get(unit, 1.0)
        types = m.get("channel_types", rec.channel_types)
        data = data * np.array([factor if kind in VOLT_TYPES else 1.0 for kind in types])[:, None]
    names = list(m["channel_names"])
    if len(names) != data.shape[0]:
        names = list(rec.channel_names) if len(rec.channel_names) == data.shape[0] else [f"ch{i + 1}" for i in range(data.shape[0])]
    return _remember(manifest_id, (data, float(sr), names))


def load_signal_from_artifact(artifact_id: str) -> tuple[np.ndarray, float, list[str], dict]:
    """Return (data, sr, names, record) for a normalized_signal artifact."""
    rec = load_artifact(artifact_id)
    if rec.get("kind") != "normalized_signal":
        raise ValueError(f"{artifact_id} is kind={rec.get('kind')}, expected normalized_signal")
    key = "art:" + artifact_id
    if key in _CACHE:
        data, sr, names = _CACHE[key]
        return data, sr, names, rec
    sig = rec.get("signal", {})
    if rec["media_type"] == "application/x-npy":
        data = np.load(resolve_inside_storage(rec["uri"]))
        sr, names = float(sig["sample_rate_hz"]), list(sig["channel_names"])
    else:
        from eegagent.worker.readers import read_record

        meta = sig or rec["summary"]
        read = read_record(resolve_inside_storage(rec["uri"]), sample_rate_hz=meta["sample_rate_hz"],
                           channel_names=meta["channel_names"])
        valid_samples = (sig.get("segments") or [{"end_sample": rec["summary"].get("n_samples", read.data.shape[1])}])[-1]["end_sample"]
        data, sr, names = read.data[:, :valid_samples], float(read.sample_rate_hz), read.channel_names
    _remember(key, (data, sr, names))
    return data, sr, names, rec


def resolve_signal(source_id: str) -> tuple[np.ndarray, float, list[str], list[str], list[str]]:
    """Return (data, sr, names, provenance_steps, source_manifest_ids) for a manifest or artifact id."""
    if source_id.startswith("art_"):
        data, sr, names, rec = load_signal_from_artifact(source_id)
        return data, sr, names, list(rec.get("provenance", {}).get("steps", [])), list(rec.get("source_manifest_ids", []))
    data, sr, names = load_signal(source_id)
    manifest = load_manifest(source_id)
    unit = manifest.get("unit") or {}
    steps = ["unit_arbitrary" if unit.get("value") == "a.u." else
             "unit_dataset_native" if unit.get("value") == "dataset_native" else
             "unit_uV" if unit.get("value") in ("V", "mV", "uV", "µV") else "unit_unknown"]
    if unit.get("source") in ("dataset_adapter", "format_assumption"):
        steps.append("unit_format_assumption")
    elif unit.get("source") == "inferred":
        steps.append("unit_inferred")
    elif unit.get("source") == "unknown":
        steps.append("unit_source_unknown")
    rate_source = manifest.get("metadata_evidence", {}).get("sample_rate_hz", {}).get("source")
    legacy_format = any((manifest.get(key) or {}).get("source") == "dataset_adapter"
                        for key in ("unit", "channel_layout"))
    if rate_source in ("dataset_adapter", "format_assumption") or (not rate_source and legacy_format):
        steps.append("sample_rate_format_assumption")
    return data, sr, names, steps, [source_id]


def source_processing(record: dict) -> dict:
    """Source-declared processing stays separate from steps executed by this application."""
    if record.get("source_manifest_ids"):
        if len(record["source_manifest_ids"]) != 1:
            return {}
        record = load_manifest(record["source_manifest_ids"][0])
    declared = record.get("metadata_evidence", {}).get("preprocessing", {})
    value = declared.get("value")
    if isinstance(value, dict):
        settings = dict(value)
        if "bandpass_hz" in settings:
            settings["highpass_hz"], settings["lowpass_hz"] = settings["bandpass_hz"]
    elif value == "deap_official_preprocessed":
        settings = {"highpass_hz": 4.0, "lowpass_hz": 45.0, "reference": "average"}
    elif isinstance(value, str) and (match := re.fullmatch(r"bandpass_([\d.]+)_([\d.]+)", value)):
        settings = {"highpass_hz": float(match[1]), "lowpass_hz": float(match[2])}
    else:
        settings = {}
    recorded = record.get("metadata_evidence", {}).get("processing_steps", {})
    if recorded.get("value"):
        settings["processing_steps"] = list(recorded["value"])
    source = declared.get("source") if declared.get("value") is not None else recorded.get("source")
    return {"value": settings, "source": source} if settings else {}


def filter_bounds(steps: list[str], declared: dict) -> tuple[float, float | None]:
    """Effective existing limits, using confirmed source facts and actual applied filters."""
    settings = declared.get("value", {}) if declared.get("source") in ("file_header", "user_confirmed") else {}
    highpass = float(settings.get("highpass_hz", 0))
    lowpass = float(settings["lowpass_hz"]) if settings.get("lowpass_hz") is not None else None
    for step in list(settings.get("processing_steps", [])) + steps:
        if match := re.match(r"bandpass_([\d.]+)_([\d.]+)", step):
            highpass = max(highpass, float(match[1]))
            lowpass = min(lowpass, float(match[2])) if lowpass is not None else float(match[2])
        elif match := re.match(r"highpass_([\d.]+)", step):
            highpass = max(highpass, float(match[1]))
    return highpass, lowpass


def resolve_channel_types(source_id: str, names: list[str]) -> list[str]:
    """Channel types stored with the manifest / signal artifact; name-based guess otherwise."""
    try:
        if source_id.startswith("art_"):
            types = (load_artifact(source_id).get("signal") or {}).get("channel_types")
        else:
            types = load_manifest(source_id).get("channel_types")
    except FileNotFoundError:
        types = None
    if types and len(types) == len(names):
        return list(types)
    return guess_channel_types(names)


def signal_time_offset(source_id: str) -> float:
    """Seconds between the original recording start and sample 0 of this signal (baseline drop, crop)."""
    if not source_id.startswith("art_"):
        return 0.0
    try:
        return float((load_artifact(source_id).get("signal") or {}).get("time_offset_s") or 0.0)
    except FileNotFoundError:
        return 0.0


def signal_metadata(source_id: str) -> dict:
    """The sample coordinate contract shared by processing, MNE and model observations."""
    data, sr, names, steps, _ = resolve_signal(source_id)
    rec = load_artifact(source_id) if source_id.startswith("art_") else load_manifest(source_id)
    sig = dict(rec.get("signal") or {})
    kind = sig.get("kind", rec.get("signal_kind", "continuous"))
    segments = sig.get("segments") or rec.get("segments")
    if not segments:
        offset = float(sig.get("time_offset_s") or 0.0)
        segments = [{"start_sample": 0, "end_sample": data.shape[1], "original_start_s": offset,
                     "original_end_s": offset + data.shape[1] / sr}]
    sig.update(kind=kind, sample_rate_hz=sr, channel_names=names,
               channel_types=resolve_channel_types(source_id, names), amplitude_unit=amplitude_unit(steps),
               amplitude_unit_source=amplitude_unit_source(steps),
               segments=[dict(segment) for segment in segments])
    sig["source_processing"] = source_processing(rec)
    if kind == "continuous" and len(segments) == 1:
        sig["time_offset_s"] = float(segments[0]["original_start_s"])
    else:
        sig.pop("time_offset_s", None)
    return sig


def time_coordinates(segment: dict, start_s: float, end_s: float, sr: float) -> dict:
    """Map a within-segment source interval to the unmodified file and event-relative axes."""
    local_start = start_s - segment["start_sample"] / sr
    local_end = end_s - segment["start_sample"] / sr
    original_end = segment.get("original_end_s", segment["original_start_s"] +
                               (segment["end_sample"] - segment["start_sample"]) / sr)
    out = {"original_time_range_s": [segment["original_start_s"] + local_start,
                                      min(original_end, segment["original_start_s"] + local_end)]}
    if "epoch_index" in segment:
        out.update(epoch_index=segment["epoch_index"],
                   epoch_relative_time_range_s=[segment["epoch_tmin_s"] + local_start,
                                                segment["epoch_tmin_s"] + local_end])
    return out


def analysis_context(source_id: str, start_s: float | None = None, end_s: float | None = None) -> dict:
    """Small scientific observation: source identity, channel/unit contract and exact time support."""
    inherited = {}
    if source_id.startswith("art_"):
        rec = load_artifact(source_id)
        if rec["kind"] != "normalized_signal":
            context = dict(rec.get("analysis") or {})
            inherited = context
            signal_source = context.get("source_id") or rec.get("summary", {}).get("source")
            if signal_source is None:
                return context
            source_id = signal_source
            if start_s is None and rec.get("time_range_s"):
                start_s, end_s = rec["time_range_s"]
    sig = signal_metadata(source_id)
    return {**inherited, **analysis_from_geometry(source_id, sig, start_s, end_s)}


def analysis_from_geometry(source_id: str, sig: dict, start_s: float | None = None, end_s: float | None = None) -> dict:
    sr = sig["sample_rate_hz"]
    start = 0.0 if start_s is None else float(start_s)
    end = sig["segments"][-1]["end_sample"] / sr if end_s is None else float(end_s)
    ranges = []
    for segment in sig["segments"]:
        a = max(start, segment["start_sample"] / sr)
        b = min(end, segment["end_sample"] / sr)
        if b > a:
            ranges.append({"source_time_range_s": [a, b], **time_coordinates(segment, a, b, sr)})
    context = {"source_id": source_id,
               "time_axis": "seconds from current signal sample 0; packed epochs are discontinuous" if sig["kind"] == "epochs"
                            else "seconds from current signal sample 0",
               "original_time_axis": "packed original epoch samples; use epoch_relative_time_range_s for event timing"
                                     if sig["kind"] == "epochs" else "seconds from original file sample 0",
               "source_time_range_s": [start, end], "original_time_ranges_s": ranges}
    if sig["kind"] == "continuous" and len(ranges) == 1:
        context["original_recording_offset_s"] = sig["time_offset_s"]
        context["original_recording_time_range_s"] = ranges[0]["original_time_range_s"]
    return context


def events_for_source(source_id: str, duration_s: float | None = None) -> list[dict]:
    """Recording events (annotations / triggers) on this signal's time axis."""
    try:
        if source_id.startswith("art_"):
            mids = load_artifact(source_id).get("source_manifest_ids") or []
            if len(mids) != 1:
                return []
            events = load_manifest(mids[0]).get("events") or []
        else:
            events = load_manifest(source_id).get("events") or []
    except FileNotFoundError:
        return []
    sig = signal_metadata(source_id)
    if "events" in sig:
        return [dict(e) for e in sig["events"] if 0 <= e["onset_s"] and
                (duration_s is None or e["onset_s"] < duration_s)]
    sr = sig["sample_rate_hz"]
    out = []
    for e in events:
        original_onset = float(e["onset_s"])
        for segment in sig["segments"]:
            duration = (segment["end_sample"] - segment["start_sample"]) / sr
            local = original_onset - segment["original_start_s"]
            if 0 <= local < duration:
                t = segment["start_sample"] / sr + local
                if duration_s is None or t < duration_s:
                    out.append(dict(e, onset_s=t))
                break
    return out
