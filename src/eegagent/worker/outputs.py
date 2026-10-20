"""plot / export / read_artifact tools."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import textwrap

import numpy as np

from eegagent.worker.processing import BANDS, _public, welch_by_segments
from eegagent.worker.signals import (amplitude_unit, amplitude_unit_source, analysis_context, resolve_channel_types,
                                    resolve_signal, signal_metadata, signal_time_offset)
from eegagent.worker.channels import VOLT_TYPES
from eegagent.worker.store import artifact_payload_path, load_artifact, load_manifest, make_artifact_id, resolve_inside_storage, save_artifact, storage_dir
from eegagent.worker.figures import BAND_COLORS, BAND_LABELS, COLORS as PALETTE, FONT_SIZES, LINE_STYLES, WIDTHS, figure, peak_preserving_indices, save_files, style, unit_note, sampling_note

VERSION = "3.5"
PLOT_PRESENTATION = {
    "raw": ("EEG 波形", "展示各通道振幅随时间的变化，便于查看波形和瞬态变化。"),
    "psd": ("功率谱密度（PSD）", "展示信号功率在各频率的分布，便于查看节律主峰和工频附近能量。"),
    "bandpower_timeline": ("频带功率时间线", "按分析窗口展示频带功率变化，用于定位功率增强或减弱的时间范围。"),
    "overview": ("EEG 分析总览", "将关键通道波形、功率谱和频带趋势放在同一张图中对照。"),
    "clean_ica_iclabel": ("ICA 成分头皮分布", "按头皮位置展示各独立成分的空间权重，并标注 ICLabel 类别、概率和剔除状态。"),
    "clean_asr": ("ASR 清洗前后对照", "叠加显示重构变化较大的通道和时段，便于比较 ASR 处理前后的波形差异。"),
}


def download_filename(record: dict, extension: str) -> str:
    summary = record.get("summary", {})
    title = summary.get("title") or PLOT_PRESENTATION.get(summary.get("kind"), ("", ""))[0]
    title = title or {"plot": "分析图", "table": "分析数据表", "report": "分析报告",
                      "normalized_signal": "处理后EEG", "features": "EEG特征", "windows": "分析窗口",
                      "prediction": "模型预测结果", "emotion_assessment": "情绪审阅记录"}.get(record["kind"], "分析产物")
    if Path(title).suffix.lower() in {".png", ".svg", ".pdf", ".csv", ".tsv", ".json", ".md", ".html", ".py", ".npy", ".npz"}:
        title = Path(title).stem
    parts = [title]
    for identifier in record.get("source_manifest_ids", [])[:1]:
        if not (storage_dir() / "manifests" / (identifier + ".json")).is_file():
            continue
        manifest = load_manifest(identifier)
        source = manifest.get("original_name") or Path(manifest["source_path"]).name
        parts.append(Path(source).stem)
        if manifest.get("trial_index") is not None:
            parts.append(f"试次{manifest['trial_index'] + 1}")
    for step in record.get("provenance", {}).get("steps", []):
        if step.startswith("asr_k"):
            parts.append("ASR" + step.removeprefix("asr_k"))
        elif step.startswith("ica_iclabel_removed_"):
            parts.append("ICA剔除" + step.removeprefix("ica_iclabel_removed_"))
    span = record.get("time_range_s")
    if span:
        parts.append(f"{span[0]:g}-{span[1]:g}秒")
    parts.append("版本" + record["artifact_id"].removeprefix("art_"))
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", "_".join(parts))
    return re.sub(r"\s+", "", stem).strip(". ") + extension


def _mpl():
    return style()


def _save_plot(fig, kind: str, inputs: dict, src_manifests: list[str], parents: list[str], time_range, summary: dict, prov: list[str]) -> dict:
    art_id = make_artifact_id("plot", VERSION, inputs)
    path = artifact_payload_path(art_id, "png")
    representations = save_files(fig, path)
    import matplotlib.pyplot as plt

    plt.close(fig)
    context = analysis_context(inputs.get("signal_source", inputs["source"]), *(time_range or [None, None]))
    geometry = signal_metadata(context["source_id"]) if context.get("source_id") else None
    if geometry and geometry["kind"] == "epochs":
        summary = {k: v for k, v in summary.items() if k not in ("original_recording_offset_s", "original_recording_time_range_s")}
        summary["time_axis"] = context["time_axis"]
    if summary.get("channels_shown") and geometry:
        lookup = dict(zip(geometry["channel_names"], geometry["channel_types"]))
        geometry = dict(geometry, channel_names=summary["channels_shown"],
                        channel_types=[lookup[name] for name in summary["channels_shown"]])
    context["aggregation"] = summary.get("aggregation") or {"plot": summary.get("summary_statistic", "individual channel traces")}
    if summary.get("n_windows") is not None and context.get("window_support"):
        context["window_support"] = dict(context["window_support"], n_windows=summary["n_windows"])
    if "measurement_settings" in summary:
        context["settings"] = summary["measurement_settings"]
    title, description = PLOT_PRESENTATION[kind]
    record = save_artifact({
        "artifact_id": art_id, "kind": "plot", "uri": str(path), "media_type": "image/png",
        "source_manifest_ids": src_manifests, "parent_artifact_ids": parents,
        "representations": representations,
        "time_range_s": time_range, "summary": {"title": title, "description": description, **summary,
                                               "kind": kind, "caption": fig._eegagent_caption,
                                               "available_formats": list(representations)},
        **({"signal": geometry} if geometry else {}), "analysis": context,
        "provenance": {"created_by": f"plot@{VERSION}", "params": inputs, "steps": prov + [f"plot_{kind}"]},
    })
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}


def _channels(names: list[str], requested: list[str] | None) -> list[int]:
    if requested is None:
        return list(range(len(names)))
    lookup = {name.strip().upper(): index for index, name in enumerate(names)}
    missing = [name for name in requested if name.strip().upper() not in lookup]
    if missing or not requested:
        raise ValueError(f"请求的通道不存在或为空：{missing or requested}")
    return list(dict.fromkeys(lookup[name.strip().upper()] for name in requested))


def _signal_channels(source_id, names, requested):
    types = resolve_channel_types(source_id, names)
    selected = _channels(names, requested) if requested is not None else [i for i, typ in enumerate(types) if typ == "eeg"]
    if not selected:
        raise ValueError("没有可绘制的 EEG 通道；请明确 channels 或先确认通道类型")
    non_voltage = [names[i] for i in selected if types[i] not in VOLT_TYPES]
    if non_voltage:
        raise ValueError(f"这些非电压通道不能共用 EEG 幅值轴：{non_voltage}；请用脚本按各通道实际单位分别绘制")
    return selected


def _time_slice(data, sr, start_s, end_s):
    total = data.shape[1] / sr
    s0 = float(start_s) if start_s is not None else 0.0
    s1 = float(end_s) if end_s is not None else total
    if s0 < 0 or s0 >= s1 or s1 > total:
        raise ValueError(f"时间范围 {s0:g}–{s1:g} s 超出信号时长 {total:g} s")
    a, b = round(s0 * sr), round(s1 * sr)
    if b <= a:
        raise ValueError("所选范围不足一个采样点；请增加时间范围")
    return data[:, a:b], a / sr, b / sr


def _caption(fig, text: str, profile="screen", notes=""):
    fig._eegagent_caption = text
    visible = text if profile == "screen" else notes
    if visible:
        lines = [textwrap.fill(line, width=110) for line in visible.splitlines()]
        fig.supxlabel("\n".join(lines), fontsize=FONT_SIZES[profile]["small"], color="#626870", ha="left", x=.02)


def _display_unit(prov):
    unit = amplitude_unit(prov)
    return "原始单位" if unit == "未知单位" else unit


def _components(summary, feature):
    if feature in summary.get("feature_components", {}):
        return summary["feature_components"][feature]
    bands = list(summary["bands"])
    return {"bandpower": bands, "de": bands, "hjorth": ["activity", "mobility", "complexity"],
            "connectivity": summary["channel_names"], "spectral_entropy": ["spectral_entropy"],
            "band_stats": [f"{band}_{stat}" for band in bands for stat in ("mean_abs_deviation", "kurtosis", "skewness")],
            "rms": ["rms"], "line_length": ["line_length"], "zero_crossing_rate": ["zero_crossing_rate"]}.get(feature)


def _window_curve(ax, centres, values, gaps, **kwargs):
    indices = peak_preserving_indices(values)
    x, y = centres[indices], values[indices]
    breaks = [(a + b) / 2 for a, b in gaps if centres[0] < (a + b) / 2 < centres[-1]]
    if breaks:
        x = np.r_[x, breaks]
        y = np.r_[y, np.full(len(breaks), np.nan)]
        order = np.argsort(x)
        x, y = x[order], y[order]
    ax.plot(x, y, **kwargs)
    return len(indices)


def _trace_curve(ax, times, values, geometry, **kwargs):
    boundaries = [s["start_sample"] / geometry["sample_rate_hz"] for s in geometry["segments"][1:]
                  if times[0] < s["start_sample"] / geometry["sample_rate_hz"] < times[-1]]
    if boundaries:
        times = np.r_[times, boundaries]
        values = np.r_[values, np.full(len(boundaries), np.nan)]
        order = np.argsort(times, kind="stable")
        times, values = times[order], values[order]
    ax.plot(times, values, **kwargs)


def _plot_overview(source_id, start_s, end_s, channels, band, profile, width):
    from matplotlib.ticker import FuncFormatter

    rec = load_artifact(source_id)
    if rec["kind"] != "features":
        raise ValueError("overview 需要包含频带功率的 features 产物")
    z = np.load(resolve_inside_storage(rec["uri"]))
    if "bandpower" not in z:
        raise ValueError("overview 需要先提取 bandpower")
    payload = json.loads(resolve_inside_storage(load_artifact(rec["parent_artifact_ids"][0])["uri"]).read_text(encoding="utf-8"))
    band = band or "alpha"
    band_names = list(rec["summary"]["bands"])
    if band not in band_names:
        raise ValueError(f"频带 {band} 不在已计算的频带中")
    bi = band_names.index(band)
    names = rec["summary"]["channel_names"]
    selected = _channels(names, channels) if channels else np.argsort(-z["bandpower"][:, :, bi].mean(axis=0))[:2].tolist()
    selected_names = [names[i] for i in selected]
    data, sr, raw_names, prov, src = resolve_signal(payload["source"])
    geometry = signal_metadata(payload["source"])
    raw_indices = _channels(raw_names, selected_names)
    scope = rec["time_range_s"]
    segment, s0, s1 = _time_slice(data, sr, start_s if start_s is not None else scope[0], end_s if end_s is not None else scope[1])
    windows = [w for w in payload["windows"] if w["start_s"] >= s0 and w["end_s"] <= s1]
    wi = [i for i, w in enumerate(payload["windows"]) if w["start_s"] >= s0 and w["end_s"] <= s1]
    if not wi:
        raise ValueError("所选范围没有完整特征窗，请按该范围重新切窗")
    gaps, covered = [], s0
    for w in windows:
        if w["start_s"] > covered:
            gaps.append([covered, w["start_s"]])
        covered = max(covered, w["end_s"])
    if covered < s1:
        gaps.append([covered, s1])
    gaps.extend([[s["start_sample"] / sr] * 2 for s in geometry["segments"][1:]])
    centres = np.array([(w["start_s"] + w["end_s"]) / 2 for w in windows])
    sizes = FONT_SIZES[profile]
    wide = width == "wide"
    fig, axes = figure(nrows=1 if wide else 3, ncols=3 if wide else 1, width=width,
                       height=2.6 if wide else 6.5, profile=profile, squeeze=False)
    axes = axes.ravel()
    signal = segment[raw_indices]
    means = signal.mean(axis=1)
    scale = float(np.percentile(np.abs(signal - means[:, None]), 98) * 2.6) or 1.0
    for i, row in enumerate(signal):
        positions = peak_preserving_indices(row, 2500)
        _trace_curve(axes[0], positions / sr + s0, (row[positions] - means[i]) / scale - i, geometry,
                     color=PALETTE[i % len(PALETTE)], lw=.5)
    axes[0].set(xlim=(s0, s1), xlabel="时间（秒）")
    axes[0].set_yticks(-np.arange(len(selected_names)), selected_names)
    axes[0].spines["left"].set_visible(False)
    axes[0].tick_params(axis="y", length=0)
    yb = -len(selected_names) - .35
    xb = s1 - .08 * (s1 - s0)
    bar = 10 ** np.floor(np.log10(scale / 2))
    axes[0].plot([xb, xb], [yb, yb + bar / scale], color="#282D33", lw=.6)
    axes[0].text(xb - .02 * (s1 - s0), yb, f"{bar:g} {_display_unit(prov)}",
                 ha="right", va="bottom", fontsize=sizes["small"])
    displayed = (signal - means[:, None]) / scale - np.arange(len(signal))[:, None]
    axes[0].set_ylim(min(yb - .3, float(displayed.min()) - .1), max(.8, float(displayed.max()) + .1))
    frequencies, psd, psd_settings = welch_by_segments(data[raw_indices], sr, geometry["segments"], round(s0*sr), round(s1*sr))
    nper = psd_settings["nperseg"]
    fm = (frequencies >= .5) & (frequencies <= min(60, sr / 2))
    for i, name in enumerate(selected_names):
        curve = dict(color=PALETTE[i % len(PALETTE)], linestyle=LINE_STYLES[i % len(LINE_STYLES)], lw=.7, label=name)
        axes[1].plot(frequencies[fm], psd[i, fm], **curve)
        _window_curve(axes[2], centres, z["bandpower"][wi, selected[i], bi], gaps, **curve)
    if (psd[:, fm] > 0).any():
        axes[1].set_yscale("log", nonpositive="mask")
        axes[1].yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    axes[1].set(xlabel="频率（Hz）", ylabel=f"功率谱密度（{_display_unit(prov)}²/Hz）")
    axes[2].set(xlim=(s0, s1), xlabel="窗口中心时间（秒）", ylabel=f"{BAND_LABELS[band]}功率（{_display_unit(prov)}²）")
    for ax in axes[1:]:
        ax.legend(frameon=False)
    for letter, ax in zip("abc", axes):
        ax.text(-.08, 1.04, letter, transform=ax.transAxes, fontsize=8, fontweight="bold", va="bottom")
    caption = (f"{s0:g}–{s1:g} 秒 · {', '.join(selected_names)}\na：各通道波形错位展示。"
               f"b：Welch 功率谱，汉明窗 {nper / sr:g} 秒，重叠 50%。"
               f"c：各特征窗口中心的{BAND_LABELS[band]}功率。")
    note, rate_note = unit_note(prov), sampling_note(prov, sr)
    _caption(fig, caption, profile, "\n".join(n for n in (note, rate_note) if n))
    offset = signal_time_offset(payload["source"])
    render = {"figure_profile": profile, "figure_width_mm": WIDTHS[width] * 25.4}
    return _save_plot(fig, "overview", {"source": source_id, "channels": selected_names, "band": band,
                      "start_s": s0, "end_s": s1, **render}, src, [source_id], [s0, s1],
                     {**render, "panels": ["waveform", "psd", "bandpower_timeline"], "channels_shown": selected_names,
                      "band": band, "power_unit": amplitude_unit(prov) + "²", "time_range_s": [s0, s1],
                      "n_windows": len(windows),
                      "original_recording_time_range_s": [s0 + offset, s1 + offset],
                      "psd_aggregation": psd_settings["aggregation"], "frequency_resolution_hz": sr / nper,
                      "measurement_settings": {"welch": psd_settings},
                      "aggregation": {"waveform": "individual channel traces", "psd": psd_settings["aggregation"],
                                      "bandpower_timeline": "individual channels at selected feature-window centers"},
                      "window_evidence_time_range_s": [windows[0]["start_s"], windows[-1]["end_s"]],
                      "unrepresented_time_ranges_s": gaps, "unit_note": note}, prov)


def plot(kind: str, source_id: str, start_s: float | None = None, end_s: float | None = None,
         channels: list[str] | None = None, band: str | None = None,
         profile: str = "screen", width: str = "wide") -> dict:
    sizes = FONT_SIZES[profile]
    render = {"figure_profile": profile, "figure_width_mm": WIDTHS[width] * 25.4}
    if kind == "overview":
        return _plot_overview(source_id, start_s, end_s, channels, band, profile, width)
    if kind == "raw":
        data, sr, names, prov, src = resolve_signal(source_id)
        geometry = signal_metadata(source_id)
        selected = _signal_channels(source_id, names, channels)
        idx = selected[:32]
        segment, s0, s1 = _time_slice(data, sr, start_s, end_s)
        seg = segment[idx]
        means = np.nanmean(seg, axis=1)
        display_indices = [peak_preserving_indices(row) for row in seg]
        centered = [row[indices] - mean for row, indices, mean in zip(seg, display_indices, means)]
        scale = float(np.nanpercentile(np.abs(np.concatenate(centered)), 98) * 2.6) or 1.0
        overview = any(len(indices) < seg.shape[1] for indices in display_indices)
        fig, ax = figure(height=max(2.3, .28 * len(idx) + 1.5), profile=profile, width=width)
        for k, i in enumerate(idx):
            ax.axhline(-k, lw=.35, color="#E8EBEF", zorder=0)
            t = display_indices[k] / sr + s0
            _trace_curve(ax, t, centered[k] / scale - k, geometry, lw=.65, color=PALETTE[0])
        ax.set_yticks([-k for k in range(len(idx))])
        ax.set_yticklabels([names[i] for i in idx], fontsize=sizes["small"])
        ax.set_xlabel("从当前信号起点计时（秒）")
        ax.set_xlim(s0, s1)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=7)
        unit = amplitude_unit(prov)
        note = unit_note(prov)
        ax.set_title("所选通道波形" if channels else "EEG 波形", loc="left")
        duration = s1 - s0
        exponent = 10 ** np.floor(np.log10(duration / 8))
        bar_s = float(min((1, 2, 5, 10), key=lambda value: abs(value * exponent - duration / 8)) * exponent)
        x1 = s1 - .08 * duration
        x0 = x1 - bar_s
        plotted_min = min(float(np.nanmin(row / scale - k)) for k, row in enumerate(centered))
        plotted_max = max(float(np.nanmax(row / scale - k)) for k, row in enumerate(centered))
        yb = min(-len(idx) - .85, plotted_min - .9)
        ax.set_ylim(yb - .85, max(.9, plotted_max + .2))
        amplitude_exp = 10 ** np.floor(np.log10(scale / 2))
        bar_amplitude = float(min((1, 2, 5, 10), key=lambda value: abs(value * amplitude_exp - scale / 2)) * amplitude_exp)
        height = bar_amplitude / scale
        ax.plot([x0, x1, x1], [yb, yb, yb + height], color="#282D33", lw=1, solid_capstyle="butt")
        ax.text((x0 + x1) / 2, yb - .12, f"{bar_s:g} 秒", ha="center", va="top", fontsize=sizes["small"])
        ax.text(x1 + .012 * duration, yb + height / 2, f"{bar_amplitude:g} {_display_unit(prov)}",
                va="center", ha="left", fontsize=sizes["small"])
        caption = f"{s0:g}–{s1:g} 秒 · {len(idx)} 通道 · {sr:g} Hz"
        if overview:
            caption += " · 按局部极值抽点展示"
        if len(selected) > 32:
            caption += f"\n展示所选 {len(selected)} 个通道中的前 32 个；其余通道可单独选择查看"
        if note:
            caption += "\n" + note
        rate_note = sampling_note(prov, sr)
        if rate_note:
            caption += "\n" + rate_note
        offset = signal_time_offset(source_id)
        if offset:
            caption += f"\n当前信号起点对应原始记录第 {offset:g} 秒"
        _caption(fig, caption, profile, "\n".join(n for n in (note, rate_note) if n))
        return _save_plot(fig, "raw", {"kind": "raw", "source": source_id, "start_s": s0, "end_s": s1, "channels": channels, **render},
                          src, [source_id] if source_id.startswith("art_") else [], [s0, s1],
                          {**render, "channels_shown": [names[i] for i in idx], "channels_omitted": [names[i] for i in selected[32:]],
                           "scale_per_row": scale, "amplitude_unit": unit, "unit_estimated": "估计" in note,
                           "unit_assumed": "格式约定" in note,
                           "amplitude_unit_source": amplitude_unit_source(prov), "sample_rate_note": rate_note,
                           "unit_note": note, "display_centering": "channel mean removed; source unchanged",
                           "time_range_s": [s0, s1], "original_recording_time_range_s": [s0 + offset, s1 + offset],
                           "source_samples_in_range": seg.shape[1], "display_points_per_channel": [len(indices) for indices in display_indices],
                           "display_sampling": "block min/max; endpoints retained" if overview else "all samples",
                           "time_axis": "seconds from source start", "original_recording_offset_s": offset}, prov)

    if kind == "psd":
        rec = load_artifact(source_id) if source_id.startswith("art_") else None
        feature_source = rec is not None and rec.get("kind") == "features"
        channel_source = source_id
        if feature_source:
            wrec = load_artifact(rec["parent_artifact_ids"][0])
            window_payload = json.loads(resolve_inside_storage(wrec["uri"]).read_text(encoding="utf-8"))
            channel_source = window_payload["source"]
        if feature_source and start_s is None and end_s is None:
            z = np.load(resolve_inside_storage(rec["uri"]))
            if "psd_mean" not in z:
                raise ValueError("该 features artifact 不含 psd；请用 features 里包含 psd 或 bandpower 的产物")
            freqs, psd = z["psd_freqs"], z["psd_mean"]
            names = rec["summary"]["channel_names"]
            src, prov, parents = rec["source_manifest_ids"], rec["provenance"]["steps"], [source_id]
            bands = rec["summary"]["bands"]
            resolution = float(freqs[1] - freqs[0])
            wins = window_payload["windows"]
            tr = [wins[0]["start_s"], wins[-1]["end_s"]]
            sr = window_payload["sample_rate_hz"]
            nperseg = int(round(sr / resolution))
            offset = signal_time_offset(channel_source)
            window_note = f"{len(wins)} 个窗口的平均功率谱 · {wins[0]['start_s']:g}–{wins[-1]['end_s']:g} 秒"
            aggregation = "mean of feature-window PSDs"
            psd_settings = rec["summary"].get("measurement_settings", {}).get("welch", {})
        else:
            data, sr, names, prov, src = resolve_signal(channel_source)
            seg, s0, s1 = _time_slice(data, sr, start_s, end_s)
            if seg.shape[1] < 2:
                raise ValueError("Welch PSD 至少需要两个采样点；请增加时间范围")
            geometry = signal_metadata(channel_source)
            freqs, psd, psd_settings = welch_by_segments(data, sr, geometry["segments"], round(s0*sr), round(s1*sr))
            nperseg = psd_settings["nperseg"]
            parents, tr = ([source_id] if source_id.startswith("art_") else []), [s0, s1]
            if feature_source and channel_source.startswith("art_"):
                parents.append(channel_source)
            bands = {k: list(v) for k, v in BANDS["standard"].items()}
            resolution = sr / nperseg
            offset = signal_time_offset(channel_source)
            window_note = f"{s0:g}–{s1:g} 秒"
            aggregation = psd_settings["aggregation"]
        method_note = f"汉明窗 {nperseg / sr:g} 秒 · 重叠 {100 * (nperseg // 2) / nperseg:g}%"
        idx = _signal_channels(channel_source, names, channels)
        psd = psd[idx]
        names = [names[index] for index in idx]
        m = (freqs >= 0.5) & (freqs <= 60)
        if not m.any():
            raise ValueError("所选信号无法估计 0.5–60 Hz 频谱；请增加时间范围或确认采样率")
        fig, ax = figure(height=3.8, profile=profile, width=width)
        mean_psd = psd[:, m].mean(axis=0)
        if 1 < len(names) <= 6:
            for i, name in enumerate(names):
                ax.plot(freqs[m], psd[i, m], lw=.7 if profile == "publication" else .85, alpha=.85,
                        color=PALETTE[i], linestyle=LINE_STYLES[i % len(LINE_STYLES)], label=name)
        elif len(names) > 6:
            for row in psd[:, m]:
                ax.plot(freqs[m], row, lw=.45, alpha=.35, color="#97A4AF", zorder=1)
            low, high = np.percentile(psd[:, m], [25, 75], axis=0)
            ax.fill_between(freqs[m], low, high, color=PALETTE[0], alpha=.17,
                            label="通道四分位范围（25–75%）", linewidth=0, zorder=2)
        ax.plot(freqs[m], mean_psd, lw=.9 if profile == "publication" else 1.6, color=PALETTE[0] if len(names) > 6 else "#282D33",
                label="通道均值" if len(names) > 1 else f"{names[0]} 功率谱", zorder=3)
        use_log = bool(np.any(psd[:, m] > 0))
        if use_log:
            ax.set_yscale("log", nonpositive="mask")
            if profile == "publication":
                from matplotlib.ticker import FuncFormatter
                ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        else:
            ax.text(.5, .6, "所选时段内功率均为零", transform=ax.transAxes,
                    ha="center", fontsize=sizes["small"], color="#626870")
        ax.yaxis.grid(True, which="major")
        for bname, (lo, hi) in bands.items():
            partial_band = hi > float(freqs[m][-1])
            hi = min(hi, float(freqs[m][-1]))
            if hi > lo:
                color = BAND_COLORS.get(bname, "#71747A")
                ax.plot([lo, hi], [1.025, 1.025], transform=ax.get_xaxis_transform(), color=color,
                        lw=.9 if profile == "publication" else 2, clip_on=False, solid_capstyle="butt")
                ax.text((lo + hi) / 2, 1.06, BAND_LABELS[bname] + ("（部分）" if partial_band else ""), transform=ax.get_xaxis_transform(),
                        ha="center", va="bottom", fontsize=sizes["small"], color=color)
        ax.set_xlabel("频率（Hz）")
        ax.set_ylabel(f"功率谱密度（{_display_unit(prov)}²/Hz）")
        ax.set_title("功率谱密度（PSD）", loc="left", pad=30)
        ax.set_xlim(0, min(60, float(freqs[m][-1])))
        ax.legend(loc="upper right", ncol=2 if 3 < len(names) <= 6 else 1)
        note = unit_note(prov)
        caption = f"Welch 法 · {window_note}\n{method_note} · 频率分辨率 {resolution:g} Hz"
        if feature_source and (start_s is not None or end_s is not None):
            caption += "\n已按所选时段从特征对应的源信号重新计算"
        if len(names) > 6:
            caption += f"\n{len(names)} 通道 · 阴影表示通道间的四分位范围"
        if note:
            caption += "\n" + note
        rate_note = sampling_note(prov, sr)
        if rate_note:
            caption += "\n" + rate_note
        if offset:
            caption += f"\n当前信号起点对应原始记录第 {offset:g} 秒"
        _caption(fig, caption, profile, "\n".join(n for n in (note, rate_note) if n))
        channel_peaks = [{"index": i, "channel": name,
                          "peak_hz": round(float(freqs[m][np.argmax(psd[i, m])]), 2) if np.max(psd[i, m]) > 0 else None}
                         for i, name in enumerate(names)]
        return _save_plot(fig, "psd", {"kind": "psd", "source": source_id, "signal_source": channel_source,
                                     "start_s": tr[0], "end_s": tr[1], "channels": channels, "aggregation": aggregation, **render}, src, parents, tr,
                          {**render, "n_channels": len(names), "peak_hz": round(float(freqs[m][np.argmax(mean_psd)]), 2) if np.max(mean_psd) > 0 else None,
                           "channels_shown": names, "channel_peaks_shown": channel_peaks, "power_unit": amplitude_unit(prov) + "²/Hz",
                           "frequency_range_hz": [float(freqs[m][0]), float(freqs[m][-1])], "frequency_resolution_hz": resolution,
                           "summary_statistic": "arithmetic channel mean", "shading": "channel IQR, not confidence interval" if len(names) > 6 else None,
                           "unit_note": note, "scale": "log; zero values masked, positive PSD values retained" if use_log else "linear; zero power",
                           "amplitude_unit_source": amplitude_unit_source(prov), "sample_rate_note": rate_note,
                           "method": method_note, "analysis_interval": window_note,
                           "measurement_settings": {"welch": psd_settings},
                           "aggregation": aggregation, "time_range_s": tr,
                           "original_recording_time_range_s": [tr[0] + offset, tr[1] + offset],
                           "time_axis": "seconds from source start", "original_recording_offset_s": offset}, prov)

    if kind in ("bandpower_timeline", "timeline"):
        rec = load_artifact(source_id)
        if rec.get("kind") != "features":
            raise ValueError("bandpower_timeline 需要 features artifact（含 bandpower 或 de）")
        z = np.load(resolve_inside_storage(rec["uri"]))
        key = "bandpower" if "bandpower" in z else ("de" if "de" in z else None)
        if key is None:
            raise ValueError("features 里没有 bandpower 或 de")
        arr = z[key]  # [W, C, B]
        wrec = load_artifact(rec["parent_artifact_ids"][0])
        window_payload = json.loads(resolve_inside_storage(wrec["uri"]).read_text(encoding="utf-8"))
        available_range = wrec["time_range_s"]
        s0 = float(start_s) if start_s is not None else available_range[0]
        s1 = float(end_s) if end_s is not None else available_range[1]
        if s0 < available_range[0] or s0 >= s1 or s1 > available_range[1]:
            raise ValueError(f"时间范围 {s0:g}–{s1:g} s 超出已计算特征范围 {available_range[0]:g}–{available_range[1]:g} s；请按所需范围重新分窗计算")
        window_indices = [i for i, w in enumerate(window_payload["windows"]) if w["start_s"] >= s0 and w["end_s"] <= s1]
        if not window_indices:
            raise ValueError("所选时间范围没有完整特征窗口；请使用更细窗口或按该范围重新分窗计算")
        wins = [window_payload["windows"][i] for i in window_indices]
        arr = arr[window_indices]
        gaps = []
        covered_until = s0
        for w in wins:
            if w["start_s"] > covered_until:
                gaps.append([covered_until, w["start_s"]])
            covered_until = max(covered_until, w["end_s"])
        if covered_until < s1:
            gaps.append([covered_until, s1])
        geometry = signal_metadata(window_payload["source"])
        gaps.extend([[s["start_sample"] / geometry["sample_rate_hz"]] * 2 for s in geometry["segments"][1:]])
        t = np.array([(w["start_s"] + w["end_s"]) / 2 for w in wins])
        band_names = _components(rec["summary"], key)
        if band is not None and band not in band_names:
            raise ValueError(f"频带 {band} 不存在；可用 {band_names}")
        sel = [band] if band else band_names
        idx = _signal_channels(window_payload["source"], rec["summary"]["channel_names"], channels)
        series = {}
        for bname in sel:
            bi = band_names.index(bname)
            values = arr[:, idx, bi]
            series[bname] = np.nanmean(values, axis=1) if np.isfinite(values).any() else np.full(len(wins), np.nan)
        unit = amplitude_unit(rec["provenance"]["steps"])
        ylab_unit = _display_unit(rec["provenance"]["steps"]) + "²" if key == "bandpower" else "比特"
        metric_name = "频带功率" if key == "bandpower" else "差分熵"
        n = len(sel)
        fig, axes = figure(nrows=n, height=1.2 * n + 1.35, sharex=True, squeeze=False, profile=profile, width=width)
        axes = [axes[i, 0] for i in range(n)]
        scales = {}
        display_points = {}
        for ax, bname in zip(axes, sel):
            y = series[bname]
            color = BAND_COLORS.get(bname, "#666")
            positive = np.isfinite(y) & (y > 0)
            use_log = bool(key == "bandpower" and positive.any() and
                           positive[np.isfinite(y)].all() and np.nanmax(y) / np.nanmedian(y) > 50)
            scales[bname] = "log" if use_log else "linear"
            display_points[bname] = _window_curve(ax, t, y, gaps, lw=.7 if profile == "publication" else 1.2,
                                                   marker="o" if len(t) <= 40 else None, ms=2.5, color=color)
            if not np.isfinite(y).any():
                ax.text(.5, .5, "当前采样率下未计算该频带",
                        transform=ax.transAxes, ha="center", va="center", fontsize=sizes["small"], color="#626870")
            if use_log:
                ax.set_yscale("log")
                if profile == "publication":
                    from matplotlib.ticker import FuncFormatter
                    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
            ax.set_ylabel(f"{BAND_LABELS[bname]}\n（{ylab_unit}）")
            ax.yaxis.grid(True, which="major")
            ax.margins(y=.16)
            ax.set_xlim(s0, s1)
        axes[-1].set_xlabel("窗口中心距当前信号起点的时间（秒）")
        axes[0].set_title(f"{metric_name}随时间的变化", loc="left")
        note = unit_note(rec["provenance"]["steps"])
        offset = signal_time_offset(window_payload["source"])
        caption = f"{s0:g}–{s1:g} 秒 · {len(idx)} 通道均值 · {len(wins)} 个窗口 · 各频带独立纵轴"
        caption += f"\n窗长 {wrec['summary']['window_s']:g} 秒 · 步长 {wrec['summary']['stride_s']:g} 秒"
        if any(value == "log" for value in scales.values()):
            caption += " · 对数坐标：" + "、".join(BAND_LABELS[name] for name, scale in scales.items() if scale == "log")
        if gaps:
            caption += f"\n有 {sum(b - a for a, b in gaps):g} 秒未被完整分析窗口覆盖"
        if any(count < len(wins) for count in display_points.values()):
            caption += " · 按局部极值抽点展示"
        if key == "de":
            caption += "\n高斯差分熵 · 以 2 为对数底"
        if note:
            caption += "\n" + note
        rate_note = sampling_note(rec["provenance"]["steps"], window_payload["sample_rate_hz"])
        if rate_note:
            caption += "\n" + rate_note
        if offset:
            caption += f"\n当前信号起点对应原始记录第 {offset:g} 秒"
        unavailable = list(rec["summary"].get("unavailable_bands", {}))
        unavailable += [bname for bname, values in series.items() if not np.isfinite(values).any()]
        if unavailable:
            caption += "\n未计算的频带：" + "、".join(BAND_LABELS[name] for name in unavailable)
        _caption(fig, caption, profile, "\n".join(n for n in (note, rate_note) if n))
        summary = {**render, "bands": sel, "n_windows": len(wins), "metric": key, "layout": "stacked_by_band" if n > 1 else "single_band",
                   "yscale": scales, "channels_shown": [rec["summary"]["channel_names"][i] for i in idx],
                   "summary_statistic": "arithmetic channel mean", "unit_note": note,
                   "amplitude_unit_source": amplitude_unit_source(rec["provenance"]["steps"]), "sample_rate_note": rate_note,
                   "unavailable_bands": unavailable,
                   "time_axis": "window center, seconds from source start", "original_recording_offset_s": offset,
                   "time_range_s": [s0, s1], "original_recording_time_range_s": [s0 + offset, s1 + offset],
                   "window_evidence_time_range_s": [wins[0]["start_s"], wins[-1]["end_s"]],
                   "unrepresented_time_ranges_s": gaps, "window_index_range": [window_indices[0], window_indices[-1]],
                   "range_selection": "complete feature windows inside the display interval", "display_points_per_band": display_points,
                   "window_s": wrec["summary"]["window_s"], "stride_s": wrec["summary"]["stride_s"]}
        return _save_plot(fig, "bandpower_timeline", {"kind": "bandpower_timeline", "source": source_id, "band": band, **render,
                                                    "channels": channels, "start_s": s0, "end_s": s1}, rec["source_manifest_ids"],
                          [source_id], [s0, s1], summary, rec["provenance"]["steps"])

    if kind == "topomap":
        raise ValueError("topomap 尚未实现（需要电极坐标）；请改用 psd 或 bandpower_timeline")
    raise ValueError(f"未知图类型 {kind}；可用：raw, psd, bandpower_timeline")


# ================================================================ export

def _table_rows(record: dict) -> tuple[list[str], list[dict]]:
    path = resolve_inside_storage(record["uri"])
    if record["media_type"] == "text/csv":
        from eegagent.worker.text import read_text
        text, _ = read_text(path)
        reader = csv.DictReader(io.StringIO(text))
        return list(reader.fieldnames or []), list(reader)
    if record["media_type"] == "text/tab-separated-values":
        from eegagent.worker.text import read_text
        text, _ = read_text(path)
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        return list(reader.fieldnames or []), list(reader)
    if record["media_type"] != "application/json":
        raise ValueError("此表保存为数值张量；用 read_artifact(part=inputs) 或 run_python 读取")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"] if isinstance(payload, dict) else payload
    columns = payload.get("columns", []) if isinstance(payload, dict) else []
    if rows and isinstance(rows[0], list):
        rows = [dict(zip(columns, row)) for row in rows]
    if not columns:
        columns = list(dict.fromkeys(key for row in rows for key in row))
    return columns, rows


def export(kind: str, source_id: str) -> dict:
    if kind == "python":
        from eegagent.worker.recipes import export_python
        return export_python(source_id)
    if kind in ("signal_csv", "signal_edf"):
        return _export_signal(kind, source_id)
    rec = load_artifact(source_id)
    if kind in ("png", "svg", "pdf"):
        return _export_figure(kind, source_id, rec)
    if kind not in ("csv", "json"):
        raise ValueError("kind 必须是 csv / json / signal_csv / signal_edf / png / svg / pdf / python")
    art_id = make_artifact_id("export", VERSION, {"kind": kind, "source": source_id})
    path = artifact_payload_path(art_id, kind)
    rows = 0
    if rec["kind"] == "features":
        z = np.load(resolve_inside_storage(rec["uri"]))
        wrec = load_artifact(rec["parent_artifact_ids"][0])
        wins = json.loads(resolve_inside_storage(wrec["uri"]).read_text(encoding="utf-8"))["windows"]
        names = rec["summary"]["channel_names"]
        data_rows = []
        unit = amplitude_unit(rec["provenance"]["steps"])
        columns = ["window", "start_s", "end_s", "channel", "feature", "component", "value", "unit",
                   "original_start_s", "original_end_s", "epoch_index", "epoch_start_s", "epoch_end_s"]
        for feat in z.files:
            if feat in ("psd_mean", "psd_freqs"):
                continue
            arr = z[feat]
            if feat == "asymmetry":
                pairs = _components(rec["summary"], feat)
                for wi in range(arr.shape[0]):
                    for pi in range(arr.shape[1]):
                        value = float(arr[wi, pi]) if np.isfinite(arr[wi, pi]) else None
                        data_rows.append([wi, wins[wi]["start_s"], wins[wi]["end_s"], "", "asymmetry", pairs[pi], value, "1", *_coordinate_columns(wins[wi])])
                continue
            comps = _components(rec["summary"], feat)
            if feat == "hjorth":
                comps = ["activity", "mobility", "complexity"]
            if feat == "connectivity":
                comps = names
            for wi in range(arr.shape[0]):
                for ci in range(arr.shape[1]):
                    for k, comp in enumerate(comps):
                        value_unit = rec["summary"].get("feature_units", {}).get(feat, "1")
                        if feat == "bandpower" or (feat == "hjorth" and comp == "activity"):
                            value_unit = unit + "²"
                        elif feat == "de":
                            value_unit = "bit"
                        elif feat == "hjorth" and comp == "mobility":
                            value_unit = "per_sample"
                        elif feat == "band_stats" and k % 3 == 0:
                            value_unit = unit
                        value = float(arr[wi, ci, k]) if np.isfinite(arr[wi, ci, k]) else None
                        data_rows.append([wi, wins[wi]["start_s"], wins[wi]["end_s"], names[ci], feat, comp, value, value_unit,
                                          *_coordinate_columns(wins[wi])])
        rows = len(data_rows)
        if kind == "csv":
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                for row in data_rows:
                    writer.writerow([*row[:6], f"{row[6]:.17g}" if row[6] is not None else "", *row[7:]])
        else:
            path.write_text(json.dumps({"columns": columns, "rows": data_rows}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    elif rec["kind"] in ("prediction", "emotion_assessment"):
        payload = json.loads(resolve_inside_storage(rec["uri"]).read_text(encoding="utf-8"))
        if kind == "json":
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            score_names = payload.get("labels", []) if payload.get("output_kind") == "classification" else []
            dimensions = payload.get("dimensions", [])
            columns = ["window", "window_end", "start_s", "end_s", "epoch_index", "epoch_start_s", "epoch_end_s",
                       "original_start_s", "original_end_s", "original_time_ranges_s", "label", "assessment_status", "rationale",
                       "model_id", "model_version", "run_id", "score_kind", "calibration_status", "weight_sha256", "input_recipe",
                       "evaluation_status", "checkpoint_selection", "split_protocol",
                       "video_manifest_id", "video_start_s", "video_end_s", "sync_offset_s", "video_role",
                       "label_text", "valence", "arousal"]
            columns += ["score:" + name for name in score_names] + ["value:" + dim["name"] + " (" + dim["unit"] + ")" for dim in dimensions]
            evaluation = (payload.get("model") or {}).get("evaluation", {})
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                for row in payload["predictions"]:
                    writer.writerow([
                        row.get("window_index", row.get("window_start")), row.get("window_end"), row["start_s"], row["end_s"],
                        row.get("epoch_index"), *row.get("epoch_relative_time_range_s", [None, None]),
                        *row.get("original_time_range_s", [None, None]),
                        json.dumps(row.get("original_time_ranges_s", []), ensure_ascii=False),
                        row.get("label"), row.get("assessment_status"), row.get("rationale"),
                        payload.get("model_id"), payload.get("model_version"), payload.get("run_id"),
                        payload.get("score_kind"), payload.get("calibration", {}).get("status"),
                        payload.get("model", {}).get("weight_sha256"),
                        json.dumps(payload.get("model", {}).get("input_recipe"), ensure_ascii=False),
                        evaluation.get("status"), evaluation.get("checkpoint_selection"), evaluation.get("split_protocol"),
                        row.get("video_manifest_id"), *row.get("video_time_range_s", [None, None]),
                        payload.get("paired_video", {}).get("sync_offset_s"), payload.get("paired_video", {}).get("video_role"),
                        row.get("label_text"), row.get("valence"), row.get("arousal"),
                        *row.get("scores", []), *row.get("values", []),
                    ])
        rows = len(payload["predictions"])
    elif rec["kind"] == "table":
        columns, table_rows = _table_rows(rec)
        if kind == "json":
            path.write_text(json.dumps({"columns": columns, "rows": table_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                                  for key, value in row.items()} for row in table_rows)
        rows = len(table_rows)
    elif rec["kind"] == "windows":
        payload = json.loads(resolve_inside_storage(rec["uri"]).read_text(encoding="utf-8"))
        if kind == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["index", "start_s", "end_s", "start_sample", "end_sample", "original_start_s", "original_end_s",
                        "epoch_index", "epoch_start_s", "epoch_end_s"])
            for r in payload["windows"]:
                w.writerow([r["index"], r["start_s"], r["end_s"], r["start_sample"], r["end_sample"], *_coordinate_columns(r)])
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(buf.getvalue())
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        rows = len(payload["windows"])
    else:
        raise ValueError(f"暂不支持导出 kind={rec['kind']} 的产物")
    title, description = {
        "features": ("逐窗 EEG 特征", "按时间窗、通道和分量列出实际计算的特征值及单位。"),
        "windows": ("分析窗口表", "列出各分析窗口的采样点和时间范围，便于定位对应信号。"),
        "prediction": ("模型预测结果", "列出各时间窗的模型输出及其时间坐标。"),
        "emotion_assessment": ("情绪审阅记录", "保存基于实际特征的分段审阅、标签和判断依据。"),
        "table": ("分析数据表", "保存本次分析的数据行与列名。"),
    }[rec["kind"]]
    summary = {"title": rec["summary"].get("title", title), "description": rec["summary"].get("description", description),
               "rows": rows, "format": kind, "source": source_id}
    if rec["kind"] in ("prediction", "emotion_assessment"):
        summary.update({key: rec["summary"][key] for key in ("model_id", "model_version", "run_id", "score_kind", "calibration", "dimensions", "label_definitions")
                        if key in rec["summary"]})
    if rec["kind"] == "features":
        summary["features"] = [name for name in z.files if name not in ("psd_mean", "psd_freqs")]
    if rec["kind"] == "table":
        summary["columns"] = columns
    record = save_artifact({
        "artifact_id": art_id, "kind": "table", "uri": str(path), "media_type": "text/csv" if kind == "csv" else "application/json",
        "source_manifest_ids": rec.get("source_manifest_ids", []), "parent_artifact_ids": [source_id], "time_range_s": rec.get("time_range_s"),
        "summary": summary,
        **{key: rec[key] for key in ("signal", "analysis") if key in rec},
        "provenance": {"created_by": f"export@{VERSION}", "params": {"kind": kind, "source": source_id}, "steps": rec["provenance"]["steps"] + [f"export_{kind}"]},
    })
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}


def _coordinate_columns(window):
    return [*window.get("original_time_range_s", [None, None]), window.get("epoch_index"),
            *window.get("epoch_relative_time_range_s", [None, None])]


def _export_figure(kind: str, source_id: str, rec: dict) -> dict:
    if rec["kind"] != "plot":
        raise ValueError("png / svg / pdf 导出需要 plot 产物")
    representation = rec.get("representations", {}).get(kind)
    if representation is None:
        if kind == "png" and rec["media_type"] == "image/png":
            representation = {"uri": rec["uri"], "media_type": rec["media_type"]}
        else:
            raise ValueError(f"这张历史图未保存 {kind.upper()}；请重新绘图后导出，不能将位图伪装为矢量图")
    record = save_artifact({
        "artifact_id": make_artifact_id("export", VERSION, {"kind": kind, "source": source_id}),
        "kind": "report", "uri": representation["uri"], "media_type": representation["media_type"],
        "source_manifest_ids": rec["source_manifest_ids"], "parent_artifact_ids": [source_id],
        "time_range_s": rec.get("time_range_s"), "summary": {**rec["summary"], "format": kind, "source": source_id},
        **{key: rec[key] for key in ("signal", "analysis") if key in rec},
        "provenance": {"created_by": f"export@{VERSION}", "params": {"kind": kind, "source": source_id},
                       "steps": rec["provenance"]["steps"] + [f"export_{kind}"]},
    })
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}


# ================================================================ signal export (csv / edf)

def _export_signal(kind: str, source_id: str) -> dict:
    """导出整理后的信号本体：带通道名的 csv 或标准 EDF。source 可为 manifest 或信号类产物。"""
    from eegagent.worker.signals import resolve_signal

    data, sr, names, steps, mids = resolve_signal(source_id)
    geometry = signal_metadata(source_id)
    unit = amplitude_unit(steps)
    art_id = make_artifact_id("export", VERSION, {"kind": kind, "source": source_id})
    notes: list[str] = []
    stored_samples = int(data.shape[1])
    file_rate = float(sr)
    representations = {}
    if kind == "signal_csv":
        path = artifact_payload_path(art_id, "csv")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time_s", *names])
        for i in range(data.shape[1]):
            w.writerow([f"{i / sr:.17g}", *(f"{v:.17g}" for v in data[:, i])])
        with path.open("w", encoding="utf-8", newline="") as f:  # newline="" 防 Windows 下 \r\n 被二次转换出空行
            metadata = {"sample_rate_hz": float(sr), "channel_names": names, "channel_types": geometry["channel_types"],
                        "unit": unit.replace("µV", "uV").replace("未知单位", "unknown"),
                        "unit_source": amplitude_unit_source(steps), "processing_steps": steps,
                        "signal_kind": geometry["kind"], "segments": geometry["segments"]}
            f.write("# EFAgent metadata: " + json.dumps(metadata, ensure_ascii=False) + "\n")
            f.write(buf.getvalue())
        media = "text/csv"
    else:
        if unit != "µV":
            raise ValueError("EDF 电压导出需要物理幅值单位；归一化或未知单位的信号请导出 signal_csv")
        path = artifact_payload_path(art_id, "edf")
        edf_sr = _write_edf(path, data, sr, names, unit)
        file_rate = edf_sr
        samples_per_record, _, records = _edf_clock(data.shape[1], sr)
        stored_samples = samples_per_record * records
        if stored_samples != data.shape[1]:
            notes.append(f"EDF 文件含 {stored_samples-data.shape[1]} 个尾部补零采样；有效信号为 {data.shape[1]} 点。")
        if edf_sr != sr:
            notes.append(f"EDF 记录时钟采用 {edf_sr:g} Hz；来源采样率为 {sr:g} Hz。")
        media = "application/x-edf"
        metadata_path = artifact_payload_path(art_id, "json")
        metadata_path.write_text(json.dumps({"source_id": source_id, "signal": geometry,
            "valid_samples": int(data.shape[1]), "stored_samples": stored_samples,
            "file_sample_rate_hz": file_rate, "file_duration_s": stored_samples/file_rate,
            "padding_samples": stored_samples-int(data.shape[1]), "unit": unit}, ensure_ascii=False, indent=2), encoding="utf-8")
        representations = {"edf": {"uri": str(path), "media_type": media},
                           "json": {"uri": str(metadata_path), "media_type": "application/json"}}
    if unit != "µV":
        notes.append(f"幅值单位为{unit}（非 µV），接收方软件里的物理量纲按此理解")
    record = save_artifact({
        "artifact_id": art_id, "kind": "normalized_signal", "uri": str(path), "media_type": media,
        "source_manifest_ids": mids, "parent_artifact_ids": [source_id],
        "time_range_s": [0.0, round(data.shape[1] / sr, 3)],
        "signal": geometry, "analysis": analysis_context(source_id),
        **({"representations": representations} if representations else {}),
        "summary": {"format": kind, "n_channels": int(data.shape[0]), "n_samples": int(data.shape[1]),
                    "stored_samples": stored_samples, "file_duration_s": stored_samples / file_rate, "file_sample_rate_hz": file_rate,
                    "sample_rate_hz": float(sr), "channel_names": names, "unit": unit, "source": source_id, "notes": notes},
        "provenance": {"created_by": f"export@{VERSION}", "params": {"kind": kind, "source": source_id}, "steps": steps + [f"export_{kind}"]},
    })
    return {"status": "ok", "artifact": _public(record), "summary": record["summary"]}


def _edf_clock(n_samples: int, sr: float) -> tuple[int, str, int]:
    from fractions import Fraction
    from math import gcd
    clock = Fraction(str(sr)).limit_denominator(1000000)
    samples = gcd(n_samples, clock.numerator)
    duration = samples / sr
    text = f"{duration:.7g}"
    if len(text) > 8 or Fraction(text) != Fraction(samples * clock.denominator, clock.numerator):
        samples, text = clock.numerator, str(clock.denominator)
    return samples, text, (n_samples + samples - 1) // samples


def _edf_range(value: float, lower: bool) -> tuple[str, float]:
    from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
    for precision in range(7, 0, -1):
        with localcontext() as context:
            context.prec = precision
            context.rounding = ROUND_FLOOR if lower else ROUND_CEILING
            rounded = +Decimal(str(value))
            candidates = [format(rounded, "g"), format(rounded, "E")]
        candidates = [re.sub(r"e([+-])0+", r"e\1", text.lower()).replace("e+", "e") for text in candidates]
        text = min(candidates, key=len)
        if len(text) <= 8:
            return text, float(text)
    raise ValueError("该物理幅值范围无法写入 EDF 的 8 字节字段，请采用 signal_csv。")


def _write_edf(path, data: np.ndarray, sr: float, names: list[str], unit: str) -> float:
    """EDF int16 writer; rational record durations preserve fractional sampling rates."""
    from datetime import datetime
    n_ch, n_total = data.shape
    spr, duration_text, n_rec = _edf_clock(n_total, sr)
    record_duration = float(duration_text)
    pad = n_rec * spr - n_total
    sig = np.pad(data, ((0, 0), (0, pad))) if pad else data
    dim = "uV" if unit == "µV" else ""
    phys_min = np.nanmin(sig, axis=1).astype(float)
    phys_max = np.nanmax(sig, axis=1).astype(float)
    flat = phys_max <= phys_min
    phys_min[flat] -= 1.0
    phys_max[flat] += 1.0
    minima = [_edf_range(float(value), True) for value in phys_min]
    maxima = [_edf_range(float(value), False) for value in phys_max]
    phys_min = np.array([value for _, value in minima])
    phys_max = np.array([value for _, value in maxima])
    dig_min, dig_max = -32768.0, 32767.0
    scale = (dig_max - dig_min) / (phys_max - phys_min)
    dig = np.rint((sig - phys_min[:, None]) * scale[:, None] + dig_min).clip(dig_min, dig_max).astype("<i2")

    def field(v, w: int) -> bytes:
        return str(v).encode("ascii", "replace")[:w].ljust(w, b" ")

    # EDF 要求通道标签唯一；中文名转 ascii 后可能撞名，撞名加序号
    labels, seen = [], {}
    for n in names:
        base = (str(n).encode("ascii", "replace").decode() or "ch")[:14]
        seen[base] = seen.get(base, 0) + 1
        labels.append(base if seen[base] == 1 else f"{base[:12]}~{seen[base]}")
    now = datetime.now()
    hdr = [
        field("0", 8), field("EFAgent export", 80), field("signal", 80),
        field(now.strftime("%d.%m.%y"), 8), field(now.strftime("%H.%M.%S"), 8),
        field(256 * (n_ch + 1), 8), field("", 44), field(n_rec, 8), field(duration_text, 8), field(n_ch, 4),
    ]
    hdr.append(b"".join(field(x, 16) for x in labels))
    hdr.append(field("", 80) * n_ch)                                    # transducer
    hdr.append(b"".join(field(dim, 8) for _ in range(n_ch)))            # phys_dim
    hdr.append(b"".join(field(text, 8) for text, _ in minima))
    hdr.append(b"".join(field(text, 8) for text, _ in maxima))
    hdr.append(field(int(dig_min), 8) * n_ch)
    hdr.append(field(int(dig_max), 8) * n_ch)
    hdr.append(field("", 80) * n_ch)                                    # prefilter
    hdr.append(field(spr, 8) * n_ch)
    hdr.append(field("", 32) * n_ch)
    with open(path, "wb") as fh:
        fh.write(b"".join(hdr))
        for r in range(n_rec):
            fh.write(dig[:, r * spr:(r + 1) * spr].tobytes())
    return spr / record_duration


# ================================================================ read_artifact

def read_artifact(artifact_id: str, part: str = "summary", start: int = 0, limit: int = 20, channel: str | None = None) -> dict:
    rec = load_artifact(artifact_id)
    limit = max(1, min(int(limit), 200))
    start = max(0, int(start))
    if part == "summary":
        return {"status": "ok", "artifact": _public(rec)}
    public = _public(rec)
    context = {key: public[key] for key in ("artifact_id", "source_manifest_ids", "parent_artifact_ids", "signal", "analysis") if key in public}
    if part == "segments" and rec.get("signal"):
        sig = rec["signal"]
        segments = sig.get("segments", [])
        return {"status": "ok", **context, "part": "segments", "start": start, "total": len(segments),
                "rows": segments[start:start + limit]}
    if rec["kind"] == "windows":
        payload = json.loads(resolve_inside_storage(rec["uri"]).read_text(encoding="utf-8"))
        rows = payload["windows"][start:start + limit]
        return {"status": "ok", **context, "part": "windows", "start": start, "total": len(payload["windows"]), "rows": rows}
    if rec["kind"] in ("emotion_assessment", "prediction") and part == "predictions":
        payload = json.loads(resolve_inside_storage(rec["uri"]).read_text(encoding="utf-8"))
        rows = payload["predictions"][start:start + limit]
        return {"status": "ok", **context, "part": "predictions", "start": start, "total": len(payload["predictions"]), "rows": rows}
    if rec["kind"] == "table" and rec["summary"].get("result_type") == "transient_candidates":
        payload = json.loads(resolve_inside_storage(rec["uri"]).read_text(encoding="utf-8"))
        return {"status": "ok", **context, "part": "rows", "start": start, "total": len(payload["rows"]),
                "rows": payload["rows"][start:start + limit]}
    if rec["kind"] == "table" and rec["summary"].get("result_type") == "model_input_tensor" and part in ("inputs", "eeg_inputs", "video_inputs"):
        with np.load(resolve_inside_storage(rec["uri"]), allow_pickle=False) as archive:
            key = "inputs" if "inputs" in archive.files else "video_inputs" if part == "video_inputs" else "eeg_inputs"
            inputs = archive[key]
        wrec = load_artifact(rec["summary"]["windows_id"])
        wins = json.loads(resolve_inside_storage(wrec["uri"]).read_text(encoding="utf-8"))["windows"]
        names = rec["summary"]["channel_names"]
        ci = [name.lower() for name in names].index(channel.lower()) if channel else None
        rows = []
        for index in range(start, min(start + limit, len(inputs))):
            tensor = inputs[index, 0] if key == "inputs" else inputs[index]
            row = {"window": index, "start_s": wins[index]["start_s"], "end_s": wins[index]["end_s"],
                   "shape": list(tensor.shape), "mean": float(tensor.mean()), "std": float(tensor.std()),
                   "min": float(tensor.min()), "max": float(tensor.max())}
            if ci is not None and key != "video_inputs":
                row["samples"] = tensor[ci].tolist()
            if key == "eeg_inputs":
                row["bands"] = rec["summary"]["bands"]
            if key == "video_inputs":
                row["video_time_range_s"] = wins[index]["video_time_range_s"]
                row["frame_times_ms"] = rec["summary"]["video_preparation"]["frame_times_ms"][index]
            rows.append(row)
        sampling = {"sample_rate_hz": rec["summary"]["model_sample_rate_hz"]} if key == "inputs" else {
            "underlying_eeg_sample_rate_hz": rec["summary"]["model_sample_rate_hz"]} if key == "eeg_inputs" else {
                "candidate_fps": rec["summary"]["video_preparation"].get("candidate_fps"), "frames_per_window": inputs.shape[1]}
        return {"status": "ok", **context, "part": key, "unit": rec["summary"]["model_amplitude_unit"] if key == "eeg_inputs" else "RGB [0,1]" if key == "video_inputs" else "a.u.",
                **sampling, "channel": channel,
                "start": start, "total": len(inputs), "rows": rows}
    if rec["kind"] == "table" and part == "rows":
        columns, rows = _table_rows(rec)
        return {"status": "ok", **context, "part": "rows", "columns": columns, "start": start,
                "total": len(rows), "rows": rows[start:start + limit]}
    if rec["kind"] == "features":
        z = np.load(resolve_inside_storage(rec["uri"]))
        names = rec["summary"]["channel_names"]
        band_names = list(rec["summary"]["bands"].keys())
        feat = part if part in z.files else None
        if feat is None:
            return {"status": "error", "error": {"code": "bad_part", "message": f"part 必须是 summary 或 {z.files}", "recoverable": True}}
        arr = z[feat]
        sel = slice(start, start + limit)
        if feat in ("psd_mean", "psd_freqs"):
            freqs = z["psd_freqs"][sel]
            rows = [{"frequency_hz": float(freq)} for freq in freqs]
            if feat == "psd_mean":
                ci = [name.upper() for name in names].index(channel.upper()) if channel else None
                values = arr[ci] if channel else arr.mean(axis=0)
                for row, value in zip(rows, values[sel]):
                    row["power_density"] = float(value)
            return {"status": "ok", **context, "part": feat, "axis": "frequency_hz", "channel": channel or "mean_over_channels",
                    "aggregation": "arithmetic mean over selected feature windows; " + ("selected channel" if channel else "equal weight over EEG channels"),
                    "unit": rec["summary"]["amplitude_unit"] + "²/Hz" if feat == "psd_mean" else "Hz",
                    "start": start, "total": len(z["psd_freqs"]), "rows": rows}
        wrec = load_artifact(rec["parent_artifact_ids"][0])
        wins = json.loads(resolve_inside_storage(wrec["uri"]).read_text(encoding="utf-8"))["windows"]
        if channel:
            ci = [n.upper() for n in names].index(channel.upper())
            vals = arr[sel, ci].tolist() if arr.ndim == 3 else arr[sel].tolist()
        else:
            vals = np.nanmean(arr[sel], axis=1).tolist() if arr.ndim == 3 else arr[sel].tolist()
        rows = [{"window": start + i, **{key: value for key, value in wins[start + i].items()
                                          if key in ("start_s", "end_s", "segment_index", "pad_right_samples", "original_time_range_s", "epoch_index", "epoch_relative_time_range_s")},
                 "values": [round(float(v), 5) if np.isfinite(v) else None for v in np.atleast_1d(vals[i])]}
                for i in range(len(vals))]
        comps = _components(rec["summary"], feat)
        units = rec["summary"].get("feature_component_units", {}).get(feat)
        unit = rec["summary"].get("feature_units", {}).get(feat, "1")
        return {"status": "ok", **context, "part": feat, "channel": channel or "mean_over_channels", "components": comps,
                "unit": unit if units is None else "mixed; see component_units", "component_units": units or [unit] * len(comps),
                "aggregation": "within each window: " + ("selected channel" if channel else "arithmetic mean with equal weight over EEG channels"),
                "start": start, "total": arr.shape[0], "rows": rows}
    return {"status": "ok", "artifact": _public(rec), "note": "该产物没有可分页读取的内容"}
