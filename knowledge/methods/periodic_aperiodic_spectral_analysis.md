---
doc_id: methods_periodic_aperiodic_spectral_analysis
title: 周期与非周期谱分析：输入、参数和拟合决策
source: Donoghue et al. 2020, Nat Neurosci23:1655–1665 (10.1038/s41593-020-00744-x, PMC作者稿全文); fooof v1.1.1 fit.py源码
version: 2026-10-12
domain: methods
tasks: [spectral_analysis, feature_extraction, compare_conditions]
---

## 什么问题需要谱参数化

固定频带功率改变可能来自峰强度、峰频移动、非周期offset或exponent变化。要区分这些统计变化，可用周期/非周期谱模型，而不能把频带功率变化直接解释为振荡强度变化。参数化结果仍不独立确定情绪、源位置或唯一生理机制。[原文Introduction：频带变化的不同来源](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

## 先区分PSD估计、API输入和模型空间

FOOOF拟合PSD，不直接由时域信号生成谱。原论文研究采用Welch谱；使用其他谱估计时，也需报告分段、窗口、重叠、分辨率、平均方式和拟合频率范围。[原文Methods → Algorithmic parameterization：PSD估计](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

原模型在semilog-power空间工作：频率是线性等间隔Hz，功率为log10。周期峰沿线性频率轴建模；不能把完整算法概括成对log-log图拟合一条直线，只有fixed模式的非周期背景有该直线关系。[原文Methods → Algorithmic parameterization：拟合空间](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)；[fixed模式与exponent](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

**fooof v1.1.1 API需要线性PSD输入**，`fit(freqs, power_spectrum, freq_range)`内部才取`np.log10(power_spectrum)`。传入已log10的功率会重复转换。软件版本与论文模型空间分别注明。[1.1.1源码输入说明](https://github.com/fooof-tools/fooof/blob/v1.1.1/fooof/objs/fit.py#L428)；[内部转换L1409](https://github.com/fooof-tools/fooof/blob/v1.1.1/fooof/objs/fit.py#L1409)

## 按定义读参数

模型为P(f)=L(f)+ΣGn(f)，在log10功率空间分解背景与Gaussian峰。背景L(f)=b−log10(k+f^χ)：b为offset、χ为exponent、k为knee。fixed模式令k=0，此时χ为log-log谱斜率的负值；knee模式可描述背景弯曲。[原文Methods → Algorithmic parameterization：模型式与背景参数](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

| 参数 | 分析时怎样解释 | 容易混淆的量 |
| --- | --- | --- |
| offset | 背景整体位置，依赖功率单位和尺度 | 不同单位/增益下的offset差异不能直接解释成生理差异 |
| exponent | 非周期背景随频率的变化，依赖拟合范围及模式 | 不是周期峰参数，也不是直接的兴奋/抑制比测量 |
| peak CF | 拟合峰中心频率，单位Hz | 与固定频带中心不同 |
| peak PW | 拟合背景以上的峰功率 | 不等于频带积分功率；重叠峰时与单个Gaussian高度不同 |
| peak BW | 返回峰宽2σ，单位Hz | 不是FWHM，也不是预设频带宽度 |

依据：[原文Methods → Algorithmic parameterization：CF、背景以上峰功率与BW=2σ](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)；[fooof1.1.1参数说明](https://github.com/fooof-tools/fooof/blob/v1.1.1/fooof/objs/fit.py#L153)与[峰参数转换](https://github.com/fooof-tools/fooof/blob/v1.1.1/fooof/objs/fit.py#L1184)。

从该模型直接推得：如果信号幅度乘c且其他条件不变，PSD乘c²，log10功率整体平移2log10|c|，offset随之改变。因此比较offset前先统一单位、增益和谱估计口径。

## 选择fixed/knee和峰设置时看什么

算法先估计背景并减去背景，寻找候选峰，再联合拟合多个Gaussian、去峰后重新拟合背景。候选峰搜索结果与最终峰可以不同；一个局部最大值不自动就是模型确认的周期峰。[原文Figure2：候选峰用于联合拟合](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)；[Methods：去峰后重新拟合背景](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

频率范围、峰宽、峰数与阈值影响拟合。存在knee却使用fixed模式时，背景弯曲可能被误拟成exponent变化或额外峰。跨被试/状态比较应固定并报告模式和频率口径，观察残差及峰数量。[原文Extended Data Figure3 A–C：knee错误建模](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

非Gaussian或偏斜峰会改变中心频率与峰数估计。[原文Extended Data Figure3 D–F：偏斜峰模拟](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)。一个非正弦时域过程可以产生多个谐波峰，多个Gaussian不自动等于多个独立振荡器。[Figure3 G–I：谐波峰](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)。谱模型返回频域参数，不能据此单独重建时域波形或判断瞬时状态。[Supplementary Modeling Note：模型输出范围](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

没有清晰峰时，固定频带仍有功率；窄带滤波后的波形看似振荡，也不证明原谱存在独立周期峰。[原文Extended Data Figure1：窄带滤波产生表观振荡](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

## 高R²和MAE不能省略口径核对

高R²或低整体误差不能独立保证参数准确，增加许多峰可以降低整体误差并过拟合噪声。结合峰数量、谱形状和残差评估结果。[原文Results → Algorithm performance against simulated data：整体拟合误差的局限](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)

原论文拟合质量段将MAE写成median absolute error；**fooof v1.1.1** `error_`的MAE代码实际为 `abs(observed_log_power − fitted_log_power).mean()`，即log10功率空间的mean absolute error。报告1.1.1软件结果应写平均绝对误差；复述原文median指标时按原文标注，不能只凭MAE缩写互换。[原文Methods → Algorithmic parameterization：median MAE原句](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)；[1.1.1源码L1318–1319](https://github.com/fooof-tools/fooof/blob/v1.1.1/fooof/objs/fit.py#L1318)

## 报告与版本

报告输入PSD单位、谱估计、频率范围/分辨率、软件版本、fixed/knee模式、峰宽/数量/检测阈值、参数定义，以及误差的空间和mean/median口径。展示原谱、拟合背景与总模型，检查残差和可疑峰。这些字段是依据模型条件形成的分析建议，不是通用情绪阈值。

原文载体为PMC作者稿，按Methods/图号定位。[当前PMC Methods的软件名称](https://pmc.ncbi.nlm.nih.gov/articles/PMC8106550/?pdf=1)与[原刊Code availability的fooof链接](https://www.nature.com/articles/s41593-020-00744-x)分别记录；名称迁移不等于API兼容。本卡实核软件代码为fooof1.1.1，对specparam或其他版本按实际文档与源码确认。
