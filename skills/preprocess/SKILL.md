---
name: preprocess
description: 根据分析目标与采集协议选择并执行 EEG 滤波、参考、重采样、基线或归一化，保存可继续分析的处理信号。适用于独立清理和批量预处理。
modalities: [eeg]
allowed-tools: [inspect_record, preprocess, clean_artifacts, read_artifact, plot, export, run_python, save_to_workspace]
---

# 预处理

## 默认做法

先看源记录的既有处理链（provenance.steps、采集说明），再决定还需要什么。一条通用的分析链：

1. `pick_eeg`：去掉 EOG、EMG、触发等非 EEG 通道（它们保留在原记录里）。
2. 陷波：源带宽包含且 PSD 存在工频峰时处理对应 50 或 60 Hz；已有充分限带或已陷波的记录按实际谱决定。
3. 带通：频谱与特征分析常用 0.5–45 Hz（或 1–40 Hz）；ERP 常用 0.1–30 Hz；要分析 γ 时上限放到 Nyquist 以下并确认工频已处理。
4. 坏道处理后重参考：覆盖全头、空间分布合适时可用平均参考（method=car，也可写 average），记录实际参与的 EEG 通道；局部覆盖时保留原参考或采用协议指定的参考。适用性取决于覆盖与目标，16 道不是统一门槛。
5. 按下游采样率重采样，降采样由合适的抗混叠算法处理；额外分析低通按目标频段与过渡带选择。查看实际工具返回的抗混叠与滤波记录。
6. 去基线用已确认的基线时段；归一化只在下游方法需要时做，并说明它改变了幅值、功率和 DE 的物理含义。

何时偏离：已做过 4–45 Hz 带通和平均参考的发行数据（DEAP、AMIGOS 预处理版）不再重复滤波和重参考；已高通过的数据无法恢复低频；用户或采集协议指定的参数优先。

## 模型输入不走通用预处理

登记模型（CAMER、EEGNet 等）的专属准备在 `run_model` / `recognize_emotion` 里按训练配方执行，直接给它原始 manifest。用户另外要求清洗的数据链与模型输入链分开保存。具体配方按需 read_skill(name=emotion_recognition, reference=contracts.md)。

## 衔接与批量

- 每一步返回的 artifact.artifact_id 作为下一步的 source_id；去伪迹用 `clean_artifacts`，方法选择见 artifact_quality。
- 批量任务按设备和采集协议分组复用同一方案，用 run_python 的 `eeg.call` 循环已有工具，每个输出保留文件、trial、参数和来源；失败项记录文件、步骤和原始报错，其余继续。
- 用户要写回工作区时先 export 成目标格式，再 `save_to_workspace`。复现已有原生处理链用 `export(kind=python, source_id=...)` 导出纯 Python，执行和核对按 scripting 的交付流程。

## 交付

处理后信号、实际步骤与参数、同范围的前后对比（PSD 或质量指标），以及被修改的通道、成分或时段。
