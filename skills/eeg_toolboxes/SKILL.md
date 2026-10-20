---
name: eeg_toolboxes
description: 用 MNE 生态完成 ERP、时频、连接性、频谱参数化及其他扩展 EEG 方法。适用于已有基础工具未覆盖的专业分析或明确的 MNE/EEGLAB 方法需求。
modalities: [eeg]
allowed-tools: [run_python, clean_artifacts, read_artifact, search_knowledge]
---

# MNE 研究方法

## 默认入口与取舍

- ERP：用已有事件与条件创建 epochs，先按协议检查基线和有效 trial 数，再平均；没有协议时先画单试次与事件时间分布，向用户确认会改变分组的缺项。
- 时频：按问题选频率与 Morlet 周期数，报告时间/频率平滑与边界影响；有事件基线时优先用相同基线做条件内归一化。
- 连接性：先选同一参考、同一长度的无明显伪迹 epochs；零时滞混合是主要问题时优先考虑 wPLI 类估计，而相关性问题可用 Pearson。先给估计量与其不确定性，再讨论脑区关系。
- 频谱参数化：从目标频段的线性 PSD 开始，先检查拟合曲线与残差，再解释周期峰和非周期参数。

常规去伪迹使用 clean_artifacts，基础特征使用对应工具。ERP、时频、FOOOF、wPLI 或复杂度等扩展方法用 run_python；相关 API 与可运行示例按需 read_skill(name=eeg_toolboxes, reference=api.md)。已有科学工具可通过 eeg.call 组合进同一分析脚本。

连续信号用 eeg.to_mne，原始分段信号用 eeg.to_epochs，保留 epoch 身份、tmin、条件与原始时间。MNE 内部幅值为 V，电极位置标明来自实际测量还是标准模板。保存处理信号用 eeg.save_raw / eeg.save_epochs，后续工具继续使用返回的派生来源。

ERP 采用真实事件、条件和基线，插值与地形图采用可用电极位置。按样本与研究问题选择估计器和参数，示例阈值仅用于示范。现有高通或参考的不可逆影响保留在处理记录中。

连接性先检索 knowledge/methods/connectivity_selection_and_interpretation.md，选择并说明估计器、epoch 数/时长、参考和频段。PPC 对应 PLV²，wpli2_debiased 对应 wPLI²，按对应量纲比较；结果描述该估计器的功能关联，因果和源定位需各自依据。

频谱参数化检索 knowledge/methods/periodic_aperiodic_spectral_analysis.md，采用线性 PSD 输入，按实际软件版本说明模型参数与误差。绘图细节按需读取 scripting 的 figures.md。

完成后保存实际计算的图、表或处理信号，记录版本、输入范围、参数、单位与来源。正文给出能回答当前问题的发现，方法与复现代码作为产物保留。
