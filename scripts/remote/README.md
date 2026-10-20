# 远程推理适配器

适配器读取网关发送的 NPZ 输入，按模型 recipe 准备张量，再返回输入张量、预测与方法元数据。服务器需要安装自己的模型代码、权重和对应依赖。

| 适配器 | 配方模板 |
| --- | --- |
| `eav_eegnet_infer.py` | `eav_eegnet_recipe.example.json` |
| `camer_eav_infer.py` | `camer_eav_recipe.example.json` |
| `camer_deap_1s_infer.py` | `camer_deap_1s_recipe.example.json` |

部署步骤：

1. 将三个适配器一起复制到 registry 中的 `remote.root`；它们会相互导入公共的加载和模型构造函数。
2. 将仓库 `src/eegagent/core/video_contract.py` 复制到相同目录，并安装 FFmpeg（含 `ffprobe`），供 CAMER 适配器核对视频时钟。
3. 复制对应 `.example.json` 模板，去掉文件名中的 `.example`，放在适配器旁。填写自己的源码、预处理实现、图像骨干和 checkpoint 路径，以及真实权重 SHA-256。
4. 核对配方中的模型结构、标签顺序与训练时输入；在本地 registry 填写同一组权重与契约。
5. 配置免交互 SSH 登录，按主 README 启动 worker 与网关。

EEGNet 适配器需要 NumPy、PyTorch 及对应预处理依赖；CAMER 还需要 OpenCV、Pillow、Transformers，DEAP 人脸裁剪需要 MediaPipe。版本采用实际训练环境。模板保留了适配器使用的键和输入配方，`/opt/efagent/configure/` 路径用于填写服务器上的真实文件。
