# 什么时候写脚本

- 先看工具能否直接做：preprocess / segment_signal / extract_features / plot / export / read_artifact 覆盖了常规链路。
- 需要脚本的典型情形：多文件/多试次循环、逐文件清洗后提取特征与总表；每通道 alpha 峰值频率；置换检验或效应量；地形图；ERP/ERSP；FOOOF、wPLI；自定义报告。
- 在循环中用 eeg.call 复用 preprocess / clean_artifacts / segment_signal / extract_features / export，再用 eeg.load_features 读取真实结果做汇总。现有算法继续由工具实现，脚本负责组合。

# 沙箱边界

- 可用库：numpy、scipy、pandas、matplotlib（Agg）、sklearn、statsmodels、pywt；MNE 生态：mne、mne_icalabel、pyprep、asrpy、autoreject、mne_connectivity、fooof、antropy、eeglabio。MNE / EEGLAB 式流程（ICA、ERP、时频、FOOOF、连接性）的写法见 skill eeg_toolboxes。
- 当前 session 与 workspace 由运行时注入。可读允许的数据目录及挂接工作区；直接文件写入仅限本会话 scratch（当前工作目录），eeg.save_* 保存产物，工作区写入通过 eeg.call 的专用文件工具执行。脚本不能联网或启动子进程，n_jobs=1；默认超时 120 s，最长 900 s。
- stdout 只保留末尾 2000 行 / 50 KB，完整输出在 scratch 的 run_NNN.out（read_file 可读）。MNE 日志默认只输出 WARNING。
- 脚本保存为 scratch/run_NNN.py；用 label 参数写一句说明。

# 组合已有工具

`eeg.call(tool_name, **arguments) -> dict` 采用与独立工具相同的参数和返回结构，自动继承当前 session/workspace。`manifest`/`manifests`、`artifact`/`artifacts` 自动归入 run_python 的真实输出；`workspace_file` 汇总为 `workspace_files`，可直接在界面打开。

可调用：list_files、read_file、read_document、inspect_record、update_record_metadata、preprocess、clean_artifacts、segment_signal、extract_features、read_artifact、plot、export、detect_transients；workspace_files 仅支持 list/inspect。用户要求保存或修订工作区文件时，还可调用 write_file、edit_file、save_to_workspace。递归 run_python、远程推理与原文件移动使用外层相应工具。

### 数组布局与读取

信号优先 `data, sr, names = eeg.load_signal(source_id)`，返回 `data[channel, sample]`；原始 epoch 的分界与原记录坐标用 `eeg.signal_metadata(source_id)`，跨边界计算按各连续段执行。特征用 `f = eeg.load_features(features_id)`；下面是原生 extract_features 的布局，自定义 features 采用其实际元数据：

| 数组 | 轴与标签 |
| --- | --- |
| bandpower、de、hjorth、band_stats | window × channel × component；标签取 summary.feature_components，DE 频带取 summary.de_bands |
| rms、line_length、zero_crossing_rate、spectral_entropy | window × channel × 1 |
| connectivity | window × channel × channel |
| asymmetry | window × electrode_pair；配对取 summary.feature_components.asymmetry |
| psd_mean、psd_freqs | channel × frequency，以及 frequency；均值谱对应整个所选窗口集合 |

窗口起止取 `f["windows"]`，通道取 `f["channel_names"]`，逐窗分量标签与单位取 summary 中的 feature_components、feature_units 与 feature_component_units。`arrays` 里不同数组的维度不同，按具体数组展开；数组维度异常时一次打印其键、shape 和分量标签后修正。

结果直接读取原生字段，例如 `eeg.call("inspect_record", path=path)["manifest"]`、`eeg.call("preprocess", source_id=sid, steps=steps)["artifact"]["artifact_id"]`。工具抛出的 ValueError/OSError/ImportError 等保留；`status=error` 转成 RuntimeError。单文件任务让实际错误返回以便修正；批量任务只在独立文件/试次边界记录失败，取消立即传播。

工作区文件接口采用相对路径（含目标文件名），父目录自动创建：

```python
# 原产物保留；目标存在时只有明确覆盖才传 overwrite=True。
eeg.call("save_to_workspace", artifact_id=table_id, path="results/features.csv")
eeg.call("write_file", path="analysis-notes.md", content=markdown)
eeg.call("edit_file", path="analysis-notes.md", old_text="旧段落", new_text="新段落")
```

`write_file(..., overwrite=False)` 创建文本；`edit_file(..., replace_all=False)` 精确替换唯一匹配，批量替换由 replace_all=True 明确指定。文件写入围绕用户要求的协议、脚本、报告或结果；分析排除、原文件整理分别由外层 workspace_files 操作。

