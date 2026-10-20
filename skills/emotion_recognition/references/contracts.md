## 标签与专用模型

当前可用路线、精确配方、标签顺序、权重 hash 和执行器以 list_models / route_model 与 prediction 元数据为准；下面说明已登记路线的输入合同，模型更新时以新登记合同为准。

### 标签约定

- 四象限：本项目 V 在前，LVLA/LVHA/HVLA/HVHA 按 `2*(V>=5)+(A>=5)` 编号。A 在前的写法换算：HAHV=HVHA、LAHV=HVLA、HALV=LVHA、LALV=LVLA。
- EAV 五类：索引顺序 neutral、sad、anger、happy、calm，不与四象限互换。
- 类别的中文名与维度含义以 label_definitions 为准；类别得分只描述该类别。

### 盲预测与自评对照

需要盲预测时，先隔离文件自评、类别标签、带情绪词的文件名和刺激类别，保存预测后再对照。用户在对话里给出的自评直接作为本次对照参考，来源写“用户自述”；`labels_available=false` 只说明文件里没有标签。用户已给类别就直接比较；只有需要按量表阈值换算类别时才问评分与阈值。读取文件内标签用 `inspect_record(include_labels=true)`。

对照时逐维说明匹配或不匹配，保留原预测。试次结束后的一次评分对应整个试次，不对应逐秒变化。

### camer_eav_photo_fixed_fold0（EEG + 被试画面）

Fixed CAMER 的 EEG 分支、Swin、MIL、双向交叉注意力、融合 Transformer 与分类头都加载真实权重。输入是完整 20 s EAV speaking 原始 trial：远端做原训练的预处理与 1 s DE-LDS，每 5 s 取均值，30 道后补 2 行零；每 5 s 从 4 fps 时间网格等距取 6 帧原始被试画面（JPEG95、RGB224，不额外裁脸）。用 `recognize_emotion` 一次传 EEG 与配对视频及用途、同步事实；分步时 route_model 与 run_model 使用同一配对输入。EAV MAT 中选中 trial 的原始起点与单 trial NPZ 的 0 秒不同，按窗口表中的坐标配对。缺少配对时只能走 EEG-only 路线，不把 EEGNet 结果称为 CAMER 融合预测。

### eav_eegnet_emotion5_loso_fold0（仅 EEG）

EEG-only speaking 五分类。需要原始 500 Hz、30 道、乳突参考、dataset_native 数值尺度；speaking/listening 未知时先确认，listening 数据不适用这份权重。run_model 按原训练代码处理完整 20 s trial，切 4 个 5 s 窗，每窗对全部通道 × 样本做 z-score。直接给原始 manifest，不先做通用滤波、平均参考、重采样或归一化。

### camer_deap_fixed_1s_fold0（EEG + 被试面部）

接收 DEAP 官方预处理波形（128 Hz、32 道、每 trial 63 s）。用户确认这种格式时，向 recognize_emotion 传 `eeg_metadata={"preprocessing":"deap_official_preprocessed"}` 和其他已知事实，保留原数字尺度与通道约定。执行器用完整 63 s 上下文生成 DE-LDS，开头 3 s 基线不输出，刺激期 3–63 s 逐秒预测；只要局部时段时也用整 trial 准备。视频从刺激开始时 sync_offset_s=3；末端没有视频覆盖的窗口作为缺项处理，不保存补零视频得到的融合结果。权重按测试集准确率选 checkpoint，1 秒是输出粒度，不是实时延迟。

### 评估与其他模型

设计或判断模型评估时检索 knowledge/methods/evaluation_protocol_and_uncertainty.md；“cross-subject”要看实际 person_id 是否隔离。基础模型输入准备见 knowledge/models/foundation_model_selection_and_inputs.md：LaBraM/NeuroLM 的 µV÷100 与 BIOT 的逐通道分位归一化不同，不能把通用 µV 数组直接送入。

## 输出

- 模型预测：直接写主要类别与中文含义、覆盖时间和关键变化，例如“60 秒全部预测为 HVLA（高效价、低唤醒），逐秒结果已保存”。用户问可靠性或需要解释类别切换时，再给 decision_margin 中相应窗口的第一、二名与差值；差值描述输出分离程度。
- 候选解释：把标签、任务依据和支持/冲突读数写在一起；仅有 α/β 变化时描述该变化。示例：“闭眼休息段枕区 α 升高 3 倍，与采集状态一致；效价未定。”需要四象限候选时，分别核对唤醒与效价的依据。
- 自评对照：先直接回答差异，例如“HALV 是高唤醒、低效价，对应 LVHA；我给的 HVLA 两个维度都没对上，α/β 走势不足以支持之前的判断。”
- 只在工具成功返回后说“已保存/已更正”，并引用对应产物；只在聊天中完成的对照说“本次对照”。
