# MNE 扩展方法与实现口径

常规链路在脚本中用 eeg.call 复用科学工具；本页 API 示例用于工具未覆盖的研究分析。精确可用方法与参数采用当前工具 schema，科学依据按需检索知识库。

- EEGLAB 本体（MATLAB）不在环境里。这里使用 Picard 扩展 infomax、mne-icalabel、asrpy、PyPREP 和 MNE；用途相近不保证与 MATLAB 的 runica/clean_rawdata/newtimef 数值等价，报告实际实现与参数。
- 按质量证据选择处理，工具会记录实际处理链。ICA 前的高通由工具执行；坏道、ASR、ICA 不需要全部串行套用，不能把参考流程当已执行历史。
- ICA+ICLabel 按工具的通道/位置条件和实际秩拟合；入口最小通道数、样本量提示及 threshold 默认值属于工程策略。保留分类结果、实际剔除与前后比较。
- ASR 的 cutoff、校准与最小通道条件采用对应实现和数据要求，记录实际校准范围；论文示例与工具默认分别说明。参数较小通常处理更激进，其影响通过前后对比判断。
- MNE 内部单位是 V（eeg.to_mne 按记录元数据换算）：峰峰值阈值写 150e-6，不是 150。估计单位仍不代表物理校准。
- raw.filter / pick / crop / set_eeg_reference 都是原地修改；一个脚本里要做多种分析时先 raw.copy()。
- 沙箱里 n_jobs 只能为 1；不能联网，mne.datasets、fsaverage、源定位不可用。

# 可行性判断

- channel_layout.source=unknown：ICA+ICLabel、RANSAC、插值、地形图不可行；ASR、滤波、分段、单通道时频仍可做。先 device_lookup 或请用户提供通道名。
- 没有事件：不能自行构造 ERP 的刺激时间。可做频谱、固定窗口时频或连接性；未确认任务设计就不把记录称为静息态。
- ICLabel 的训练输入为 1–100 Hz 与确定的电压尺度；本次有效带宽、采样率和单位采用源记录及处理链。按实际成分、波形与频谱核对处理结果。
- 分段（epoched）.set 保留每个 epoch 的边界、tmin 和事件零点。ERP 用 eeg.to_epochs 重建原始分段，不能把打包数组当连续 EEG 再按段起点制造刺激事件。preprocess、segment_signal 与 Welch 计算遵循连续段边界。

# 用工具（优先）

- 坏道：clean_artifacts(source_id, method="bad_channels", action="interpolate")；summary.by_criterion 说明每个坏道被哪个判据命中。
- 突发伪迹：clean_artifacts(method="asr", cutoff=20)。
- 眼电 / 肌电：clean_artifacts(method="ica_iclabel", threshold=0.8)；summary.removed 列出剔除的成分，ica_solution_path 可在脚本里 mne.preprocessing.read_ica 读回。

# 用脚本（run_python，工具不覆盖时）

## 分段与 ERP

已分段的记录直接读取：

```python
epochs = eeg.to_epochs("SOURCE_ID")  # V；原始 tmin、条件、epoch 身份
epochs = epochs.copy().filter(0.1, 30.0, picks="eeg")
epochs.apply_baseline((None, 0))
print(epochs.event_id, epochs.tmin)
aid = eeg.save_epochs(epochs, {"kind": "erp_input"}, steps=["bandpass_0.1_30_fir", "epoch_baseline_to_0"])
```

连续记录按真实事件分段：

下面参数仅为示例。事件标记、时间零点、条件、基线和剔除阈值需要符合记录；0.1 Hz 低截止不能恢复上游已被 4 Hz 高通去掉的信息。

```python
import mne, numpy as np
raw = eeg.to_mne("SOURCE_ID")                      # V；带位置与记录事件
raw_erp = raw.copy().filter(0.1, 30.0, picks="eeg")  # 在副本上滤波：raw.filter 是原地修改，会影响后面的分析
events, event_id = mne.events_from_annotations(raw_erp)
print(event_id)
epochs = mne.Epochs(raw_erp, events, event_id=event_id, tmin=-0.2, tmax=0.8, baseline=(None, 0),
                    reject=dict(eeg=150e-6), picks="eeg", preload=True)
print(epochs)                                      # 各条件剩余 epoch 数
evokeds = {k: epochs[k].average() for k in event_id if len(epochs[k])}
fig = mne.viz.plot_compare_evokeds(evokeds, picks="eeg", combine="mean", show=False)[0]
fig.suptitle("事件相关电位条件对照")
for ax in fig.axes:
    ax.set(xlabel="事件相对时间（秒）", ylabel="振幅（µV）", title="")
eeg.save_figure(fig, {"kind": "erp", "n_epochs": {k: len(epochs[k]) for k in evokeds}},
                title="事件相关电位条件对照", description="各条件按有效试次平均后的 ERP；条件名称取实际事件标签。")
rows = []
for k, ev in evokeds.items():
    ch, lat, amp = ev.get_peak(ch_type="eeg", tmin=0.25, tmax=0.5, mode="pos", return_amplitude=True)
    rows.append({"condition": k, "channel": ch, "latency_s": round(float(lat), 3), "amplitude_uV": round(float(amp) * 1e6, 2)})
eeg.save_table(rows, {"window_s": [0.25, 0.5]}, name="erp_peaks")
print(rows)
```