## 可复用脚本与实际交付

用户要求可运行或可复用脚本时，将实际执行的纯 Python 源码保存为 .py；Markdown 说明和代码围栏放在单独 README/报告。脚本用 eeg.call 调用本次科学工具，或复用对应版本的原生实现与精确参数，保证滤波、DE、Hjorth、单位与窗口定义和本次结果一致。依赖预置 eeg 的源码注明在 EFAgent 的 run_python 环境运行。

复现已有原生链优先 `export(kind="python", source_id=...)`，支持 features、normalized_signal、windows 及它们的数值导出产物。导出的纯源码通过 eeg.call 重放真实 graph，保留实际输入元数据快照并核对工具版本；顶部 INPUTS/OUTPUT_PATH 可编辑以用于新输入和结果路径。自定义 run_python 产物缺少原生 graph 时，按实际脚本交付自定义或自包含实现，说明环境依赖并数值核对；近似算法按近似实现描述。

下面在一次 run_python 中导出、落盘并执行已有 features 产物的原生复现；`features_id` 换成本次真实产物。执行后核对数组、窗口/单位和目录清单；容差按实际计算精度选择。

```python
from pathlib import Path
import numpy as np

recipe = eeg.call("export", kind="python", source_id=features_id)
saved = eeg.call("save_to_workspace", artifact_id=recipe["artifact"]["artifact_id"], path="results/replay.py")
code = Path(saved["path"]).read_text(encoding="utf-8")
scope = {"eeg": eeg, "__name__": "__main__"}
exec(compile(code, saved["path"], "exec"), scope)
replayed = eeg.load_features(scope["RESULT_ID"])
for name, values in eeg.load_features(features_id)["arrays"].items():
    np.testing.assert_allclose(replayed["arrays"][name], values, rtol=1e-8, atol=1e-10, equal_nan=True)
```

保存后通过 read_file 取得该文件的完整源码（长文件按 end_line 续读），原样作为外层 run_python 的 code 在代表数据上执行。核对实际范围、行数、关键读数和生成文件；执行发现的问题直接修正已保存脚本后再验证。用户明确保存草稿/待调试代码时，按其要求保存并说明状态。

本次报告要实际调用 eeg.save_report 或 write_file 保存；需要写回工作区的产物调用 save_to_workspace。交付前用 workspace_files(action=list, path=目标目录) 实际列目录，与用户要求的文件清单对照并补齐，再读取报告和代表表头/结果核对。最终“已保存”描述对应本次真实存在的文件，脚本中待执行的导出语句作为脚本能力说明。

## 批量清洗与特征示例

先根据真实采集说明选方案，再写循环。下面示例假设本次协议已经明确采用 1–40 Hz 带通、4 秒窗/2 秒步长；这只是这次分析配方，其他采样率、目标频带和模型输入采用对应参数。`reader_metadata` 填已确认且读取需要的 sample_rate_hz/channel_names；`record_metadata` 填本次已给单位/参考等事实。自带参数的文件采用其真实记录，批内存在不同协议时先分组。坏道、ASR 或 ICA 的 method/参数在质量检查后按适用条件加入，调用同一 clean_artifacts。

