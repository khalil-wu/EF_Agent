"""EEG file readers behind one function. inspect_record reads the first seconds; signals.load_signal reads everything.

Formats
  via MNE-Python  EDF/EDF+, BDF, GDF, BrainVision (.vhdr), EEGLAB (.set, continuous or epoched), FIF, Neuroscan CNT,
                  EGI MFF, Nihon Kohden (.eeg), Persyst (.lay), Curry (.cdt), Nexstim (.nxe), NeuroElectrics (.nedf)
  native          DEAP-shaped .dat, CSV/TXT/TSV (OpenBCI GUI and generic tables), NPY/NPZ, MATLAB .mat (heuristic)

Voltage channels read through MNE come back in µV (MNE uses V). Native formats come back as stored; the manifest's
unit tells signals.load_signal how to scale them (it may be user-confirmed later).
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from eegagent.worker.channels import VOLT_TYPES, eeg_indices, merge_channel_types

MNE_EXTS = {".edf", ".bdf", ".gdf", ".vhdr", ".ahdr", ".set", ".fif", ".fif.gz", ".cnt", ".mff", ".nxe", ".lay", ".cdt",
            ".nedf", ".eeg"}
NATIVE_EXTS = {".dat", ".csv", ".txt", ".tsv", ".npy", ".npz", ".mat"}
SIDECARS = {".fdt": ".set", ".vmrk": ".vhdr"}  # data / marker file → header file
SUPPORTED = MNE_EXTS | NATIVE_EXTS | set(SIDECARS)
MAX_EVENTS = 2000
SR_KEYS = ("srate", "fs", "sfreq", "sampling_rate", "sample_rate", "samplerate", "sampling_rate_hz", "sample_rate_hz", "fsample", "sr")
NAME_KEYS = ("ch_names", "channel_names", "channels", "labels", "chanlocs", "electrodes", "clab", "chan_names")


def inferred(value, source: str, confidence: float | None = None) -> dict:
    out = {"value": value, "source": source}
    if confidence is not None:
        out["confidence"] = confidence
    return out


def infer_unit(sample: np.ndarray) -> dict:
    """Amplitude heuristic. µV-scale EEG lives roughly in ±1e2; V-scale in ±1e-4. mV is ambiguous."""
    if sample.size == 0:
        return inferred("unknown", "unknown")
    p99 = float(np.nanpercentile(np.abs(sample), 99))
    if p99 == 0 or not np.isfinite(p99):
        return inferred("unknown", "unknown")
    if 1e-6 < p99 < 5e-3:
        return inferred("V", "inferred")
    if 1.0 < p99 < 5e3:
        return inferred("uV", "inferred")
    if 5e-3 <= p99 <= 1.0:
        return inferred("unknown", "inferred")  # could be mV or badly scaled µV; no calibrated probability
    return inferred("unknown", "unknown")


def _unit_from_metadata(metadata: dict, sample: np.ndarray) -> dict:
    declared = np.asarray(metadata.get("unit", ""))
    if declared.size == 1 and str(declared.item()) in ("unknown", "未知单位"):
        return inferred("unknown", "file_header")
    if declared.size == 1 and str(declared.item()) in ("V", "mV", "uV", "µV", "dataset_native", "a.u."):
        source = str(np.asarray(metadata.get("unit_source", "confirmed")).item())
        return inferred(str(declared.item()).replace("µV", "uV"),
                        source if source in ("inferred", "format_assumption", "unknown") else "file_header")
    return infer_unit(sample)


@dataclass
class Record:
    data: np.ndarray                 # [C, N]; N may be truncated by max_seconds
    sample_rate_hz: float | None
    channel_names: list[str]
    channel_types: list[str]
    n_samples: int                   # full length of the recording
    fmt: str
    unit: dict
    dataset: str = "unknown"
    events: list[dict] = field(default_factory=list)
    labels: dict | None = None
    trial_id: str | None = None
    trial_index: int | None = None
    trial_count: int = 1
    source_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_types: list[str] | None = None  # channel types declared by the file itself (MNE formats)
    format_profile: str | None = None
    metadata_evidence: dict = field(default_factory=dict)
    signal_kind: str = "continuous"
    segments: list[dict] = field(default_factory=list)


def file_ext(path: Path) -> str:
    name = path.name.lower()
    return ".fif.gz" if name.endswith(".fif.gz") else path.suffix.lower()


def resolve_main_file(path: Path) -> Path:
    """Sidecar → header file: x.fdt → x.set, x.vmrk / x.eeg → x.vhdr (when that header exists)."""
    ext = file_ext(path)
    if ext in SIDECARS:
        main = path.with_suffix(SIDECARS[ext])
        if main.exists():
            return main
        if ext in (".vmrk", ".eeg"):
            for header in path.parent.glob("*.vhdr"):
                referenced = re.findall(r"(?im)^(?:DataFile|MarkerFile)\s*=\s*(.+)$", header.read_text(encoding="utf-8-sig", errors="replace"))
                if any((header.parent / name.strip()).resolve() == path.resolve() for name in referenced):
                    return header
        raise FileNotFoundError(f"{path.name} 是附属数据文件，同目录没有 {main.name}；请提供 {SIDECARS[ext]} 头文件")
    if ext == ".eeg" and path.with_suffix(".vhdr").exists():
        return path.with_suffix(".vhdr")
    if ext == ".eeg":
        for header in path.parent.glob("*.vhdr"):
            referenced = re.findall(r"(?im)^DataFile\s*=\s*(.+)$", header.read_text(encoding="utf-8-sig", errors="replace"))
            if any((header.parent / name.strip()).resolve() == path.resolve() for name in referenced):
                return header
    return path


def record_files(path: Path, source_files=()) -> list[Path]:
    files = [path, *(Path(name) for name in source_files)]
    ext = file_ext(path)
    if ext in (".vhdr", ".ahdr"):
        header = path.read_text(encoding="utf-8-sig", errors="replace")
        files += [path.parent / name.strip() for name in re.findall(r"(?im)^(?:DataFile|MarkerFile)\s*=\s*(.+)$", header)]
    elif ext == ".set" and not source_files:
        from pymatreader import read_mat
        metadata = read_mat(str(path))
        eeg = metadata.get("EEG", metadata)
        if isinstance(eeg["data"], str):
            files.append(path.parent / eeg["data"])
    return list(dict.fromkeys(file for file in files if file.is_file()))


def content_sha(path: Path, source_files=()) -> str:
    """Local content identity, including sidecars; never an authentication of dataset origin."""
    h = hashlib.sha256()
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file():
                h.update(f"{p.relative_to(path)}|{p.stat().st_size}\n".encode("utf-8"))
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
        return h.hexdigest()
    for f in record_files(path, source_files):
        if f.exists() and f.is_file():
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()


def to_microvolts(data: np.ndarray, types: list[str]) -> np.ndarray:
    scale = np.array([1e6 if t in VOLT_TYPES else 1.0 for t in types], dtype=float)[:, None]
    return np.asarray(data, dtype=float) * scale


def read_record(path: str | Path, *, sample_rate_hz: float | None = None, channel_names: list[str] | None = None,
                trial_index: int | None = None, max_seconds: float | None = None) -> Record:
    path = resolve_main_file(Path(path))
    ext = file_ext(path)
    if ext == ".dat":
        rec = _read_deap(path, trial_index, max_seconds, sample_rate_hz)
    elif ext in (".csv", ".txt", ".tsv"):
        rec = _read_table(path, sample_rate_hz, max_seconds)
    elif ext in (".npy", ".npz"):
        rec = _read_array(path, sample_rate_hz, max_seconds)
    elif ext == ".mat":
        rec = _read_mat(path, sample_rate_hz, trial_index, max_seconds)
    elif ext in MNE_EXTS:
        rec = _read_mne(path, max_seconds)
    else:
        raise ValueError(f"不支持的格式 {ext}；支持：{', '.join(sorted(SUPPORTED))}")
    if rec.sample_rate_hz is None and sample_rate_hz:
        rec.sample_rate_hz = float(sample_rate_hz)
    if sample_rate_hz is not None and ext not in MNE_EXTS:
        rec.metadata_evidence["sample_rate_hz"] = inferred(float(sample_rate_hz), "user_confirmed")
    if channel_names:
        names = [str(c).strip() for c in channel_names]
        if len(names) == len(rec.channel_names):
            rec.channel_names = names
            rec.channel_types = merge_channel_types(names, rec.file_types)
            rec.metadata_evidence["channel_names"] = inferred(names, "user_confirmed")
        else:
            rec.warnings.append(f"提供的通道名有 {len(names)} 个，文件有 {len(rec.channel_names)} 个通道，未采用")
    return rec


# ---------------------------------------------------------------- MNE formats

def _read_mne(path: Path, max_seconds: float | None) -> Record:
    import mne

    ext = file_ext(path)
    try:
        raw = mne.io.read_raw(str(path), preload=False, verbose="ERROR")
    except TypeError as exc:
        if ext == ".set" and "trials" in str(exc):
            return _record_from_epochs(mne.read_epochs_eeglab(str(path), verbose="ERROR"), "set", max_seconds)
        raise
    except (FileNotFoundError, OSError) as exc:
        hint = {".set": "EEGLAB 的数据可能在同名 .fdt 文件里，需要与 .set 放在同一目录",
                ".vhdr": "BrainVision 需要 .vhdr、.vmrk、.eeg 在同一目录，且文件名与 .vhdr 里记录的一致"}.get(ext, "")
        raise FileNotFoundError(f"读取 {path.name} 失败：{exc}。{hint}".strip()) from exc
    sr = float(raw.info["sfreq"])
    names = list(raw.ch_names)
    file_types = list(raw.get_channel_types())
    types = merge_channel_types(names, file_types)
    n_total = int(raw.n_times)
    stop = n_total if not max_seconds else min(n_total, int(round(sr * max_seconds)))
    data = to_microvolts(raw.get_data(start=0, stop=stop), types)
    rec = Record(data, sr, names, types, n_total, ext.lstrip(".").replace(".gz", ""), inferred("uV", "file_header", 1.0),
                 file_types=file_types, source_files=[str(name) for name in raw.filenames if name is not None])
    rec.metadata_evidence["preprocessing"] = inferred({"highpass_hz": float(raw.info["highpass"]),
                                                       "lowpass_hz": float(raw.info["lowpass"])}, "file_header")
    rec.events = _events_from_raw(raw, rec.warnings)
    return rec


def _events_from_raw(raw, warnings: list[str]) -> list[dict]:
    import mne

    events: list[dict] = []
    ann = raw.annotations
    offset = raw.first_time if ann.orig_time is not None else 0.0
    for onset, dur, desc in zip(ann.onset, ann.duration, ann.description):
        events.append({"onset_s": round(float(onset) - offset, 4), "duration_s": round(float(dur), 4), "label": str(desc)})
    stim = [n for n, t in zip(raw.ch_names, raw.get_channel_types()) if t == "stim"]
    if stim and not events:
        try:
            found = mne.find_events(raw, stim_channel=stim[0], shortest_event=1, initial_event=True, verbose="ERROR")
            sr = float(raw.info["sfreq"])
            for s, _, code in found:
                events.append({"onset_s": round((int(s) - raw.first_samp) / sr, 4), "duration_s": 0.0, "label": f"{stim[0]}={int(code)}"})
        except Exception:  # noqa: BLE001 — a malformed trigger channel must not block reading the EEG
            warnings.append(f"触发通道 {stim[0]} 无法解析事件")
    if len(events) > MAX_EVENTS:
        warnings.append(f"事件共 {len(events)} 个，manifest 只保留前 {MAX_EVENTS} 个")
        events = events[:MAX_EVENTS]
    return events


def epochs_to_continuous(epochs) -> tuple[np.ndarray, list[dict], str]:
    """Pack epoch samples into [C, E*T] (SI); event onsets remain at each epoch's event zero."""
    data = epochs.get_data()
    n_ep, n_ch, n_t = data.shape
    sr = float(epochs.info["sfreq"])
    cont = data.transpose(1, 0, 2).reshape(n_ch, n_ep * n_t)
    inv = {v: k for k, v in epochs.event_id.items()}
    events = [{"onset_s": float(i * n_t / sr - epochs.tmin), "duration_s": 0.0,
               "label": inv.get(int(code), str(int(code))), "epoch_index": int(epochs.selection[i]), "event_code": int(code)}
              for i, code in enumerate(epochs.events[:, 2])]
    note = f"分段（epoched）数据：{n_ep} 个 epoch × {n_t / sr:g} s（tmin={epochs.tmin:g} s）；保留 epoch 边界与事件零点"
    return cont, events, note


