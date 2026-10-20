---
doc_id: models_foundation_selection_inputs
title: 基础模型选型与输入准备：LaBraM、BIOT、NeuroLM、BENDR
doc_type: guide
source: "四模型本地原论文method/appendix与固定commit公开源码；详细版本见model_cards.md"
version: "2026-10-12"
domain: models
tasks: [data_understanding, feature_extraction, emotion_recognition, preprocess, artifact_quality]
---

基础模型可用于表征迁移，或经特定指令训练后输出指定任务答案。选择时先确定任务、标签和评测协议，再核对输入及执行器。采样率、道数、tensor形状相同，只满足形式条件的一部分；参考、montage、幅值、窗口和已训练头仍影响结果。

本文是公开方法的选型导读，不是性能排名；四模型具体论文/代码版本与许可边界见[模型卡](model_cards.md#论文与源码版本)。

## 按目的选候选

| 目的 | 可研究候选 | 选择依据 | 使用前需要 |
|---|---|---|---|
| 有标签EEG，训练专门分类/回归头 | LaBraM | masked神经码预训练，下游平均池化+任务头 | 实际checkpoint、任务头/标签顺序、通道身份、输入recipe和评测 |
| 数据源通道组/时长不同，联合训练或迁移 | BIOT | 每道独立token，频谱与channel embedding组织异构输入 | montage、token身份、STFT、缺片实现、目标任务训练 |
| 同一模型完成多个已定义任务，EEG+指令给类别答案 | NeuroLM | 冻结VQ编码、LLM预训练、联合指令微调 | 配套VQ/LLM、instruction-tuned权重、prompt/答案词表、mask与解码 |
| 比较连续表示对比预训练与完整/简单分类头 | BENDR | 连续卷积表示及多条下游微调路径 | DN3/Deep1010版本、19+1道、256Hz、scale channel和头 |
| 问题是信号结构、质量、频带或事件前后变化 | 普通信号工具 | 直接回答物理测量问题 | 原校准单位、参考、事件上下文、明确窗长 |

前三项的训练/输出依据分别为：原文物理第4页：“For downstream tasks, we use average pooling on the output embeddings followed by task-specific prediction heads.” [定位](https://openreview.net/forum?id=QzTpTRVtrP)；原文物理第9页：“During fine-tuning, we then remove the prediction layers from these pre-trained model and add a new prediction layer to fit the TUEV dataset.” [定位](https://openreview.net/forum?id=c2LZyTyddi)；原文物理第6页：“the loss is only calculated on the answer part of the text to make the prediction more stable.” [定位](https://openreview.net/forum?id=Io9yFt7XH7)。BENDR的简化下游路径：原文物理第8页：“Ignore the pre-trained transformer, average pool the BENDR to four concatenated vectors, add a new classification layer” [定位](https://arxiv.org/abs/2101.12037)。这些证据不赋予任意新标签语义。

若目标是情绪，应先说明标签来自何种任务。LaBraM的直接情绪实验为SEED-V五类；NeuroLM为SEED三类；BIOT和BENDR原论文EEG任务没有直接情绪实验。DEAP valence/arousal、SEED三类、SEED-V五类与自然情境自评不能无训练地互换。公开encoder加随机新head不是已经训练好的情绪分类器。

## 幅值与采样率

| 模型 | 公开EEG输入数值配方 | 不可互换之处 |
|---|---|---|
| LaBraM | 0.1–75Hz、50Hz陷波、200Hz；µV读取后÷100，即0.1mV数值单位 | “µV”标签没有表达最终100倍缩放；频谱target z-score另算 |
| BIOT | EEG实验200Hz；每样本逐道除abs幅值95%分位数；n_fft200/hop100 | 不等于µV÷100或z-score；改FFT/采样须匹配投影层 |
| NeuroLM | 0.1–75Hz、按地区50/60Hz陷波、200Hz；µV÷100 | V直接÷100与先转µV差10⁶倍；频谱target归一化另算 |
| BENDR | 256Hz；每序列共同min/max至[-1,1]，另加相对幅度常数道 | 不是逐道z-score；scale channel和dataset幅值范围是输入的一部分 |

核心原文：原文物理第7页：“we normalize it by setting the unit to 0.1 mV to guarantee the value mainly between -1 to 1.” [定位](https://openreview.net/forum?id=QzTpTRVtrP)；原文物理第3页：“we use the 95-percentile of the absolute amplitude to normalize each channel.” [定位](https://openreview.net/forum?id=c2LZyTyddi)；原文物理第7页：“Given that EEG signal values typically range between -100 µV to 100 µV, all values are divided by 100 for normalization.” [定位](https://openreview.net/forum?id=Io9yFt7XH7)；原文物理第5页：“so that the maximum and minimum values within each sequence equal 1 and −1 respectively.” [定位](https://arxiv.org/abs/2101.12037)；原文物理第6页：“the target sampling frequency of 256 Hz.” [定位](https://arxiv.org/abs/2101.12037)。完整滤波句和源码路径在相应[模型卡](model_cards.md)。

记录单位变换发生在读取、loader还是executor，避免同一步缩放两次。100µV在LaBraM/NeuroLM公开流程中为数值1；传100或0.0001可有相同shape而语义不同。这是单位公式的数学推论，不是新的性能结论。

若分析幅值异常、绝对功率，应保留原校准单位。BIOT的样本幅值归一化和BENDR min/max服务训练表示，不能替代原始测量报告。未知文件也不天然具有BENDR作者各数据集的data_min/data_max；公开[配置](https://github.com/SPOClab-ca/BENDR/blob/ac918abaec111d15fcaa2a8fcd2bd3d8b0d81a10/configs/downstream_datasets.yml#L6)有不同范围。

## 分开记录三个时间尺度

连续记录长度、任务样本窗、patch/token或卷积表示尺度分别记录。1s patch不等于每1s已有可靠情绪标签；1024patch不等于1024个原始采样点。

| 模型 | patch/token或表示尺度 | 原论文样本窗例子 | 具体实现需核对 |
|---|---|---|---|
| LaBraM | 200点/1s，不重叠 | 预训练256patch资源配置；TUAB10s、TUEV5s、SEED-V1s、MoBI2s | 时间嵌入、头；固定5s不是通用模型要求 |
| BIOT | 200点FFT/100点hop，1s token/0.5s overlap | PREST10s、SHHS30s、TUEV5s | token位置、STFT频率维度与总序列 |
| NeuroLM | 200点/1s，不重叠 | SEED4s、HMC30s，其他任务见第6页Table1 | EEG与指令token占用、padding/mask |
| BENDR | 输入256Hz、96倍下采样，约2.67表示/s | 预训练60s；论文下游MMI/BCIC6s、ERN/P3002s、睡眠30s | 事件起点、卷积/位置上下文和代码版本 |

原文：原文物理第7页：“The time window w of a patch is set to 200 (1 second).” [定位](https://openreview.net/forum?id=QzTpTRVtrP)；原文物理第7页：“To ensure stable computing resource usage, the number of patches (sequence length) is limited to 256.” [定位](https://openreview.net/forum?id=QzTpTRVtrP)；原文物理第17页：“The default configuration in the main paper is (1) sampling: 200Hz, (2) token length: 1s, (3) overlaps: 0.5s as reference.” [定位](https://openreview.net/forum?id=c2LZyTyddi)；原文物理第7页：“The patch size P is set to 200 (1 second), consistent with that of LaBraM.” [定位](https://openreview.net/forum?id=Io9yFt7XH7)；原文物理第6页：“During pre-training, we extracted sequences of 60 seconds (every 60 seconds)” [定位](https://arxiv.org/abs/2101.12037)；原文物理第7页：“the effective sampling frequency of BENDR is 96 times smaller (≈2.67 Hz) than the original” [定位](https://arxiv.org/abs/2101.12037)。原文窗口分别见LaBraM物理第6/19页、BIOT第6页Table1、NeuroLM第6页Table1、BENDR第10页Table2，定位链接在[模型卡](model_cards.md)。

LaBraM的patch数为C×floor(T/200)；BIOT公开EEG STFT每道token数为floor((T−200)/100)+1。这里T是点数、C是实际道数；计数仅解释输入组织，不给速度/效果保证。LaBraM公开[时间表](https://github.com/935963004/LaBraM/blob/c431221e6cfd23dbfa9950e0180682fb322b0548/modeling_finetune.py#L286)长度16，不能把架构“arbitrary”宣传成当前checkpoint无限时长。

NeuroLM的1024补齐发生在tokenizer/自回归预训练并mask attention，不是缺电极补零。原文物理第7页：“we pad zeros to ensure the length is equal to 1024 at neural tokenizer training and multi-channel autoregressive pre-training stages.” [定位](https://openreview.net/forum?id=Io9yFt7XH7)；原文物理第7页：“The attention values for these zero paddings will be masked.” [定位](https://openreview.net/forum?id=Io9yFt7XH7)。BENDR公开[erpbci配置](https://github.com/SPOClab-ca/BENDR/blob/ac918abaec111d15fcaa2a8fcd2bd3d8b0d81a10/configs/downstream_datasets.yml#L114)是tmin=-0.1/tlen=0.7，与预印本P300的-0.7/2s不同；复现先固定论文、commit与配置版本。

## 电极身份、参考与montage

FP1-F7是两个测量相减后的双极导联，不能把FP1改名得到；C3还需说明参考。channel embedding参与计算，因此列顺序变化时身份ID必须同步。只检查道数不足以判断兼容。

BIOT监督EEG实验的16montage按原文顺序为：

```text
FP1-F7  F7-T7  T7-P7  P7-O1  FP2-F8  F8-T8  T8-P8  P8-O2
FP1-F3  F3-C3  C3-P3  P3-O1  FP2-F4  F4-C4  C4-P4  P4-O2
```

原文物理第6页：“we used the common 16 bipolar montage channels in the international 10-20 system.” [定位](https://openreview.net/forum?id=c2LZyTyddi)；完整列表在[原文物理第13页](https://openreview.net/forum?id=c2LZyTyddi)，exact quote：“First, the 16 montages (in 10-20 international system) are”。公开[TUAB制作](https://github.com/ycq091044/BIOT/blob/d138e32634e52ae9fa6ec98ac9c4087b14ca869a/datasets/TUAB/process.py#L61)实际执行差分。

BIOT的18项checkpoint表为上述16项加C3-A2/C4-A1，而非每样本同时18道；SHHS只用这两道30s。原文物理第13页：“We use the C3/A2 and C4/A1 EEG channels.” [定位](https://openreview.net/forum?id=c2LZyTyddi)；原文物理第13页：“split each recordings into 30-second samples.” [定位](https://openreview.net/forum?id=c2LZyTyddi)；[公开checkpoint表](https://github.com/ycq091044/BIOT/blob/d138e32634e52ae9fa6ec98ac9c4087b14ca869a/README.md#L51)。

## 缺道与缺片按模型处理

| 模型 | 公开方法/实现 | 不可据此推断 |
|---|---|---|
| LaBraM | 实际名字映射空间嵌入，输入可用通道子集 | 未知电极已有嵌入、任意参考已验证 |
| BIOT | 论文丢缺失token；公开forward按i+n_channel_offset取身份 | 直接删中间道后压缩列表仍保持身份；任意缺片接口已实现 |
| NeuroLM | 训练序列padding的attention被mask | padding等于任意缺电极补零能力 |
| BENDR | 19个EEG模板道缺失置0、额外道忽略，另加scale channel | 其他模型共享此补道策略、缺道无性能代价 |

依据：原文物理第4页：“In cases of missing values, we drop the corresponding tokens directly” [定位](https://openreview.net/forum?id=c2LZyTyddi)；原文物理第6页：“All surplus channels were ignored, and missing channels set to 0.” [定位](https://arxiv.org/abs/2101.12037)；NeuroLM见上节padding原文。公开实现分别为[LaBraM查名](https://github.com/935963004/LaBraM/blob/c431221e6cfd23dbfa9950e0180682fb322b0548/utils.py#L713)、[BIOT索引](https://github.com/ycq091044/BIOT/blob/d138e32634e52ae9fa6ec98ac9c4087b14ca869a/model/biot.py#L112)、[NeuroLM查名/mask](https://github.com/935963004/NeuroLM/blob/0cda9876d8ce6ee07ed0c43eee5e9a6f5c24b177/dataset.py#L48)。

## 评测和结论边界

LaBraM的SEED-V将各session的trial三分后合并；NeuroLM的SEED将trial按时间9:3:3再合并session。二者均不能直接称未见被试LOSO。原文物理第19页：“We merge each part from all sessions of subjects and derive the training, validation, and test set.” [定位](https://openreview.net/forum?id=QzTpTRVtrP)；原文物理第7页：“We split total 15 trials into training, validation, and test trials by 9:3:3 according to the chronological order” [定位](https://openreview.net/forum?id=Io9yFt7XH7)。选择模型时用与目标场景相符的被试/时间划分评估，而不把论文某个均值当个人预测置信度。

NeuroLM的EEG-text是空间分布级对齐，不是逐样本语义配对；原论文使用指定任务prompt和答案类别。原文物理第4页：“we propose to align EEG with text space-wise instead of embedding-wise.” [定位](https://openreview.net/forum?id=Io9yFt7XH7)；原文物理第15页：“Question: Which emotion type does this EEG segment belong to? Answer: {Positive, Neutral, Negative}” [定位](https://openreview.net/forum?id=Io9yFt7XH7)。这不提供自由生成自然语言诊断的验证。

更多参数或预训练数据不自动保证当前目标最优。NeuroLM指出大模型更易overfit；BENDR比较完整与较简单下游模型时置信区间重叠。原文物理第7页：“models with more parameters are more prone to overfitting” [定位](https://openreview.net/forum?id=Io9yFt7XH7)；原文物理第10页：“confidence intervals always overlapped” [定位](https://arxiv.org/abs/2101.12037)。它们是各自实验范围内的观察，不形成跨论文排名。

## 当前项目如何使用

当前接入能力由 `list_models`、registry 与实际执行器提供。公开资料描述模型方法与候选输入；项目自有权重的实际 recipe、标签顺序和 hash 读取其登记条目。表征候选、研究参考与已登记情绪任务路线分别查询，避免用历史笔记的安装状态判断当前可运行能力。

未知记录可先用普通工具回答结构、掉线/饱和、工频、频带功率和时间变化，保留原始单位与事件上下文。实际可执行路线读取当前工具返回。报告测量发现与假设；缺少任务模型时不把频带变化升级成情绪、疾病或睡眠标签。

最终类别需要实际checkpoint/hash、已训练头或指令、精确输入recipe、执行器及适合使用场景的验证。源码MIT、论文许可、数据条款和具体权重许可分别判断；BENDR本地缺LICENSE为信息缺口，不自动等于已证明禁止使用。详细版本与来源见[模型卡](model_cards.md#论文与源码版本)。



## 2026-10-14 补充：布局无关路线（HEAR、LUNA）

两条 2025 年公开路线把电极布局异构作为一等问题；它们同样是公开 recipe，选型时与上表并列考虑；项目接入状态从运行工具目录读取。

- **HEAR**（arXiv 2510.12515）：用可学习、基于坐标的空间嵌入把不同布局和数量的电极映射到统一表示空间，再由空间引导 Transformer 建模时空依赖。原文物理第 1 页：“HEAR employs a learnable, coordinate-based spatial embedding to map electrodes with diverse layouts and varying counts into a unified representational space.”；同页报告 8,782 小时、150+ 种布局、最多 1,132 电极的训练数据。使用前提是电极的 3D 坐标身份真实可得；坐标缺失或错误时空间嵌入没有可靠依据，道数相同不能代替。
- **LUNA**（arXiv 2510.22257，NeurIPS 2025）：以 Q 个可学习 query 与交叉注意力把任意通道集合压缩为固定大小 latent（对通道置换不变、随通道数线性），时间注意力只作用于 latent。公开预处理：0.1–75 Hz 带通、50/60 Hz 陷波、重采样 256 Hz、逐通道 z-score；多数数据 5 秒非重叠样本（SEED-V 用 1 秒），patch 为 40 个采样点（原文物理第 6 页）。论文报告 TUAB 81.57%、TUAR 0.921 AUROC、TUSL 0.802、SEED-V 39.18%（第 2 页），并称相对全注意力降 FLOPs 约 300×、显存约 10×。这些是论文基准，不是本项目复现结果；其逐通道 z-score 与 LaBraM/BIOT/NeuroLM/BENDR 的数值配方不同，不可互换。
