"""The `eeg` helper object available inside sandboxed scripts (run_python).

Scripts get `eeg` preloaded. `eeg.save_*` writes scientific artifacts with provenance and source context;
ordinary CSV/report/image files written in scratch are also delivered when the script finishes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from eegagent.worker import store
from eegagent.worker.processing import _public
from eegagent.worker.processing import load_windows as _load_windows
from eegagent.worker.signals import amplitude_unit, resolve_signal, signal_time_offset, signal_metadata
from eegagent.worker.figures import COLORS, BAND_COLORS, figure, save_files, unit_note, sampling_note
from eegagent.worker.progress import report

VERSION = "1.3"
ARTIFACT_KINDS = {"features", "prediction", "aggregate", "plot", "table", "report", "windows", "normalized_signal",
                  "embedding", "expression_timeline", "custom"}
LOCAL_TOOLS = {
    "list_files", "read_file", "read_document", "workspace_files", "inspect_record", "update_record_metadata",
    "preprocess", "clean_artifacts", "segment_signal", "extract_features", "read_artifact", "plot", "export",
    "detect_transients", "write_file", "edit_file", "save_to_workspace",
}
TOOL_LABELS = {
    "list_files": "查找数据文件", "read_file": "读取文本", "read_document": "读取文档", "workspace_files": "查看工作区文件",
    "inspect_record": "检查记录", "update_record_metadata": "登记采集参数", "preprocess": "预处理 EEG",
    "clean_artifacts": "清理 EEG 伪迹", "segment_signal": "生成分析窗口", "extract_features": "提取 EEG 特征",
    "read_artifact": "读取分析结果", "plot": "生成分析图", "export": "导出结果", "detect_transients": "检查瞬态候选",
    "write_file": "写入工作区文件", "edit_file": "修改工作区文本", "save_to_workspace": "保存结果到工作区",
}


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False, default=_default))


def _default(o: Any):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


class EEGApi:
    """Helper API injected into scripts as ``eeg``."""

    colors = COLORS
    band_colors = BAND_COLORS
    figure = staticmethod(figure)
    amplitude_unit = staticmethod(amplitude_unit)
    unit_note = staticmethod(unit_note)
    sampling_note = staticmethod(sampling_note)
    signal_time_offset = staticmethod(signal_time_offset)
    signal_metadata = staticmethod(signal_metadata)

    def __init__(self, session_id: str, scratch: Path, run_id: str, code_sha: str,
                 workspace_id: str | None = None, call_tool: Callable | None = None,
                 checkpoint: Callable | None = None):
        self.session_id = session_id
        self.scratch = Path(scratch)
        self.run_id = run_id
        self._code_sha = code_sha
        self.created: list[dict] = []
        self.manifests: dict[str, dict] = {}
        self.workspace_files: dict[tuple[str, str], dict] = {}
        self.workspace_id = workspace_id
        self._call_tool = call_tool
        self._checkpoint = checkpoint
        self._created_ids: set[str] = set()
        self._source_names: dict[str, str] = {}
        self._call_count = 0
        self._manifests: set[str] = set()
        self._parents: set[str] = set()
        self._n = 0

    def call(self, tool_name: str, **arguments) -> dict:
        """Reuse a local tool with this script's session, workspace, progress and output registration."""
        if tool_name not in LOCAL_TOOLS:
            raise ValueError(f"eeg.call 只调用本地工具；不支持 {tool_name}")
        if tool_name == "workspace_files" and arguments.get("action") not in {"list", "inspect"}:
            raise ValueError("脚本内 workspace_files 只用于 list/inspect；原文件整理直接调用 workspace_files 工具")
        self._call_count += 1
        source = next((arguments[key] for key in ("path", "source_id", "manifest_id", "windows_id", "artifact_id")
                       if arguments.get(key)), "")
        detail = self._source_names.get(str(source), str(source))
        stage = f"script_call_{self._call_count}"
        label = TOOL_LABELS[tool_name]
        report(stage, label, detail)
        args = {**arguments, "_session_id": self.session_id, "_workspace_id": self.workspace_id}
        if self._call_tool is None:
            from eegagent.worker.tools import run_tool
            result = run_tool(tool_name, args)
        else:
            result = self._call_tool(tool_name, args)
        if result.get("status") == "error":
            error = result["error"]
            raise RuntimeError(f"{tool_name}: {error['message']}")
        for manifest in ([result["manifest"]] if result.get("manifest") else []) + result.get("manifests", []):
            identifier = manifest["manifest_id"]
            self.manifests[identifier] = manifest
            self._manifests.add(identifier)
            self._source_names[identifier] = detail or manifest.get("source_path", identifier)
        for artifact in ([result["artifact"]] if result.get("artifact") else []) + result.get("artifacts", []):
            self._remember_artifact(artifact)
            identifier = artifact["artifact_id"]
            self._parents.add(identifier)
            self._manifests.update(artifact.get("source_manifest_ids", []))
            self._source_names[identifier] = next((self._source_names[mid] for mid in artifact.get("source_manifest_ids", [])
                                                   if mid in self._source_names), detail)
        for item in ([result["workspace_file"]] if result.get("workspace_file") else []) + result.get("workspace_files", []):
            self.workspace_files[(item["workspace_id"], item["path"])] = item
        if self._checkpoint:
            self._checkpoint()
        report(stage, label, f"{detail} · 完成" if detail else "完成")
        return result

    def _remember_artifact(self, artifact: dict) -> None:
        identifier = artifact["artifact_id"]
        if identifier not in self._created_ids:
            self._created_ids.add(identifier)
            self.created.append(artifact)
            if self._checkpoint:
                self._checkpoint()

    def _artifact_id(self, kind: str, name: str | None, *, parents=None, manifests=None) -> str:
        self._n += 1
        return store.make_artifact_id("run_python", VERSION, {
            "session_id": self.session_id, "run_id": self.run_id, "workspace_id": self.workspace_id,
            "code_sha": self._code_sha, "n": self._n, "name": name, "kind": kind,
            "source_manifest_ids": sorted(set(manifests or []) | self._manifests),
            "parent_artifact_ids": sorted(set(parents or []) | self._parents),
        })

    # ------------------------------------------------------------ loading

    def manifest(self, manifest_id: str) -> dict:
        m = store.load_manifest(manifest_id)
        self._manifests.add(manifest_id)
        return m

    def load_signal(self, source_id: str):
        """(data [C, N], sample_rate_hz, channel_names). Check signal_steps for physical units or normalization."""
        data, sr, names, _prov, src = resolve_signal(source_id)
        self._manifests.update(src)
        if source_id.startswith("art_"):
            self._parents.add(source_id)
        return data, sr, names

    def signal_steps(self, source_id: str) -> list[str]:
        """Processing chain already applied to a signal (e.g. ['unit_uV', 'notch_50', 'bandpass_0.5_50'])."""
        return list(resolve_signal(source_id)[3])

    def load_windows(self, windows_id: str):
        """(windows: list of {index,start_s,end_s,start_sample,end_sample}, data, sr, names)"""
        rec, payload, data, sr, names, _prov, src = _load_windows(windows_id)
        self._manifests.update(src)
        self._parents.add(windows_id)
        return payload["windows"], data, sr, names

    def load_features(self, features_id: str) -> dict:
        """{'arrays': {name: ndarray}, 'summary': dict, 'windows': list, 'channel_names': list, 'bands': dict}"""
        rec = store.load_artifact(features_id)
        if rec.get("kind") != "features":
            raise ValueError(f"{features_id} is kind={rec.get('kind')}, expected features")
        z = np.load(store.resolve_inside_storage(rec["uri"]))
        arrays = {k: z[k] for k in z.files}
        wins: list = []
        if rec.get("parent_artifact_ids"):
            wrec = store.load_artifact(rec["parent_artifact_ids"][0])
            wins = json.loads(store.resolve_inside_storage(wrec["uri"]).read_text(encoding="utf-8")).get("windows", [])
        self._manifests.update(rec.get("source_manifest_ids", []))
        self._parents.add(features_id)
        s = rec.get("summary", {})
        return {"arrays": arrays, "summary": s, "windows": wins, "channel_names": s.get("channel_names", []), "bands": s.get("bands", {})}

    def artifact(self, artifact_id: str) -> dict:
        rec = store.load_artifact(artifact_id)
        self._parents.add(artifact_id)
        self._manifests.update(rec.get("source_manifest_ids", []))
        return rec

    def artifact_path(self, artifact_id: str) -> Path:
        rec = store.load_artifact(artifact_id)
        self._parents.add(artifact_id)
        self._manifests.update(rec.get("source_manifest_ids", []))
        return store.resolve_inside_storage(rec["uri"])

    # ------------------------------------------------------------ MNE bridge

    def to_mne(self, source_id: str, montage: str | None = "auto", events: bool = True):
        """mne.io.Raw (units V) for a sig_… / normalized_signal art_…: channel types, standard 10-05 positions for
        10-20 names (montage=None to skip), recording events and discontinuity annotations.
        Epoched signals remain discontinuous; use eeg.to_epochs to reconstruct their native event-relative axes."""
        from eegagent.worker.mne_bridge import source_raw

        raw, _positioned = source_raw(source_id, montage=montage, with_events=events)
        _data, _sr, _names, _prov, src = resolve_signal(source_id)
        self._manifests.update(src)
        if source_id.startswith("art_"):
            self._parents.add(source_id)
        raw.info["description"] = f"eegagent_source={source_id}"
        return raw

    def to_epochs(self, source_id: str, montage: str | None = "auto"):
        """MNE EpochsArray (V) with the original epoch tmin, condition labels and epoch identities."""
        from eegagent.worker.mne_bridge import source_epochs

        epochs = source_epochs(source_id, montage=montage)
        self._manifests.update(resolve_signal(source_id)[4])
        if source_id.startswith("art_"):
            self._parents.add(source_id)
        return epochs

    def save_raw(self, raw, summary: dict | None = None, *, steps: list[str] | None = None, name: str | None = None,
                 parents: list[str] | None = None) -> str:
        """Save an MNE Raw as a normalized_signal artifact (µV). steps names what the script did, in tool naming
        (e.g. ['bandpass_1_40_fir', 'ica_removed_2']); the source's processing chain is prepended automatically."""
        from eegagent.worker.mne_bridge import from_raw, geometry_from_raw, raw_events
        from eegagent.worker.processing import save_signal

        data, sr, names, types = from_raw(raw)
        desc = str(raw.info.get("description") or "")
        source = desc.split("eegagent_source=", 1)[1].split()[0] if "eegagent_source=" in desc else None
        base_steps = list(resolve_signal(source)[3]) if source else []
        kind, segments = geometry_from_raw(raw, source) if source else ("continuous", None)
        art_id = self._artifact_id("normalized_signal", name, parents=parents)
        record = save_signal(art_id, data, sr, names, types, source_manifest_ids=sorted(self._manifests),
                             parents=sorted(set(parents or []) | self._parents), steps=base_steps + list(steps or ["mne_script"]),
                             summary=_jsonable(dict(summary or {}, name=name) if name else (summary or {})),
                             params=_jsonable({"run_id": self.run_id, "code_sha": self._code_sha}),
                             created_by=f"run_python@{VERSION}", signal_kind=kind, segments=segments,
                             time_offset_s=float(raw.first_time), events=raw_events(raw))
        self._remember_artifact(_public(record))
        return art_id

    def save_epochs(self, epochs, summary: dict | None = None, *, steps: list[str] | None = None, name: str | None = None,
                    parents: list[str] | None = None) -> str:
        """Save MNE epochs with per-epoch original coordinates after selection, crop, baseline or resample."""
        from eegagent.worker.processing import save_signal
        from eegagent.worker.readers import _record_from_epochs
        from eegagent.worker.signals import time_coordinates

        packed = _record_from_epochs(epochs, "set", None)
        desc = str(epochs.info.get("description") or "")
        source = desc.split("eegagent_source=", 1)[1].split()[0] if "eegagent_source=" in desc else None
        base_steps = list(resolve_signal(source)[3]) if source else ["unit_uV"]
        if source:
            source_geometry = signal_metadata(source)
            source_segments = source_geometry["segments"]
            for i, segment in enumerate(packed.segments):
                if epochs.metadata is not None and "eegagent_segment_index" in epochs.metadata:
                    previous = source_segments[int(epochs.metadata.iloc[i]["eegagent_segment_index"])]
                    shift = epochs.tmin - previous["epoch_tmin_s"]
                    segment.update(epoch_index=previous["epoch_index"], original_start_s=previous["original_start_s"] + shift,
                                   original_epoch_tmin_s=previous.get("original_epoch_tmin_s", previous["epoch_tmin_s"]))
                else:
                    start = float(epochs.events[i, 0]) / packed.sample_rate_hz + epochs.tmin
                    stop = start + (segment["end_sample"] - segment["start_sample"]) / packed.sample_rate_hz
                    matching = [s for s in source_segments if s["start_sample"]/source_geometry["sample_rate_hz"] <= start
                                and stop <= s["end_sample"]/source_geometry["sample_rate_hz"] + 1e-9]
                    if not matching:
                        raise ValueError("待保存的 epoch 跨越了源信号的不连续边界；请按边界重新构建 epochs")
                    segment["original_start_s"] = time_coordinates(matching[0], start, stop, source_geometry["sample_rate_hz"])["original_time_range_s"][0]
                segment["original_end_s"] = segment["original_start_s"] + (segment["end_sample"]-segment["start_sample"])/packed.sample_rate_hz
                packed.events[i]["epoch_index"] = segment["epoch_index"]
        art_id = self._artifact_id("epochs", name, parents=parents)
        record = save_signal(art_id, packed.data, packed.sample_rate_hz, packed.channel_names, packed.channel_types,
                             source_manifest_ids=sorted(self._manifests), parents=sorted(set(parents or []) | self._parents),
                             steps=base_steps + list(steps or ["mne_epochs_script"]), summary=_jsonable(summary or {}),
                             params={"run_id": self.run_id, "code_sha": self._code_sha}, created_by=f"run_python@{VERSION}",
                             signal_kind="epochs", segments=packed.segments, events=packed.events)
        self._remember_artifact(_public(record))
        return art_id

    # ------------------------------------------------------------ saving

    def save_artifact(self, kind: str, data: Any, summary: dict | None = None, *, name: str | None = None,
                      parents: list[str] | None = None, manifests: list[str] | None = None,
                      time_range_s: list[float] | None = None, params: dict | None = None,
                      title: str | None = None, description: str | None = None) -> str:
        """Persist data as a citable artifact. data: Figure → png/svg/pdf; str → md; bytes → bin; ndarray or
        dict of ndarrays → npz; pandas DataFrame or list of dicts → csv; other dict/list → json."""
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"kind must be one of {sorted(ARTIFACT_KINDS)}")
        art_id = self._artifact_id(kind, name, parents=parents, manifests=manifests)
        ext, media, writer = self._encoder(data)
        path = store.artifact_payload_path(art_id, ext)
        written = writer(path)
        representations = written if media == "image/png" else None
        figure_summary = {"available_formats": list(representations)} if representations else {}
        metadata = dict(summary or {})
        if title is not None:
            metadata["title"] = title
        if description is not None:
            metadata["description"] = description
        if representations:
            axes_titles = list(dict.fromkeys(text for ax in data.axes for loc in ("center", "left", "right")
                                            if (text := ax.get_title(loc=loc))))
            metadata.setdefault("title", data.get_suptitle() or " · ".join(axes_titles[:2]) or "分析图")
            caption = getattr(data, "_eegagent_caption", "")
            if caption:
                metadata.setdefault("caption", caption)
            axes_labels = list(dict.fromkeys(label for ax in data.axes for label in (ax.get_xlabel(), ax.get_ylabel()) if label))
            metadata.setdefault("description", metadata.get("caption") or "；".join(axes_labels[:3]))
        record = store.save_artifact({
            "artifact_id": art_id, "kind": kind, "uri": str(path), "media_type": media,
            "source_manifest_ids": sorted(set(manifests or []) | self._manifests),
            "parent_artifact_ids": sorted(set(parents or []) | self._parents),
            "time_range_s": time_range_s,
            "summary": _jsonable({**metadata, **({"name": name} if name else {}), **figure_summary}),
            **({"representations": representations} if representations else {}),
            "provenance": {"created_by": f"run_python@{VERSION}",
                           "params": _jsonable({"run_id": self.run_id, "code_sha": self._code_sha, **(params or {})}),
                           "steps": ["script"]},
        })
        pub = _public(record)
        self._remember_artifact(pub)
        return art_id

    def save_figure(self, fig, summary: dict | None = None, **kw) -> str:
        return self.save_artifact("plot", fig, summary, **kw)

    def save_table(self, rows: Any, summary: dict | None = None, columns: list[str] | None = None, **kw) -> str:
        if columns and isinstance(rows, list):
            import pandas as pd
            rows = pd.DataFrame(rows, columns=columns)
        return self.save_artifact("table", rows, summary, **kw)

    def save_report(self, markdown: str, summary: dict | None = None, **kw) -> str:
        return self.save_artifact("report", str(markdown), summary, **kw)

    # ------------------------------------------------------------ encoders

    @staticmethod
    def _encoder(data: Any):
        try:
            from matplotlib.figure import Figure
        except ImportError:  # pragma: no cover
            Figure = ()  # type: ignore
        try:
            import pandas as pd
        except ImportError:  # pragma: no cover
            pd = None
        if Figure and isinstance(data, Figure):
            return "png", "image/png", lambda p: save_files(data, p)
        if isinstance(data, str):
            return "md", "text/markdown", lambda p: p.write_text(data, encoding="utf-8")
        if isinstance(data, (bytes, bytearray)):
            return "bin", "application/octet-stream", lambda p: p.write_bytes(bytes(data))
        if isinstance(data, np.ndarray):
            return "npz", "application/x-npz", lambda p: np.savez_compressed(p, data=data)
        if isinstance(data, dict) and data and all(isinstance(v, np.ndarray) for v in data.values()):
            return "npz", "application/x-npz", lambda p: np.savez_compressed(p, **data)
        if pd is not None and isinstance(data, pd.DataFrame):
            return "csv", "text/csv", lambda p: data.to_csv(p, index=False)
        if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
            import csv

            keys: list[str] = []
            for r in data:
                for k in r:
                    if k not in keys:
                        keys.append(k)

            def _w(p: Path):
                with p.open("w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=keys)
                    w.writeheader()
                    for r in data:
                        w.writerow({k: _default(v) if isinstance(v, (np.generic, np.ndarray, Path)) else v for k, v in r.items()})

            return "csv", "text/csv", _w
        return "json", "application/json", lambda p: p.write_text(json.dumps(_jsonable(data), ensure_ascii=False, indent=1), encoding="utf-8")
