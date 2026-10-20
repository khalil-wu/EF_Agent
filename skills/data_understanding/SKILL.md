---
name: data_understanding
description: 理解新记录或采集文件夹的结构、采集协议、试次与同步关系，确定本次分析所需的元数据和范围。适用于新设备、未知格式、批量数据与采集说明。
modalities: [eeg, video, document]
allowed-tools: [list_files, workspace_files, read_file, read_document, inspect_record, update_record_metadata, device_lookup, search_knowledge]
---

# 数据理解

## 默认做法

上传附件已按内容登记：长数值波形是 EEG，自评 CSV、事件 TSV、TXT/Word/JSON 说明是文档。先读与本次目标有关的说明，再用其中的采样率、通道与处理历史更新 EEG 记录；标签表用于分组或对照，保留表的来源与行号。

符合 DEAP 预处理结构的记录已按公开规格使用 128 Hz、Geneva 前 32 道和 63 s 时间轴，直接做频谱与特征分析。送专用模型前才核对官方预处理合同；用户给出不同采集事实时更新记录。识别数据集时查对应 knowledge/datasets 笔记的文件名、字段、设备与通道特征，再与文件内容核对。

先把用户的分析目标对应到实际文件、被试、试次、事件与时间范围。已有文件引用或路径直接读取；目标未知时查当前工作区，按分页结果覆盖目标集合，遵守排除清单。批量任务读取采集说明、文件命名规则与标签 sidecar，确认哪些文件属于同一记录。

采样率、完整通道名、单位、参考和既有处理链优先采用文件及用户已给的信息；将补充事实通过 update_record_metadata 登记并使用返回的新 manifest。只有影响当前计算且现有资料查不到的缺项才集中询问。设备库用于提出候选配置，按真实设备与采集记录确认。

一般记录已有滤波可登记 `preprocessing={"highpass_hz":1,"lowpass_hz":40}`；这是来源已做的处理事实，不会重新执行。已知数据集的专属输入合同仍用对应名称。工具的 provenance.steps 记录本系统实际执行，source_processing 保留上游声明。带 `# EFAgent metadata: {...}` 头的导出 CSV 会读取明确的采样率、通道和单位，a.u. 保留任意单位身份。

按当前问题读取必要内容：文本用 read_file，已登记文档用 read_document，波形结构用 inspect_record。trial_count 是原文件的试次数，trial_index 从 0 起；默认首试次只是当前视图。全文件任务覆盖实际试次集合并在汇总中保留文件、trial 和原始时间坐标。

采样率决定 Hz 与秒，单位决定物理幅值，电极位置决定脑区、插值与地形图；为当前方法补齐对应信息。未知物理单位仍可保留原始数值做适用的相对分析，单通道可分析波形与频谱。格式或数组形状用于选择读取方式，数据来源和既有处理历史采用实际记录。

视频按被试画面、刺激或其他用途登记；配对分析采用真实同步与解码帧覆盖。sync_offset_s 表示视频 0 秒在原 EEG 坐标中的位置，video_time=original_eeg_time-offset；试次起点和裁剪偏移取实际窗口表。用户已给用途与对齐关系时直接采用。

模型输入以 list_models、route_model 和实际执行器的登记合同为准。核对特定数据集或情绪模型时按需 read_skill(name=emotion_recognition, reference=contracts.md)，通用数据理解保留原始尺度、真实通道和试次边界。

完成后给出可分析的范围与必要数据概况，保存实际登记的元数据；批量任务同时形成文件—被试—试次的对应关系。需要质量筛查时转 artifact_quality，需要执行清洗或特征计算时继续对应工具。