```python
reader_metadata = {}   # 来自当前采集协议；未知且影响计算的事实先集中补齐。
record_metadata = {}   # 例如 {"unit": "uV", "reference": "mastoids"}，仅填已确认事实。
steps = [{"op": "pick_eeg"}, {"op": "bandpass", "low": 1.0, "high": 40.0}]
files = []
offset = 0
while True:
    page = eeg.call("list_files", kind="eeg", offset=offset, limit=100)
    files.extend(page["files"])
    if page["next_offset"] is None:
        break
    offset = page["next_offset"]

results = []
for item in files:
    path = item["path"]
    try:
        first = eeg.call("inspect_record", path=path, **reader_metadata)["manifest"]
    except InterruptedError:
        raise
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        results.append({"file": path, "trial_index": None, "status": "failed", "step": "inspect_record", "error": str(error)})
        continue
    for trial_index in range(first["trial_count"]):
        stage = "inspect_record"
        try:
            manifest = first if trial_index == 0 else eeg.call(
                "inspect_record", path=path, trial_index=trial_index, **reader_metadata)["manifest"]
            if record_metadata:
                stage = "update_record_metadata"
                manifest = eeg.call("update_record_metadata", manifest_id=manifest["manifest_id"], **record_metadata)["manifest"]
            source_id = manifest["manifest_id"]
            stage = "preprocess"
            processed = eeg.call("preprocess", source_id=source_id, steps=steps)
            processed_id = processed["artifact"]["artifact_id"]
            stage = "segment_signal"
            windows = eeg.call("segment_signal", source_id=processed_id, window_s=4, stride_s=2)
            stage = "extract_features"
            features = eeg.call("extract_features", windows_id=windows["artifact"]["artifact_id"],
                                features=["bandpower", "de", "hjorth", "spectral_entropy"])
            stage = "export"
            exported = eeg.call("export", kind="csv", source_id=features["artifact"]["artifact_id"])
            row = {"file": path, "trial_index": manifest["trial_index"], "trial_id": manifest["trial_id"],
                   "manifest_id": source_id, "processed_id": processed_id,
                   "features_id": features["artifact"]["artifact_id"], "csv_id": exported["artifact"]["artifact_id"],
                   "n_windows": features["summary"]["n_windows"], "status": "completed", "error": ""}
            results.append(row)
        except InterruptedError:
            raise
        except (ValueError, OSError, ImportError, RuntimeError) as error:
            results.append({"file": path, "trial_index": trial_index, "status": "failed", "step": stage, "error": str(error)})
        # 每个已完成 trial 留一份清单快照，后续可据此确定尚未处理的集合。
        eeg.save_table(results, {"name": "batch_manifest", "processed_items": len(results)})

completed = [row for row in results if row["status"] == "completed"]
table_id = eeg.save_table(results, {"name": "batch_manifest", "completed": len(completed), "failed": len(results)-len(completed)})
print({"files": len(files), "completed_trials": len(completed), "failed_items": len(results)-len(completed), "manifest": table_id})
```

逐窗特征产物保留真实通道、频带、单位、窗口和原始坐标。需要单张长表时，用 eeg.load_features 读取每个真实 features_id，按其 arrays 维度展开，并加入 file/trial/处理来源；不同定义的特征保留对应字段。复检清洗效果时，对源与派生信号按同范围、通道和指标生成结果，批次汇总保留修改数量与失败项。用户要求“保存到这个文件夹”时，将导出的表、信号及最终清单用 save_to_workspace 写到其结果目录。

# eeg 辅助 API（脚本里已预置 `eeg`）

```python
eeg.scratch                              # Path，本会话工作目录
eeg.call(tool_name, **arguments) -> dict  # 继承 session/workspace，组合现有工具
eeg.manifest(manifest_id) -> dict
eeg.load_signal(source_id) -> (data, sr, names)   # [C, N]；按元数据换算为 µV，未知单位保留原数值，归一化产物为 a.u.
eeg.signal_steps(source_id) -> list[str]           # 已应用的处理链，如 ['unit_uV','notch_50','bandpass_0.5_50']
eeg.signal_metadata(source_id) -> dict            # 通道/单位、continuous 或 epochs、原始时间、事件坐标与 source_processing
eeg.load_windows(windows_id) -> (windows, data, sr, names)   # windows: [{index,start_s,end_s,start_sample,end_sample}]
eeg.load_features(features_id) -> {"arrays": {name: ndarray}, "summary", "windows", "channel_names", "bands"}  # 各数组轴见上表
eeg.artifact(artifact_id) -> dict ; eeg.artifact_path(artifact_id) -> Path
eeg.to_mne(source_id, montage="auto", events=True) -> mne.io.Raw   # 单位 V；10-05 标准模板位置不是被试实测位置
eeg.save_raw(raw, summary, steps=[...], name=None) -> artifact_id  # 存回 µV 的 normalized_signal，工具可继续使用
eeg.to_epochs(source_id, montage="auto") -> mne.EpochsArray      # 原始 epoched 数据；保留 tmin 与条件
eeg.save_epochs(epochs, summary, steps=[...], name=None) -> artifact_id  # 保留 epoch 身份、边界和各自的原始时间
eeg.save_artifact(kind, data, summary, name=None, title=None, description=None, parents=None, time_range_s=None, params=None) -> artifact_id
eeg.save_figure(fig, summary={...}, title="图的语义名称", description="一句说明图在比较什么或用于查看什么") -> artifact_id
eeg.save_table(rows, summary={...}, columns=[...], title="表的语义名称", description="一句说明数据行和单位") -> artifact_id
eeg.save_report(markdown, summary={...}, title="报告名称", description="报告内容摘要") -> artifact_id
eeg.figure(nrows=1, ncols=1, width="single", height=None, **kwargs) -> (fig, axes)
eeg.colors ; eeg.band_colors                     # 统一的分类颜色与频带颜色
```

