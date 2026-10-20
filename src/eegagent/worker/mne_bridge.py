"""Bridge between the project's signals (µV ndarray [C, N] + names + types) and MNE-Python Raw objects.

Used by clean_artifacts and by eeg.to_mne / eeg.save_raw inside run_python. MNE works in SI units (V); the rest of
the project works in µV. Electrode positions come from the standard 10-05 template matched on cleaned names, so
"EEG Fp1-REF" still gets Fp1's position while the channel keeps its original name.
"""

from __future__ import annotations

import numpy as np

from eegagent.worker.channels import MNE_TYPES, OLD_TO_NEW, VOLT_TYPES, guess_channel_types, normalize_channel

NEW_TO_OLD = {v: k for k, v in OLD_TO_NEW.items()}


def unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        n = str(n)
        if n in seen:
            seen[n] += 1
            out.append(f"{n}-{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def standard_positions(names: list[str], kind: str = "standard_1005") -> tuple[dict[str, np.ndarray], dict]:
    """{original name: xyz} for names found in an MNE standard montage, plus the montage fiducials."""
    import mne

    pos = mne.channels.make_standard_montage(kind).get_positions()
    lut = {k.lower(): np.asarray(v) for k, v in pos["ch_pos"].items()}
    out: dict[str, np.ndarray] = {}
    used: set[str] = set()
    for n in names:
        key = normalize_channel(n)
        for cand in (key, OLD_TO_NEW.get(key), NEW_TO_OLD.get(key)):
            if cand and cand in lut and cand not in used:
                out[n] = lut[cand]
                used.add(cand)
                used.update(x for x in (OLD_TO_NEW.get(cand), NEW_TO_OLD.get(cand)) if x)
                break
    return out, {"coord_frame": pos["coord_frame"], "nasion": pos["nasion"], "lpa": pos["lpa"], "rpa": pos["rpa"]}


def to_raw(data_uv: np.ndarray, sr: float, names: list[str], ch_types: list[str] | None = None, montage: str | None = "auto",
           events: list[dict] | None = None, segments: list[dict] | None = None):
    """Build an mne.io.RawArray (V) from µV data. Returns (raw, positioned_channel_names)."""
    import mne

    names = unique_names(list(names))
    types = list(ch_types) if ch_types and len(ch_types) == len(names) else guess_channel_types(names)
    types = [t if t in MNE_TYPES else "misc" for t in types]
    scale = np.array([1e-6 if t in VOLT_TYPES else 1.0 for t in types], dtype=float)[:, None]
    info = mne.create_info(names, float(sr), types)
    values = np.asarray(data_uv, dtype=float) * scale
    if segments and len(segments) > 1:
        raw = mne.concatenate_raws([mne.io.RawArray(values[:, s["start_sample"]:s["end_sample"]], info.copy(), verbose="ERROR")
                                   for s in segments], verbose="ERROR")
    else:
        raw = mne.io.RawArray(values, info, verbose="ERROR")
    positioned: list[str] = []
    if montage:
        kind = "standard_1005" if montage == "auto" else montage
        eeg_names = [n for n, t in zip(names, types) if t == "eeg"]
        pos, fid = standard_positions(eeg_names, kind)
        if pos:
            mont = mne.channels.make_dig_montage(ch_pos=pos, **fid)
            raw.set_montage(mont, on_missing="ignore", verbose="ERROR")
            positioned = list(pos)
    dur = raw.n_times / raw.info["sfreq"]
    ev = [e for e in (events or []) if 0 <= float(e.get("onset_s", -1)) < dur]
    boundaries = [{"onset_s": segment["start_sample"] / sr, "duration_s": 0.0, "label": label}
                  for segment in (segments or [])[1:] for label in ("EDGE boundary", "BAD boundary")]
    annotations = ev + boundaries
    if annotations:
        raw.set_annotations(mne.Annotations([float(e["onset_s"]) for e in annotations],
                                            [float(e.get("duration_s") or 0.0) for e in annotations],
                                            [str(e.get("label", "event")) for e in annotations]))
    return raw, positioned


def from_raw(raw) -> tuple[np.ndarray, float, list[str], list[str]]:
    """Inverse of to_raw: (data µV, sr, names, types)."""
    types = list(raw.get_channel_types())
    scale = np.array([1e6 if t in VOLT_TYPES else 1.0 for t in types], dtype=float)[:, None]
    return raw.get_data() * scale, float(raw.info["sfreq"]), list(raw.ch_names), types


def source_raw(source_id: str, montage: str | None = "auto", with_events: bool = True):
    """Load a sig_/art_ source as an MNE Raw with types, standard positions and recording events."""
    from eegagent.worker.signals import amplitude_unit, events_for_source, resolve_channel_types, resolve_signal, signal_metadata

    data, sr, names, _prov, _src = resolve_signal(source_id)
    if amplitude_unit(_prov) != "µV":
        raise ValueError("该信号没有可用于 MNE Raw 的物理单位；请使用归一化前信号并确认原始单位")
    types = resolve_channel_types(source_id, names)
    events = events_for_source(source_id, data.shape[1] / sr) if with_events else None
    return to_raw(data, sr, names, types, montage=montage, events=events, segments=signal_metadata(source_id)["segments"])


def source_epochs(source_id: str, montage: str | None = "auto"):
    """Rebuild stored epochs directly, preserving their event-relative tmin instead of re-epoching a packed Raw."""
    import mne
    import pandas as pd
    from eegagent.worker.signals import signal_metadata

    raw, _ = source_raw(source_id, montage=montage, with_events=False)
    sig = signal_metadata(source_id)
    segments = sig["segments"]
    if sig["kind"] != "epochs":
        raise ValueError("to_epochs 需要原始或处理后的 epoched 信号；连续 EEG 使用 to_mne 再按事件构建 MNE Epochs")
    lengths = {s["end_sample"] - s["start_sample"] for s in segments}
    tmins = {s["epoch_tmin_s"] for s in segments}
    if len(lengths) != 1 or len(tmins) != 1:
        raise ValueError("当前 epoch 的长度或事件相对起点不同，不能作为同一个 EpochsArray；请按相同范围选取各 epoch")
    values = raw.get_data()
    epoch_data = np.stack([values[:, s["start_sample"]:s["end_sample"]] for s in segments])
    events = np.asarray([[s.get("original_event_sample", i), 0, s["event_code"]] for i, s in enumerate(segments)])
    event_id = {s["event_label"]: s["event_code"] for s in segments}
    metadata = pd.DataFrame({"eegagent_segment_index": range(len(segments)),
                             "original_epoch_index": [s["epoch_index"] for s in segments]})
    epochs = mne.EpochsArray(epoch_data, raw.info, events=events, event_id=event_id,
                             tmin=segments[0]["epoch_tmin_s"], baseline=None, metadata=metadata, verbose="ERROR")
    epochs.info["description"] = f"eegagent_source={source_id}"
    return epochs


def geometry_from_raw(raw, source_id: str) -> tuple[str, list[dict]]:
    """Keep each source segment's original coordinates through MNE crop/resample."""
    from eegagent.worker.signals import signal_metadata

    sig = signal_metadata(source_id)
    source_sr, sr = sig["sample_rate_hz"], float(raw.info["sfreq"])
    start, end = float(raw.first_time), float(raw.first_time) + raw.n_times / sr
    segments, cursor = [], 0
    intersections = [(segment, max(start, segment["start_sample"] / source_sr), min(end, segment["end_sample"] / source_sr))
                     for segment in sig["segments"] if min(end, segment["end_sample"]/source_sr) > max(start, segment["start_sample"]/source_sr)]
    for index, (segment, a, b) in enumerate(intersections):
        shift = a - segment["start_sample"] / source_sr
        length = int(raw._raw_lengths[index]) if len(raw._raw_lengths) == len(intersections) else round((b-a)*sr)
        updated = dict(segment, start_sample=cursor, end_sample=min(raw.n_times, cursor + length),
                        original_start_s=segment["original_start_s"] + shift,
                        original_end_s=min(segment.get("original_end_s", segment["original_start_s"] +
                                             (segment["end_sample"]-segment["start_sample"])/source_sr),
                                             segment["original_start_s"] + b-segment["start_sample"]/source_sr))
        if "epoch_tmin_s" in updated:
            updated["epoch_tmin_s"] += shift
        segments.append(updated)
        cursor += length
    return sig["kind"], segments


def raw_events(raw) -> list[dict]:
    """MNE annotation onsets include first_time after crop; expose onsets relative to saved sample 0."""
    return [{"onset_s": float(onset - raw.first_time), "duration_s": float(duration), "label": str(label)}
            for onset, duration, label in zip(raw.annotations.onset, raw.annotations.duration, raw.annotations.description)
            if label not in ("EDGE boundary", "BAD boundary")]