def _record_from_epochs(epochs, fmt: str, max_seconds: float | None) -> Record:
    cont, events, note = epochs_to_continuous(epochs)
    names = list(epochs.ch_names)
    file_types = list(epochs.get_channel_types())
    types = merge_channel_types(names, file_types)
    sr = float(epochs.info["sfreq"])
    n_total = cont.shape[1]
    n_t = len(epochs.times)
    segments = [{"start_sample": i * n_t, "end_sample": (i + 1) * n_t,
                 "original_start_s": i * n_t / sr, "epoch_index": int(epochs.selection[i]),
                 "epoch_tmin_s": float(epochs.tmin), "original_epoch_tmin_s": float(epochs.tmin),
                 "event_code": int(event[2]), "event_label": events[i]["label"],
                 "original_event_sample": int(event[0])}
                for i, event in enumerate(epochs.events)]
    if max_seconds:
        cont = cont[:, : int(round(sr * max_seconds))]
    rec = Record(to_microvolts(cont, types), sr, names, types, n_total, fmt, inferred("uV", "file_header", 1.0),
                 events=events[:MAX_EVENTS], warnings=[note], file_types=file_types,
                 signal_kind="epochs", segments=segments)
    rec.metadata_evidence["preprocessing"] = inferred({"highpass_hz": float(epochs.info["highpass"]),
                                                       "lowpass_hz": float(epochs.info["lowpass"])}, "file_header")
    return rec


