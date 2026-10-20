---
doc_id: methods_primary_decisions
title: 原始资料的分析决策：特征、伪迹、连接性、数据集与模型解释
source: Duan et al. 2013 (10.1109/NER.2013.6695876); Shi et al. 2013 EMBC; Smith et al. 2017 (10.1016/j.ijpsycho.2016.11.005); Koelstra et al. 2012 (10.1109/T-AFFC.2011.15); Lee et al. 2024 (10.1038/s41597-024-03838-4); Jung et al. 2000 (10.1111/1469-8986.3720163); Chang et al. 2020 (10.1109/TBME.2019.2930186, 作者全文重印); Winkler et al. 2015 (10.1109/EMBC.2015.7319296, 作者会议稿重印); Vinck et al. 2011 (10.1016/j.neuroimage.2011.01.055); Donoghue et al. 2020 (10.1038/s41593-020-00744-x, PMC作者稿); Guo et al. 2017 (arXiv 1706.04599v2); Lawhern et al. 2018 (arXiv 1611.08024v4); OHBM COBIDAS MEEG report (OSF a8dhx)
version: 2026-10-12
domain: methods
tasks: [data_understanding, feature_extraction, emotion_recognition, spectral_analysis, preprocess, artifact_quality, report, compare_conditions]
---

## 同名差分熵的计算条件需要逐项核对

