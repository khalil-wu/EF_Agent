"""Tool definitions served to the gateway (GET /tools) and dispatch (POST /tools/{name}).

Definitions follow contracts/tool.schema.json. Only tools listed here are callable.
``requires`` is enforced by the gateway's before_tool hook. The gateway injects ``_session_id`` into every
call; tools that need it (scratch dir, read_file roots) receive it, the rest ignore it.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any, Callable

from eegagent.worker.cleaning import clean_artifacts
from eegagent.worker.attachments import read_document, register_attachment, register_upload
from eegagent.worker.devices import device_lookup
from eegagent.worker.emotion import save_emotion_assessment
from eegagent.worker.emotion_pipeline import recognize_emotion
from eegagent.worker.files import edit_file, list_files, read_file, save_to_workspace, write_file
from eegagent.worker.inspect_record import inspect_record
from eegagent.worker.knowledge import knowledge_graph, search_knowledge
from eegagent.worker.outputs import export, plot, read_artifact
from eegagent.worker.processing import extract_features, preprocess, segment_signal
from eegagent.worker.registry import check_model_compat
from eegagent.worker.models import list_models, route_model
from eegagent.worker.remote_model import run_model
from eegagent.worker.metadata import update_record_metadata
from eegagent.worker.measurements import quality_summary, detect_transients
from eegagent.worker.sandbox import run_python
from eegagent.worker.store import load_manifest, save_manifest
from eegagent.worker.workspaces import workspace_files, quality as workspace_quality
from eegagent.worker.local_analysis import analyze_signal

_SRC = {"type": "string", "description": "manifest id（sig_…）或 normalized_signal 产物 id（art_…）"}
_RO = {"runtime": "python_worker", "async": False, "execution_mode": "sequential", "permission_mode_min": "readonly", "estimated_cost": "1", "requires": {}}
CLEANING_DEPS = {"bad_channels": ("pyprep",), "asr": ("asrpy",), "ica_iclabel": ("mne_icalabel", "picard")}
CLEANING_MISSING = {method: [module for module in modules if find_spec(module) is None]
                    for method, modules in CLEANING_DEPS.items()}
CLEANING_METHODS = [method for method, missing in CLEANING_MISSING.items() if not missing]
RECORD_METADATA_PROPERTIES = {
    "sample_rate_hz": {"type": "number", "exclusiveMinimum": 0},
    "channel_names": {"type": "array", "items": {"type": "string"}},
    "unit": {"type": "string", "enum": ["V", "mV", "uV", "dataset_native", "a.u."]},
    "dataset": {"type": "string", "description": "用户声明的数据集名称"},
    "reference": {"type": "string", "enum": ["referential", "bipolar", "average", "mastoids", "unknown"]},
    "baseline_duration_s": {"type": "number", "minimum": 0, "description": "用户确认的开头基线时长；登记不执行去基线"},
    "label_names": {"type": "array", "items": {"type": "string"}, "description": "文件标签列的实际含义，长度需与 labels.values 一致"},
    "task_mode": {"type": "string", "enum": ["speaking", "listening", "unknown"], "description": "用户或采集说明确认的 EAV 任务模式；speaking 权重不能用于 listening"},
    "preprocessing": {"anyOf": [{"type": "string", "enum": ["deap_official_preprocessed", "eav_raw"]},
        {"type": "object", "properties": {"highpass_hz": {"type": "number", "minimum": 0},
            "lowpass_hz": {"type": "number", "exclusiveMinimum": 0},
            "bandpass_hz": {"type": "array", "items": {"type": "number", "minimum": 0}, "minItems": 2, "maxItems": 2},
            "notch_hz": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0}},
            "reference": {"type": "string", "enum": ["referential", "bipolar", "average", "mastoids", "unknown"]}}, "additionalProperties": False}],
        "description": "登记已有处理事实，不执行处理。已知数据集用契约名；一般记录用明确的highpass_hz/lowpass_hz等对象。采样率、通道和数值单位分别登记。"},
    "video_role": {"type": "string", "enum": ["face", "stimulus", "other"]},
    "sync_offset_s": {"type": "number", "description": "视频0秒在原始EEG轴的位置；单trial视频对齐该trial的原始起点"},
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {"name":"analyze_signal","version":"1.0","description":"一次完成所选 EEG 记录/试次的本地质量、波形、PSD、逐窗特征和 CSV。没有适用分类模型而用户需要情绪分析时设 emotion_review=true，同时保存测量审阅；不运行分类模型。eeg_metadata 只传消息或采集说明已有的事实，已登记字段省略。", "input_schema":{"type":"object","properties":{"manifest_id":{"type":"string"},"path":{"type":"string"},"trial_index":{"type":"integer"},"start_s":{"type":"number"},"end_s":{"type":"number"},"window_s":{"type":"number","default":4},"stride_s":{"type":"number","default":2},"features":{"type":"array","items":{"type":"string"}},"emotion_review":{"type":"boolean","default":False},"eeg_metadata":{"type":"object","additionalProperties":False,"properties":{key:value for key,value in RECORD_METADATA_PROPERTIES.items() if key not in ("video_role","sync_offset_s","label_names")}}}},"output_schema":{"type":"object"}, **_RO},
    {"name":"workspace_quality","version":"1.0","description":"一次检查当前工作区中全部未排除 EEG 文件及其全部试次的完整质量，保存逐文件/试次 CSV。不会调用情绪分类模型，不删除或自动排除文件。已有采样率可一次传入；文件自带参数优先采用。", "input_schema":{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string"},"sample_rate_hz":{"type":"number","exclusiveMinimum":0}}},"output_schema":{"type":"object"}, **_RO},
    {"name": "workspace_files", "version": "1.0", "description": "管理当前挂接的本地工作区。list 查看目录，inspect 登记 EEG/视频/文档结构并返回实际 manifest_id；exclude/include 控制批量分析范围，不删除文件；mkdir/rename/move 整理原文件；trash 移到可恢复的回收区，restore 恢复。仅按用户的文件管理目标操作。",
     "input_schema": {"type":"object","properties":{"action":{"type":"string","enum":["list","inspect","exclude","include","mkdir","rename","move","trash","restore"]},"path":{"type":"string"},"paths":{"type":"array","items":{"type":"string"}},"destination":{"type":"string"},"trash_id":{"type":"string"}},"required":["action"]},"output_schema":{"type":"object"}, **_RO},
    # ------------------------------------------------------------ files
    {
        "name": "list_files", "version": "1.0",
        "description": "在允许的数据目录中查找 EEG、视频或说明文件。返回路径供 inspect_record/read_file 使用。",
        "input_schema": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "文件名通配，如 *.edf 或 s01*"},
            "kind": {"type": "string", "enum": ["eeg", "video", "any", "all"], "default": "eeg"},
            "root": {"type": "string", "description": "只在某个允许的目录下找（可选）"},
            "limit": {"type": "integer", "default": 50}, "offset": {"type": "integer", "default": 0}}},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "read_file", "version": "1.1",
        "description": "按行读取文本、CSV 表头、脚本或 scratch 输出。相对路径基于当前工作区。二进制数据用 inspect_record，文档正文用 read_document。",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer", "default": 1, "description": "起始行，从 1 起"},
            "limit": {"type": "integer", "default": 200}}, "required": ["path"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "write_file", "version": "1.0",
        "description": "在当前工作区写入协议、配置、Python脚本或报告等文本文件。path为包含文件名的相对路径，自动创建父目录；现存文件只有overwrite=true才覆盖。返回真实文件路径，可打开预览。",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False}}, "required": ["path", "content"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "edit_file", "version": "1.0",
        "description": "精确修改当前工作区的文本文件，将old_text替换为new_text。默认old_text须唯一匹配，replace_all=true替换全部匹配。相对路径；保留未修改部分和原有换行。",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string", "minLength": 1}, "new_text": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False}}, "required": ["path", "old_text", "new_text"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "save_to_workspace", "version": "1.0",
        "description": "将真实分析产物复制到当前工作区的指定相对文件路径，保留原产物及来源记录。path须包含文件名；自动创建父目录，现存目标只有overwrite=true才覆盖。",
        "input_schema": {"type": "object", "properties": {
            "artifact_id": {"type": "string"}, "path": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False}}, "required": ["artifact_id", "path"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "read_document", "version": "1.0",
        "description": "按页读取已登记的 PDF、Word、Markdown、TXT、JSON 和标签/事件表正文，返回页码及继续读取的 next_offset。查找相关段落先 search_knowledge(scope=session)；长页按 has_more 与 offset 继续。",
        "input_schema": {"type": "object", "properties": {
            "manifest_id": {"type": "string"}, "page": {"type": "integer", "default": 1},
            "limit": {"type": "integer", "default": 3}, "offset": {"type": "integer", "minimum": 0, "default": 0}}, "required": ["manifest_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    # ------------------------------------------------------------ data
    {
        "name": "recognize_emotion", "version": "1.0",
        "description": "一次完成情绪识别：登记消息或采集说明中已给出的EEG元数据及配对录像用途/同步，自动选择适用的已登记模型，执行分类并默认导出CSV。用户无需指定模型名。eeg_metadata只填用户或采集说明明确给出的事实；已登记字段可省略。返回全部类别计数、实际时间覆盖、逐窗预览、得分差距最小的窗口（decision_margin）与结果产物，可直接据此回答，不必再route/run/read/export。缺项或输入不符时返回具体原因，不执行推理。",
        "input_schema": {"type": "object", "properties": {
            "manifest_id": {"type": "string"}, "video_manifest_id": {"type": "string"},
            "eeg_metadata": {"type": "object", "description": "仅用户或采集说明明确提供的EEG采集与处理事实；不从形状猜测，不填模型默认值。", "additionalProperties": False,
                             "properties": {key: value for key, value in RECORD_METADATA_PROPERTIES.items()
                                            if key not in ("video_role", "sync_offset_s")}},
            "video_role": {"type": "string", "enum": ["face", "stimulus", "unknown"], "default": "unknown"},
            "sync_offset_s": RECORD_METADATA_PROPERTIES["sync_offset_s"],
            "start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
            "max_trials": {"type": "integer", "minimum": 1},
            "export_formats": {"type": "array", "items": {"type": "string", "enum": ["csv", "json"]}, "uniqueItems": True, "default": ["csv"], "description": "按需下载格式；空数组表示不导出"},
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 900, "default": 300}},
            "required": ["manifest_id"], "additionalProperties": False},
        "output_schema": {"type": "object"}, **dict(_RO, estimated_cost="complete_trials * remote_model"),
    },
    {
        "name": "inspect_record", "version": "1.4",
        "description": "读取 EEG 元数据与质量。已有记录传 manifest_id，工作区新文件传 path。quality_scope=full 检查完整记录，或用 start_s/end_s/channels 指定范围，返回逐通道 RMS、幅值、平直、极值重复与噪声份额；默认 initial 仅做开头初筛。include_labels=true 用于标签对照。",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "manifest_id": {"type": "string", "description": "已有记录的 id；读取该记录，不按路径重新创建记录"},
            "quality_scope": {"type": "string", "enum": ["initial", "full"], "default": "initial"},
            "start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
            "channels": {"type": "array", "items": {"type": "string"}},
            "sample_rate_hz": {"type": "number", "description": "文件不含采样率时由用户提供"},
            "channel_names": {"type": "array", "items": {"type": "string"}, "description": "用户提供的通道名，长度需等于通道数"},
            "trial_index": {"type": "integer", "description": "数据集文件（如 DEAP .dat）中的 trial 序号，从 0 起"},
            "include_labels": {"type": "boolean", "default": False, "description": "仅用户明确要求读自评标签或预测后对照时开启"}},
            "anyOf": [{"required": ["path"]}, {"required": ["manifest_id"]}]},
        "output_schema": {"type": "object", "properties": {"status": {"type": "string"}, "manifest": {"type": "object"}}}, **_RO,
    },
    {
        "name": "check_model_compat", "version": "1.0",
        "description": "对一个 manifest 重新计算 registry 中每个模型的兼容性（compatible / mappable / incompatible）。上传时已自动执行；用户补充通道名、采样率或设备信息后才需要重跑。",
        "input_schema": {"type": "object", "properties": {"manifest_id": {"type": "string"}}, "required": ["manifest_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "device_lookup", "version": "1.0",
        "description": "按厂商、型号、通道数查询设备默认电极布局、采样率与单位。已有采集说明或用户信息优先；需要采用设备默认配置时确认后登记。",
        "input_schema": {"type": "object", "properties": {
            "vendor": {"type": "string"}, "model": {"type": "string"}, "n_channels": {"type": "integer"}, "sampling_rate_hz": {"type": "number"}}},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "list_models", "version": "1.0",
        "description": "查看实际登记的模型、任务、输入要求、标签顺序、权重与执行器接入状态。识别模型与对话 LLM 是不同的模型。未接入的模型不能执行分类。",
        "input_schema": {"type": "object", "properties": {"task": {"type": "string", "enum": ["emotion", "sleep_stage", "embedding"]}}},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "route_model", "version": "1.0",
        "description": "为指定模型任务选择可执行的识别模型，返回输入要求与待补参数。面部输入使用 video_role=face，sync_offset_s 使用记录或用户提供的同步偏移。没有可执行模型时继续完成可用的信号分析。",
        "input_schema": {"type": "object", "properties": {
            "task": {"type": "string", "enum": ["emotion", "sleep_stage", "embedding"]},
            "manifest_id": {"type": "string"}, "model_id": {"type": "string"},
            "video_manifest_id": {"type": "string"},
            "video_role": {"type": "string", "enum": ["face", "stimulus", "unknown"], "default": "unknown"},
            "sync_offset_s": {"type": "number"},
            "start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
            "max_trials": {"type": "integer", "minimum": 1}}, "required": ["task", "manifest_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "run_model", "version": "1.0",
        "description": "按登记模型执行情绪识别，保存prediction和输入张量；常规识别用recognize_emotion一步完成，本工具用于指定模型或分步核对。配对录像传video_manifest_id，沿用确认的用途与同步偏移；video time=original EEG time-offset。EAV路线准备整20秒、输出5秒窗；DEAP路线准备63秒官方预处理波形，3秒基线不预测、刺激期逐1秒输出，start/end只选择预测时段、仍用整trial准备上下文。max_trials限制trial数量。专属处理在服务器执行，不重复通用滤波。score_kind标明得分类型，decision_margin给出各窗第一、二类得分差。",
        "input_schema": {"type": "object", "properties": {
            "source_id": _SRC, "model_id": {"type": "string"},
            "video_manifest_id": {"type": "string"},
            "video_role": {"type": "string", "enum": ["face", "stimulus", "unknown"], "default": "unknown"},
            "sync_offset_s": {"type": "number", "description": "视频0秒在原始EEG时间轴的位置，video_time=original_eeg_time-offset"},
            "start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
            "max_trials": {"type": "integer", "minimum": 1},
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 900, "default": 300}},
            "required": ["source_id", "model_id"]},
        "output_schema": {"type": "object"}, **dict(_RO, estimated_cost="complete_trials * remote_model"),
    },
    {
        "name": "update_record_metadata", "version": "1.0",
        "description": "登记用户或采集说明提供的采样率、通道、单位、参考、基线、标签含义与同步信息。普通消息和首条消息中的说明也要登记；后续分析使用返回的新 manifest_id。原记录与产物保留。",
        "input_schema": {"type": "object", "properties": {
            "manifest_id": {"type": "string"}, **RECORD_METADATA_PROPERTIES}, "required": ["manifest_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    # ------------------------------------------------------------ processing
    {
        "name": "preprocess", "version": "1.3",
        "description": "按 steps 顺序处理 EEG，返回可继续分析的信号。滤波、重采样与基线处理分别在连续段或 epoch 内执行，保留每段原始时间及事件相对坐标。参数按任务或训练契约确定。",
        "input_schema": {"type": "object", "properties": {
            "source_id": _SRC,
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "op": {"type": "string", "enum": ["pick_eeg", "pick_channels", "rereference", "notch", "bandpass", "resample", "remove_baseline", "normalize"]},
                "channels": {"type": "array", "items": {"type": "string"}}, "channel": {"type": "string"},
                "freq": {"type": "number", "description": "陷波频率 Hz"}, "q": {"type": "number"},
                "low": {"type": "number", "description": "带通下限 Hz"}, "high": {"type": "number", "description": "带通上限 Hz"},
                "order": {"type": "integer"}, "method": {"type": "string", "description": "参考 car（平均参考，也可写 average）/channel；滤波 iir/fir；归一化按方法选"},
                "target_hz": {"type": "number"}, "seconds": {"type": "number"}, "drop": {"type": "boolean"}}, "required": ["op"]}},
        },
            "required": ["source_id", "steps"]},
        "output_schema": {"type": "object"}, **dict(_RO, execution_mode="sequential", estimated_cost="n_samples * n_channels"),
    },
    {
        "name": "clean_artifacts", "version": "1.5",
        "description": "使用坏道检测、ASR 或 ICA+ICLabel 处理 EEG 伪迹，返回处理信号与实际修改摘要。ICA 图展示独立成分的空间权重，ASR 图展示局部前后波形；按返回图的真实用途解释。ICLabel 分类采用已确认电压尺度，ASR 的相对改动可用于推断或原生数值尺度。",
        "input_schema": {"type": "object", "properties": {
            "source_id": _SRC,
            "method": {"type": "string", "enum": CLEANING_METHODS},
            "exclude": {"type": "array", "items": {"type": "string"}, "description": "ica_iclabel：要剔除的 ICLabel 类别，默认 eye blink, muscle artifact, heart beat, line noise, channel noise"},
            "threshold": {"type": "number", "default": 0.8, "description": "ica_iclabel：类别概率阈值"},
            "n_components": {"type": "integer", "description": "ica_iclabel：成分数，省略时采用实际拟合数据的有效秩；已有参考、插值或重复通道可能降低秩"},
            "max_fit_s": {"type": "number", "default": 600, "description": "ica_iclabel：用于拟合的最长记录秒数，保留实际复现参数"},
            "cutoff": {"type": "number", "default": 20, "description": "asr：基于校准统计的重构阈值参数，按采集协议和处理前后读数选择"},
            "action": {"type": "string", "enum": ["interpolate", "drop", "mark"], "default": "interpolate", "description": "bad_channels：坏道处理方式"},
            "ransac": {"type": "boolean", "description": "bad_channels：是否运行 RANSAC，默认在通道 ≥16 且都有标准位置时运行"},
            "plot": {"type": "boolean", "default": True}},
            "required": ["source_id", "method"]},
        "output_schema": {"type": "object"}, **dict(_RO, execution_mode="sequential", estimated_cost="ica: n_channels^2 * n_samples"),
    },
    {
        "name": "segment_signal", "version": "1.3",
        "description": "按指定时间范围、窗长和步长生成窗口表，窗口不跨连续段或 epoch 边界。每窗带源时间、原始时间与 epoch 事件相对时间；max_windows 对全部可用窗均匀选取预算。",
        "input_schema": {"type": "object", "properties": {
            "source_id": _SRC, "window_s": {"type": "number"}, "stride_s": {"type": "number", "description": "默认等于 window_s"},
            "partial": {"type": "string", "enum": ["drop", "pad", "keep"], "default": "drop"},
            "max_windows": {"type": "integer", "description": "只限制预算，均匀抽样，不改时间戳"},
            "start_s": {"type": "number"}, "end_s": {"type": "number"}},
            "required": ["source_id", "window_s"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "extract_features", "version": "1.6",
        "description": "从窗口表计算 EEG 特征，返回数值、单位、实际频带、Welch 参数及聚合口径。connectivity 是逐窗 Pearson 通道相关。bands 控制普通频带特征；torcheeg_var 的 DE 单独使用其四频带，不改变 bandpower。",
        "input_schema": {"type": "object", "properties": {
            "windows_id": {"type": "string", "description": "segment_signal 产物 id"},
            "features": {"type": "array", "items": {"type": "string", "enum": ["psd", "bandpower", "de", "hjorth", "band_stats", "spectral_entropy", "asymmetry", "connectivity", "rms", "line_length", "zero_crossing_rate"]}},
            "bands": {"type": "string", "enum": ["standard", "camer_5band"], "default": "standard"},
            "de_method": {"type": "string", "enum": ["libeer_filtfilt", "torcheeg_var"], "default": "libeer_filtfilt"}},
            "required": ["windows_id", "features"]},
        "output_schema": {"type": "object"},
        **dict(_RO, estimated_cost="n_windows * n_channels", requires={"artifact_kind": {"windows_id": "windows"}}),
    },
    {
        "name": "save_emotion_assessment", "version": "1.2",
        "description": "保存基于EEG特征的情绪审阅。只记录测量发现时 assessment_status=unassessed、label=null；给出候选判断时用 hypothesis，label 取自约定 labels，并在 rationale 写支持与冲突的具体读数。它是分析记录，与模型预测分开保存。时间覆盖由实际窗口派生。",
        "input_schema": {"type": "object", "properties": {
            "features_id": {"type": "string", "description": "extract_features 产生的真实 features 产物 id"},
            "assessment_status": {"type": "string", "enum": ["unassessed", "hypothesis"], "description": "unassessed 保存测量发现但不定情绪；hypothesis 是用户明确要求的候选假设"},
            "labels": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}, "description": "约定的标签空间；unassessed 可省略，不自动填四象限"},
            "label": {"type": ["string", "null"], "description": "hypothesis 必须属于 labels；unassessed 省略或为 null"},
            "rationale": {"type": "string", "minLength": 1, "description": "结合已读取特征、时序和通道差异说明判断依据"},
            "predictions": {"type": "array", "description": "可选逐窗或分段判断，时间由窗口表派生", "items": {
                "type": "object", "additionalProperties": False, "properties": {
                    "window_start": {"type": "integer", "minimum": 0, "description": "起始特征窗口行号，从 0 起"},
                    "window_end": {"type": "integer", "minimum": 0, "description": "包含末窗的结束行号，省略时等于 window_start"},
                    "assessment_status": {"type": "string", "enum": ["unassessed", "hypothesis"], "description": "省略时与整段状态一致"},
                    "label": {"type": ["string", "null"]}, "rationale": {"type": "string"}}, "required": ["window_start"]}}},
            "required": ["features_id", "assessment_status", "rationale"], "additionalProperties": False},
        "output_schema": {"type": "object"},
        **dict(_RO, requires={"artifact_kind": {"features_id": "features"}}),
    },
    # ------------------------------------------------------------ outputs
    {
        "name": "plot", "version": "3.4",
        "description": "绘制波形、PSD、频带时间线或 overview 主图，保存 PNG/SVG/PDF。overview 使用 bandpower 的 features，将关键通道波形、PSD、频带趋势合为 a/b/c。profile=publication 使用投稿字号/线宽，width=single/wide 对应 89/183 mm；默认 screen 适合界面查看。指定范围使用 start_s/end_s，时间线仅展示已计算的完整窗。",
        "input_schema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["raw", "psd", "bandpower_timeline", "overview"]}, "source_id": {"type": "string"},
            "profile": {"type": "string", "enum": ["screen", "publication"], "default": "screen"},
            "width": {"type": "string", "enum": ["single", "wide"], "default": "wide"},
            "start_s": {"type": "number", "description": "相对 source 起点的开始秒数，默认完整范围起点"}, "end_s": {"type": "number", "description": "相对 source 起点的结束秒数，默认完整范围终点"},
            "channels": {"type": "array", "items": {"type": "string"}}, "band": {"type": "string"}},
            "required": ["kind", "source_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "detect_transients", "version": "1.1",
        "description": "按峰幅、半峰宽与最小间隔查找瞬态候选，保存候选区间表；候选是信号测量，不直接给疾病类别。min_amplitude 使用当前源信号的幅值单位。需要滤波时先 preprocess；候选表用 read_artifact 分页读取。",
        "input_schema": {"type": "object", "properties": {
            "source_id": _SRC, "start_s": {"type": "number", "minimum": 0}, "end_s": {"type": "number", "minimum": 0},
            "channels": {"type": "array", "items": {"type": "string"}},
            "min_amplitude": {"type": "number", "minimum": 0, "default": 70},
            "min_width_s": {"type": "number", "minimum": 0, "default": 0.02},
            "max_width_s": {"type": "number", "minimum": 0, "default": 0.2},
            "min_distance_s": {"type": "number", "minimum": 0, "default": 0.2},
            "max_candidates_per_channel": {"type": "integer", "minimum": 1, "default": 50}}, "required": ["source_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "export", "version": "3.4",
        "description": "特征、窗口、候选区间或脚本数据表导出CSV/JSON，信号导出CSV/EDF，图导出PNG/SVG/PDF。kind=python从真实原生处理产物追溯参数并生成可运行.py，以eeg.call复用同版本工具；需要复现脚本时直接导出。特征CSV保存逐窗特征；PSD数组用read_artifact读取。",
        "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["csv", "json", "signal_csv", "signal_edf", "png", "svg", "pdf", "python"]}, "source_id": {"type": "string"}}, "required": ["kind", "source_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    {
        "name": "read_artifact", "version": "1.2",
        "description": "分页读取产物及科学上下文。psd_mean/psd_freqs 返回频率轴，bandpower/de/hjorth/rms 等返回时间窗、原始时间和单位。summary 读元数据，segments 读信号连续段/epoch 坐标，windows 读窗口，predictions 读情绪分段，rows 读CSV/JSON数据表及瞬态候选。",
        "input_schema": {"type": "object", "properties": {
            "artifact_id": {"type": "string"}, "part": {"type": "string", "default": "summary"},
            "start": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 20}, "channel": {"type": "string"}},
            "required": ["artifact_id"]},
        "output_schema": {"type": "object"}, **_RO,
    },
    # ------------------------------------------------------------ scripting & knowledge
    {
        "name": "run_python", "version": "1.3",
        "description": "执行自定义 Python 分析或多文件循环。eeg.call复用本地读取、科学与工作区工具；eeg.save_*保存带来源和科学上下文的产物。scratch中实际写出的CSV、报告、图和脚本也会登记为可预览下载的文件。继承当前工作区，工作区写入通过eeg.call完成。详细写法见scripting/references/api.md。",
        "input_schema": {"type": "object", "properties": {
            "code": {"type": "string", "description": "完整可运行的 Python 代码；用 print 输出摘要，用 eeg.save_* 保存要引用的结果"},
            "timeout_s": {"type": "number", "default": 120, "description": "最长 900"},
            "label": {"type": "string", "description": "一句话说明这段脚本做什么（写入脚本文件头）"}},
            "required": ["code"]},
        "output_schema": {"type": "object", "properties": {"status": {"type": "string"}, "stdout": {"type": "string"}, "artifacts": {"type": "array"}, "manifests": {"type": "array"}, "workspace_files": {"type": "array"}, "files": {"type": "array"}}},
        **dict(_RO, execution_mode="sequential", estimated_cost="script"),
    },
    {
        "name": "search_knowledge", "version": "2.1",
        "description": "检索项目知识与会话文档，返回可引用原文与出处。k 是主匹配数量；笔记的已核对来源链接另带最多两个原文片段，图谱另带最多两个支持片段。论文结论与公式优先引用返回的原文；笔记用于导读。scope=session 查上传文档，project 查领域知识，all 合并；doc_type=documentation 查官方文档。引用写 [k_id]，上传文档全文用 read_document。",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"}, "k": {"type": "integer", "default": 5},
            "domain": {"type": "string"}, "doc_type": {"type": "string", "enum": ["note", "paper", "documentation", "document"]},
            "scope": {"type": "string", "enum": ["all", "project", "session"], "default": "all"},
            "tasks": {"type": "array", "items": {"type": "string"}},
            "expand_graph": {"type": "boolean", "default": True}},
            "required": ["query"]},
        "output_schema": {"type": "object", "properties": {"status": {"type": "string"}, "chunks": {"type": "array"}, "facts": {"type": "array"}, "entities": {"type": "array"}}}, **_RO,
    },
    {
        "name": "knowledge_graph", "version": "1.0",
        "description": "查知识图谱：给一个实体（数据集、设备、模型、方法、论文、频带方案、伪迹…的名字或 id）返回它的关系与证据块；或给 path_from/path_to 找两者之间的关系链（如「Emotiv EPOC 的数据能不能喂 CAMER」→ EPOC —recorded_with← AMIGOS ←evaluated_on— CAMER）。list_types=true 列出全部实体。回答里引用事实的 evidence 块 id。",
        "input_schema": {"type": "object", "properties": {
            "entity": {"type": "string"}, "relation": {"type": "string"}, "depth": {"type": "integer", "default": 1},
            "path_from": {"type": "string"}, "path_to": {"type": "string"}, "list_types": {"type": "boolean", "default": False}}},
        "output_schema": {"type": "object"}, **_RO,
    },
]
if not CLEANING_METHODS:
    TOOL_DEFINITIONS = [definition for definition in TOOL_DEFINITIONS if definition["name"] != "clean_artifacts"]


def _manifest_view(manifest: dict, include_labels: bool = False) -> dict:
    visible = dict(manifest, labels_available=bool(manifest.get("labels")))
    segments = visible.pop("segments", [])
    if manifest.get("signal_kind") == "epochs":
        visible.update(n_segments=len(segments), segments_preview=segments if len(segments) <= 4 else segments[:2] + segments[-2:],
                       time_axis="packed epochs; boundaries are discontinuous",
                       epoch_tmin_range_s=[min(s["epoch_tmin_s"] for s in segments), max(s["epoch_tmin_s"] for s in segments)])
    if not include_labels:
        visible.pop("labels", None)
    return visible


def _run_inspect(a: dict) -> dict:
    if a.get("manifest_id"):
        manifest = load_manifest(a["manifest_id"])
        if a.get("trial_index") is not None and a["trial_index"] != manifest.get("trial_index"):
            manifest = update_record_metadata(manifest["manifest_id"], trial_index=a["trial_index"])["manifest"]
    else:
        manifest = inspect_record(path=a["path"], sample_rate_hz=a.get("sample_rate_hz"),
                                  channel_names=a.get("channel_names"), trial_index=a.get("trial_index"))
    if a.get("quality_scope") == "full" or any(key in a for key in ("start_s", "end_s", "channels")):
        manifest["quality"] = quality_summary(manifest["manifest_id"], a.get("start_s"), a.get("end_s"), a.get("channels"))
        save_manifest(manifest)
    return {"status": "ok", "manifest": _manifest_view(manifest, a.get("include_labels", False))}


def _run_upload(a: dict, sid: str | None) -> dict:
    result = register_upload(a["path"], sid, a.get("sample_rate_hz"), a.get("channel_names"), a.get("trial_index"))
    if result["manifest"]["modality"] == "eeg":
        result["manifest"] = _manifest_view(result["manifest"])
    return result


def _run_metadata(a: dict) -> dict:
    result = update_record_metadata(a["manifest_id"], sample_rate_hz=a.get("sample_rate_hz"), channel_names=a.get("channel_names"),
                                    unit=a.get("unit"), video_role=a.get("video_role"), sync_offset_s=a.get("sync_offset_s"),
                                    dataset=a.get("dataset"), reference=a.get("reference"), baseline_duration_s=a.get("baseline_duration_s"),
                                    label_names=a.get("label_names"), task_mode=a.get("task_mode"), preprocessing=a.get("preprocessing"))
    result["manifest"] = _manifest_view(result["manifest"])
    return result


def _run_recognition(a: dict, sid: str | None) -> dict:
    result = recognize_emotion(**a, _session_id=sid)
    result["manifests"] = [_manifest_view(manifest) for manifest in result["manifests"]]
    return result


def _run_compat(a: dict) -> dict:
    m = load_manifest(a["manifest_id"])
    m["compat"] = check_model_compat(m)
    save_manifest(m)
    return {"status": "ok", "compat": m["compat"]}


def _run_clean(a: dict) -> dict:
    if a["method"] not in CLEANING_METHODS:
        raise ImportError(f"{a['method']} 缺少依赖：{', '.join(CLEANING_MISSING.get(a['method'], []))}")
    return clean_artifacts(a["source_id"], a["method"], exclude=a.get("exclude"), threshold=a.get("threshold", 0.8),
                            n_components=a.get("n_components"), max_fit_s=a.get("max_fit_s", 600.0), cutoff=a.get("cutoff", 20.0),
                           action=a.get("action", "interpolate"), ransac=a.get("ransac"), plot=a.get("plot", True))


DISPATCH: dict[str, Callable[..., dict]] = {
    "analyze_signal": lambda a, sid: analyze_signal(a,sid),
    "workspace_quality": lambda a, sid: workspace_quality(a["workspace_id"],sid,a.get("pattern"),a.get("path",""),a.get("sample_rate_hz")),
    "workspace_files": lambda a, sid: workspace_files(a, a.get("workspace_id")),
    "list_files": lambda a, sid: list_files(a.get("pattern"), a.get("kind", "eeg"), a.get("root"), a.get("limit", 50), True, sid),
    "read_file": lambda a, sid: read_file(a["path"], a.get("offset", 1), a.get("limit", 200), sid, a.get("workspace_id")),
    "write_file": lambda a, sid: write_file(a.get("workspace_id"), a["path"], a["content"], a.get("overwrite", False)),
    "edit_file": lambda a, sid: edit_file(a.get("workspace_id"), a["path"], a["old_text"], a["new_text"], a.get("replace_all", False)),
    "save_to_workspace": lambda a, sid: save_to_workspace(a.get("workspace_id"), a["artifact_id"], a["path"], a.get("overwrite", False)),
    "read_document": lambda a, sid: read_document(a["manifest_id"], a.get("page", 1), a.get("limit", 3), a.get("offset", 0), sid),
    "register_attachment": lambda a, sid: register_attachment(a["path"], sid),
    "register_upload": lambda a, sid: _run_upload(a, sid),
    "inspect_record": lambda a, sid: _run_inspect(a),
    "check_model_compat": lambda a, sid: _run_compat(a),
    "update_record_metadata": lambda a, sid: _run_metadata(a),
    "recognize_emotion": lambda a, sid: _run_recognition(a, sid),
    "list_models": lambda a, sid: list_models(a.get("task")),
    "route_model": lambda a, sid: route_model(a["task"], load_manifest(a["manifest_id"]),
                                            load_manifest(a["video_manifest_id"]) if a.get("video_manifest_id") else None,
                                            a.get("video_role", "unknown"), a.get("sync_offset_s"), a.get("model_id"),
                                            start_s=a.get("start_s"), end_s=a.get("end_s"), max_trials=a.get("max_trials")),
    "run_model": lambda a, sid: run_model(a["source_id"], a["model_id"], sid, a.get("start_s"), a.get("end_s"),
                                         a.get("max_trials"), a.get("timeout_s", 300), a.get("video_manifest_id"),
                                         a.get("video_role", "unknown"), a.get("sync_offset_s")),
    "device_lookup": lambda a, sid: device_lookup(a.get("vendor"), a.get("model"), a.get("n_channels"), a.get("sampling_rate_hz")),
    "preprocess": lambda a, sid: preprocess(a["source_id"], a["steps"]),
    "clean_artifacts": lambda a, sid: _run_clean(a),
    "segment_signal": lambda a, sid: segment_signal(a["source_id"], a["window_s"], a.get("stride_s"), a.get("partial", "drop"),
                                                    a.get("max_windows"), a.get("start_s"), a.get("end_s")),
    "extract_features": lambda a, sid: extract_features(a["windows_id"], a["features"], a.get("bands", "standard"), a.get("de_method", "libeer_filtfilt")),
    "save_emotion_assessment": lambda a, sid: save_emotion_assessment(a["features_id"], a.get("labels", []), a.get("label"), a["rationale"], a.get("predictions"), a["assessment_status"]),
    "plot": lambda a, sid: plot(a["kind"], a["source_id"], a.get("start_s"), a.get("end_s"), a.get("channels"), a.get("band"), a.get("profile", "screen"), a.get("width", "wide")),
    "detect_transients": lambda a, sid: detect_transients(a["source_id"], a.get("start_s"), a.get("end_s"), a.get("channels"),
                                                       a.get("min_amplitude", 70), a.get("min_width_s", .02), a.get("max_width_s", .2),
                                                       a.get("min_distance_s", .2), a.get("max_candidates_per_channel", 50)),
    "export": lambda a, sid: export(a["kind"], a["source_id"]),
    "read_artifact": lambda a, sid: read_artifact(a["artifact_id"], a.get("part", "summary"), a.get("start", 0), a.get("limit", 20), a.get("channel")),
    "run_python": lambda a, sid: run_python(a["code"], sid, a.get("timeout_s"), a.get("label"), a.get("workspace_id")),
    "search_knowledge": lambda a, sid: search_knowledge(a["query"], a.get("k", 5), a.get("domain"), a.get("tasks"), a.get("doc_type"), a.get("expand_graph", True), sid, a.get("scope", "all")),
    "knowledge_graph": lambda a, sid: knowledge_graph(a.get("entity"), a.get("relation"), a.get("depth", 1), a.get("path_from"), a.get("path_to"), a.get("list_types", False)),
}


def run_tool(name: str, args: dict) -> dict:
    if name not in DISPATCH:
        raise KeyError(f"unknown tool: {name}")
    args = dict(args or {})
    session_id = args.pop("_session_id", None)
    workspace_id = args.pop("_workspace_id", None)
    if name in {"read_file", "write_file", "edit_file", "save_to_workspace", "run_python"}:
        args["workspace_id"] = workspace_id
    if workspace_id and name in ["inspect_record", "analyze_signal"] and args.get("path"):
        from eegagent.worker.workspaces import load as load_workspace, resolve as resolve_workspace, excluded
        workspace = load_workspace(workspace_id)
        source = resolve_workspace(workspace,args["path"])
        if excluded(workspace,source):
            raise ValueError("此记录已排除分析；先在工作区恢复参与")
        args["path"] = str(source)
    if name == "workspace_quality":
        result = workspace_quality(workspace_id,session_id,args.get("pattern"),args.get("path",""),args.get("sample_rate_hz"))
    elif name == "workspace_files":
        result = workspace_files(args, workspace_id,session_id)
    elif name == "list_files":
        result = list_files(args.get("pattern"), args.get("kind", "eeg"), args.get("root"), args.get("limit", 50), True, session_id, workspace_id, args.get("offset",0))
    else:
        result = DISPATCH[name](args, session_id)
    artifacts = ([result["artifact"]] if "artifact" in result else []) + result.get("artifacts", [])
    if artifacts:
        result["result_links"] = [{"artifact_id": record["artifact_id"], "kind": record["kind"],
                                   "format": record["summary"].get("format"),
                                   "uri": "artifact://" + record["artifact_id"],
                                   "download_url": "/artifacts/" + record["artifact_id"] + "/file"}
                                  for record in artifacts]
    return result
