---
doc_id: methods_emotion_eval
title: 情绪标签空间、划分协议与 EEG 情绪识别的泛化边界
source: Russell J.A. 1980, J Pers Soc Psychol 39:1161 (circumplex); Koelstra S. et al. 2012, IEEE Trans Affect Comput 3(1):18-31 (DEAP); Li X. et al. 2022, "EEG based Emotion Recognition: A Tutorial and Review", ACM Comput Surv 55(4); Zheng & Lu 2015; LibEER split.py; Guo C. et al. 2017 ICML (temperature scaling)
version: 2026-10-15
domain: methods
tasks: [emotion_recognition, data_understanding, compare_conditions]
---

## 标签空间

- 维度模型（Russell 环状模型）：效价 valence（不愉快–愉快）× 唤醒 arousal（平静–激动）。DEAP、AMIGOS、MAHNOB 用 SAM 量表 1–9 自评；DEAP 另有 dominance、liking（1–9）和 familiarity（1–5）。
- 二值化：1–9 自评常用阈值 5，但需保存等号规则、评分列顺序与整数类别映射。TorchEEG Binary(5) 用 ≥5 为高；LibEER bounds=[5,5] 时 ≤5 为低。四象限定义标签语义，具体权重的索引顺序采用其训练记录、运行登记与 prediction.label_definitions。AMIGOS 自评 arousal 在前，20 s 外部标注为 −1 至 1，不能套同一阈值。
- 缩写顺序：本项目 V 在前；DEAP 等资料也使用 A 在前的 LALV、LAHV、HALV、HAHV。换成 V 在前分别为 LVLA、HVLA、LVHA、HVHA。HALV 是高唤醒、低效价，不能读成高效价、低唤醒；HALV/LVHA 与 HVLA 两个维度都相反。比较前按维度含义统一写法，不能只看字母位置。
- 离散模型：Ekman 基本情绪；EAV 用 neutral、sadness、anger、happiness、calmness 五类；SEED 用 positive、neutral、negative 三类。
- 自评是常用的实验标注来源，记录评分主体、量表与单位；它描述被试报告的体验，不保证跨个体共同阈值。
- 上述是数据集量表规范；上传文件仅有四列 numbers 时，不能自行将它们当成被试自评或真值。先确认标签顺序、量表、评分主体与分类阈值。
- 用户明确说“我的自评是 HALV”时，该类别可直接作为本次对照参考，来源为用户自述。文件 labels_available=false 只说明文件未提供标签，不能据此否定或搁置用户已经给出的自评。类别对照不必再索要数字；实际要做阈值换算时才补相应量表与评分。

## 划分协议决定结果可信度

完整评估设计见 [泄漏、评分单位与不确定性](evaluation_protocol_and_uncertainty.md)。协议名在文献中有歧义，核对实际person/session/trial集合；LibEER v3将不同session视为不同“subjects”，复现该设置不能自动证明对新自然人泛化。