原始依据：[Duan 2013，公式与实现](https://bcmi.sjtu.edu.cn/home/blu/)；[Shi 2013，推导](https://bcmi.sjtu.edu.cn/home/blu/)。公式与适用条件按链接原文核对。

- Duan 的情绪实验用 62 通道、1000 Hz 采集，分析前降到 200 Hz；文中频带是 delta 1–3、theta 4–7、alpha 8–13、beta 14–30、gamma 31–50 Hz，不能把这套范围当成所有 DE 实现的固定标准。
- 原式给出高斯条件下 h(X)=½ log(2πeσ²)。公式使用 log 记号；实际实现还要明确对数底、方差估计、滤波、窗口和单位。变更幅值单位或对数底会改变数值，不因名称和 C×5 形状相同而认为可以混用。
- Shi 的推导对经过带通的子带信号和固定长度窗口讨论 DE 与对数能量的关系。其零均值方差估计式使用 1/N；项目 LibEER 风格计算用 ddof=1 时，应按对应实现和训练契约解释。
- Shi 原文在 Eq.(4)–(6)附近的说明文字写有 N/P_i，而显示的等式对应 P_i=Nσ²。保存原文及原式，并将该文字与等式的不一致标为需要核对；不能直接把这句话抄成计算代码。

## 不对称特征的方向和研究用途不能混用

原始依据：[Duan 的 DASM/RASM](https://bcmi.sjtu.edu.cn/home/blu/)；[Smith 2017 的记录、参考与分数计算](https://pmc.ncbi.nlm.nih.gov/articles/PMC6449497/)，见原文“Obtaining the best estimates of Frontal Asymmetry”及“Transforming Raw Signals into Asymmetry Scores”。

Duan 的 DASM 是左右配对通道的 DE 差 h(X_left)−h(X_right)，RASM 是其比值。常用 FAA 是右侧 alpha 功率对数减左侧对数，它们的信号、符号方向和构念不同。

FAA 必须说明实际左右电极、参考、频带、伪迹处理和时长。已确认的两个额叶配对测点可以支持受限的左右比较；电极总数少于 8 不构成统一禁用理由。少通道平均参考和空间覆盖会影响解释，AF7/AF8 结果也不自动等价于 F3/F4 协议。

文献中的群体关联、实验范式与本次观测分别注明依据。单个 FAA 或 DE 正负没有通用情绪阈值，多特征组合也不自动形成分类器。普通评阅报告测量发现，证据不能定类时保存 unassessed/null；用户请求的候选保存 hypothesis；专用模型预测必须来自实际执行器。

## 数据集论文中的采集条件不是当前文件的来源证明

原始依据：[DEAP 采集](https://archive-ouverte.unige.ch/unige:47405)、[DEAP 实验基线](https://archive-ouverte.unige.ch/unige:47405)；[EAV 设备](https://www.nature.com/articles/s41597-024-03838-4)、[EAV 发布格式](https://www.nature.com/articles/s41597-024-03838-4)。采集条件、论文分析流程和发行包分别核对。

DEAP 原论文说明采集用 32 通道、512 Hz EEG；原始版、发布的预处理版和论文中不同分析步骤要分别核对，不能把论文里某个降采样步骤套到所有文件。EAV 说明 30 个具体电极、500 Hz、乳突参考及听/说任务组织。

采样率、单位、参考和实际电极应来自文件、实验记录或已确认信息。文件名、形状和本地哈希不能独立证明官方来源。研究时间窗、实验基线和发布文件内的基线长度也需要区分。

## 去伪迹的工具输出只说明实际执行过的检查

原始依据：paper_bigdely2015_prep、paper_piontonachini2019、paper_mullen2015、paper_jas2017；[Jung 2000 讨论](https://sccn.ucsd.edu/~jung/pdf/PSY00.pdf)、[Chang 2020 作者全文重印](https://escholarship.org/uc/item/7bk415gm)、[Winkler 2015 作者会议稿重印](https://depositonce.tu-berlin.de/items/36df0227-56b2-45ab-abbb-1948144580aa)及 doc_mne_ica。

PREP、ICA/ICLabel、ASR 与 Autoreject 解决的问题和处理单位不同。记录实际坏道、拟合条件、移除成分、校准区间、阈值及处理前后结果。没有发现一种伪迹不代表所有伪迹已排除；分类分数也不表示每个成分标签在当前记录上已获验证。

处理产物需保留通道、参考、采样率、参数、处理链及分析时间。论文中的算法描述和当前安装版本的实现应共同核对。

ASR 的20–30是 Chang 在20份模拟驾驶记录中的去伪迹/保留脑IC功率折中；参考筛选、IIR统计处理、短窗重建及 ICA/ICLabel 代理评估均影响结论。该研究没有实际脑源真值，不能把IC保留率当成所有真实神经源的保留保证。参考中反复存在的眼/肌活动可能残留，未进入校准的脑活动也可能被删。[设置与代理评估第5–10页](https://escholarship.org/uc/item/7bk415gm)；[结果与限制第19–20页](https://escholarship.org/uc/item/7bk415gm)

Winkler 的21人听觉 oddball 研究用 FastICA、MARA，在高通后的数据拟合ICA，再把权重应用到未做该高通的数据；1–2 Hz 在三项质量指标上较好。这个流程支持保护后续分析所需的慢成分，但该实验不是情绪任务，也不为全部数据、ICA算法或低通/高通实现提供统一最优参数。[作者重印第1–5页](https://depositonce.tu-berlin.de/items/36df0227-56b2-45ab-abbb-1948144580aa)

## 连接性指标的估计对象与空间解释分别说明

原始依据：[Vinck 2011 定义与采样偏差](https://repository.ubn.ru.nl/bitstream/handle/2066/115486/115486.pdf?sequence=1&isAllowed=y)、[零滞后与远源解释限制](https://repository.ubn.ru.nl/bitstream/handle/2066/115486/115486.pdf?sequence=1&isAllowed=y)及 [连接性选择知识卡](connectivity_selection_and_interpretation.md)。

coherence、PLV、PPC、imcoh 与 wPLI 衡量不同统计关系。PPC目标为PLV²；wpli2_debiased目标为去偏的wPLI²，仍有有限样本权重偏差，不能把原值、平方值和不同估计器直接比较大小。[wPLI平方去偏并非完全无偏：weighting bias原文](https://repository.ubn.ru.nl/bitstream/handle/2066/115486/115486.pdf?sequence=1&isAllowed=y)。报告有效独立epoch数、epoch长度、谱估计与参考；高度重叠窗口不按独立样本累计。

imcoh/wPLI侧重非零相位滞后，可能漏掉真实零滞后作用；非零滞后也可能是远处作用源的投影。指标不能独立证明当前两测点的局部脑连接、因果、方向或情绪类别。

## 周期与非周期谱参数的模型空间和API输入分别说明

原始依据：[Donoghue 2020 Methods的拟合空间](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)、[Results的拟合误差限制](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)、[周期/非周期谱知识卡](periodic_aperiodic_spectral_analysis.md)及 fooof v1.1.1源码。

模型沿线性等间隔频率轴、在log10功率空间拟合非周期背景和周期峰；fooof 1.1.1 API接收线性PSD，并在内部取log10。exponent是非周期参数，峰功率描述拟合背景之上的成分；频带功率改变也可能来自背景、峰频移动或重叠，不自动代表振荡强度改变。

拟合质量需结合频率范围、fixed/knee模式、峰数量与残差。原文拟合质量段称median absolute error，而fooof 1.1.1 `error_`的MAE实现为log10功率空间的mean absolute error；报告必须注明具体软件版本和误差定义，不能仅按缩写互换。[原文Methods的median MAE原句](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)；[fooof1.1.1平均绝对误差代码](https://github.com/fooof-tools/fooof/blob/v1.1.1/fooof/objs/fit.py#L1318)

## 网络论文与预测概率不等于当前情绪模型已经可靠

原始依据：paper_lawhern2018_eegnet、paper_wang2024_eegpt、paper_guo2017 和实际权重登记表。

EEGNet 原论文的网络架构及 BCI 实验不证明本项目情绪训练实例的标签、权重或跨设备性能；EEGPT 表征也需要对应任务的实际预测头。模型输入、训练预处理、权重和输出轴须与具体实例匹配，预测来自真正的执行器。

上述要求用于专用 EEG 模型。当前路径由确定性工具完成测量；证据不足以定情绪时保存 unassessed/null，用户要求类别判断时可保存 hypothesis。没有适用映射时不强选标签；保存一个 LLM 候选不等于验证分类器。α/β 走势与 FAA 只按研究支持程度解释，不能直接给唤醒或效价。用户随后给出的自评可直接对照，先统一维度缩写并保留原预测；HALV=LVHA，与 HVLA 两维均未匹配。聊天先回答实际发现或对照，不堆叠免责声明，也不在没有保存工具成功时声称已存档。

概率校准针对模型输出与真实正确性之间的匹配；softmax=0.9 不是当前个体识别准确率为 90%。校准数据与最终评测数据应隔离，并报告任务、实验划分和实际指标。

## 报告同时保留原始依据和当前计算范围

原始依据：paper_pernet2018_cobidas_preprint；doc_eeg_bids_spec；本项目产物契约。

说明通道、采样率、单位、参考、时间范围、窗口、处理参数、实验单位与统计方法。邻近或重叠窗口不应被当成独立被试增加样本量。

引用已取得的具体原文，研究建议引用资料，当前数值引用实际产物。原 PDF 的图像、未转写公式和未经逐项核对的复杂表格，保留原文件定位后再核对；资料库的解析文字不表示已分析所有图像或证明所有科学结论。