# ---------------------------------------------------------------- DEAP pickle (restricted unpickler)

class _SafeUnpickler(pickle.Unpickler):
    """DEAP .dat files are pickles; only numpy array reconstruction is allowed, never arbitrary callables."""

    ALLOWED = {("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
               ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
               ("numpy", "ndarray"), ("numpy", "dtype"), ("copy_reg", "_reconstructor"), ("copyreg", "_reconstructor"),
               ("__builtin__", "object"), ("builtins", "object"), ("_codecs", "encode")}  # py3 protocol≤2 bytes

    def find_class(self, module, name):
        if (module, name) in self.ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"不允许的 pickle 对象 {module}.{name}（.dat 只接受 DEAP 预处理版的 numpy 数组）")


def _read_dat_payload(path: Path) -> dict:
    with path.open("rb") as f:
        return _SafeUnpickler(f, encoding="latin1").load()


def read_deap_dat(path: Path):
    obj = _read_dat_payload(path)
    return np.asarray(obj["data"]), (np.asarray(obj["labels"]) if obj.get("labels") is not None else None)


def _deap_record(data: np.ndarray, labels, trial_index: int | None, max_seconds: float | None, fmt: str,
                 sample_rate_hz: float | None = None, metadata: dict | None = None) -> Record:
    from eegagent.worker.channel_orders import DEAP_MAHNOB_CHANNEL_ORDER

    idx = int(trial_index or 0)
    if not 0 <= idx < data.shape[0]:
        raise ValueError(f"trial_index {idx} 超出范围 0–{data.shape[0] - 1}")
    n_total = data.shape[2]
    # The published DEAP preprocessed layout (8064 samples = 63 s at 128 Hz, Geneva order) is used for measurement;
    # its evidence stays format_assumption, so model routing still needs the user's confirmation.
    if sample_rate_hz is None:
        sample_rate_hz = 128.0
    stop = n_total if not max_seconds else min(n_total, int(sample_rate_hz * max_seconds))
    names = list(DEAP_MAHNOB_CHANNEL_ORDER)
    lab = None
    if labels is not None and labels.ndim == 2:
        lab = {"values": [float(x) for x in labels[idx]]}
    selected = data[idx, :32, :stop].astype(float)
    evidence = {
        "origin": inferred("unverified", "unknown"),
        "data_shape": inferred(list(data.shape), "file_content"),
        "labels_shape": inferred(list(labels.shape), "file_content") if labels is not None else inferred(None, "unknown"),
        "sample_rate_hz": inferred(128.0, "format_assumption"),
        "channel_names": inferred(list(DEAP_MAHNOB_CHANNEL_ORDER), "format_assumption"),
        "channel_selection": inferred({"kept_indices": [0, 31], "excluded_indices": [32, 39],
                                        "roles": "first 32 EEG, last 8 peripheral"}, "format_assumption"),
        "unit": inferred("unknown", "unknown"),  # 官方readme未明示预处理数组数值单位
        "reference": inferred("average", "format_assumption"),
        "preprocessing": inferred({"bandpass_hz": [4, 45], "baseline_s": 3}, "format_assumption"),
        "label_names": inferred(["valence", "arousal", "dominance", "liking"], "format_assumption"),
    }
    metadata = metadata or {}
    for key in NAME_KEYS:
        if key in metadata:
            declared_names = _name_list(metadata[key], 40) or _name_list(metadata[key], 32)
            if declared_names:
                names = declared_names[:32]
                evidence["channel_names"] = inferred(names, "file_header")
                break
    unit = _unit_from_metadata(metadata, selected)
    if unit["source"] == "file_header":
        evidence["unit"] = unit
    if metadata.get("reference") in ("average", "referential", "bipolar"):
        evidence["reference"] = inferred(metadata["reference"], "file_header")
    if lab is not None and "label_names" in metadata:
        declared_labels = _name_list(metadata["label_names"], len(lab["values"]))
        if declared_labels:
            lab["names"] = declared_labels
            evidence["label_names"] = inferred(declared_labels, "file_header")
    return Record(selected, sample_rate_hz, names, ["eeg"] * 32, n_total, fmt,
                  unit, labels=lab, trial_id=f"trial_{idx + 1:02d}", trial_index=idx, trial_count=data.shape[0],
                  format_profile="deap_preprocessed", metadata_evidence=evidence,
                  warnings=["按 DEAP 预处理版格式读取：128 Hz，前 32 道按 Geneva 顺序为 EEG（后 8 道外周信号未载入），"
                            "每 trial 63 秒含 3 秒基线。实际采样率或通道不同时登记真实值；送入 DEAP 专用模型前需确认是官方预处理波形"])


