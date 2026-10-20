"""Device database for device_lookup. Deterministic: no inference from signal statistics.

Sources: vendor documentation for default electrode placements. Users must confirm; a lookup result is
reported with source=device_lookup and only becomes user_confirmed after the user says so.
"""

from __future__ import annotations

DEVICES: list[dict] = [
    {"vendor": "OpenBCI", "model": "Ganglion", "n_channels": 4, "sampling_rate_hz": 200, "unit": "uV",
     "default_layout": ["Fp1", "Fp2", "O1", "O2"], "reference": "ear_clip", "notes": "默认布局可自定义；用户必须确认实际电极位置"},
    {"vendor": "OpenBCI", "model": "Cyton", "n_channels": 8, "sampling_rate_hz": 250, "unit": "uV",
     "default_layout": ["Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"], "reference": "ear_clip (SRB2)",
     "notes": "布局以实际装配确认；GUI 与 BrainFlow 已缩放 EXG 通常为 µV。仅原始 ADS1299 计数在4.5V参考/增益24时乘4500000/24/(2^23-1)，不能重复缩放"},
    {"vendor": "OpenBCI", "model": "Cyton+Daisy", "n_channels": 16, "sampling_rate_hz": 125, "unit": "uV",
     "default_layout": ["Fp1", "Fp2", "F3", "F4", "F7", "F8", "T7", "T8", "C3", "C4", "P7", "P8", "P3", "P4", "O1", "O2"],
     "reference": "ear_clip (SRB2)", "notes": "Ultracortex Mark IV 官方默认 16 通道位置（顺序以 GUI 通道设置为准）；16 通道时采样率 125 Hz"},
    {"vendor": "Emotiv", "model": "EPOC / EPOC+ / EPOC X", "n_channels": 14, "sampling_rate_hz": 128, "unit": "uV",
     "default_layout": ["AF3", "F7", "F3", "FC5", "T7", "P7", "O1", "O2", "P8", "T8", "FC6", "F4", "F8", "AF4"],
     "reference": "CMS/DRL at P3/P4 (or mastoids)", "notes": "与 AMIGOS 数据集通道顺序一致；128 或 256 SPS；硬件带宽 0.2–45 Hz，内置 50/60 Hz 数字陷波；14-bit LSB 0.51 µV"},
    {"vendor": "Emotiv", "model": "Insight", "n_channels": 5, "sampling_rate_hz": 128, "unit": "uV",
     "default_layout": ["AF3", "AF4", "T7", "T8", "Pz"], "reference": "CMS/DRL left mastoid", "notes": ""},
    {"vendor": "InteraXon", "model": "Muse 2 / Muse S", "n_channels": 4, "sampling_rate_hz": 256, "unit": "uV",
     "default_layout": ["TP9", "AF7", "AF8", "TP10"], "reference": "Fpz (CMS/DRL)", "notes": "额头与耳后电极；Mind Monitor 导出 CSV 原始列为 µV；不适合需要中央/顶区通道的分析"},
    {"vendor": "NeuroSky", "model": "MindWave", "n_channels": 1, "sampling_rate_hz": 512, "unit": "uV",
     "default_layout": ["Fp1"], "reference": "A1 ear clip", "notes": "单通道额头 EEG；只能做 alpha/theta 等单通道频谱分析"},
    {"vendor": "BioSemi", "model": "ActiveTwo 32", "n_channels": 32, "sampling_rate_hz": 512, "unit": "uV",
     "default_layout": ["FP1", "AF3", "F3", "F7", "FC5", "FC1", "C3", "T7", "CP5", "CP1", "P3", "P7", "PO3", "O1", "OZ", "PZ",
                        "FP2", "AF4", "FZ", "F4", "F8", "FC6", "FC2", "CZ", "C4", "T8", "CP6", "CP2", "P4", "P8", "PO4", "O2"],
     "reference": "CMS/DRL common-mode circuit; analysis reference is separate", "notes": "BDF单位与刻度由各通道头提供；MNE get_data EEG为V，to_data_frame默认EEG已转µV；采样率/布局按实际记录"},
    {"vendor": "Brain Products", "model": "BrainAmp (EAV 30ch)", "n_channels": 30, "sampling_rate_hz": 500, "unit": "uV",
     "default_layout": ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "FC5", "FC1", "FC2", "FC6", "T7", "C3", "Cz", "C4", "T8",
                        "CP5", "CP1", "CP2", "CP6", "P7", "P3", "Pz", "P4", "P8", "PO9", "O1", "Oz", "O2", "PO10"],
     "reference": "mastoids", "notes": "EAV 数据集采集配置"},
    {"vendor": "Neuroscan", "model": "SynAmps2 (64ch Quik-Cap)", "n_channels": 64, "sampling_rate_hz": 1000, "unit": "uV",
     "default_layout": [], "reference": "vertex", "notes": "CNT 文件含通道名，通常无需查表"},
    {"vendor": "g.tec", "model": "g.Nautilus 8", "n_channels": 8, "sampling_rate_hz": 250, "unit": "uV",
     "default_layout": ["Fz", "C3", "Cz", "C4", "Pz", "PO7", "Oz", "PO8"], "reference": "right earlobe", "notes": ""},
    {"vendor": "Unicorn", "model": "Hybrid Black", "n_channels": 8, "sampling_rate_hz": 250, "unit": "uV",
     "default_layout": ["Fz", "C3", "Cz", "C4", "Pz", "PO7", "Oz", "PO8"], "reference": "mastoids", "notes": ""},
]


def device_lookup(vendor: str | None = None, model: str | None = None, n_channels: int | None = None,
                  sampling_rate_hz: float | None = None) -> dict:
    v = (vendor or "").strip().lower()
    mo = (model or "").strip().lower()
    scored: list[tuple[int, dict]] = []
    for d in DEVICES:
        score = 0
        if v and v in d["vendor"].lower():
            score += 3
        if mo and (mo in d["model"].lower() or d["model"].lower() in mo):
            score += 3
        if n_channels and d["n_channels"] == int(n_channels):
            score += 2
        if sampling_rate_hz and abs(d["sampling_rate_hz"] - float(sampling_rate_hz)) < 1e-6:
            score += 1
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    matches = [dict(d, match_score=s) for s, d in scored[:5]]
    return {
        "status": "ok",
        "matches": matches,
        "note": "匹配结果来自设备默认布局，必须由用户确认实际电极位置后才能作为 user_confirmed 写入记忆" if matches
        else "设备库无匹配；请向用户询问设备型号和每个通道的电极位置",
    }
