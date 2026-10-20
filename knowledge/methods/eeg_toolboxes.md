---
doc_id: methods_toolboxes
title: EEG 处理工具箱：EEGLAB 流程与 MNE-Python 生态的对应、前提与参数
source: Delorme A., Makeig S. 2004, J Neurosci Methods 134:9-21 (EEGLAB); Gramfort A. et al. 2013, Front Neurosci 7:267 (MNE-Python); Pion-Tonachini L., Kreutz-Delgado K., Makeig S. 2019, NeuroImage 198:181-197 (ICLabel); Li A. et al. 2022, JOSS 7(76):4484 (MNE-ICALabel); Lee T-W., Girolami M., Sejnowski T.J. 1999, Neural Comput 11(2):417-441 (extended infomax); Ablin P., Cardoso J-F., Gramfort A. 2018, IEEE Trans Signal Process 66(15):4040-4049 (Picard); Winkler I. et al. 2015, IEEE EMBC, PMID 26737196 (高通与 ICA); Mullen T. et al. 2015, IEEE TBME 62(11):2553-2567 (ASR); Chang C-Y. et al. 2020, IEEE TBME 67(4):1114-1121 (ASR 参数); sccn/clean_rawdata clean_artifacts.m 文档与 EEGLAB 自动化流程教程; Bigdely-Shamlo N. et al. 2015, Front Neuroinform 9:16 (PREP); PyPREP 文档与源码 (find_noisy_channels.py 默认阈值); Jas M. et al. 2017, NeuroImage 159:417-429 (Autoreject); Donoghue T. et al. 2020, Nat Neurosci 23:1655-1665 (FOOOF); Nolte G. et al. 2004, Clin Neurophysiol 115:2292-2307 (虚部相干); Vinck M. et al. 2011, NeuroImage 55:1548-1565 (wPLI); antropy 文档
version: 2026-10-12
domain: methods
tasks: [preprocess, artifact_quality, scripting, spectral_analysis, feature_extraction, compare_conditions, general_eeg_analysis]
---

## EEGLAB 流程与 Python 实现的对应

EEGLAB（MATLAB）本体不在本项目环境里；下面列的是 MNE 生态的对应任务，实现和默认参数并非逐项数值等价。回答里写明实际执行的软件、算法与参数。

- 导入 pop_loadset / pop_biosig → inspect_record（MNE 读 EEGLAB .set 含 .fdt、EDF/BDF、BrainVision、FIF、CNT、GDF、MFF）。分段（epoched）的 .set 会按顺序拼接并把每个 epoch 起点记为事件。
- 电极位置 pop_chanedit（查 10-5 标准帽）→ eeg.to_mne 与 clean_artifacts 按清洗后的通道名匹配 MNE standard_1005 模板；ch1…chN 这类名字没有位置。
- 滤波 pop_eegfiltnew（加窗 sinc FIR，零相位）→ preprocess 的 bandpass method=fir；默认 method=iir 为 4 阶 Butterworth 零相位（sosfiltfilt）。
- 重参考 pop_reref([]) → preprocess rereference method=car（只对 EEG 类型通道求平均）。
- clean_rawdata → clean_artifacts 的 bad_channels（坏道）与 asr（突发伪迹重建）。
- runica('extended',1) + ICLabel + pop_icflag + pop_subcomp → clean_artifacts 的 ica_iclabel。
- pop_interp（球面样条）→ clean_artifacts bad_channels 的 action=interpolate。
- pop_epoch / pop_rmbase → run_python 里 mne.Epochs(raw, events, tmin, tmax, baseline)。
- newtimef（ERSP/ITC）→ mne.time_frequency 的 tfr_morlet / tfr_multitaper（run_python）。
- STUDY 组分析 → run_python 多文件汇总，统计用 scipy / statsmodels / mne.stats 置换检验。

## ICA 与 ICLabel

