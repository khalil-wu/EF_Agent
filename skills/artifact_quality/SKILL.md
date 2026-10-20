---
name: artifact_quality
description: 检查 EEG 质量并按证据处理坏道、工频和伪迹，比较处理前后结果。适用于单记录去噪、批量质量筛查、文件排除与异常时段定位。
modalities: [eeg]
allowed-tools: [workspace_quality, workspace_files, inspect_record, detect_transients, clean_artifacts, preprocess, plot, read_artifact, run_python]
---

# 质量与伪迹

## 先看什么

- 单条记录：`inspect_record(quality_scope=full)` 得到逐通道 RMS、峰峰值、平坦比例、极值重复和工频占比；同一时段画波形和 PSD 对照。局部问题传 start_s/end_s/channels。
- 整个文件夹：`workspace_quality` 一次覆盖全部未排除文件和试次，并保存逐文件 CSV。
- 开头初筛（quality_scope=initial）只代表前几秒，结论要写明检查范围。

## 默认处理顺序

1. 工频：PSD 在 50 Hz（中国、欧洲）或 60 Hz（美洲）及谐波处有尖峰时陷波，频率不超过 Nyquist。
2. 坏道：`clean_artifacts(method=bad_channels)` 使用 PyPREP 判据（平坦、幅值偏差、高频噪声、相关、信噪比、RANSAC；RANSAC 需要 ≥16 道且都有标准位置）。默认球面样条插值；电极位置不可靠时用 mark 或 drop。
3. 重参考：坏道处理后再做平均参考，避免坏道污染参考。
4. 突发大伪迹（运动、电极弹跳）：有足够的多通道连续数据和低伪迹参考时考虑 `method=asr`，cutoff 默认 20；20–30 来自 Chang 2020 的模拟驾驶记录研究，按本次前后检查调整。单位已确认时 changed_fraction 表示任一通道改动 >1 µV 的采样时点占比；推断/原生数值尺度用 relative_rms_change 表示重构差异 RMS/输入 RMS，绝对比例为 null。
5. 眼动、肌电、心电等刻板伪迹：`method=ica_iclabel`，本执行器需要 ≥8 道有位置的 EEG 和已确认电压尺度；默认成分数采用实际有效秩。工具按已有滤波事实补充拟合副本所需的频带，把成分剔除应用到原数据；ICLabel 概率 ≥0.8 的所选类别成分被剔除。分类概率是模型输出，采用哪条处理结果由同范围波形/特征复检决定。

何时偏离：少于 8 道或几十秒的短记录优先用波形、局部指标与时段排除；ASR 在本项目至少需要 4 个 EEG 通道，短记录也存在校准数据不足的问题。先检查源数据是否已按发行协议清洗，按残留问题决定新增处理。用户给了判据或流程时采用该方案。

ICLabel 的训练频段是 1–100 Hz；已被限制为 4–45 Hz 或低采样率的记录，默认先采用适用的波形/频谱评阅与 ASR。用户明确研究 ICA 时保留成分分布、实际拟合带宽与分类输出，按成分时间过程、频谱和同范围处理前后结果决定是否采用该清洗版本。

## 判读要点

- 前额 1–4 Hz 大幅慢波伴随空间衰减提示眨眼；F7/F8 反相方波提示水平眼动；颞区、额区 >20 Hz 宽带爆发提示肌电；全通道同时饱和提示运动或线缆。
- 单个尖峰或瞬态用 `detect_transients` 列出时间、幅值和半峰宽，再回看波形确认性质。
- “坏道”“排除”这类结论要写出所用判据（PyPREP 哪一项、阈值多少，或用户/协议给的标准）。
- measured 是检查完成数。没有验收标准时，回答“2/2 条记录已检查，未检出平坦道，工频占比为……”，把局部可疑项直接列出；合格数量来自实际给定判据的判定。

## 交付

给出：发现的问题（通道、时段、指标值）、采用的处理与参数、修改了多少（坏道数、剔除成分及其 ICLabel 类别和概率、ASR 改动比例）、同通道同时段同指标的前后对比，以及质量表和处理后信号的产物。批量任务保留逐文件、逐试次结果；按用户标准排除文件用 `workspace_files(action=exclude)`，它只改变分析集合。

MNE 实现细节按需 read_skill(name=eeg_toolboxes, reference=api.md)；方法出处用 search_knowledge 查对应论文。
