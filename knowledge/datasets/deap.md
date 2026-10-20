---
doc_id: dataset_deap
title: DEAP 数据集规范
source: Koelstra S. et al. 2012, "DEAP: A Database for Emotion Analysis using Physiological Signals", IEEE Trans Affect Comput 3(1):18-31; DEAP 官方 data_preprocessed_python 说明
version: 2026-10-15
domain: datasets
tasks: [emotion_recognition, data_understanding, feature_extraction]
license: 数据需向 QMUL 申请 EULA；本文只记录公开规范
---

## 采集

- 32 名被试（16 男 16 女，19–37 岁），每人观看 40 段 1 分钟音乐视频；32 通道 EEG 以 512 Hz 记录，原论文采集描述另有 13 个外周生理信号；22 人有正面面部录像。原实验有起始 2 分钟基线和每 trial 5 秒刺激前基线，不能与发行包保留的 3 秒混同。
- 自评：valence、arousal、dominance、liking 各 1–9 连续值，familiarity 1–5。

原文：[采集与参与者](https://archive-ouverte.unige.ch/unige:47405)；[实验与基线](https://archive-ouverte.unige.ch/unige:47405)。

## 预处理版格式（data_preprocessed_python）

- 文件 s01.dat … s32.dat，每个是 pickle dict：data 形状 40 × 40 × 8064（trial × channel × sample），其中 32 EEG + 8 外周；labels 40 × 4 按 valence、arousal、dominance、liking 排列。这是该 Python 发行包约定，不是原始采集数组。
- 官方 readme 记载：128 Hz、EOG 去除、4–45 Hz 带通、CAR、Geneva 通道顺序、按 Experiment_id 排序。8064/128=63 s；第三方 reader 通常取前 3 s 为基线。官方原句“60 second trials and a 3 second pre-trial baseline removed”与同页数组长度须一起读；不能据 removed 推定数组只剩60 s或已经执行某种逐点/特征基线减法。
- 前 32 EEG 通道按 Geneva 顺序：Fp1 AF3 F3 F7 FC5 FC1 C3 T7 CP5 CP1 P3 P7 PO3 O1 Oz Pz Fp2 AF4 Fz F4 F8 FC6 FC2 Cz C4 T8 CP6 CP2 P4 P8 PO4 O2；33–40 为 hEOG、vEOG、zEMG、tEMG、GSR、respiration、plethysmograph、temperature。官方 readme 未明示预处理数组数值单位，不能从 BioSemi BDF 的 µV 推定任意 pickle 数组也储存 µV。各信号量纲与换算分别登记。

发布依据：已取得官方 readme 的2023-10-30历史快照，[DEAP预处理：4到45Hz带通、CAR与Geneva排序](https://web.archive.org/web/20231030222856id_/https://www.eecs.qmul.ac.uk/mmv/datasets/deap/readme.html)；[DEAP数组8064样本与标签列](https://web.archive.org/web/20231030222856id_/https://www.eecs.qmul.ac.uk/mmv/datasets/deap/readme.html)。[LibEER读取：DEAP128Hz采样率与前3秒基线](https://github.com/XJTU-EEG/LibEER/blob/dddff9776dbdae21195fe320dff0a5ba61628a18/LibEER/data_utils/load_data.py)另用于核对第三方三秒基线约定。

## 常见处理

- 基线：确认前 3 s 确为基线后，再按研究或训练协议处理。仅丢弃基线与减去基线特征是不同操作，TorchEEG/LibEER 的具体实现和训练配置需要核对，不能默认等价。
- 标签阈值需同时说明等号规则、维度顺序和类别索引。常见 5 阈值中，TorchEEG 用 ≥5 为高，LibEER bounds=[5,5] 时 ≤5 为低；不能仅凭“阈值5”确定权重标签。
- 窗口：1 s（CAMER、SEED 风格 DE）或 4 s（频谱）；60 s 试次切 1 s 窗得 60 个样本。
- 划分按实际训练脚本与目标说明；同 trial 窗口不得跨训练/测试后冒充独立试次或新被试泛化。cross-trial 不自动等于 LOSO。
- 因已 4–45 Hz 带通，delta（< 4 Hz）与 45 Hz 以上不可信。

## 如何识别

- 预处理版：`s01.dat`…`s32.dat`，pickle dict，`data` 为 40 × 40 × 8064、`labels` 为 40 × 4。8064 点在 128 Hz 下正好 63 s（3 s 基线 + 60 s 刺激）。
- 原始版：`s01.bdf`…`s32.bdf`，BioSemi BDF，48 道 512 Hz（32 EEG、12 外周、3 未用、1 Status）。
- 同形状、同命名的文件按上述规格读取；数据由用户自行重构或导出时，以用户提供的采集事实为准。

## 与本项目的对应

- inspect_record 识别预处理版结构后直接按规格读取：128 Hz、Geneva 通道顺序、前 32 道 EEG，来源标为 format_assumption，可以立即做波形、频谱和特征测量。数值单位在发行说明中未写明，按原数字尺度做相对比较。
- 送入 DEAP 专用模型前需确认是官方预处理波形（update_record_metadata 或 recognize_emotion 的 eeg_metadata 中 preprocessing=deap_official_preprocessed）；模型兼容性检查把 format_assumption 视为未确认。
- CAMER DEAP 一秒适配器的契约：128 Hz、32 道、逐 1 s 五频带 DE 后对 60 个刺激窗做 LDS，每窗 4 帧被试面部，四象限 LVLA/LVHA/HVLA/HVHA（V≥5、A≥5 为高）；远端按此配方准备输入，见 scripts/remote/camer_deap_1s_infer.py。
