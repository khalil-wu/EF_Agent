---
doc_id: eeg_basics_rhythms
title: EEG 节律、频带、幅值单位、电极命名与参考
source: Kane et al. 2017, IFCN glossary, Clin Neurophysiol Pract 2:170-185, DOI 10.1016/j.cnp.2017.07.002; Kane et al. 2019 corrigendum, DOI 10.1016/j.cnp.2019.06.001; Acharya et al. 2016 ACNS Guideline 2 (electrode nomenclature), DOI 10.1097/WNP.0000000000000316; Acharya et al. 2016 ACNS Guideline 3 (standard montages), DOI 10.1097/WNP.0000000000000317; Vidaurre et al. 2009; Zheng & Lu 2015; LibEER/TorchEEG 源码
version: 2026-10-12
domain: eeg_basics
tasks: [spectral_analysis, data_understanding, artifact_quality, emotion_recognition, feature_extraction]
license: 本文为原文转述与短句引用；IFCN 2017 词汇表及 2019 勘误原文为 CC BY-NC-ND 4.0；ACNS 与期刊论文保留原版权
---

## 频带定义与不同文献的切分

- IFCN 2017 词汇表并按 2019 勘误修订：delta 0.1–<4 Hz（常规 EEG 的实用下限为 0.5 Hz）；theta 4–<8 Hz；alpha 8–13 Hz（含）；beta >13–30 Hz；gamma >30–80 Hz。theta 的连续区间不能等同于仅列整数的“4–7 Hz”；gamma 的词汇定义不代表当前设备或处理后的记录具有完整的 80 Hz 带宽。
- 情感计算常用切分不同：SEED / Zheng & Lu 2015 为 delta 1–3、theta 4–7、alpha 8–13、beta 14–30、gamma 31–50；LibEER DE 为 0.5–4、4–8、8–14、14–30、30–50；TorchEEG DE 四频带为 4–8、8–14、14–31、31–49；本项目工具默认 delta 0.5–4、theta 4–8、alpha 8–13、beta 13–30、gamma 30–50。
- 2019 勘误只修订 IFCN 的 beta 定义，不会改写 SEED 等研究各自的 14–30 Hz 协议。频带边界与端点纳入规则会影响积分；回答里写明实际边界，跨研究比较数值前先对齐。

原文定位：

- [IFCN 2017，delta 及常规 EEG 的实用下限](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)
- [IFCN 2017，theta 4–<8 Hz](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)
- [IFCN 2017，alpha 8–13 Hz](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)
- [IFCN 2017，gamma >30–80 Hz](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)；原句：“Frequency band from >30 to 80 Hz.”
- [IFCN2019勘误：beta频带定义 >13–30 Hz](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6606822/fullTextXML)；原句：“Beta band: Frequency band of >13–30 Hz inclusive.”

## 后部优势节律（PDR）与 alpha 节律

- PDR 是主要在清醒闭眼时见于枕部或顶部的节律性活动，健康成人通常落在 alpha 频段。不能把所有年龄的 PDR 都预设为 8–13 Hz。
- alpha 节律是清醒时后部的 8–13 Hz 节律，通常枕区幅值最大，闭眼、身体放松与相对精神活动减少时最明显，可被注意（尤其视觉注意）或精神活动阻断/衰减。只有频率落在 alpha 范围，空间分布、状态和反应性不符合定义时，应写“alpha 频段活动”或使用相应特定节律名称。
- 词汇表描述成人 alpha 节律幅值多数低于 50 µV，儿童常更高。这是特定节律的人群描述，不是当前记录的正常上限，也不能直接转换成 RMS 或 PSD 的判据；报告实际幅值及其计算口径。
- Asymmetry 条目比较两半球同源区的幅值、频率或波形，给出幅差超过 50%、PDR 频差至少 1 Hz 的实践参考，并明确这些值本质上具有任意性。原条目未规定幅差百分比的计算分母；不能据此自定算法，更不能把这个幅值差替换为左右频带功率差或 FAA。当前记录仍需结合年龄、状态、同源测点、参考、伪迹及实际测量解释，频带统计本身不构成临床诊断。
- 困倦可能伴随 alpha 减少与中央区 theta 增加，但其解释取决于年龄、任务、参考与伪迹。不存在本项目可直接采用的“清醒 theta 占比不超过 30%”通用判据；频带比例不能直接识别清醒/睡眠状态。

原文定位：

