"""clean_artifacts: EEGLAB-style artifact cleaning on MNE-Python implementations.

  ica_iclabel   ICA (Picard solver, extended-infomax objective) + ICLabel component classification; removes components
                whose top class is an artifact with probability ≥ threshold.  EEGLAB: runica + ICLabel + pop_icflag + pop_subcomp
  asr           Artifact Subspace Reconstruction (asrpy).                       EEGLAB: clean_rawdata 'BurstCriterion'
  bad_channels  PyPREP NoisyChannels (flat, deviation, HF noise, correlation, SNR, dropout, RANSAC), then spherical-spline
                interpolation, drop, or mark only.                              EEGLAB: clean_rawdata channel criteria / PREP

Each run writes a normalized_signal artifact (unless nothing changes) and a figure, and reports exactly what was removed.
Infeasible requests raise ValueError with the reason and the alternative, so the model can degrade instead of failing.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
from scipy import signal as sps

from eegagent.worker.channels import eeg_indices
from eegagent.worker.mne_bridge import standard_positions, to_raw
from eegagent.worker.processing import _public, save_signal
from eegagent.worker.progress import report
from eegagent.worker.signals import amplitude_unit, amplitude_unit_source, filter_bounds, events_for_source, resolve_channel_types, resolve_signal, signal_metadata, signal_time_offset
from eegagent.worker.store import artifact_exists, load_artifact, make_artifact_id

VERSION = "1.5"
ICLABEL_CLASSES = ["brain", "muscle artifact", "eye blink", "heart beat", "line noise", "channel noise", "other"]
ICLABEL_TEXT = {"brain": "脑源活动", "muscle artifact": "肌电", "eye blink": "眨眼", "heart beat": "心搏",
                "line noise": "工频干扰", "channel noise": "通道噪声", "other": "其他"}
DEFAULT_EXCLUDE = ["eye blink", "muscle artifact", "heart beat", "line noise", "channel noise"]
LABEL_ALIASES = {"eye": "eye blink", "eog": "eye blink", "blink": "eye blink", "muscle": "muscle artifact", "emg": "muscle artifact",
                 "heart": "heart beat", "ecg": "heart beat", "line": "line noise", "channel": "channel noise"}
METHODS = ("ica_iclabel", "asr", "bad_channels")


def clean_artifacts(source_id: str, method: str, *, exclude: list[str] | None = None, threshold: float = 0.8,
                    n_components: int | None = None, cutoff: float = 20.0, action: str = "interpolate",
                    ransac: bool | None = None, max_fit_s: float = 600.0, plot: bool = True) -> dict:
    method = (method or "").strip().lower()
    if method not in METHODS:
        raise ValueError(f"method 必须是 {', '.join(METHODS)} 之一")
    if action not in ("interpolate", "drop", "mark"):
        raise ValueError("action 必须是 interpolate、drop 或 mark")
    excl = [LABEL_ALIASES.get(str(x).strip().lower(), str(x).strip().lower()) for x in (exclude or DEFAULT_EXCLUDE)]
    params = {"source": source_id, "method": method}
    if method == "ica_iclabel":
        params.update(exclude=sorted(excl), threshold=float(threshold), n_components=n_components, max_fit_s=float(max_fit_s))
    elif method == "asr":
        params.update(cutoff=float(cutoff))
    else:
        params.update(action=action, ransac=ransac)
    art_id = make_artifact_id("clean_artifacts", VERSION, params)
    if artifact_exists(art_id):
        report("clean_cached", "读取已有清洗结果", method)
        return _cached(art_id)

    report("clean_load", "读取待清洗信号", method)
    data, sr, names, prov, src = resolve_signal(source_id)
    if amplitude_unit(prov) == "a.u.":
        raise ValueError("伪迹清理使用归一化前的信号；当前信号为任意单位，原幅值关系已改变")
    if not np.isfinite(data).all():
        raise ValueError("信号里有 NaN/Inf；先用 run_python 检查缺失段，或只截取完整的时间段")
    types = resolve_channel_types(source_id, names)
    ctx = {"art_id": art_id, "source_id": source_id, "sr": float(sr), "prov": prov, "src": src,
           "offset": signal_time_offset(source_id), "params": params, "plot": plot,
           "geometry": signal_metadata(source_id), "events": events_for_source(source_id)}
    if method == "ica_iclabel":
        return _ica(np.array(data, dtype=float), names, types, ctx, excl, float(threshold), n_components, float(max_fit_s))
    if method == "asr":
        return _asr(np.array(data, dtype=float), names, types, ctx, float(cutoff))
    return _bad_channels(np.array(data, dtype=float), names, types, ctx, action, ransac)


# ---------------------------------------------------------------- shared

def _cached(art_id: str) -> dict:
    rec = load_artifact(art_id)
    out = {"status": "ok", "cached": True, "artifact": _public(rec), "summary": rec.get("summary", {})}
    fig = (rec.get("summary") or {}).get("figure_id")
    if fig and artifact_exists(fig):
        out["artifacts"] = [_public(load_artifact(fig))]
    return out


def _finish(ctx: dict, data: np.ndarray, names: list[str], types: list[str], steps: list[str], summary: dict, figure) -> dict:
    sid = ctx["source_id"]
    report("clean_save_signal", "保存清洗后信号", f"{len(names)} 通道 · {data.shape[1]} 采样点")
    record = save_signal(ctx["art_id"], data, ctx["sr"], names, types, source_manifest_ids=ctx["src"],
                         parents=[sid] if sid.startswith("art_") else [], steps=ctx["prov"] + steps, summary=summary,
                         params=ctx["params"], created_by=f"clean_artifacts@{VERSION}",
                         signal_kind=ctx["geometry"]["kind"], segments=ctx["geometry"]["segments"], events=ctx["events"])
    out = {"status": "ok", "artifact": _public(record), "summary": record["summary"]}
    if figure is not None and ctx["plot"]:
        report("clean_save_figure", "保存清洗分析图", ctx["params"]["method"])
        fig_art = _save_figure(figure, ctx, record)
        record["summary"]["figure_id"] = fig_art["artifact_id"]
        from eegagent.worker.store import save_artifact

        save_artifact(record)
        out["artifacts"] = [fig_art]
        out["summary"] = record["summary"]
    elif figure is not None:
        import matplotlib.pyplot as plt

        plt.close(figure)
    return out


def _save_figure(fig, ctx: dict, record: dict) -> dict:
    from eegagent.worker.outputs import _save_plot

    kind = f"clean_{ctx['params']['method']}"
    res = _save_plot(fig, kind, {"kind": kind, "source": record["artifact_id"]}, ctx["src"], [record["artifact_id"]],
                     ctx.get("figure_time_range_s", record.get("time_range_s")),
                     {"method": ctx["params"]["method"], **ctx.get("figure_summary", {})}, record["provenance"]["steps"])
    return res["artifact"]


def _mpl():
    from eegagent.worker.outputs import _mpl as setup

    return setup()


# ---------------------------------------------------------------- ICA + ICLabel

def _ica_component_figure(ica, components, n_components, threshold):
    _mpl()
    fig = ica.plot_components(picks=[item["index"] for item in components], show=False)
    for ax, item in zip(fig.axes, components):
        state = " · 已剔除" if item["removed"] else " · 保留"
        ax.set_title(f"IC {item['index']} · {ICLABEL_TEXT[item['label']]} · 概率 {item['probability']:.2f}{state}", fontsize=8)
    fig.suptitle("ICA 成分头皮分布", fontsize=11, weight="normal")
    caption = (f"颜色表示成分空间权重（任意单位）· 标准电极模板\n"
               f"Picard 扩展信息最大化 · 平均参考 · 共 {n_components} 个成分 · 剔除阈值 {threshold:g}\n"
               "成分类别和概率来自 ICLabel 分类")
    fig._eegagent_caption = caption
    fig.supxlabel(caption, fontsize=8, color="#626870")
    return fig


def _ica(data, names, types, ctx, excl, threshold, n_components, max_fit_s) -> dict:
    import mne
    from mne_icalabel import label_components

    sr = ctx["sr"]
    eeg = eeg_indices(types)
    pos, _ = standard_positions([names[i] for i in eeg])
    rows = [i for i in eeg if names[i] in pos]
    n = len(rows)
    if n < 8:
        raise ValueError(f"本项目 ICA+ICLabel 执行器需要至少 8 个可定位 EEG 通道，当前为 {n} 个。"
                         "可改用 method=asr 或按时间段剔除伪迹；通道名未知时先 device_lookup 或请用户提供通道名。")
    if amplitude_unit_source(ctx["prov"]) != "confirmed" or amplitude_unit(ctx["prov"]) != "µV":
        raise ValueError("ICLabel 的功率谱输入需要已确认的电压尺度；本记录可先做 ASR、坏道检测和相对特征，提供原始单位后再做 ICLabel 分类")
    notes: list[str] = []
    skipped = [names[i] for i in eeg if i not in rows]
    if skipped:
        notes.append(f"{len(skipped)} 个 EEG 通道没有标准位置，未参与 ICA，原样保留：{', '.join(skipped[:8])}")
    epoched = ctx["geometry"]["kind"] == "epochs"
    report("ica_prepare", "准备 ICA 拟合数据", f"{n} 个 EEG 通道 · 平均参考")
    if epoched:
        from eegagent.worker.mne_bridge import source_epochs

        raw = source_epochs(ctx["source_id"]).pick(rows)
    else:
        raw, _ = to_raw(data[rows], sr, [names[i] for i in rows], ["eeg"] * n, segments=ctx["geometry"]["segments"])
    raw.set_eeg_reference("average", projection=False, verbose="ERROR")
    hi = min(100.0, sr / 2 - 1.0)
    existing_highpass, existing_lowpass = filter_bounds(ctx["prov"], ctx["geometry"].get("source_processing", {}))
    low = None if existing_highpass >= 1 else 1.0
    high = None if existing_lowpass is not None and existing_lowpass <= hi else hi
    report("ica_filter", "准备 ICA 拟合频带", f"已有高通 {existing_highpass:g} Hz；按需补充至 1–{hi:g} Hz")
    if epoched:
        n_fit_epochs = max(1, min(len(raw), int(max_fit_s * sr / len(raw.times))))
        filt = raw[:n_fit_epochs].copy()
        stop = n_fit_epochs * len(raw.times) / sr
    else:
        stop = float(min(raw.times[-1], max_fit_s))
        filt = raw.copy().crop(0, stop)
    if low is not None or high is not None:
        filt.filter(low, high, verbose="ERROR")
    rank = int(mne.compute_rank(filt, rank=None, proj=False, verbose="ERROR")["eeg"])
    if rank < 2:
        raise ValueError(f"ICA 拟合数据的有效秩为 {rank}，不能分解多个独立成分")
    n_comp = rank if n_components is None else int(n_components)
    if not 2 <= n_comp <= rank:
        raise ValueError(f"n_components={n_comp} 超出实际有效秩 {rank}；采用 2–{rank} 个成分或省略该参数")
    fit_highpass = max(existing_highpass, low or 0.0)
    fit_lowpass = min(existing_lowpass, high) if existing_lowpass is not None and high is not None else existing_lowpass or high
    if fit_highpass != 1 or fit_lowpass != 100:
        notes.append(f"本次成分分类输入通带 {fit_highpass:g}–{fit_lowpass:g} Hz；ICLabel 的训练输入为 1–100 Hz")
    decim = max(1, int(sr / (2.5 * fit_lowpass)))
    n_fit = int((len(filt) * len(filt.times) if epoched else filt.n_times) / decim)
    k = n_fit / float(n_comp ** 2)
    if k < 20:
        notes.append(f"本次拟合 {n_fit} 个抽取样本、{n_comp} 个成分，N/成分数²={k:.1f}")
    ica = mne.preprocessing.ICA(n_components=n_comp, method="picard", fit_params=dict(ortho=False, extended=True),
                                random_state=97, max_iter="auto")
    report("ica_fit", "分解 ICA 独立成分", f"{n_comp} 个成分 · {stop:g} 秒拟合数据")
    ica.fit(filt, decim=decim, verbose="ERROR")
    report("ica_classify", "识别 ICA 成分类别", "ICLabel 成分分类与概率")
    ic = label_components(filt, ica, method="iclabel")
    labels = [str(x) for x in ic["labels"]]
    probs = np.asarray(ic["y_pred_proba"], dtype=float)
    removed = [i for i, (lab, p) in enumerate(zip(labels, probs)) if lab in excl and p >= threshold]
    report("ica_apply", "应用 ICA 成分剔除", f"剔除 {len(removed)}/{n_comp} 个成分 · 阈值 {threshold:g}")
    cleaned = ica.apply(raw.copy(), exclude=removed, verbose="ERROR")
    out = data.copy()
    cleaned_data = cleaned.get_data()
    if epoched:
        cleaned_data = cleaned_data.transpose(1, 0, 2).reshape(n, data.shape[1])
    out[rows] = cleaned_data * 1e6
    evr = float(ica.get_explained_variance_ratio(filt, components=removed, ch_type="eeg")["eeg"]) if removed else 0.0
    borderline = [i for i, (lab, p) in enumerate(zip(labels, probs)) if lab in excl and 0.5 <= p < threshold]
    if borderline:
        notes.append(f"{len(borderline)} 个成分被判为伪迹但概率在 0.5–{threshold:g} 之间，未剔除：{borderline[:10]}")
    art_path = ica_path(ctx["art_id"])
    ica.save(art_path, overwrite=True, verbose="ERROR")
    summary = {
        "method": "ica_iclabel",
        "algorithm": "Picard（扩展 infomax 目标，等价 EEGLAB runica -extended）+ ICLabel",
        "n_channels_ica": n, "n_components": n_comp, "effective_rank": rank,
        "fit": {"highpass_hz": fit_highpass, "lowpass_hz": fit_lowpass, "decim": decim, "fit_seconds": round(stop, 1), "k": round(k, 1)},
        "source_processing": ctx["geometry"].get("source_processing", {}),
        "fit_filter_applied": {"highpass_hz": low, "lowpass_hz": high},
        "reference": "ICA 通道已转为平均参考",
        "exclude_labels": excl, "threshold": threshold,
        "removed": [{"index": i, "label": labels[i], "prob": round(float(probs[i]), 3)} for i in removed],
        "label_counts": dict(Counter(labels)),
        "removed_variance_ratio": round(evr, 4),
        "non_brain_components": [{"index": i, "label": labels[i], "prob": round(float(probs[i]), 3)}
                                 for i in range(n_comp) if labels[i] != "brain"][:30],
        "ica_solution_path": str(art_path),
        "notes": notes,
    }
    fig = None
    if ctx["plot"]:
        report("ica_figure", "绘制 ICA 成分头皮分布", "空间权重、ICLabel 类别与剔除状态")
        picks = removed[:12] or list(range(min(8, n_comp)))
        components = [{"index": i, "label": labels[i], "probability": float(probs[i]), "removed": i in removed} for i in picks]
        fig = _ica_component_figure(ica, components, n_comp, threshold)
        ctx["figure_time_range_s"] = [0.0, stop]
        ctx["figure_summary"] = {"components_shown": picks, "fit_time_range_s": [0.0, stop],
                                 "components": components,
                                 "n_components": n_comp, "removed_count": len(removed), "threshold": threshold,
                                 "aggregation": "independent component spatial weights over standard electrode positions",
                                 "original_recording_offset_s": ctx["offset"], "spatial_positions": "standard template; not measured positions",
                                 "interpretation": "Model component labels, not independently confirmed artifact sources"}
    return _finish(ctx, out, names, types, ["rereference_car", f"ica_iclabel_removed_{len(removed)}"], summary, fig)


def ica_path(art_id: str):
    from eegagent.worker.store import ensure_layout

    return ensure_layout() / "artifacts" / f"{art_id}-ica.fif"


# ---------------------------------------------------------------- ASR

def _asr(data, names, types, ctx, cutoff) -> dict:
    import asrpy

    sr = ctx["sr"]
    eeg = eeg_indices(types)
    if len(eeg) < 4:
        raise ValueError(f"ASR 不可行：只有 {len(eeg)} 个 EEG 通道（子空间重建依赖多通道协方差）。少通道设备请按时间段剔除伪迹，"
                         "或用 run_python 做阈值检测。")
    x = data[eeg].copy()
    notes: list[str] = []
    steps: list[str] = []
    segments = ctx["geometry"]["segments"]
    existing_highpass, _ = filter_bounds(ctx["prov"], ctx["geometry"].get("source_processing", {}))
    if existing_highpass == 0:
        sos = sps.butter(4, 0.5, btype="highpass", fs=sr, output="sos")
        for segment in segments:
            a, b = segment["start_sample"], segment["end_sample"]
            x[:, a:b] = sps.sosfiltfilt(sos, x[:, a:b], axis=1)
        steps.append("highpass_0.5")
        notes.append("本次 ASR 输入先做 0.5 Hz 高通，输出保留该处理")
    dur = x.shape[1] / sr
    if dur < 30:
        notes.append(f"只有 {dur:.0f} s 数据，ASR 用来校准的干净片段偏少，重建可能过强或不足")
    y = np.empty_like(x)
    for segment in segments:
        a, b = segment["start_sample"], segment["end_sample"]
        report("asr_calibrate", "校准 ASR 重构", f"{a / sr:g}–{b / sr:g} 秒 · cutoff {cutoff:g}")
        raw, _ = to_raw(x[:, a:b], sr, [names[i] for i in eeg], ["eeg"] * len(eeg), montage=None)
        asr = asrpy.ASR(sfreq=sr, cutoff=cutoff)
        asr.fit(raw)
        report("asr_transform", "重构 ASR 信号", f"{a / sr:g}–{b / sr:g} 秒")
        y[:, a:b] = asr.transform(raw).get_data() * 1e6
    diff = y - x
    unit, unit_source = amplitude_unit(ctx["prov"]), amplitude_unit_source(ctx["prov"])
    physical = unit == "µV" and unit_source == "confirmed"
    changed_fraction = float((np.abs(diff) > 1.0).any(axis=0).mean()) if physical else None
    input_rms = float(np.sqrt(np.mean(x ** 2)))
    relative_rms_change = float(np.sqrt(np.mean(diff ** 2)) / input_rms) if input_rms > 0 else None
    sec = max(1, round(sr))
    n_sec = int(np.ceil(x.shape[1] / sec))
    per_sec = np.array([np.sqrt(np.mean(diff[:, i * sec:(i + 1) * sec] ** 2)) for i in range(n_sec)])
    top = sorted(np.argsort(-per_sec)[:5].tolist())
    ch_rms = np.sqrt(np.mean(diff ** 2, axis=1))
    order = np.argsort(-ch_rms)[:5]
    summary = {
        "method": "asr", "cutoff": cutoff, "boundary_policy": "independent calibration and reconstruction per continuous segment",
        "n_segments": len(segments),
        "changed_fraction": round(changed_fraction, 4) if changed_fraction is not None else None,
        "amplitude_unit": unit, "amplitude_unit_source": unit_source,
        "relative_rms_change": relative_rms_change,
        "change_metric": {"baseline": "ASR input, including the initial highpass when applied",
                          "absolute_threshold": 1.0 if physical else None, "threshold_unit": "µV" if physical else None,
                          "relative_definition": "RMS(reconstructed - ASR input) / RMS(ASR input)"},
        "source_processing": ctx["geometry"].get("source_processing", {}),
        "most_changed_seconds": [{"start_s": float(i * sec / sr), "end_s": float(min((i + 1) * sec, x.shape[1]) / sr),
                                  "rms_change": float(per_sec[i])} for i in top if per_sec[i] > 0],
        "channel_rms_change_top5": {names[eeg[i]]: float(ch_rms[i]) for i in order},
        "notes": notes,
    }
    out = data.copy()
    out[eeg] = y
    fig = None
    if ctx["plot"]:
        report("asr_figure", "绘制 ASR 清洗前后对照", "选择重构变化较大的通道与时段")
        plt = _mpl()
        c = int(np.argmax(per_sec))
        a = max(0, min(c * sec - 4 * sec, x.shape[1] - 10 * sec))
        b = min(x.shape[1], a + 10 * sec)
        segment = next(s for s in segments if s["start_sample"] <= c * sec < s["end_sample"])
        a, b = max(a, segment["start_sample"]), min(b, segment["end_sample"])
        chs = order[: min(6, len(order))]
        t = np.arange(a, b) / sr
        from eegagent.worker.figures import figure, COLORS, unit_note, sampling_note

        fig, axes = figure(nrows=len(chs), height=.95 * len(chs) + 1.3, sharex=True, squeeze=False)
        for ax, i in zip(axes[:, 0], chs):
            ax.plot(t, x[i, a:b], lw=.7, color="#B2BBC3", label="ASR 处理前")
            ax.plot(t, y[i, a:b], lw=.9, color=COLORS[0], label="ASR 处理后")
            label_unit = "µV" if physical else "估计 µV" if unit == "µV" else "原始数值"
            ax.set_ylabel(f"{names[eeg[i]]}\n（{label_unit}）", fontsize=8)
            ax.yaxis.grid(True)
        axes[0, 0].legend(loc="upper right", ncol=2)
        axes[0, 0].set_title("ASR 清洗前后波形对照", loc="left")
        axes[-1, 0].set_xlabel("从当前信号起点计时（秒）")
        caption = (f"重构阈值 {cutoff:g} · 展示重构变化较大的通道和时段\n"
                   "用于查看处理差异的局部对照")
        if steps:
            caption += "\n两条波形均包含初始的 0.5 Hz 高通处理"
        note = unit_note(ctx["prov"])
        if note:
            caption += "\n" + note
        rate_note = sampling_note(ctx["prov"], sr)
        if rate_note:
            caption += "\n" + rate_note
        if ctx["offset"]:
            caption += f"\n当前信号起点对应原始记录第 {ctx['offset']:g} 秒"
        fig._eegagent_caption = caption
        fig.supxlabel(caption, fontsize=8, color="#626870")
        ctx["figure_time_range_s"] = [float(a / sr), float(b / sr)]
        ctx["figure_summary"] = {"channels_shown": [names[eeg[i]] for i in chs], "unit_note": note,
                                 "sample_rate_note": rate_note,
                                 "selection": "largest channel and interval reconstruction changes",
                                 "original_recording_offset_s": ctx["offset"], "time_axis": "seconds from source start",
                                 "comparison_preprocessing": steps}
    return _finish(ctx, out, names, types, steps + [f"asr_k{cutoff:g}"], summary, fig)


# ---------------------------------------------------------------- bad channels (PREP)

def _bad_channels(data, names, types, ctx, action, ransac) -> dict:
    from pyprep.find_noisy_channels import NoisyChannels

    sr = ctx["sr"]
    eeg = eeg_indices(types)
    if len(eeg) < 3:
        raise ValueError(f"坏道检测不可行：只有 {len(eeg)} 个 EEG 通道（相关性判据需要比较通道之间）。单通道设备只能看幅值与频谱是否异常。")
    eeg_names = [names[i] for i in eeg]
    pos, _ = standard_positions(eeg_names)
    raw, _ = to_raw(data[eeg], sr, eeg_names, ["eeg"] * len(eeg))
    all_positioned = len(pos) == len(eeg)
    use_ransac = bool(ransac) if ransac is not None else (all_positioned and len(eeg) >= 16)
    notes: list[str] = []
    if use_ransac and not all_positioned:
        use_ransac = False
        notes.append("RANSAC 需要所有通道都有标准位置，已关闭")
    elif not use_ransac:
        notes.append("未运行 RANSAC（需要全部通道有标准位置且 ≥ 16 通道）")
    by, bads_raw = {}, []
    for segment in ctx["geometry"]["segments"]:
        report("bad_channels_detect", "检查坏道", f"{segment['start_sample'] / sr:g}–{segment['end_sample'] / sr:g} 秒")
        piece = raw.copy().crop(segment["start_sample"] / sr, (segment["end_sample"] - 1) / sr)
        nd = NoisyChannels(piece, do_detrend=True, random_state=42, ransac=use_ransac)
        nd.find_all_bads(ransac=use_ransac)
        for criterion, detected in nd.get_bads(as_dict=True).items():
            if detected and criterion != "bad_all":
                by.setdefault(criterion, []).extend(detected)
        bads_raw.extend(nd.get_bads())
    by = {key: list(dict.fromkeys(value)) for key, value in by.items()}
    bads_raw = list(dict.fromkeys(bads_raw))
    name_of = dict(zip(raw.ch_names, eeg_names))
    bads = [name_of.get(b, b) for b in bads_raw]
    by = {k: [name_of.get(b, b) for b in v] for k, v in by.items()}
    act = action if bads else "none"
    if act == "interpolate":
        if not all_positioned:
            act = "drop"
            notes.append("部分通道没有标准位置，无法做球面样条插值，已改为删除坏道")
        elif len(bads) > 0.25 * len(eeg):
            act = "mark"
            notes.append(f"坏道 {len(bads)}/{len(eeg)} 超过 25%，插值不可靠，只标记不修改；请先检查电极与数据质量")
    summary = {"method": "bad_channels", "bad_channels": bads, "by_criterion": by, "ransac": use_ransac, "action": act,
               "n_eeg_channels": len(eeg), "notes": notes,
               "boundary_policy": "detect within each continuous segment; union channels detected in any segment"}
    if act in ("mark", "none"):
        summary["note"] = "没有检出坏道，信号未修改" if act == "none" else "只标记，信号未修改；需要时用 action=drop 删除或补充电极信息后插值"
        return {"status": "ok", "summary": summary}
    out, out_names, out_types = data, list(names), list(types)
    if act == "interpolate":
        raw.info["bads"] = bads_raw
        raw.interpolate_bads(reset_bads=True, mode="accurate", verbose="ERROR")
        out = data.copy()
        out[eeg] = raw.get_data() * 1e6
    else:
        keep = [i for i in range(len(names)) if not (types[i] == "eeg" and names[i] in bads)]
        out, out_names, out_types = data[keep], [names[i] for i in keep], [types[i] for i in keep]
    summary["n_channels_after"] = len(out_names)
    return _finish(ctx, out, out_names, out_types, [f"bad_channels_{act}_{len(bads)}"], summary, None)