def _read_deap(path: Path, trial_index: int | None, max_seconds: float | None,
               sample_rate_hz: float | None = None) -> Record:
    obj = _read_dat_payload(path)
    data = np.asarray(obj["data"])
    labels = np.asarray(obj["labels"]) if obj.get("labels") is not None else None
    if data.ndim != 3 or data.shape[1:] != (40, 8064):
        raise ValueError(f"无法识别的 .dat：data 形状 {data.shape}（读取器支持 trial×40×8064 的 DEAP 兼容结构）")
    sr = sample_rate_hz
    if sr is None:
        sr = next((_scalar(obj[key]) for key in SR_KEYS if key in obj and _scalar(obj[key])), None)
    rec = _deap_record(data, labels, trial_index, max_seconds, "dat", sr, obj)
    if sr is not None:
        rec.metadata_evidence["sample_rate_hz"] = inferred(sr, "file_header")
    return rec


# ---------------------------------------------------------------- text tables

def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _read_table(path: Path, sample_rate_hz: float | None, max_seconds: float | None) -> Record:
    from io import StringIO

    from eegagent.worker.text import SIGNAL_METADATA_PREFIXES, delimiter_for, read_text

    text, encoding = read_text(path)
    comments: list[str] = []
    metadata = {}
    header_line = ""
    n_skip = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("%", "#")):
            if s.startswith(SIGNAL_METADATA_PREFIXES):
                metadata = json.loads(s.partition(":")[2].strip())
            comments.append(s)
            n_skip += 1
            continue
        header_line = s
        break
    delim = delimiter_for(header_line)
    tokens = [t.strip().strip('"') for t in (header_line.split(delim) if delim else header_line.split())]
    has_header = any(t and not _is_number(t) for t in tokens)
    sr = float(sample_rate_hz) if sample_rate_hz else None
    if sr is None and "sample_rate_hz" in metadata:
        sr = float(metadata["sample_rate_hz"])
    warnings: list[str] = []
    if encoding != "utf-8-sig":
        warnings.append(f"文本按 {encoding} 编码读取")
    if sr is None:
        for c in comments:  # OpenBCI GUI: "%Sample Rate = 250 Hz"
            m = re.search(r"sample\s*rate\s*[=:]\s*([\d.]+)", c, re.I)
            if m:
                sr = float(m.group(1))
                warnings.append(f"采样率 {sr:g} Hz 取自文件注释头")
                break
    skip = n_skip + (1 if has_header else 0)
    n_rows = sum(1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith(("%", "#"))) - (1 if has_header else 0)
    max_rows = None
    if max_seconds:
        max_rows = int((sr or 250) * max_seconds) if sr else 5000
    arr = np.genfromtxt(StringIO(text), delimiter=delim, skip_header=skip, max_rows=max_rows, comments=None, ndmin=2)
    names = tokens if has_header else [f"ch{i + 1}" for i in range(arr.shape[1])]
    if len(names) != arr.shape[1]:
        names = (names + [f"ch{i + 1}" for i in range(len(names), arr.shape[1])])[: arr.shape[1]]
    if names and names[0].lower() in ("time", "timestamp", "t", "time_s", "time (s)", "time(s)"):
        t = arr[:, 0]
        if len(t) > 2 and sr is None and np.isfinite(t[:100]).all():
            dt = float(np.median(np.diff(t[:1000])))
            if dt > 0:
                sr = round(1.0 / dt, 3)
        if sr and len(t) > 1 and (not np.isfinite(t).all() or
                                  not np.allclose(np.diff(t), 1.0 / sr, rtol=1e-3, atol=1e-6)):
            raise ValueError("时间列不等间隔、存在断点或与采样率冲突；不能作为连续均匀采样EEG，需按实际时间轴处理")
        arr, names = arr[:, 1:], names[1:]
    if "channel_names" in metadata:
        declared_names = list(metadata["channel_names"])
        if len(declared_names) != arr.shape[1] or has_header and names != declared_names:
            raise ValueError("EFAgent 元数据的 channel_names 与实际数据列不一致")
        names = declared_names
    keep = [i for i in range(arr.shape[1]) if np.isfinite(arr[:, i]).any()]
    if len(keep) < arr.shape[1]:
        dropped = [names[i] for i in range(arr.shape[1]) if i not in keep]
        warnings.append(f"非数值列已忽略：{', '.join(dropped[:8])}{'…' if len(dropped) > 8 else ''}")
        arr, names = arr[:, keep], [names[i] for i in keep]
    types = merge_channel_types(names, metadata.get("channel_types"))
    data = arr.T.astype(float)
    eeg = eeg_indices(types)
    unit = _unit_from_metadata(metadata, data[eeg] if eeg else data)
    if sr is None:
        warnings.append("采样率无法从文件得知，需要用户提供")
    sr_source = "file_header" if "sample_rate_hz" in metadata or any("文件注释头" in w for w in warnings) else "derived_time_column"
    evidence = {"sample_rate_hz": inferred(sr, sr_source if sr else "unknown"),
                "channel_names": inferred(names, "file_header" if has_header or "channel_names" in metadata else "generated")}
    for key in ("reference", "preprocessing", "baseline_duration_s", "processing_steps"):
        if key in metadata:
            evidence[key] = inferred(metadata[key], "file_header")
    return Record(data, sr, names, types, max(n_rows, data.shape[1]), "csv" if path.suffix.lower() != ".tsv" else "tsv",
                  unit, warnings=warnings, metadata_evidence=evidence,
                  signal_kind=metadata.get("signal_kind", "continuous"), segments=metadata.get("segments", []))


