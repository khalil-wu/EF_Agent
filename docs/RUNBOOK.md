# EFAgent 运行说明

安装与启动见根目录 README。网关默认监听 `127.0.0.1:8080`，Python worker 监听 `127.0.0.1:8100`。

## 配置

`scripts/serve.ps1` 从当前终端读取 `EFAGENT_*` 环境变量；不加载私人脚本。已有 `EEGAGENT_*` 配置仍可使用，同名 `EFAGENT_*` 优先。

| 变量 | 用途 / 默认值 |
| --- | --- |
| `EFAGENT_LLM_BASE_URL` | Chat Completions API 根地址，包含 `/v1` |
| `EFAGENT_LLM_MODEL` | 对话模型 ID |
| `EFAGENT_LLM_API_KEY` | API 密钥 |
| `EFAGENT_LLM_PROXY` | `environment`（默认）或 `direct` |
| `EFAGENT_STORAGE_DIR` | 会话、上传与产物目录，默认仓库内 `storage/` |
| `EFAGENT_DATA_DIRS` | 可读取的数据目录，多个目录用分号分隔 |
| `EFAGENT_ADDR` | 网关地址，默认 `127.0.0.1:8080` |
| `EFAGENT_WORKER_URL` | worker 地址，默认 `http://127.0.0.1:8100` |
| `EFAGENT_MODEL_REGISTRY` | 模型登记文件，默认 `configs/model_registry.yaml` |
| `EFAGENT_KNOWLEDGE_DIR` | 知识目录，默认 `knowledge/` |

设置好环境变量后运行 `& .\scripts\start.ps1`，在后台启动两个进程并打开网页；重复运行会复用已就绪的服务。`-NoBrowser` 仅启动服务。两个进程继承同一组配置，启动日志位于 `storage/logs/`。

调试时仍可分别运行 `serve.ps1 -Component worker` 和 `serve.ps1 -Component gateway`，让服务在终端前台运行；Ctrl+C 停止对应进程。

## 工作区与资料

在网页上传文件，或挂接数据文件夹。将采集事实、视频配对与交付约定写入工作区根目录的 `EFAGENT.md`；兼容原有 `EEGAGENT.md` 和 `AGENTS.md`，按上述顺序选取。

仓库附带领域知识笔记和图谱。论文全文、下载缓存和用户文档保存在本地；可上传 PDF、Markdown 或文本供当前会话检索。项目资料的批量导入可使用：

```powershell
& .\.venv\Scripts\python.exe -m eegagent.knowledge ingest --help
```

## 接入专用情绪模型

`configs/model_registry.yaml` 保留输入通道、采样率、窗口、预处理与标签契约，初始状态为 `pending` / `planned` / `research_only`。对话模型和这些分析模型分别配置。

1. 在自己的推理服务器准备模型代码、运行环境和权重。
2. 将 `scripts/remote/` 中对应适配器放在执行目录；适配器参数见各文件的 `--help`。
3. 复制 registry 到本地配置文件，填写权重 `file_uri`、真实 `sha256` 和 `remote.host/python/root/script/gpu`；SSH 主机名可来自本机 SSH config。
4. 核对训练时输入和标签契约，将已配置条目的 `status` 设为 `registered`。
5. 两个终端设置 `EFAGENT_MODEL_REGISTRY` 指向该文件，然后启动服务。

本地 registry 可命名为 `model_registry.local.yaml`，它已被 Git 忽略。推理通过 SSH/SCP 发送本次输入并取回预测、输入张量与方法记录。

## 验证

访问 `/health` 检查网关和 worker 连接。开发检查：

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[test]"
& .\.venv\Scripts\python.exe -m pytest
cd gateway
go test ./...
```

前端检查与构建命令见根 README。图标与字体采用本地图标子集和系统字体，界面运行无需字体 CDN。
