---
doc_id: methods_spectral
title: 频谱特征、频带功率、额叶 alpha 不对称、时域与连接性特征
source: Welch P.D. 1967, IEEE Trans Audio Electroacoust 15(2):70-73, DOI 10.1109/TAU.1967.1161901; SciPy signal.welch official documentation; Vidaurre et al. 2009, Neural Networks 22:1313-1319, DOI 10.1016/j.neunet.2009.07.020 (§2 restates Hjorth 1970 formulas; the 1970 full article has not been acquired); Smith et al. 2017 FAA primer; Allen, Coan & Nazarian 2004; Coan & Allen 2004; Davidson 1998; Harmon-Jones 2003; Inouye et al. 1991 (spectral entropy)
version: 2026-10-12
domain: methods
tasks: [spectral_analysis, feature_extraction, emotion_recognition, artifact_quality, compare_conditions]
---

## PSD 与频带功率

### 子段、平均与估计条件

Welch 法把数据划分为长度 L 的子段，子段可以重叠；每段乘窗、计算经窗能量归一化的修正周期图，再对实际子段平均。原论文讨论二阶平稳随机序列，方差结论另有高斯过程、谱在估计器通带内近似平坦等条件。对 EEG 的短时谱估计需选择状态相对一致的时间范围，避免把眼开闭、任务切换或伪迹造成的不同状态平均成一个稳定谱。

外层分析窗口与内部 Welch 子段是两个参数：外层窗口控制本次特征覆盖的时间，内部 L 决定每个谱估计使用的连续观测时长。4 s 的特征窗口不意味着内部 Welch 子段也是 4 s。数据断点、独立 epoch 或剔除片段的边界也不是真实的连续波形，子段不应跨越它们；比较多个 epoch 的平均谱时要注明按 epoch 等权还是按实际 Welch 子段数加权。

本项目通常采用约 2 s 的 Hamming 子段、约 50% 重叠、常数去趋势、density 标度与算术平均，实际子段可受可用连续数据长度限制，以产物中的 nperseg、noverlap、nfft、window、detrend、average 和边界/聚合规则为准。对同样长的可用数据、相同窗型和近似平稳状态，更长子段可缩窄谱平滑核，但平均的子段数会减少；谱波动可能增大。更短子段改善时间局部性与平均次数，同时更容易把相近的频率成分混合。重叠子段相关，不能把子段数直接当独立样本量；50% 重叠不是所有窗型和任务的统一最优值。