- 算法：所核 EEGLAB runica 源码默认 extended=0；runica('extended',1) 明确启用 extended infomax。本项目使用 Picard 的 ortho=False、extended=True 配置，不能保证在所有数据上收敛更快。
- ICLabel（Pion-Tonachini et al. 2019）把每个成分分成 7 类：Brain、Muscle、Eye、Heart、Line Noise、Channel Noise、Other。MNE-ICALabel（Li et al. 2022）是同一网络的 Python 移植。它的输入前提（源码里的检查）：平均参考、1–100 Hz 滤波、扩展 infomax 分解，并且需要电极坐标来算地形图特征。
- 高通：Winkler2015在21人听觉oddball、FastICA/MARA与其质量指标下，1–2 Hz较好；可在高通副本拟合并把权重应用到原低截止数据，前提是通道、顺序、参考及秩一致。这不是所有任务的最优高通。[ICA高通拟合后向低截止数据迁移权重](https://depositonce.tu-berlin.de/items/36df0227-56b2-45ab-abbb-1948144580aa)。
- 数据量：通道、有效秩、源混合、时长与伪迹类型共同影响分离质量。N 与 n² 的经验比例不是成功保证；本项目最低 8 个可定位 EEG 通道与样本量提示属于工程约定，不是 ICA 的通用门槛。
- 秩：成分数不超过实际数据秩。满秩 n 通道平均参考通常减少一维，插值、投影或其他依赖可进一步降秩，不能普遍固定为 n−1。当前工具的具体选择不等于一般方法保证。
- 剔除阈值：本工具默认 threshold=0.8，是工程策略。ICLabel 分类和成分剔除是两步，记录实际类别、概率、exclude 与阈值；0.8/0.9 不是通用正确性保证。低于阈值的成分可复核，不将“Brain 永不剔除”当算法性质。
- 报告：成分数、剔除了哪些成分（类别与概率）、剔除成分解释的方差比例、拟合用的高通与时长。

原文：[ICLabel 的使用与分类阈值](https://pmc.ncbi.nlm.nih.gov/articles/PMC6592775/)。默认值另按 runica/MNE-ICALabel 实际源码核对；CAR、1–100 Hz、extended infomax 是设计条件，偏离时报告适用性，不代表不能运行或性能已知。

源码：[runica 的 extended 默认值](https://github.com/sccn/eeglab/blob/develop/functions/sigprocfunc/runica.m)。

## ASR 与 clean_rawdata

- ASR（Mullen et al. 2015）：用低伪迹参考数据的窗口 RMS 分布构造方向依赖阈值，检测短窗高方差子空间并重建。参考可另采或从数据筛选；不是直接将方差与 k 倍标准差比较。
- clean_rawdata 的主要参数（clean_artifacts.m 文档与 EEGLAB 自动化流程教程）：FlatlineCriterion 5（平坦超过 5 s 的通道删除）、ChannelCriterion 0.8（与邻近通道重建信号的相关低于该值视为坏道；教程示例用 0.87）、LineNoiseCriterion 4（工频噪声超过总体 4 个标准差）、Highpass [0.25 0.75]、BurstCriterion（ASR 的 k，教程常用 20）、WindowCriterion 0.25（修复后仍有超过 25% 通道异常的窗口剔除）、BurstRejection 默认 off（重建而不是删除）。
- k 的取值：Mullen论文示例5–7、当前asrpy默认20、所核clean_rawdata源码默认BurstCriterion=5，各自归属对应实现。已核读Chang2020作者博士论文的全文重印：20份模拟驾驶记录中建议20–30，依据ICA/ICLabel代理评估而无真实脑源真值；更小k通常更激进，但效果依实现、校准和数据条件。它不是通用推荐，也不说明所有k<10必然伤脑活动。[研究折中与范围](https://escholarship.org/uc/item/7bk415gm)。
- 前提：报告校准区间与有效数据、滤波和实际参数。论文的一分钟参考示例不是统一最低时长；本项目少于 4 通道不做属于工程限制。
- 适用：短时大幅伪迹（运动、咬牙、电极碰撞）。持续的眨眼与眼动更适合 ICA；两者常组合：先 ASR 再 ICA。

原文：[Mullen 2015 的校准、阈值与重建](https://pmc.ncbi.nlm.nih.gov/articles/PMC4710679/)。clean_rawdata 的 Highpass [0.25,0.75] 是过渡带设置；上述软件默认值各自归属对应实现，不能拼成统一默认。

源码：[clean_rawdata 的独立默认值](https://github.com/sccn/clean_rawdata/blob/master/clean_artifacts.m)。

## 坏道检测（PREP）与插值

- PyPREP 0.9.0 的判据按该实现记录：平坦使用 std 或 MAD <1e-15 V；稳健偏差/高频噪声默认 z>5；相关判据为 1 s 窗内与其他通道绝对相关的第 98 百分位 <0.4，异常窗占比 >1%；RANSAC 当前默认窗长 5 s、预测相关阈值 0.75、异常窗比例 0.4。PREP 原论文与实现版本默认值不逐项等同。
- RANSAC 需要所有通道都有电极坐标，而且运行明显更慢；本项目在通道 ≥ 16 且都有位置时才默认开启。
- 插值：球面样条需要相关电极坐标；评估位置覆盖、坏道分布和用途。本项目超过约四分之一坏道不插值是工程政策，不是统一可靠性界限。无坐标时仍可标记或按任务删除。
- 平坦通道也可能是参考电极或未接的输入，删除前先看通道名和设备说明。

原文：[PREP 的相关性与 RANSAC 描述](https://doi.org/10.3389/fninf.2015.00016)。安装版本阈值以 PyPREP 源码为准。

## Autoreject 与分段剔除

- Autoreject（Jas et al. 2017）在分段数据上交叉验证选择峰峰值阈值，决定插值或剔除 epoch；需要相应数据与坐标。n_jobs=1 是本项目沙箱限制，不是算法限制。
- 适合 ERP 等事件相关分析；连续静息态数据先切成固定长度 epoch（mne.make_fixed_length_epochs）再用。
- 简单替代：mne.Epochs(reject=dict(eeg=150e-6)) 按峰峰值剔除（MNE 内部单位是 V）。

## 频谱参数化（FOOOF / specparam）

- FOOOF 在对数功率谱上拟合非周期背景及周期峰。exponent 本身是非周期参数；峰参数描述相对于拟合背景的周期成分，仍依赖频率范围、分辨率及模型拟合。
- 拟合范围常用 1–40 Hz；peak_width_limits 一般 (1, 8)；要报告拟合 r² 和误差，拟合差的通道不要解释。
- 本轮安装为 fooof 1.1.1；specparam 的 API 按实际版本核对，不假定兼容。

## 连接性（mne-connectivity）

- spectral_connectivity_epochs 支持 coh、cohy、imcoh、plv、ciplv、ppc、pli、wpli、wpli2_debiased，需要分段数据。
- coh 与 PLV 可能受共同源、容积传导与参考影响；imcoh 和 wPLI 侧重非零相位滞后，可减弱部分零滞后混合，也可能忽略真实零滞后作用。不能据此宣称真实连接、方向或因果。
- 有限 epoch 数影响估计偏差与方差。PPC 估计 PLV²，wpli2_debiased 估计去偏 WPLI²，不是 coherence 的通用替代，也不能与 PLV/WPLI 原值直接比大小。报告 epoch 数、长度、估计器与参考；重参考可能改变结果。

## 非线性与复杂度指标（antropy）

- 排列熵、谱熵、SVD 熵、近似熵、样本熵、Higuchi / Katz / Petrosian 分形维数、DFA、Lempel-Ziv 复杂度。
- 这些指标对信号长度、采样率、滤波和嵌入参数敏感；跨设备或跨采样率不能直接比较，同一被试内比较时固定参数并写明。

## 在本项目里怎么用

- 确定性工具：clean_artifacts（method=bad_channels / asr / ica_iclabel），每次输出新的 normalized_signal 产物与对比图，可以继续 segment_signal、extract_features、plot。
- 脚本：run_python 里 eeg.to_mne(id) 得到带通道类型、10-05 位置和记录事件（annotations）的 Raw（单位 V）；处理后 eeg.save_raw(raw, summary, steps=[…]) 存回 µV 信号产物，steps 按工具命名写清做了什么（如 bandpass_1_40_fir、ica_removed_2）。
- 沙箱限制：不能联网，所以 mne.datasets、fsaverage 等需要下载的资源不可用，不做源定位；不能起子进程，n_jobs 只能为 1。