## 时频（ERSP）

基线校正须有真实基线；功率图的颜色表示所用 logratio，不能直接称统计显著。

```python
freqs = np.arange(4.0, 31.0, 1.0)
power = epochs.compute_tfr("morlet", freqs=freqs, n_cycles=freqs / 2.0, return_itc=False, average=True)
power.apply_baseline((-0.2, 0.0), mode="logratio")
fig = power.plot(picks=[epochs.ch_names[0]], dB=False, show=False)[0]
fig.axes[0].set(xlabel="事件相对时间（秒）", ylabel="频率（Hz）", title=epochs.ch_names[0]+" 时频功率")
if len(fig.axes) > 1:
    fig.axes[-1].set_ylabel("相对基线的对数功率比")
eeg.save_figure(fig, {"kind": "ersp", "baseline": "logratio -0.2–0 s"},
                title="事件相关时频功率", description="颜色为相对真实事件基线的 log10 功率比。")
```

## FOOOF：alpha 峰频率与 1/f 指数

```python
from fooof import FOOOFGroup
from fooof.analysis import get_band_peak_fg
psd = raw.compute_psd(method="welch", fmin=1.0, fmax=40.0, n_fft=int(4 * raw.info["sfreq"]), picks="eeg")
fg = FOOOFGroup(peak_width_limits=(1.0, 8.0), max_n_peaks=6, verbose=False)
fg.fit(psd.freqs, psd.get_data(), [1.0, 40.0])
alpha = get_band_peak_fg(fg, [7.0, 14.0])          # 每通道 [中心频率, 功率, 带宽]，没有峰为 nan
rows = [{"channel": ch, "alpha_cf_hz": None if np.isnan(a[0]) else round(float(a[0]), 2),
         "exponent": round(float(e), 3), "r2": round(float(r), 3)}
        for ch, a, e, r in zip(psd.ch_names, alpha, fg.get_params("aperiodic_params", "exponent"), fg.get_params("r_squared"))]
eeg.save_table(rows, {"fit_range_hz": [1, 40], "median_exponent": float(np.median([r["exponent"] for r in rows]))}, name="fooof")
```

## 连接性（wPLI）

wPLI 是估计的功能连接指标，不证明直接结构连接、信息流方向或因果关系。

```python
from mne_connectivity import spectral_connectivity_epochs
ep = mne.make_fixed_length_epochs(raw, duration=2.0, preload=True).pick("eeg")
con = spectral_connectivity_epochs(ep, method="wpli2_debiased", mode="multitaper", fmin=8.0, fmax=13.0, faverage=True, verbose=False)
m = con.get_data(output="dense")[:, :, 0]          # 下三角有值
m = m + m.T
```

## Autoreject（分段数据）

```python
from autoreject import AutoReject
ep = mne.make_fixed_length_epochs(raw, duration=2.0, preload=True).pick("eeg")
ar = AutoReject(n_interpolate=[1, 2, 4], random_state=42, n_jobs=1, verbose=False)
ep_clean, log = ar.fit_transform(ep, return_log=True)
print(int(log.bad_epochs.sum()), "/", len(ep), "epochs rejected")
```

## 把 MNE 处理结果存回工具链

```python
raw_clean = raw.copy().filter(1.0, 40.0, picks="eeg")
aid = eeg.save_raw(raw_clean, {"what": "FIR 1–40 Hz"}, steps=["bandpass_1_40_fir"], name="fir_1_40")
print(aid)                                          # 之后可 segment_signal / extract_features / plot
```

导出给 EEGLAB 打开：`mne.export.export_raw(str(eeg.scratch / "clean.set"), raw_clean, fmt="eeglab", overwrite=True)`（文件在 scratch，告诉用户路径）。

# 回答要点（按本次问题取舍）

报告实际实现、影响结论的参数、修改内容与对应产物即可，按问题取舍，不固定输出模板。MNE 现成图也需检查尺寸、坐标与颜色，绘图决策见 scripting 的 [figures.md](../../scripting/references/figures.md)。

# 示例用户话语

- 用 EEGLAB 那套流程帮我去掉眼电
- 做个 ICA 看看有哪些伪迹成分
- 哪些通道是坏道，插值一下
- 按事件分段画 ERP，比较两种条件
- 算一下每个通道的 alpha 峰频率和 1/f 斜率
