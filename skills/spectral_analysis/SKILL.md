---
name: spectral_analysis
description: 分析 EEG 功率谱、频带功率、谱峰与随时间变化。适用于节律、主频、频谱比较及频带趋势问题。
modalities: [eeg]
allowed-tools: [preprocess, segment_signal, extract_features, plot, read_artifact, run_python, export]
---

# 频谱分析

## 默认做法

- 整体谱：`plot(kind=psd)`，summary 的 peak_hz 与 channel_peaks_shown 给出总体和各通道峰频。需要逐频点 PSD 时，对分窗结果 `extract_features(features=[psd])`，再对返回的 features 产物 `read_artifact(part=psd_mean, channel=...)`；图像产物本身不含可分页的频谱数组。
- 随时间变化：`segment_signal` 4 s 窗、2 s 步长起步，再 `extract_features(bandpower, spectral_entropy, ...)`；时间线用 `plot(kind=bandpower_timeline)`，概览主图用 `plot(kind=overview, source_id=features_id)`。
- 工具内部 Welch 子段约 2 s、Hamming 窗、50% 重叠；未补零时频率格点 = fs / nperseg（2 s 子段即 0.5 Hz）。外层 4 s 窗决定时间覆盖，不决定频率分辨率。
- 标准频带（bands=standard）：delta 0.5–4、theta 4–8、alpha 8–13、beta 13–30、gamma 30–50 Hz；CAMER 五频带（bands=camer_5band）的 alpha 为 8–14、beta 为 14–30 Hz。

## 判读

- 主峰：报告峰频、所在通道和时段，以及与其他通道或时段的倍数。闭眼时枕区 α 增强、睁眼抑制是常见状态差异，说明时结合空间分布和实验记录。
- 需要区分周期峰与 1/f 背景、或比较峰的高度时做频谱参数化（FOOOF/specparam：输入线性频率和线性功率，内部取 log），报告拟合范围、峰参数和误差；方法见 knowledge/methods/periodic_aperiodic_spectral_analysis.md。
- 高频升高先看颞区、额区波形排除肌电；50/60 Hz 尖峰与相邻频率背景对照判断工频。
- PSD 单位是功率密度（如 µV²/Hz），频带功率是积分（µV²），两者分开写。
- 要定位节律的起止时间：先用 4 s 窗粗定位，再在保留前后上下文的连续信号上做目标频带滤波与 Hilbert 包络，按 0.5–1 s 输出步长细查，并与原波形对照。阈值来自本次基线或用户给定标准，保存阈值、连续超阈区间与边界规则；短步长是包络的报告粒度，频率分辨能力仍取决于观测长度与滤波器。

## 交付

主要节律或谱峰、对应通道、出现时段和支持读数，完整的特征表与频谱图。投稿图用 profile=publication 与 single/wide 宽度。情绪相关解释转 emotion_recognition；条件比较的统计按独立实验单位做（见 compare_conditions）。
