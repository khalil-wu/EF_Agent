---
name: scripting
description: 编写并运行已有工具未直接覆盖的 EEG 统计、图表、批量处理与报告脚本；在脚本中组合现有工具并汇总真实产物。
modalities: [eeg, document]
allowed-tools: [run_python, read_artifact, read_file, workspace_files, write_file, edit_file, export, save_to_workspace]
---

# Python 分析与批处理

## 默认工作流

先读已有 manifest/产物，用最短脚本完成数据读取、计算与保存，再汇总输出；批量时在文件/试次边界记录成功或失败。用 eeg.call 复用工具，不重写滤波、DE、绘图与导出。只有用户所需算法超出现有工具时才写对应计算。

长脚本先用 write_file 分段保存纯 Python，再执行该文件的源码；stdout 只打印关键读数与产物 ID。这样工具参数不必携带整份长报告或大数组。

信号使用 eeg.load_signal、特征使用 eeg.load_features、时间与单位使用对应 metadata/summary 读取接口；这些接口直接提供真实数组布局和窗口表。需要处理原始未知格式时先 inspect_record，而不是猜测 NPZ 字段、维度或广播关系。遇到索引/形状错误先读取相关 shape、数组键和 feature_components，修正数据理解后再运行。

API、允许调用的工具、返回结构和批量示例按需 read_skill(name=scripting, reference=api.md)。当前 session 与 workspace 自动沿用，文件范围遵守用户引用、采集说明与排除清单。全文件任务读取完整文件集合及实际 trial_count，按试次执行并保留来源和时间坐标。

数据读取使用已有 manifest/artifact ID 或当前允许的工作区路径。脚本写 scratch，通过 eeg.save_* 保存图、表、信号与报告；用户要求落到工作区时用 save_to_workspace。用户要求保存或修改协议、脚本、文本结果时用 write_file/edit_file，写入内容围绕已授权目标。

复现已有原生处理/特征结果时，优先 export(kind=python, source_id=实际产物ID)，保存纯源码到工作区；自定义脚本复用实际实现和精确参数。交付前原样运行已保存源码，在代表数据上核对数值和文件；环境、用法和近似方法差异放独立 README/报告。

批量执行在文件/试次边界记录实际成功结果；某项失败时保存对应文件、trial、步骤和原始错误，继续独立可执行项，最终汇总完成/失败集合。中断后按已保存清单判断哪些尚需处理，不把会话恢复等同于已实现的逐文件自动断点。

保持单位、参考、时间和处理链一致。统计单位来自实验设计，相关窗口按相关观测处理。核对算法依据时读取对应版本实现或方法规范，数值实验用于检验具体假设。

stdout 打印简短摘要，大数组保存在产物。绘图前按需 read_skill(name=scripting, reference=figures.md)，使用 eeg.figure 与 eeg.save_figure。交付前实际列出目标目录，对照用户所需的脚本、信号、表和报告补齐缺项，读取代表结果核对内容。完成状态以本次执行后真实存在的文件与产物为准，正文直接给结果入口。