# ---------------------------------------------------------------- numpy arrays

def _orient(arr: np.ndarray) -> np.ndarray:
    # EEG has far more samples than channels.
    return arr if arr.shape[0] <= arr.shape[1] else arr.T


def _scalar(v) -> float | None:
    try:
        a = np.asarray(v, dtype=float).ravel()
        return float(a[0]) if a.size == 1 and np.isfinite(a[0]) and a[0] > 0 else None
    except (TypeError, ValueError):
        return None


def _name_list(v, n: int) -> list[str] | None:
    if isinstance(v, (list, tuple, np.ndarray)):
        items = list(np.asarray(v, dtype=object).ravel()) if not isinstance(v, (list, tuple)) else list(v)
        if items and all(isinstance(x, dict) for x in items):  # EEGLAB chanlocs
            items = [x.get("labels") for x in items]
        if len(items) == n and all(isinstance(x, (str, np.str_)) for x in items):
            return [str(x).strip() for x in items]
    if isinstance(v, dict) and "labels" in v:  # pymatreader turns struct arrays into dict of lists
        return _name_list(v["labels"], n)
    return None


def _read_array(path: Path, sample_rate_hz: float | None, max_seconds: float | None) -> Record:
    meta: dict = {}
    if path.suffix.lower() == ".npz":
        z = np.load(path, allow_pickle=False)
        key = "data" if "data" in z.files else max(z.files, key=lambda k: z[k].size)
        arr = z[key]
        meta = {k: z[k] for k in z.files if k != key}
    else:
        arr = np.load(path, allow_pickle=False)
    if arr.ndim != 2:
        raise ValueError(f"需要二维数组 [channels, samples]，实际形状 {arr.shape}")
    arr = _orient(arr)
    sr = float(sample_rate_hz) if sample_rate_hz else None
    warnings: list[str] = []
    if sr is None:
        for k in SR_KEYS:
            if k in meta and _scalar(meta[k]):
                sr = _scalar(meta[k])
                warnings.append(f"采样率 {sr:g} Hz 取自 npz 的 {k}")
                break
    names = None
    for k in NAME_KEYS:
        if k in meta:
            names = _name_list(meta[k], arr.shape[0])
            if names:
                break
    names_source = "file_header" if names else "generated"
    names = names or [f"ch{i + 1}" for i in range(arr.shape[0])]
    n_total = arr.shape[1]
    if max_seconds:
        arr = arr[:, : int((sr or 250) * max_seconds)]
    types = merge_channel_types(names, None)
    eeg = eeg_indices(types)
    if sr is None:
        warnings.append("采样率无法从文件得知，需要用户提供")
    evidence = {"sample_rate_hz": inferred(sr, "file_header" if sr else "unknown"),
                "channel_names": inferred(names, names_source)}
    for field_name in ("reference", "preprocessing", "input_stage", "task_mode", "baseline_duration_s", "dataset"):
        if field_name in meta:
            evidence[field_name] = inferred(np.asarray(meta[field_name]).item(), "file_header")
    profile = str(np.asarray(meta.get("format_profile", "")).item())
    is_eav = profile == "eav_raw_trial"
    is_deap = profile == "deap_preprocessed_trial"
    unit = _unit_from_metadata(meta, arr[eeg] if eeg else arr)
    if is_eav:
        if n_total != 10000:
            raise ValueError("eav_raw_trial NPZ 需要一个完整的 10000 采样点原始 trial；电极由真实通道名匹配")
        evidence.update(unit=unit, dataset=inferred(str(np.asarray(meta["dataset"]).item()), "file_header"),
                        reference=inferred(str(np.asarray(meta["reference"]).item()), "file_header"),
                        task_mode=inferred(str(np.asarray(meta.get("task_mode", "unknown")).item()), "file_header"))
        evidence.setdefault("input_stage", inferred("raw_eav_trial", "file_header"))
        evidence.setdefault("preprocessing", inferred("eav_raw", "file_header"))
        if "source_provenance" in meta:
            evidence["source_provenance"] = inferred(json.loads(str(meta["source_provenance"].item())), "file_header")
        warnings.append("EAV 原始 trial 声明；保持存储的 dataset_native 数值，物理单位与原始文件来源仍未验证")
    if is_deap:
        if n_total != 8064:
            raise ValueError("deap_preprocessed_trial 需要完整8064采样点（63秒）波形；电极由真实通道名匹配")
        evidence.update(unit=unit, dataset=inferred(str(meta["dataset"].item()), "file_header"),
                        reference=inferred(str(meta["reference"].item()), "file_header"),
                        preprocessing=inferred(str(meta["preprocessing"].item()), "file_header"))
        evidence.setdefault("input_stage", inferred("deap_preprocessed_trial", "file_header"))
        evidence.setdefault("baseline_duration_s", inferred(3., "format_assumption"))
        if "source_provenance" in meta:
            evidence["source_provenance"] = inferred(json.loads(str(meta["source_provenance"].item())), "file_header")
        warnings.append("DEAP预处理波形格式声明；保留原数值，3秒基线为训练格式约定，来源与物理单位仍未认证")
    return Record(arr.astype(float), sr, names, types, n_total, path.suffix.lower().lstrip("."),
                  unit, warnings=warnings, metadata_evidence=evidence,
                  dataset=evidence.get("dataset", {"value": "unknown"})["value"], format_profile=profile or None)


