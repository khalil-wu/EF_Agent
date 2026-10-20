---
doc_id: dataset_mahnob
title: MAHNOB-HCI 数据集规范
source: Soleymani M., Lichtenauer J., Pun T., Pantic M. 2012, "A Multimodal Database for Affect Recognition and Implicit Tagging", IEEE Trans Affect Comput 3(1):42-55; mahnob-db.eu 数据说明; TorchEEG MAHNOBDataset 文档
version: 2026-10-15
domain: datasets
tasks: [emotion_recognition, data_understanding]
license: 需在 mahnob-db.eu 申请 EULA；本文只记录公开规范
---

## 采集

- 招募30人（17女/13男，19–40岁），论文分析排除P9/P12/P15后使用27人（16女/11男）。CAMER25人是另一个筛选子集。
- 原论文物理第2页Table1给32道EEG256 Hz、外周256 Hz、6摄像机60 fps、眼动60 Hz；该嵌入图像表已目视核对，不是自动文本提取结果。第4页的1024→256 Hz明确描述ECG/GSR/呼吸/温度，不能推为EEG原采集1024 Hz。实际输入采样率仍读BDF头。
- 情绪诱发实验：20 段电影片段（34.9–117 s），每段前后有中性片段；隐式标注实验：图片/视频加正确或错误标签。
- 标签（session.xml）：feltArsl、feltVlnc、feltCtrl、feltPred 各 1–9；feltEmo 为离散情绪编号（neutral、anxiety、amusement、sadness、joy、disgust、anger、surprise、fear 等）。

原文：[招募与排除](https://mahnob-db.eu/hci-tagging/media/uploads/taffcsi-2010-11-0112-2.pdf)；[采集统计表（查看原PDF表图）](https://mahnob-db.eu/hci-tagging/media/uploads/taffcsi-2010-11-0112-2.pdf)；[外周降采样](https://mahnob-db.eu/hci-tagging/media/uploads/taffcsi-2010-11-0112-2.pdf)。

## 文件结构

- Sessions/<id>/session.xml、EEG/外周BDF和视频。官方手册第5页规定BDF47道、最后一道Status；情绪刺激开始/停止对应 **0→16上升沿**。tagging有start/tag/end三个脉冲，第二脉冲32/48/64/80编码标签正确性与反馈（手册第17页Table11）。TorchEEG前两个事件的reader约定不能覆盖tagging记录。
- 手册第6页Table3列EEG1–32单位µV与Geneva顺序（同DEAP预处理版）。实际BDF physical dimension/刻度仍逐通道读头；MNE get_data() EEG返回V，换µV乘1e6；to_data_frame()默认已换µV。
- 第13页Table5的feltEmo与键盘反馈号码不同，不能把按键1–9当XML类索引。第18页Table12另列P3/P9/P12/P15/P16缺失/部分流及P26摄像机缺失；保留试次级筛选记录。

原文：[Status0→16与47道](https://mahnob-db.eu/hci-tagging/media/uploads/manual.pdf)；[单位与通道](https://mahnob-db.eu/hci-tagging/media/uploads/manual.pdf)；[三脉冲tagging](https://mahnob-db.eu/hci-tagging/media/uploads/manual.pdf)；[缺失流](https://mahnob-db.eu/hci-tagging/media/uploads/manual.pdf)。

## 常见处理

- 文件保留刺激前后各30 s上下文，不足部分全通道zero-pad；不能把补零段当实测信号统计。情绪trial的 **15 s neutral clip** 含基线，原论文说明该响应独立保存。30 s上下文与15 s独立基线分别记录。
- TorchEEG默认128 Hz、前30 s分块基线属于reader/transform约定；复现权重时核对其实际算子，不能称官方30 s基线。EEG与面部视频按时间戳/事件对齐，登记真实保留被试、试次、基线、补零与标签规则。

原文：[30 s上下文与补零](https://mahnob-db.eu/hci-tagging/media/uploads/manual.pdf)；[15秒独立中性基线（neutral clip）](https://mahnob-db.eu/hci-tagging/media/uploads/manual.pdf)；[情绪基线响应单独存储](https://mahnob-db.eu/hci-tagging/media/uploads/taffcsi-2010-11-0112-2.pdf)。

## 如何识别

- 情绪实验 EEG/外周信号文件形如 `Part_1_S_Trial1_emotion.bdf`（被试号、试次号），与会话目录中的 `session.xml` 和视频放在一起；官方手册给出的隐式标注实验命名为 `Part [被试号] Trial[试次号] tagging[实验id].bdf`。
- BDF 共 47 道：32 道 EEG（Geneva 顺序，与 DEAP 预处理版相同），其余为 EXG、GSR、呼吸、体温等外周通道，最后一道是 Status；EEG 256 Hz。
- 看到这种命名和通道结构即可按 MAHNOB-HCI 读取，情绪标签从 session.xml 读，不在 BDF 里。
- BioSemi 原始 BDF 保存相对 CMS 的未参考电压，含共模干扰与电极直流偏移：先重参考（平均或乳突）再看幅值与频谱。

## 与本项目的对应

- CAMER 上游 recipe 为 128 Hz、其参数表的 32 通道顺序、1 s 窗五带 DE、每窗 4 帧同步面部视频与四象限。公开 recipe 用于理解方法；具体 BDF 的采集顺序与处理历史读取文件及采集记录，权重输入配方和类别索引采用实际登记合同。
