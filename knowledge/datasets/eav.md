---
doc_id: dataset_eav
title: EAV 数据集规范（EEG-Audio-Video）
source: Lee M-H. et al. 2024, "EAV: EEG-Audio-Video Dataset for Emotion Recognition in Conversational Contexts", Scientific Data 11:1026; github.com/nubcico/EAV
version: 2026-10-12
domain: datasets
tasks: [emotion_recognition, data_understanding]
license: 论文 CC BY 4.0；数据按其发布条款
---

## 采集

- 42 名被试，基于提示的对话场景，五种情绪：neutral、anger、happiness、sadness、calmness；每人 200 次交互（听 100 次、说 100 次），共 8400 次。
- EEG：Brain Products BrainAmp，30 通道 Ag/AgCl，500 Hz，乳突参考，AFz 接地；电极顺序 Fp1 Fp2 F7 F3 Fz F4 F8 FC5 FC1 FC2 FC6 T7 C3 Cz C4 T8 CP5 CP1 CP2 CP6 P7 P3 Pz P4 P8 PO9 O1 Oz O2 PO10。
- 音频与面部视频同步记录。

原文：[EAV 采集、30通道500Hz与参考](https://www.nature.com/articles/s41597-024-03838-4)。

## 发布格式

- 处理后 EEG：200 instances × 10000 time points（20 s × 500 Hz）× 30 channels；标签 one-hot 200 × 10（5 情绪 × 听/说两种任务）。基线实验只用说话试次（音频只覆盖说话）。

原文：[发布形状与标签](https://www.nature.com/articles/s41597-024-03838-4)；[论文基线模型评估](https://www.nature.com/articles/s41597-024-03838-4)。这里“基线实验”不是刺激前中性 EEG 基线。

## 如何识别

- EEG MAT 存为 10000 点 × 30 道 × trial（500 Hz，每 trial 20 s，原数字尺度），同名 `*_label.mat` 为 10 × trial 的 one-hot（5 情绪 × 听/说）。
- 30 道顺序见上；乳突参考。看到这种形状与标签 sidecar 即按 EAV 读取，说话/聆听任务从标签行区分。

## 与本项目的对应

- EAV 适配器路线（见 scripts/remote/）：CAMER Fixed 与 EEGNet 都接收完整 20 s speaking 原始 trial。CAMER 远端做 detrend、50 Hz 陷波、0.5–45 Hz 带通、MAD 裁剪、降到 128 Hz，逐 1 s 五频带 DE（γ 上限 45 Hz）经 LDS 后每 5 s 取均值，30 道后补 2 行零，每 5 s 取 6 帧原始被试画面；输出 neutral/sad/anger/happy/calm 五类。直接把原始 manifest 交给 run_model，不先做通用滤波或重参考。
- 通用设备映射需要实际确认的电极位置；缺失通道只有训练契约明确支持 mask 时才可按对应策略处理，不能给未声明支持的 CAMER 模型自行补零或 mask。
