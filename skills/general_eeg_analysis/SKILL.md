---
name: general_eeg_analysis
description: 探索 EEG 波形、节律和时间变化，组合已有工具完成没有指定专门方法的信号分析。适用于单记录概览与采集批次探索。
modalities: [eeg]
allowed-tools: [analyze_signal, inspect_record, preprocess, segment_signal, extract_features, plot, read_artifact, run_python]
---

# EEG 探索

## 默认做法

- 单条记录的概览（质量、波形、PSD、逐窗特征和 CSV）用 `analyze_signal` 一次完成；它不做清洗。需要清洗、自定义特征、全部试次或多个文件时，组合原子工具或 run_python。
- 先看全段：波形和 PSD 找出主要节律与异常时段，再对关键通道、关键时段画局部图。用户指定了时间范围时，切窗、绘图和比较都用同一范围。
- 逐窗特征从 4 s 窗、2 s 步长开始，按事件时长和需要的时间分辨率调整。长记录保存完整逐窗结果，按需分页读取。

## 判读

- 用数值描述节律：峰频、通道、时段、相对其他通道或时段的倍数。通道平均只说明总体趋势，有空间差异时按通道或脑区报告。
- 额叶不对称只用额叶左右电极对；没有 F3/F4 等时就说明这份记录不能算 FAA。
- 统计比较的单位是被试、试次或独立记录，重叠窗口不是独立样本。

## 何时转向其他 skill

情绪问题加载 emotion_recognition，睡眠问题加载 sleep_analysis，清洗与坏道加载 artifact_quality，事件平均、时频、连接性加载 eeg_toolboxes，自定义统计或批量加载 scripting。

## 交付

先给主要发现和必要的图，再交付特征表并说明覆盖的文件、试次和时间范围。用户补充采集说明或纠正时，用新事实重新核对结果。