标签实现：[TorchEEG Binary 的等号规则](https://github.com/torcheeg/torcheeg/blob/87e595ba0d97a52ad4d8e36dbd956c655b4e1993/torcheeg/transforms/label/binary.py)；[LibEER 的二值化与维度顺序](https://github.com/XJTU-EEG/LibEER/blob/dddff9776dbdae21195fe320dff0a5ba61628a18/LibEER/data_utils/preprocess.py)。

- 窗口随机划分：相邻/同 trial 窗口混入训练与测试，不能作为独立 trial 或新被试泛化证据；结果是否受影响需结合具体数据与协议。
- 跨 trial：同一 trial 不跨折。CAMER 公开配置为 five_fold_cross_trial；是否按单被试分别训练、是否被试重叠由实际 split 脚本确定，不能仅凭名称称被试内或 LOSO。
- 被试独立 / 留一被试（LOSO）：训练集不含测试被试，对应「新用户不校准」。
- 跨数据集 / 跨设备：不同采集系统、通道、刺激会改变分布，性能需要目标条件下的独立验证，不能用原数据集准确率代替。

## 泛化边界

- 报告文献准确率时必须带具体论文、分类任务、样本、划分协议与指标，不能把不同实验拼成通用性能区间。
- EEG 特征可以用于建立和检验情绪模型，但当前记录中的 α↑、β/γ↓并不独立证明低唤醒，反向走势也不独立证明高唤醒。FAA 的主要理论涉及趋近/回避动机，趋近并不等于正效价（例如愤怒也可能涉及趋近）；其符号不能直接给当前个体正负效价。关联的适用程度取决于实验、人群、参考、质量及已验证映射，见 methods_spectral。
- 本项目实际计算频带功率、DE、谱熵、Hjorth 等测量；保存 emotion_assessment 时，unassessed 记录发现且 label=null，hypothesis 记录用户要求的候选。没有适用映射时不强选四象限。LLM 候选与专用 EEG 模型预测分别表述，专用模型仍需真实执行器；保存一个候选不是 validation。
- 被试内标注可用于校准或适应，但适应数据必须与最终评测数据隔离。

## 本项目情绪分析与对照单位

- 用户指定标签就使用该任务标签；明确要求效价×唤醒候选时可采用四象限。一般特征审阅不需要人为生成标签空间，证据不足时直接保存未评定结果与具体发现。
- 用户指定起止秒数时全链路使用同一范围；未指定时覆盖整段。短窗是测量单位；候选和参考标签按照真实试次/记录标注单位比较。
- 盲预测阶段隔离自评、标签字段、带情绪词的文件名和刺激类别，先冻结预测再读参考。用户随后给出的自评可以用作对照，但不能反过来改原预测使其匹配。只有试次标签时按试次比较，局部时间线不能当有逐秒真值的成绩。
- 候选与自评不一致时，先按 valence/arousal 分别指出不匹配。没有原因证据就承认本次候选没对上，不假设伪迹、分析片段或评分时点造成错误，也不将不一致弱化成“没有强冲突”。原助手回答不是证据，必要时回查原测量和文献映射。
- 对话先回答具体发现或对照结果，再给必要依据与实际结果引用，不堆叠免责声明。聊天里的对照不等于新增产物；只有相应保存工具真正成功后，才声称“已保存/已更正/已并列存档”并引用新文件。

## 评估指标

- 类别不平衡时报 macro-F1、balanced accuracy、适用的 AUROC，并写明类别比例。50%/25%/20% 是均匀随机猜测的准确率；实际 accuracy 基线还应报告多数类和具体随机策略，不能把所有指标都用该数字比较。
- 窗口级预测按 trial 或被试聚合后再评估；同一 trial 的相邻窗口不是独立样本。
- 模型、固定特征 LLM 与自主工具 Agent 使用相同记录、候选标签和最终评分单位。切片模型的聚合方法在验证集冻结；不能拿逐窗正确比例与整段单个标签的准确率直接比较。合成信号只验证工具流程与数值，不用于证明真实情绪识别精度。
- 概率校准：softmax 可称模型置信分数，但不能未经验证解释为实际正确率。temperature scaling 在独立校准集拟合，再在隔离评测集检查 ECE/可靠性；使用该方法不保证分布变化后的校准。

原文：[Guo 2017 的置信度/校准定义](https://arxiv.org/abs/1706.04599)；[校准方法](https://arxiv.org/abs/1706.04599)。该论文不能证明本项目 EEG 模型已经校准。

分布变化后的校准另见[Ovadia2019第7页](https://arxiv.org/abs/1906.02530v2)。其任务是图像、文本和类别数据；在EEG上的结论需要目标域评测。ECE低也不保证类别预测有信息，同时检查概率评分与分类性能。

## 视频与多模态

- 面部表情识别与 EEG 是独立证据；无同步时钟时不能逐窗融合，只能分别报告并注明。
- 刺激视频是实验上下文，不能替代被试面部视频或证明被试感到的情绪；只有训练契约明确支持刺激输入的模型才可按对应任务使用。
