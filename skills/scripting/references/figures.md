# 科研绘图

先确定要展示或比较的量和单位，再选择能回答问题的图形。标题、坐标和图例保持简洁，完整方法放在复现记录中。

## 运行时接口

```python
steps = eeg.signal_steps(source_id)
unit = eeg.amplitude_unit(steps)  # µV / a.u. / 未知单位
note = eeg.unit_note(steps)      # 仅未确认单位或归一化时有简短说明
rate_note = eeg.sampling_note(steps, sample_rate_hz)
fig, ax = eeg.figure(width="wide", height=3.2, profile="publication")  # wide 183 mm / single 89 mm
ax.plot(time_s, measured_values, color=eeg.colors[0], label="实测信号")
ax.set(xlabel="从当前信号起点计时（秒）", ylabel=f"振幅（{unit}）")
ax.legend()
if note or rate_note:
    fig.supxlabel("\n".join(text for text in (note, rate_note) if text), fontsize=8)
fid = eeg.save_figure(fig, {
    "kind": "signal_detail", "source": source_id,
    "unit": unit, "unit_note": note, "time_range_s": [float(time_s[0]), float(time_s[-1])],
    "sample_rate_note": rate_note,
    "original_recording_offset_s": eeg.signal_time_offset(source_id),
    "method": "direct trace; no smoothing",
}, name="signal_detail", time_range_s=[float(time_s[0]), float(time_s[-1])])
```

`eeg.figure(nrows=…, ncols=…, width=…, height=…, sharex=…, squeeze=…)` 沿用 Matplotlib 的 axes 返回规则。`eeg.colors` 和 `eeg.band_colors` 供同语义重复使用。普通 pyplot 与 MNE 图也有同一默认字体、线宽及配色。`save_figure` / Figure 类型的 `save_artifact` 同时保留 300 dpi PNG、可编辑文字 SVG 和嵌入字体 PDF；一个图对应一个引用。`export(kind="svg"/"pdf", source_id=fid)` 可导出矢量文件。不要用 `style.use()` 或自写 rcParams 重置这套样式；确有期刊格式要求才做局部覆盖。

## 选择与布局

投稿使用 profile="publication"，正文 6–6.5 pt，单栏/双栏宽 89/183 mm，导出保持目标尺寸；界面查看用 profile="screen"。内置 plot(kind="overview") 可直接生成 a/b/c 主图。完整方法保存在图的 caption 元数据，投稿图只显示影响读数的单位说明。

- 波形：同类通道共用一种色、纵向错位显示；明确展示去均值和统一比例；标尺放在无信号留白区。避免几十种颜色。
- PSD：少量通道可逐条比较；多通道用淡灰个体曲线、主色均值和通道 IQR。IQR 是通道间差异，**不是置信区间**，不能支持独立样本或显著性推断。
- 频带趋势：分面共享时间轴，明确各频带独立 y 轴和窗口中心。重叠窗口不能当独立重复。缺失或不可计算的数据保留缺口并说明，不能补零。
- 峰值频率等接近的数值用点图而非截断柱状图；柱状图从零开始。条件比较展示真实个体数据；独立单位应是被试或 trial，不能虚构误差线与星号。
- Topomap：只有确认的电极位置和映射才画；空间插值只用于显示，不能当作测量值。比较条件应共用色标，注明单位、频带和聚合范围。
- 单图回答一个问题。常规标题短、正常字重；坐标写量和单位；图例避开数据。复杂模型/事件定义和完整方法放正文图注，避免都塞进图内。

## 数值与证据

1. 不为了美观平滑、截掉异常点、隐藏低功率频段或改单位。若用户要求聚焦范围，图与 summary 都写实际范围、筛选规则、变换和原数据引用。
2. 用户提供的单位先登记，图直接使用该单位。物理单位未知写“原始数值”，标准化写“任意单位”；估计电压写“估计 µV”。不同物理量分别绘图。标题、坐标、图例与可见说明用中文，保留真实通道名、单位和规范缩写。
3. 时间写 source 起点或原记录起点；裁剪/基线删除后保存 offset。频谱写算法、段长、窗函数、重叠、分辨率、频带范围。DE 注明对数底和参考幅值单位。
4. 功率突增只是观测，不能自动宣判为伪迹、情绪或睡眠阶段。源格式、频谱和近零共平均不能认证数据来自官方发行包。
5. 保存后核对轴范围、单位、通道顺序、可读字号、图例遮挡和数据缺口。输出结论引用正确的图，不用同一图证明未测量的事实。