- [IFCN 2017，alpha 节律的状态、反应性与成人幅值](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)；原句：“Amplitude varies but is mostly below 50 µV in the adult, but often much higher in children.”
- [IFCN 2017，PDR 的闭眼后部分布与健康成人频带](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)
- [IFCN 2017，Asymmetry 的幅差、PDR 频差及阈值任意性](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)；原句：“although it should be recognized that these are essentially arbitrary values.”

## 其他节律

- mu 节律按 IFCN 2017 为 7–11 Hz，清醒时出现在中央或中央-顶区，波形呈弓形；对侧运动、运动意象、准备运动或触觉刺激时可衰减。词汇表描述幅值多数低于 50 µV，同样不是自动判据。它与 alpha/PDR 的频率有部分重叠，地形与反应性是区分依据，不能仅凭 C3/C4 的某个峰值识别 mu。
- beta 的修订频率为 >13–30 Hz。词汇表描述清醒时额-中央 beta 最典型，幅值多数低于 30 µV；其他 beta 可位于别处、弥漫分布或受酒精、巴比妥类、苯二氮䓬类及静脉麻醉药影响。这个额-中央节律的描述不构成所有 beta 活动的统一幅值上限。
- theta 为 4–<8 Hz，需结合年龄、任务与清醒/困倦状态。词汇表明确把认知任务、尤其问题解决时的额中线 theta 描述为一种正常变异，多见于年轻健康成人；不能单凭 theta 增加认定困倦或异常。
- delta 为 0.1–<4 Hz，常规 EEG 实用下限 0.5 Hz；睡眠、年龄、慢波形态及空间分布影响解释。眨眼、缓慢眼动或出汗也能产生低频成分，delta 功率不能直接识别它们或作病理分类。
- IFCN 的 gamma 为 >30–80 Hz；本项目默认计算 30–50 Hz 是另一套分析边界。将头皮高频功率解释为脑源活动前核对肌电、工频、硬件带宽与已有滤波，不能把频带名当作来源判断。

原文定位：

- [IFCN 2017，mu 的 7–11 Hz、地形、反应性与幅值](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)；原句：“Rhythm at 7–11 Hz, composed of arch-shaped waves occurring over the central or centro-parietal regions of the scalp during wakefulness.”
- [IFCN 2017，额-中央 beta 幅值描述（频率另按勘误）](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)；原句：“Amplitude of fronto-central beta rhythm varies but is mostly below 30 µV.”
- [IFCN 2017，认知任务中的额中线 theta 正常变异](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)
- [2019 勘误，beta 节律的修订频率](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6606822/fullTextXML)

## 幅值与单位

- 幅值依状态、年龄、参考、设备与处理而变，注明峰峰值/RMS等口径。大幅活动是结合波形与空间分布核查的线索，不能仅凭 ±100 µV 或统一范围判伪迹。
- EEG 幅值表示电极对之间的电位差，受导联方式、极间距离及颅骨等结构影响。临床描述常用峰峰值；信号的 RMS、PSD 峰高和频带功率是不同量，单位及数值不能互换。
- 文件单位与缩放以元数据为准：EDF/BDF 查物理单位及数值映射，BrainVision 查 .vhdr 的单位与分辨率，不能由 .eeg 扩展名判定数值是 µV。MNE 读取的 EEG 以 V 表示，本项目在明确单位时换算为 µV；自制设备或文本导出也可能是 ADC 计数、V、mV 或 µV。单位来源为“推断”时结果要注明。