- kind 按实际结果使用 features / aggregate / plot / table / report / custom 等；prediction 仅用于真实执行的预测。data 类型决定文件：Figure→png（并附 SVG/PDF）、str→md、ndarray 或 dict[ndarray]→npz、DataFrame 或 list[dict]→csv、其它→json。
- summary 必须是小的、可读的 JSON（关键数值、通道、时间范围），它会进入上下文；大数组只放 data。
- source_processing 是带来源的上游处理声明；signal_steps 是本系统处理链。脚本选择后续滤波时结合两者，不能把声明写成刚执行的处理。
- 图和表的 `title` 使用可读的内容名称，例如“ICA 成分头皮分布”“ASR 清洗前后波形”“逐通道 α 频带功率”；`description` 用一句话写清坐标、对照对象或用途。两者作为 `summary.title`、`summary.description` 传给界面；`name` 只是脚本内的技术名称。原方法细节可单独写 `summary.caption`。
- 图中的可见标题、坐标轴、图例和说明均用中文；保留实际通道名、科学单位及 ICA/PSD 等缩写。`eeg.figure` 和原生 pyplot 已默认使用可显示中文的字体；例如 `ax.set(xlabel="时间（秒）", ylabel="振幅（µV）")`，图例写“处理前”“处理后”。
- `save_figure` 没有显式标题时，会保留图本身的总标题或子图标题；说明取显式 caption 或实际坐标轴标签。需要语义完整的图，用上面的两个参数；仅存 PNG 文件名不包含科学解释。scratch 普通写出的 CSV、MD、图等也会自动登记为可下载文件，科学图优先用 `save_figure` 同时交付用途与来源。
- 每个 save_* 返回 artifact_id；回答里用 artifact://<id> 引用。图会自动展示。

# 写法规则

1. 需要核对输入或排查结果时打印相关形状、采样率和通道，不重复输出已有完整元数据。
2. 计算与实际处理链一致，滤波按目标选择。原始 PSD 不要求先带通；不要为满足步骤形式改变谱或重复滤波。文件结构/频谱痕迹不能写成已执行的处理链。
3. 结果用 eeg.save_*，并 print 一段 ≤ 20 行的摘要（不要 print 整个数组）。
4. 统计检验：说明检验方法、样本是窗口还是 trial、相邻窗口的相关性；报告效应量。
5. 出错时根据 traceback 修改实际原因再运行；缺执行器、数据或方法条件时先说明缺项，重复同一失败不能解决它。
6. 时间轴以秒为单位，从信号起点算；计算结果保留真实起止秒与窗口定义，正文按用户问题描述事件时段。
7. 绘图规范与例子见 [figures.md](figures.md)。使用 eeg.figure，信息密度决定尺寸；数值、范围与不确定性应可核对，不靠大标题、全框、堆叠颜色制造“论文风格”。

# 示例：每通道 alpha 频带内谱最大值的位置

下面定位 8–13 Hz 内的最大谱值，不证明已检出独立振荡峰；需要检出峰时再拟合非周期背景并检查峰参数。

```python
import numpy as np
from scipy import signal as sps
data, sr, names = eeg.load_signal("art_xxxxxxxx")   # 采样率已确认的信号
print(data.shape, sr, eeg.signal_steps("art_xxxxxxxx"))
f, p = sps.welch(data, fs=sr, nperseg=int(4*sr), axis=1)
m = (f >= 8) & (f <= 13)
peak = f[m][np.argmax(p[:, m], axis=1)]
rows = [{"channel": n, "alpha_band_max_frequency_hz": round(float(v), 2)} for n, v in zip(names, peak)]
tid = eeg.save_table(rows, {"n_channels": len(names), "median_band_max_hz": float(np.median(peak))}, name="alpha_band_maxima",
                     title="逐通道 α 频带最大值频率", description="列出每个通道在 8–13 Hz 内功率最大的频率，单位为 Hz。")
fig, ax = eeg.figure(width="wide", height=max(2.2, len(names) * 0.16 + 1.0))
y = np.arange(len(names))
ax.scatter(peak, y, s=16, color=eeg.colors[0])
ax.set_yticks(y, names); ax.invert_yaxis(); ax.set_xlim(8, 13)
ax.set_xlabel("α 频带最大功率对应频率（Hz）")
fid = eeg.save_figure(fig, {"kind": "alpha_band_maximum", "nperseg": int(4*sr), "band_hz": [8, 13]}, name="alpha_band_maxima",
                      title="逐通道 α 频带最大值频率", description="横轴为 8–13 Hz 内功率最大的频率，纵轴为实际通道名称。")
print("table", tid, "figure", fid); print(rows[:8])
```

# 结果解释

回答用户的问题，引用支持结论的图表，说明影响解释的方法与范围。manifest/artifact ID 用于工具调用和链接，正文无需逐项列出后台 ID。
