---
doc_id: dataset_amigos
title: AMIGOS 数据集规范
source: Miranda-Correa et al., AMIGOS arXiv2017作者稿（期刊卷期2021）；官方readme2024-12-17历史快照；TorchEEG AMIGOSDataset本地审查版本
version: 2026-10-12
domain: datasets
tasks: [emotion_recognition, data_understanding]
license: 需向 QMUL 申请；本文只记录公开规范
---

## 采集

- 40 名被试；EEG 用 Emotiv EPOC 14 通道（AF3 F7 F3 FC5 T7 P7 O1 O2 P8 T8 FC6 F4 F8 AF4），128 Hz；同时记录 ECG（Shimmer）、GSR、正面视频与 Kinect 深度视频。
- 两个实验：16 段短视频个人观看（40 人）；4 段长视频个人或 4 人小组观看（37 人，8/24/28 缺席）。实验人数与第三方读取器排除名单分别记录。
- 五项自评为 1–9，七种基本情绪可多选，不是默认七分类。TorchEEG 读取自评按 arousal、valence、dominance、liking、familiarity 排列；20 s 外部效价/唤醒标注为 −1 至 1，不可混同 trial 自评或套阈值 5。

原文：[采集与实验人数](https://arxiv.org/abs/1702.02510)；[自评](https://arxiv.org/abs/1702.02510)；[外部标注](https://arxiv.org/abs/1702.02510)。

## 预处理版格式

- 官方原始 EEG 为128 Hz，25列矩阵第4–17列为14 EEG道、单位µV；ECG原采集256 Hz、GSR128 Hz。预处理 `joined_data{1,YY}` 为 samples×17：前14 EEG，15/16为ECG Right/Left，17为GSR。EEG已按128 Hz、CAR、4–45 Hz处理，trial按Video_Number排序。
- `labels_selfassessment{1,YY}` 为1×12：arousal、valence、dominance、liking、familiarity、neutral、disgust、happiness、surprise、anger、fear、sadness。`labels_ext_anotation{1,YY}`（官方拼写）为ZZ×3：segment_index、valence、arousal，对应20 s片段；readme没有规定三标注者聚合算子，不能据形状推成平均或投票。
- 官方具体 EEG 处理表未明示 EOG 去除、基线扣除或标准化；下载页总述不代替逐信号规范。论文第10页另描述CAR、2 Hz高通、BSS、3–47 Hz PSD的特征基线分析，与发行4–45 Hz并存。版本和实际处理历史分别登记。
- TorchEEG AMIGOSDataset 默认跳过 9、12、21、22、23、24、33；这不是官方统一排除名单。它将前 5×128 点分为五个 1 s 块逐点平均，得到 14×128 的基线波形；是否扣除由实际 transform 决定，不是每通道对整段五秒求一个标量均值。记录版本、实际保留被试和试次。

原文：[论文的基线分析处理](https://arxiv.org/abs/1702.02510)；[AMIGOS预处理EEG：128Hz、CAR与4到45Hz带通](https://web.archive.org/web/20241217212428id_/http://www.eecs.qmul.ac.uk/mmv/datasets/amigos/readme.html)；[AMIGOS joined_data17列与labels_ext_anotation标签列顺序](https://web.archive.org/web/20241217212428id_/http://www.eecs.qmul.ac.uk/mmv/datasets/amigos/readme.html)。readme为2024-12-17官方历史快照；五秒短视频基线见原论文物理第5页。读取约定见TorchEEG `datasets/module/emotion_recognition/amigos.py`，本地LibEER reader表没有AMIGOS。

源码：[TorchEEG 默认排除名单](https://github.com/torcheeg/torcheeg/blob/87e595ba0d97a52ad4d8e36dbd956c655b4e1993/torcheeg/datasets/module/emotion_recognition/amigos.py)；[标签及五秒分块基线](https://github.com/torcheeg/torcheeg/blob/87e595ba0d97a52ad4d8e36dbd956c655b4e1993/torcheeg/datasets/module/emotion_recognition/amigos.py)。

## 如何识别

- 预处理版：`Data_Preprocessed_P01.mat`…`P40.mat`，含 `joined_data`（每 trial samples × 17：14 道 EPOC EEG + ECG 右/左 + GSR，128 Hz，已 CAR 与 4–45 Hz 带通）和 `labels_selfassessment`（每 trial 1 × 12）。
- 14 道 EEG 顺序 AF3 F7 F3 FC5 T7 P7 O1 O2 P8 T8 FC6 F4 F8 AF4（Emotiv EPOC）。看到这种文件名和 17 列结构即按 AMIGOS 读取，自评标签在同一 .mat 里。

## 与本项目的对应

- 通道顺序与 CAMER 公开 AMIGOS_CHANNEL_ORDER 对应。公开 recipe 为 128 Hz、14 通道、1 s 非重叠窗、五带 DE、每窗 4 帧同步面部 ROI与 four-class/five_fold_cross_trial。camer_amigos_v1 目前未登记可执行权重，能否运行以 list_models 为准。
- 通用设备映射到该模型需要全部 14 个 EPOC 位置；只有 4 通道或缺前额通道的设备不可映射。
