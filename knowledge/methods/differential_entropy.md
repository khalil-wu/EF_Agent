---
doc_id: methods_de
title: 差分熵（DE）特征：定义、频带、实现差异、LDS 平滑
source: Duan R-N., Zhu J-Y., Lu B-L. 2013, "Differential entropy feature for EEG-based emotion classification", IEEE/EMBS NER, pp.81-84; Zheng W-L., Lu B-L. 2015, IEEE Trans Auton Ment Dev 7(3):162-175; Zheng, Zhu, Lu 2014 ICME; Shi L-C., Jiao Y-Y., Lu B-L. 2013 EMBC (DE for vigilance); LibEER data_utils/preprocess.py; TorchEEG transforms.BandDifferentialEntropy
version: 2026-10-20
domain: methods
tasks: [feature_extraction, emotion_recognition, spectral_analysis]
---

## 定义

- 差分熵是连续随机变量的香农熵推广：h(X) = −∫ f(x) log f(x) dx。
- 若某频带内的 EEG 片段近似高斯 N(μ, σ²)，则 h = ½·log(2πe·σ²)。固定长度、相应方差/能量定义下，DE 与对数能量有确定关系；Duan 2013 引用了 Shi 2013 的推导，并非在该文重新证明。对数变换压缩数值动态范围，不保证分类性能或时间稳定性。

原文：[Duan 2013，高斯 DE 公式](https://bcmi.sjtu.edu.cn/home/blu/)；[Shi 2013，DE 与能量推导](https://bcmi.sjtu.edu.cn/home/blu/)。

## Duan 2013 的实验结果及适用范围

Duan 2013 在六名参与者的正/负情绪二分类自采实验中，使用 LDS 平滑和线性 SVM，按同一次实验前八段训练、后四段测试，报告五频带联合 DE 平均准确率 84.22%、能量谱 76.56%。这是该实验条件下的比较，不是 SEED 三分类成绩，也不是当前权重或新被试上的精度。

原文：[实验与划分](https://bcmi.sjtu.edu.cn/home/blu/)；[Duan2013情绪实验：DE84.22%与能量谱ES76.56%，Table II](https://bcmi.sjtu.edu.cn/home/blu/)；[参与者与任务](https://bcmi.sjtu.edu.cn/home/blu/)。

## 不同实现与兼容条件

- SEED / Zheng & Lu 2015：五频带 delta 1–3、theta 4–7、alpha 8–13、beta 14–30、gamma 31–50 Hz；预处理降采样 200 Hz、0.3–50 Hz 带通；用 256 点 STFT，每秒一个特征；62 通道 × 5 带 = 310 维。
- 上游 LibEER `de_extraction`：0.5–4、4–8、8–14、14–30、30–50 Hz；在传入整 trial 上逐带做 3 阶 Butterworth `filtfilt`，再切窗求 ½·log2(2πe·var)，ddof=1。本项目 `libeer_filtfilt` 当前在每个分析窗口内单独 `sosfiltfilt`，属于窗口内滤波的 LibEER 风格实现，不保证与上游整 trial 处理数值等价。
- 本项目 `extract_features` 的频带与滤波方法分别选取：默认 `bands=standard` 的 alpha 是 8–13 Hz、beta 是 13–30 Hz；`bands=camer_5band` 才采用上面 LibEER 风格的 alpha 8–14 Hz、beta 14–30 Hz。方法名 `libeer_filtfilt` 本身不改写 bands。`torcheeg_var` 使用其专属四频带。复现以结果中的 de_bands、de_filter、de_method 和 de_unit 为准。
- 远端 CAMER 适配器的输入配方（见 scripts/remote/ 与 configs/model_registry.yaml）：整 trial 上三阶五频带 filtfilt，逐 1 秒窗求方差（ddof=1，下限 1e-6）的 log2 DE，再做 LDS 平滑。DEAP 一秒路线频带 0.5–4、4–8、8–14、14–30、30–50 Hz，对 60 个刺激窗做 LDS、3 秒基线不参与；EAV 路线先降到 128 Hz，γ 上限 45 Hz，20 个 1 秒帧平滑后每 5 帧取均值。`run_model` 在远端按这套配方准备输入；本地 `extract_features` 的 `libeer_filtfilt` 是不含 LDS 的同类 DE，用于测量与比较。
- LibEER `de_reduced_extraction`：1–4、4–8、8–14、14–31、31–50 Hz，STFT 实部能量取 log2(100·E)，尺度与上者不同。
- TorchEEG `BandDifferentialEntropy`：四频带 theta 4–8、alpha 8–14、beta 14–31、gamma 31–49；5 阶 Butterworth 单向 lfilter；½·log2(2πe·var)。
- TorchEEG 上述方差用 ddof=0；LibEER 为 ddof=1。仅对数底差异可明确换算，但幅值单位、频带、滤波作用范围和估计器差异不能靠名称或 C×5 形状消除。推理与对应权重训练配置保持一致。

原文：[Zheng & Lu 2015 的特征设置](https://bcmi.sjtu.edu.cn/home/blu/)。软件口径见上游 LibEER `data_utils/preprocess.py` 的 de_extraction/de_reduced_extraction、TorchEEG `transforms/numpy/band.py` 及本项目 `worker/processing.py`；这些实现事实不由论文标题代替。

源码：[LibEER 整 trial 滤波后切窗](https://github.com/XJTU-EEG/LibEER/blob/dddff9776dbdae21195fe320dff0a5ba61628a18/LibEER/data_utils/preprocess.py)。

## 窗口与平滑

- 一秒窗是部分研究的特征约定，不代表情绪变化存在普遍的一秒下限。
- LDS 是状态空间模型；Kalman 滤波可因果递推，使用未来观测的平滑是另一估计方式。LibEER `lds` 用全序列均值初始化后向前递推，参数为先验方差 0.01、过程噪声 1e-4、观测噪声 1；该具体实现包含全段信息，不能未经修改称为实时因果处理。Duan 2013 使用 20 s 平滑窗。平滑不得跨越应隔离的训练/评测边界，报告实际初始化、时间范围与参数。
- 128 Hz 的 Nyquist 频率为 64 Hz；50 Hz 是否有明显衰减需查实际硬件与滤波器响应，不能仅凭占 Nyquist 的比例判断。已验证的源带宽若截止 45 Hz，就不能解释被去除的 45–50 Hz 部分。

源码：[LibEER 的全序列初始化与向前递推](https://github.com/XJTU-EEG/LibEER/blob/dddff9776dbdae21195fe320dff0a5ba61628a18/LibEER/data_utils/preprocess.py)。

## 使用规则

- 计算前核对可追溯的源带宽，按目标决定是否滤波。仅当源数据的 4–45 Hz 限带已确认时，才可说明 delta 与 gamma 上段被源处理限制；不能根据 DEAP-like 格式推断本文件已限带。新增滤波不能恢复先前丢失的频段。
- DE 是能量相关观测量，多特征组合也不自动形成有效分类器。一般问题报告测量发现，证据不能定类时保存 unassessed/null；用户要求的候选可保存 hypothesis，并写明支持与冲突证据。专用模型预测由实际执行器按对应训练契约产生。
- 报告时写明：实现（libeer_filtfilt / torcheeg_var）、频带边界、窗口、是否 LDS。
