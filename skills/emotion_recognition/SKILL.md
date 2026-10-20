---
name: emotion_recognition
description: 执行情绪分类、审阅与情绪有关的 EEG 特征，或给出并保存候选判断。适用于 EEG/被试视频识别、已有标签对照及情绪时间变化问题。
modalities: [eeg, face_video]
allowed-tools: [recognize_emotion, analyze_signal, segment_signal, extract_features, read_artifact, save_emotion_assessment, plot, route_model, run_model, export]
---

# 情绪分析

## 有适用模型时：recognize_emotion 一步完成

把消息或采集说明中已给出的 EEG 元数据（eeg_metadata）、配对被试录像（video_manifest_id、video_role=face、sync_offset_s）和目标时段一次传给 `recognize_emotion`。它登记事实、选择已登记模型、在远端按训练配方准备输入、执行并导出 CSV，返回类别计数、时间覆盖、逐窗预览和 decision_margin。用户不需要说模型名。只有指定模型、分步核对或重新导出时才用 route_model / run_model。

回答方式：
- 直接给类别结果（中文含义取 label_definitions）、覆盖时段和结果链接。
- 结果存在类别切换、关键冲突或用户问可靠性时，用 decision_margin 定位类别得分接近的窗口；需要报告时给第一、二名与差值。日常回答以主要类别和时间变化为主，分数按 score_kind 称为“得分”（logits）或“概率”。
- 模型名、权重和配方留在产物来源，用户问到时再展开（细节 read_skill(name=emotion_recognition, reference=contracts.md)）。

## 没有适用模型时：特征分析 + 你的判断

route_model 或 recognize_emotion 说明不兼容时，直接转特征分析，不要停下来问是否继续：

常规整段或指定时段的测量先用 `analyze_signal(emotion_review=true)`，一次完成质量、波形、PSD、逐窗特征、CSV 和 unassessed 审阅；读取已有结果后再给候选解释。需要清洗、指定特征口径或逐窗候选时才按下面的原子步骤执行，已有产物直接复用。

1. 必要时清洗（见 artifact_quality），分窗（4 s 窗 2 s 步长，或按问题选），提取 bandpower、de、spectral_entropy、hjorth；有额叶左右电极对时加 asymmetry。
2. 读关键时段和通道的数值。
3. 按问题给出判断：
   - 唤醒：先比较同一记录中的任务/基线时段和空间分布。枕区 α 增强可对应闭眼，额颞宽带高频可来自肌电，先结合波形和采集说明解释。只有多项变化与任务一致时，才提出偏低或偏高唤醒的候选；孤立的 α/β 比值不指定情绪。
   - 效价：FAA 用额叶左右电极对，负值与回避相关、正值与趋近相关；这描述动机方向，不能直接换成高/低效价。缺少任务适用的效价证据时保留效价未定。
4. 保存：只描述测量时 `save_emotion_assessment(assessment_status=unassessed, label=null)`；用户要判断且证据支持候选时用 hypothesis，rationale 写支持和冲突的具体读数。只支持唤醒倾向时直接说明，保存 unassessed 与该测量解释；只有效价和唤醒都有依据时才给四象限候选。用户指定类别空间时按其空间作候选或保留未评定；逐段判断引用窗口行号与起止时间。

回答时把判断与本次实验事实放在一起，例如“闭眼休息段的枕区 α 比任务段高 3 倍，与采集状态一致；效价未定”。有独立的任务依据支持低唤醒候选时再给该候选，并列出对应读数；α 升高、β 降低本身作为测量变化描述。

## 对照用户自评

用户给出自评时，先统一维度和顺序再比较：本项目 V 在前（HVLA = 高效价、低唤醒）；A 在前的写法 HALV = 高唤醒、低效价 = LVHA。逐维说匹配或不匹配，保留原预测或原候选，不改写成自评标签。没对上就直接承认，并说明哪条依据不足。

## 视频与时间

被试面部录像才用于联合识别，刺激视频不能替代。video_time = original_eeg_time − sync_offset_s；trial/epoch 边界与原始时间坐标取产物记录。