原文定位：[IFCN 2017，Amplitude, EEG 的峰峰值口径](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6123891/fullTextXML)：“often expressed as the difference between the maximum and minimum deviation (i.e. peak-to-peak)”。幅值与功率单位的计算区别见[PSD 与频带功率](../methods/spectral_features_and_asymmetry.md#psd-与频带功率)。

## 10-20 / 10-10 电极命名

- 奇数左、偶数右、z 中线；Fp 前额、F 额、C 中央、P 顶、O 枕、T 颞；AF/FC/CP/PO 为 10-10 中间位置。
- ACNS 2016 Guideline 2 专门规定电极位置命名；Guideline 3 专门规定标准蒙太奇，两者不是同一篇。2016 版摘要分别说明其前身为 2006 Guideline 5 与 Guideline 6，引用时不能只凭编号匹配旧文件。
- 10-10 命名将 T3/T4、T5/T6 分别改为 T7/T8、P7/P8；位置别名需要映射，旧标签本身并不代表文件错误。Guideline 2 的 Recommendations 4 明确允许继续使用旧名或同时使用新旧名。P7/P8 在该命名逻辑中指后颞位置，不能只因 P 前缀便把它们归为一般顶叶测点。
- 数据集的通道顺序不是字母顺序：DEAP/MAHNOB 32 通道（Biosemi Geneva 顺序）为 Fp1 AF3 F3 F7 FC5 FC1 C3 T7 CP5 CP1 P3 P7 PO3 O1 Oz Pz Fp2 AF4 Fz F4 F8 FC6 FC2 Cz C4 T8 CP6 CP2 P4 P8 PO4 O2。
- 只有 ch1…chN 这类名字且缺少外部电极表或已核实位置映射时，通道位置未知：不对称、地形图、按脑区解释缺少依据，频谱与时域特征仍可做。通道顺序须以对应发行及文件实际通道表为准，不能由名字排序重建位置。

原文定位：[ACNS Guideline 2，第 2 页（原刊 p.309），§III](https://www.acns.org/UserFiles/file/Guideline2-GuidelinesforStandardElectrodePositionNomenclature_v1.pdf)：“The modified 10-10 terminology replaces the inconsistent T3/T4 and T5/T6 terms with the consistent T7/T8 and P7/P8.”；[第 4 页（p.311），Recommendations 4](https://www.acns.org/UserFiles/file/Guideline2-GuidelinesforStandardElectrodePositionNomenclature_v1.pdf)允许“to continue to use T3/4 and T5/6, or to use both terms, at present.”

## 参考方式

- 记录/分析参考可为乳突、Cz等；BioSemi CMS/DRL 是共模控制回路，不能与已实施的分析参考混同。
- CAR 对明确的 EEG 参考集合求平均；坏道、空间覆盖和电极分布影响结果，不用统一 16 通道门槛保证适用性。少通道/局部覆盖时按对应协议，并记录实际参考集合。
- ACNS 临床蒙太奇建议结合纵向双极（LB）、横向双极（TB）与参考（R）表示，并清楚标明每条导联的电极对。Guideline 3 中“不少于 16 同步通道”是临床记录建议，不是 CAR 算法的通用适用门槛。
- 参考选择与目标和状态有关：Guideline 3 讨论 Cz、耳部和平均参考各自的局限，并描述临床平均参考有时排除 Fp1/Fp2/F7/F8 以减少眼动污染。这个具体场景不能直接改写某个情感数据集或训练协议的参考集合；实际分析需要记录参与平均的电极及坏道处理。
- 重参考可能改变不对称与连接性；比较前对齐参考。数据集某一发行或分析流程采用 CAR，不证明当前文件已做 CAR。
- 双极导联也用于定量 BCI 特征分析。例如 Vidaurre 2009 使用 FC3–CP3、FCz–CPz、FC4–CP4，随后计算特征与分类；不能按“临床/定量”给双极与参考作绝对分工。按数据、任务、空间滤波及模型输入契约报告实际导联关系。

原文定位：[ACNS Guideline 3，第 1 页（原刊 p.312），§2.1(c)](https://medi-guide.meditool.cn/ymtpdf/02507BE5-E7D7-723A-3B82-8C7BF038BFD5.pdf)：“that both bipolar and referential montages be used for clinical interpretation”；[第 2 页（p.313），§2.4 与参考讨论](https://medi-guide.meditool.cn/ymtpdf/02507BE5-E7D7-723A-3B82-8C7BF038BFD5.pdf)；[Vidaurre 2009，第 2 页（p.1314），§4.1](https://pub.ista.ac.at/~schloegl/publications/Vidaurre2009tdp.pdf)：“Bipolar channels are widely used by several BCI-groups”。

## 采样与滤波

- Nyquist：采样率必须高于目标频带上限的两倍；128 Hz 的 Nyquist 频率是 64 Hz。实际可解释带宽还取决于硬件抗混叠、原始滤波和过渡带，不能仅由采样率认定 45–50 Hz 可靠。若确认源数据已 4–45 Hz 带通，4 Hz 以下与 45 Hz 以上不能恢复解释。
- 零相位离线滤波可避免相位延迟，但仍改变幅频响应并可能将瞬态在时间上扩散；实时任务按因果性需求选择。ERP 对高通截止敏感，参数按任务和训练协议。
- 工频：50 Hz（中国大陆、欧洲、澳大利亚、日本东部）；60 Hz（美国、加拿大、韩国、中国台湾、日本西部）；不确定时看 PSD 尖峰或问用户。
- 降采样前满足抗混叠要求，可由合适的重采样算法实现；分析带通、参考与重采样顺序按实际依赖和协议，不设统一先后。上采样不产生新采集信息。
