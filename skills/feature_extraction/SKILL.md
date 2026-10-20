---
name: feature_extraction
description: 提取并导出窗口级 EEG 特征与多文件特征总表。适用于频带功率、差分熵、Hjorth、谱熵、不对称及明确的时域或连接性特征需求。
modalities: [eeg]
allowed-tools: [segment_signal, extract_features, read_artifact, export, run_python, save_to_workspace]
---

# 特征提取

## 默认做法

1. `segment_signal`：频谱类特征 4 s 窗、2 s 步长；情绪模型风格的 DE 用 1 s 不重叠窗；尾部不足一窗默认丢弃（partial=drop）。清洗后用处理结果的 artifact_id 作为 source_id。
2. `extract_features`：按问题选择 bandpower、de、hjorth、spectral_entropy、asymmetry、rms、line_length 等；只汇总 EEG 通道。
3. `export(kind=csv)` 得到逐窗表；多文件合并用 run_python + `eeg.call`，总表保留文件、被试/试次、通道、窗口起止时间、单位和处理版本。写回工作区用 `save_to_workspace`。

## 各特征的口径

- 频带功率：Welch PSD 在频带内梯形积分，单位为振幅单位的平方（µV²）；相对功率的分母见 relative_bandpower_basis。
- 差分熵：`de_method` 决定滤波与方差实现，`bands` 决定频带。默认 `de_method=libeer_filtfilt`、`bands=standard`：0.5–4、4–8、8–13、13–30、30–50 Hz；3 阶 Butterworth 零相位，½·log2(2πe·var)，ddof=1，在每个窗内滤波。要用 LibEER 风格的 α 8–14、β 14–30，明确传 `bands=camer_5band`。`torcheeg_var` 使用其专属四频带与单向滤波。采用产物中的 de_bands、de_method 和 de_filter 复核；CAMER 远端的整 trial 滤波与 LDS 是另一条输入链，见 knowledge/methods/differential_entropy.md。
- Hjorth：用逐采样差分计算，activity 单位为振幅平方，mobility 为每采样，complexity 无量纲。
- 不对称：FAA = ln(右 α) − ln(左 α)，只有 F3/F4、F7/F8、AF3/AF4 算额叶不对称；其他左右对是相应脑区的半球差。
- connectivity 是逐窗 Pearson 通道相关；wPLI、PPC 等相位类指标用 eeg_toolboxes。
- 时域：RMS 去均值；line_length 为每采样间隔的平均绝对差；zero_crossing_rate 为每秒次数。

## 交付

逐窗特征表（含时间与单位）、必要的汇总，以及来源映射。保留实际 n_windows、尾部丢弃/补齐策略与未覆盖时长；partial=drop 的尾部不写成已被逐窗特征覆盖。大数组留在产物里，按需 `read_artifact` 读取相关部分。单个特征是测量值；情绪判断转 emotion_recognition，条件比较按独立实验单位聚合。