原文定位：[Welch 1967，第 1 页（原刊 p.70），The Method](https://ocean.phys.msu.ru/courses/geoseries/1967%20Welch%2C%20The%20Use%20of%20Fast%20Fourier%20Transform%20for%20the%20Estimation%20of%20Power%20Spectra_%20A%20Method%20Based%20on%20Time%20Averaging%20Over%20Short%2C%20Modified%20Periodograms.pdf)：“We take segments, possibly overlapping, of length L with the starting points of these segments D units apart.”；[第 2–3 页（pp.71–72），The Variances of the Estimates](https://ocean.phys.msu.ru/courses/geoseries/1967%20Welch%2C%20The%20Use%20of%20Fast%20Fourier%20Transform%20for%20the%20Estimation%20of%20Power%20Spectra_%20A%20Method%20Based%20on%20Time%20Averaging%20Over%20Short%2C%20Modified%20Periodograms.pdf)给出相关性与重叠条件。[SciPy 官方 welch 原文：重叠取决于窗及需求](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)：“An appropriate amount of overlap will depend on the choice of window and on your requirements.”

### 格点间距、实际分辨能力与 ENBW

设采样率为 fs，内部子段长度 L=nperseg，FFT 长度为 nfft，则频率格点间距为 Δf=fs/nfft，连续观测时长为 T=L/fs。决定谱平滑尺度的是 T 与窗函数：Welch 原文描述谱窗宽度约为 1/L（其频率单位为每样本周期），换算成 Hz 后尺度约为 fs/L。实际能否区分两个邻近成分还受窗的主瓣、泄漏、信噪比及成分相对幅值影响，不能只报告一个格点间距便声称可以分开任意相差该值的峰。

nfft>L 时零填充能使频率轴与数值峰定位更细，频率格点变密，但不会延长已有的观测子段或缩窄由它及窗型决定的谱窗。不得把零填充后的 fs/nfft 当成新增的真实分辨能力；当前工具 nfft=L 时两个数值尺度相同，也仍需交代窗型。

窗 w 的等效噪声带宽（ENBW）为：

```text
ENBW = fs × sum(|w|²) / |sum(w)|²       （Hz）
P_spectrum(f) / P_density(f) = ENBW     （相同数据、窗及单双侧口径）
```

这是由 SciPy 官方文档给出的两种标度之比得到的窗带宽口径。ENBW 衡量平坦噪声通过窗后等效汇聚的带宽，不是半功率宽度、峰频估计误差或两个峰的统一可分离阈值。对周期 Hamming 窗，由 w[n]=0.54−0.46 cos(2πn/L) 可得 ENBW≈1.363 fs/L；周期 Hann 窗约为 1.5 fs/L。这些倍数相对于 fs/L，不是任意零填充后格点间距的倍数。

例如 fs=256 Hz、L=512（2 s）时，周期 Hamming 的 ENBW 约为 0.681 Hz；nfft=512 的格点间距为 0.5 Hz，零填充到 nfft=2048 后为 0.125 Hz，但 ENBW 仍约为 0.681 Hz。该例是上述窗公式的计算说明，不是对某条当前记录的测量结果。

原文定位：[Welch 1967，第 2 页（p.71），Fig.1 后的谱窗说明](https://ocean.phys.msu.ru/courses/geoseries/1967%20Welch%2C%20The%20Use%20of%20Fast%20Fourier%20Transform%20for%20the%20Estimation%20of%20Power%20Spectra_%20A%20Method%20Based%20on%20Time%20Averaging%20Over%20Short%2C%20Modified%20Periodograms.pdf)：“width is of the order of 1/L”；[第 4 页（p.73），The Spacing of the Spectral Estimates](https://ocean.phys.msu.ru/courses/geoseries/1967%20Welch%2C%20The%20Use%20of%20Fast%20Fourier%20Transform%20for%20the%20Estimation%20of%20Power%20Spectra_%20A%20Method%20Based%20on%20Time%20Averaging%20Over%20Short%2C%20Modified%20Periodograms.pdf)：“estimates spaced at intervals of 1/M rather than 1/L”。[SciPy 官方 welch 原文：spectrum/density 与 ENBW 比值](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)明确给出 spectrum/density 比值为“sum(abs(window)**2)*fs / abs(sum(window))**2”；Hamming/Hann 倍数是按该式推导，未冒称 Welch 1967 原文给出的 ENBW 数字。

### 功率单位、积分与跨记录比较

绝对频带功率为 PSD 在所选频带内的积分，P_band=∫PSD(f)df。输入振幅单位确认为 µV 且使用 scaling=density 时，PSD 的单位为 µV²/Hz，积分后的频带功率为 µV²；输入为 V 时分别为 V²/Hz 与 V²，单位未知或为 ADC 计数时保留原口径。振幅从 V 改为 µV 的数值乘 10⁶，PSD 与功率数值相应乘 10¹²，不能对功率只乘 10⁶。

PSD 格点的均值仍是密度，直接求和也没有完成频率积分；离散积分要使用实际频率轴及相应步长/积分规则，并写明频带端点如何纳入。scaling=spectrum 返回平方幅值谱，单位为振幅单位的平方，但其窗归一化不同，不能沿用 density 的频率积分规则。比较谱峰高度也需对齐子段长度、窗、标度与单双侧谱；密度峰高变化本身不等同于某个节律幅值的变化。

当前工具的绝对频带功率由实际 PSD 频率轴作梯形积分；频带中的可用格点、源数据的已滤波范围与 Nyquist 上限决定数值可覆盖的内容。相对功率的分母以输出的 relative_bandpower_basis 为准，不能默认总是 0.5–50 Hz，也不能因名为 gamma 就认为算到了 IFCN 的 80 Hz。处理后谱的积分描述该处理和带宽下的功率，不能恢复已滤掉的频段。

EEG 的非周期背景常随频率下降，但这不保证每段记录 delta 最高；滤波、周期峰与伪迹均会改变分布。同一信号的共同线性增益可在功率比例中约掉，仍不能据此认定不同设备、通道增益、频率响应、参考与带宽下的相对功率可比；对数功率也不自动消除这些差异。峰频及谱峰值取自当前估计，结合年龄、状态和电极位置解释，不把未经核实的固定正常范围作为自动判据。

原文定位：[SciPy 官方 welch 原文：density/spectrum 单位与输入条件](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)：“*Pxx* has units of V\*\*2/Hz”与“*Pxx* has units of V\*\*2”，条件是 x 以 V、fs 以 Hz 表示；[Welch 1967，p.70 的窗能量归一化 U 与平均周期图公式](https://ocean.phys.msu.ru/courses/geoseries/1967%20Welch%2C%20The%20Use%20of%20Fast%20Fourier%20Transform%20for%20the%20Estimation%20of%20Power%20Spectra_%20A%20Method%20Based%20on%20Time%20Averaging%20Over%20Short%2C%20Modified%20Periodograms.pdf)。

## 闭眼/睁眼与状态变化

- 闭眼时常见枕部 alpha 增强，睁眼或视觉任务可伴抑制；方向和幅度报告本次测量，不预设固定倍数或 beta 必须升高。状态解释需相应实验、空间分布与伪迹证据。
- 描述变化可报告差值和比值。统计检验必须匹配独立试次/被试及配对结构；重叠窗口不能视为独立样本，要用分块/层级方法或试次聚合。未经有效检验不用“统计显著”。变化的生理原因不能由单张谱图确定。

## 额叶 alpha 不对称（FAA）

- 定义：FAA = ln(右侧 alpha 功率) − ln(左侧 alpha 功率)，报告实际同源左右电极、频带、参考和处理。alpha 功率与皮层活动反向，因此：

  | FAA 符号 | alpha 分布 | 相对活动 | Davidson 框架中的关联 |
  | --- | --- | --- | --- |
  | 正值 | 右侧 alpha 更高 | 左额相对更活跃 | 趋近动机 |
  | 负值 | 左侧 alpha 更高 | 右额相对更活跃 | 回避/退缩动机 |

  头皮混合与参考会影响这种解释，它不是直接的皮层激活测量。
- 只有额叶电极对（F3/F4 为主，F7/F8、AF3/AF4 为补充）构成 FAA；C3/C4、P3/P4、O1/O2 等是其他脑区的半球差，不用于趋近/回避解释。
- 理论：Davidson 的趋近/回避研究讨论额叶不对称与动机的群体关联；Harmon-Jones 2003 指出愤怒也可涉及趋近，所以 FAA 对应的是动机方向而不是效价本身。单段 FAA 描述这段记录的偏侧化，个体动机或情绪的判断需要更多证据。
- Smith 2017 讨论参考方式、远源 alpha 混合以及聚合多个短 epoch 的可靠性。约 1–3 分钟无伪迹数据的研究结果不能泛化成所有任务的最低时长；几秒观测也不能直接称稳定个体特质。心理关联的效应依构念、人群、范式和处理方式而变，本库未核对统一效应量。
- 需要已知的左右测点身份；标准通道名是本项目的映射方式。AF7/AF8 的比较不能直接替代已验证的 F3/F4 协议。

原文：[Smith 2017 的 FAA 分数与解释条件](https://pmc.ncbi.nlm.nih.gov/articles/PMC6449497/)；[心理构念与趋近/回避](https://pmc.ncbi.nlm.nih.gov/articles/PMC6449497/)。

## 时域与非线性特征

- Hjorth 的连续时间定义已在 Vidaurre 2009 全文 §2 对 Hjorth 1970 的重述中核对：activity A(x)=var(x)，mobility M(x)=sqrt(var(dx/dt)/var(x))，complexity C(x)=M(dx/dt)/M(x)。实际核对的是作者机构托管的 2009 完整论文；1970 起源论文仅核实书目与索引摘要，未声称取得其全文。
- 当前工具以逐采样差分 Δx 与 Δ²x 替代连续导数，没有乘采样率：activity 单位为输入振幅单位的平方，mobility 标为 per_sample，complexity 无量纲。把差分乘 fs 可近似转换成按秒的导数，但 mobility 此时仍不是 PSD 峰频 Hz；连续导数定义下，单一正弦 x(t)=a sin(2πft) 的 mobility=2πf。一般信号中它描述频率内容/变化速度，不能直接当作主频、某个频带中心或分类标签。
- 对长、均匀采样的纯正弦，忽略有限窗口端点效应时，差分 mobility≈2|sin(πf/fs)|；低频相对采样率足够小时约为 2πf/fs，complexity 约为 1。这解释了同一个 1 Hz 正弦在 fs=100 Hz 时差分 mobility 约为 0.0628，乘 fs 后约为 6.28 s⁻¹，不等于 1 Hz。complexity 是两个 mobility 的比值，可描述与纯正弦的形状差异，但不是以 Hz 表示的带宽。
- 跨记录比较 Hjorth 参数要对齐采样率、差分/导数口径、滤波与窗口长度；差分强调变化较快的成分，噪声与伪迹也会影响它。activity、mobility 或 complexity 的单个变化都不能独立识别伪迹类别。
- 谱熵：所选 PSD 格点归一化后的香农熵除以 log(N)，描述功率分布的均匀程度；高值不证明噪声，低值不证明生理节律。记录频率范围、格点数和估计参数。
- 频带统计量（MAD、峰度、偏度）描述分布与波动；高峰度可提示尖峰、重尾或非平稳片段，需要结合原始波形与空间分布解释，不能独立识别某类伪迹。

原文定位：[Vidaurre 2009，第 2 页（原刊 p.1314），§2 的三参数公式](https://pub.ista.ac.at/~schloegl/publications/Vidaurre2009tdp.pdf)。其文字描述为“Mobility is the mean frequency and Complexity the change in frequency.”；上面的单位与正弦例由该导数公式及本项目实际差分实现推得，不能把这句概括替换成“主峰 Hz”或“带宽 Hz”。

## 连接性

- Pearson 相关、coherence 和 PLV 可受共同源、容积传导与参考影响。imcoh、wPLI 侧重非零相位滞后成分，可减弱部分零滞后混合，同时可能忽略真实零滞后作用；它们不能直接证明真实脑区连接、方向或因果。本工具的 connectivity 是 Pearson 相关，其他估计器在 run_python 中明确选择。
- 重参考可能改变连接性；PPC 估计 PLV²、wpli2_debiased 估计去偏 WPLI²，不能与原值或 coherence 直接互换。记录 epoch 数、长度、频谱估计器与参考。

## 报告规范

- 每个数值带：实际频带与端点规则、外层窗口与 stride、Welch 子段与 FFT/窗/标度、输入及输出单位、预处理链、实际参考与通道集合、时间范围和产物 id。频率格点间距与 ENBW 分别注明其口径。
- 不做统计检验不写「显著」；功率变化不直接写成心理状态。