# ---------------------------------------------------------------- MATLAB .mat (heuristic)

def _natkey(k: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", k)]


def _walk(d, prefix="", depth=0):
    """遍历嵌套结构：dict（struct）、list/tuple 与 object 数组（MATLAB cell）。

    AMIGOS 等数据集把每个 trial 的矩阵装进 cell（如 joined_data{1..20}），
    只递归 dict 会漏掉 cell 里的数值矩阵。
    """
    if depth > 2:
        return
    if isinstance(d, dict):
        for k, v in d.items():
            if str(k).startswith("__"):
                continue
            key = f"{prefix}{k}"
            yield key, v
            sep = "." if isinstance(v, dict) else ""
            yield from _walk(v, key + sep, depth + 1)
    elif isinstance(d, (list, tuple)) or (isinstance(d, np.ndarray) and d.dtype == object and d.ndim > 0):
        for i, v in enumerate(d):
            key = f"{prefix}[{i}]"
            yield key, v
            sep = "." if isinstance(v, dict) else ""
            yield from _walk(v, key + sep, depth + 1)


def _read_mat(path: Path, sample_rate_hz: float | None, trial_index: int | None, max_seconds: float | None) -> Record:
    try:
        from pymatreader import read_mat

        d = read_mat(str(path))
    except ImportError:
        from scipy.io import loadmat

        d = loadmat(str(path), squeeze_me=True, simplify_cells=True)
    for key in ("seg", "seg1"):
        if key in d and np.asarray(d[key]).ndim in (2, 3) and np.asarray(d[key]).shape[:2] == (10000, 30):
            return _read_eav_trial(path, d, key, trial_index, max_seconds, sample_rate_hz)
    items = list(_walk(d))
    arrays = {k: np.asarray(v) for k, v in items if isinstance(v, np.ndarray) and v.dtype.kind in "fiu" and v.ndim in (2, 3) and v.size >= 256}
    if not arrays:
        raise ValueError(f"{path.name} 里没有找到数值矩阵（需要 [channels, samples] 或 [trials, channels, samples]）")
    lower = {k.split(".")[-1].lower(): v for k, v in items}
    # DEAP data_preprocessed_matlab: data 40×40×8064 + labels 40×4
    for k, a in arrays.items():
        if a.ndim == 3 and a.shape[1:] == (40, 8064):
            sr = sample_rate_hz
            if sr is None:
                sr = next((_scalar(lower[key]) for key in SR_KEYS if key in lower and _scalar(lower[key])), None)
            rec = _deap_record(a, np.asarray(lower.get("labels")) if lower.get("labels") is not None else None,
                               trial_index, max_seconds, "mat", sr, lower)
            if sr is not None:
                rec.metadata_evidence["sample_rate_hz"] = inferred(sr, "file_header")
            return rec
    warnings: list[str] = []
    three = {k: a for k, a in arrays.items() if a.ndim == 3}
    two = {k: _orient(a) for k, a in arrays.items() if a.ndim == 2}
    chosen_key = None
    ti = None
    if three:
        chosen_key = max(three, key=lambda k: three[k].size)
        a = three[chosen_key]
        n_trials = a.shape[0]
        ti = int(trial_index or 0)
        if not 0 <= ti < a.shape[0]:
            raise ValueError(f"trial_index {ti} 超出范围 0–{a.shape[0] - 1}")
        arr = _orient(a[ti])
        warnings.append(f"{chosen_key} 是三维数组 {a.shape}，按 [trial, channel, sample] 取第 {ti} 个 trial（trial_index 可改）")
    else:
        groups: dict[int, list[str]] = {}
        for k, a in two.items():
            groups.setdefault(a.shape[0], []).append(k)
        best = max(groups.values(), key=lambda ks: (len(ks) > 1, sum(two[k].size for k in ks)))
        best = sorted(best, key=_natkey)  # 自然序：joined_data[2] 排在 joined_data[10] 前
        n_trials = len(best)
        if len(best) > 1:  # SEED-style: one 2-D array per trial
            ti = int(trial_index or 0)
            if not 0 <= ti < len(best):
                raise ValueError(f"trial_index {ti} 超出范围 0–{len(best) - 1}")
            warnings.append(f"文件含 {len(best)} 个同通道数矩阵（{', '.join(best[:6])}{'…' if len(best) > 6 else ''}），按 trial_index={ti} 取 {best[ti]}")
        else:
            ti = None
        chosen_key = best[ti or 0]
        arr = two[chosen_key]
    sr = float(sample_rate_hz) if sample_rate_hz else None
    if sr is None:
        for k in SR_KEYS:
            if k in lower and _scalar(lower[k]):
                sr = _scalar(lower[k])
                warnings.append(f"采样率 {sr:g} Hz 取自 .mat 的 {k}")
                break
    names = None
    for k in NAME_KEYS:
        if k in lower:
            names = _name_list(lower[k], arr.shape[0])
            if names:
                break
    names_source = "file_header" if names else "generated"
    names = names or [f"ch{i + 1}" for i in range(arr.shape[0])]
    n_total = arr.shape[1]
    if max_seconds:
        arr = arr[:, : int((sr or 250) * max_seconds)]
    types = merge_channel_types(names, None)
    eeg = eeg_indices(types)
    if sr is None:
        warnings.append("采样率无法从文件得知，需要用户提供")
    warnings.append(f".mat 按启发式读取：数据取自变量 {chosen_key}")
    return Record(arr.astype(float), sr, names, types, n_total, "mat", _unit_from_metadata(lower, arr[eeg] if eeg else arr),
                  trial_id=f"{chosen_key}" if ti is not None else None, trial_index=ti, warnings=warnings,
                  trial_count=n_trials,
                  metadata_evidence={"sample_rate_hz": inferred(sr, "file_header" if sr else "unknown"),
                                     "channel_names": inferred(names, names_source)})


def _read_eav_trial(path: Path, metadata: dict, key: str, trial_index: int | None,
                    max_seconds: float | None, sample_rate_hz: float | None) -> Record:
    """EAV's stored [10000 samples, 30 channels, trials] layout; retain its native scale."""
    from eegagent.worker.channel_orders import EAV_CHANNEL_ORDER

    values = np.asarray(metadata[key])
    if values.ndim == 2:
        values = values[:, :, None]
    ti = int(trial_index or 0)
    if not 0 <= ti < values.shape[2]:
        raise ValueError(f"trial_index {ti} 超出 EAV trial 范围 0–{values.shape[2] - 1}")
    sr = float(sample_rate_hz or 500)
    data = values[:, :, ti].T.astype(float)
    if max_seconds:
        data = data[:, :round(max_seconds * sr)]
    unit = inferred("dataset_native", "file_content")
    evidence = {"sample_rate_hz": inferred(sr, "format_assumption"),
                "channel_names": inferred(list(EAV_CHANNEL_ORDER), "format_assumption"),
                "unit": unit, "dataset": inferred("EAV", "format_assumption"),
                "reference": inferred("mastoids", "format_assumption"),
                "input_stage": inferred("raw_eav_trial", "format_assumption"),
                "task_mode": inferred("unknown", "unknown"),
                "source_provenance": inferred({"variable": key, "trial_index": ti,
                                               "n_trials": values.shape[2]}, "file_content")}
    labels = None
    sidecar = path.with_name(path.stem + "_label.mat")
    if sidecar.exists():
        from scipy.io import loadmat

        onehot = np.asarray(loadmat(str(sidecar))["label"])
        if onehot.shape != (10, values.shape[2]) or not np.isin(onehot[:, ti], (0, 1)).all() or onehot[:, ti].sum() != 1:
            raise ValueError("EAV label sidecar 需要 10 × trial 的 one-hot 标签，且选中列只能有一个类别")
        class_index = int(onehot[:, ti].argmax())
        evidence["task_mode"] = inferred("speaking" if class_index % 2 else "listening", "file_content")
        evidence["source_provenance"]["value"]["task_mode_source"] = str(sidecar)
        labels = {"values": [class_index // 2], "names": ["emotion_class_index"]}
    if "label" in metadata:
        label = np.asarray(metadata["label"]).ravel()
        if len(label) == values.shape[2]:
            labels = {"values": [float(label[ti])], "names": ["class_index"]}
    return Record(data, sr, list(EAV_CHANNEL_ORDER), ["eeg"] * 30, 10000, "mat", unit,
                  dataset="EAV", trial_id=f"{key}_trial_{ti}", trial_index=ti,
                  trial_count=values.shape[2],
                  format_profile="eav_raw_segments", metadata_evidence=evidence, labels=labels,
                  warnings=["EAV seg 格式候选；500 Hz、通道顺序与乳突参考来自数据集契约，未验证文件来源；数值保留原生尺度，物理单位未知"],
                  signal_kind="epochs", segments=[{"start_sample": 0, "end_sample": 10000,
                                                  "original_start_s": ti * 10000 / sr,
                                                  "epoch_index": ti, "epoch_tmin_s": 0.0,
                                                  "original_epoch_tmin_s": 0.0}])
