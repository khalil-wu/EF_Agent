---
doc_id: devices_catalog
title: 常见 EEG 设备参数：通道、采样率、单位、参考、文件格式
source: Emotiv EPOC+ / EPOC X 技术规格页 (emotiv.gitbook.io); OpenBCI 商店 Ultracortex Mark IV 默认位置与 BrainFlow 文档; InteraXon Muse 2 规格; NeuroSky MindWave 规格; BioSemi ActiveTwo 文档; Brain Products BrainAmp; g.tec g.Nautilus; Unicorn Hybrid Black; Smith et al. 2017 FAA 方法学 (10.1016/j.ijpsycho.2016.11.005)
version: 2026-10-12
domain: devices
tasks: [data_understanding, emotion_recognition, artifact_quality]
---

## 消费级与低通道设备

- Emotiv EPOC / EPOC+ / EPOC X：14 通道 AF3 F7 F3 FC5 T7 P7 O1 O2 P8 T8 FC6 F4 F8 AF4；参考 CMS/DRL 在 P3/P4（EPOC X 可改乳突）；128 或 256 SPS；硬件带宽 0.2–45 Hz（EPOC X 0.16–43 Hz）；内置 50/60 Hz 数字陷波；14-bit 时 LSB 0.51 µV，16-bit 时 0.1275 µV；盐水海绵电极。导出 CSV/EDF 为 µV。通道集合与 AMIGOS 一致。
- Emotiv Insight：5 通道 AF3 AF4 T7 T8 Pz，参考左乳突。
- InteraXon Muse 2 / Muse S：4 通道 TP9 AF7 AF8 TP10，参考 Fpz，256 Hz，干电极；Mind Monitor 导出 CSV 的 RAW 列为 µV。无中央/顶区通道，不能做 mu 节律或 C3/C4 分析；FAA 只能用 AF7/AF8 近似。
- NeuroSky MindWave：单通道 Fp1 对 A1 耳夹，512 Hz；只能做单通道频谱、眨眼计数。
- OpenBCI 的板型/布局分别确认。Cyton 原始 ADS1299 计数在参考电压 4.5 V、增益24时可按 counts×4500000/24/(2^23−1) 转 µV；这不是 BrainFlow 已缩放输出的通用后处理。BrainFlow EXG 尽可能直接返回 µV，GUI 导出与原始计数按实际接口区分。电极位置由用户实际装配决定。
- Unicorn Hybrid Black：8 通道 Fz C3 Cz C4 Pz PO7 Oz PO8，250 Hz，24-bit，乳突参考；g.tec g.Nautilus 8 常用同样布局。

## 研究级设备

- BioSemi ActiveTwo 使用 CMS/DRL 共模控制回路，分析参考另行记录，不能据此推定 CAR。原始 BDF 中每道是该电极相对 CMS 的电压，仍含共模干扰（如 50 Hz）与电极直流偏移；官方说明要求在分析软件中选择参考（Cz、双耳/乳突平均、全部电极平均或双极导联），选定参考后才有完整的 80 dB 共模抑制。因此未参考的原始幅值偏大属于正常现象：先选参考并高通，再评估幅值、坏道与频谱。BDF 各通道头提供 physical dimension 与物理/数字刻度，不能统称文件单位 V。MNE get_data() 的 EEG 默认为 V，转 µV 乘1e6；to_data_frame() 默认已为 µV，不能重复换算。采样率与电极顺序按实际记录。
- Brain Products BrainAmp / actiCHamp：常 500–1000 Hz；BrainVision 三文件格式（.vhdr/.vmrk/.eeg），单位多为 µV；EAV 用 BrainAmp 30 通道 500 Hz 乳突参考。
- Neuroscan SynAmps：CNT 格式，含通道名，常 1000 Hz。

## 使用规则

原始资料：[BioSemi 的 BDF 格式](https://www.biosemi.com/faq/file_format.htm)、[CMS/DRL 与参考](https://www.biosemi.com/faq/cms%26drl.htm)、[BrainFlow 输出单位](https://brainflow.readthedocs.io/en/stable/DataFormatDesc.html#units-of-measure)、[MNE get_data](https://mne.tools/stable/generated/mne.io.Raw.html)、[MNE to_data_frame](https://mne.tools/stable/generated/mne.io.Raw.html)。

本轮直接核对了 BioSemi 文件/参考说明、BrainFlow 单位说明和 EAV 的 BrainAmp 采集原文；其他硬件代际、固定带宽/LSB/布局规格尚未逐项重审，不作为实际采集参数的确认依据。

- 设备库给出的是「默认」布局，不是「实际」布局；必须让用户确认后才能作为 user_confirmed，未确认的推断不写入长期记忆。
- 干电极与头带设备阻抗高，工频与运动伪迹重；质量检查先于任何解释。
- 额叶不对称需要确认的左右额叶配对、参考方式、频带、时间范围和伪迹检查；通道数 < 8 本身不是统一禁用条件。AF7/AF8 的结果不能冒充已验证的 F3/F4 协议，也不能直接判断个体情绪。
- 地形图需要实际电极位置和足够的空间覆盖；少量测点的插值图不能被解释为完整头皮活动。连接分析另需核对通道、参考、伪迹和体积传导影响，不用同一个通道数阈值替代所有方法条件。
- 分类模型按实际训练契约检查通道集合、带宽、单位、参考和处理；设备名称或通道数量达到某个阈值不等于模型可用。
