# EFAgent 运行说明

安装与启动见根目录 README。网关默认监听 `127.0.0.1:8080`，Python worker 监听 `127.0.0.1:8100`。

## 配置

`scripts/start.ps1`、`serve.ps1` 和 `knowledge.ps1` 共用 `environment.ps1`，从当前终端读取环境变量；可用 `-Config` 显式加载本机 PowerShell 配置文件。已有 `EEGAGENT_*` 配置仍可使用，同名 `EFAGENT_*` 优先。本机配置文件不要提交到 Git。

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
| `EFAGENT_LLM_CAPABILITIES` | 对话模型能力文件，默认 `configs/conversation_models.json` |
| `EFAGENT_PYTHON` | worker 解释器，默认 `.venv/Scripts/python.exe` |
| `EFAGENT_LOCAL_FONTS` | 本机字体 CSS；同目录 `fonts/` 随静态资源发布，字体不进入 Git |

设置好环境变量后运行 `& .\scripts\start.ps1`，在后台启动两个进程并打开网页。`-NoBrowser` 仅启动服务。相同构建和配置复用进程；变化后只重启受影响的组件，网关会按源码增量构建。存在运行中的分析时保留原进程，分析结束后再次运行命令更新。两个进程继承同一组配置，启动日志位于 `storage/logs/`。

`/health` 提供应用、组件、构建、配置标识、源码目录、数据目录与 PID。端口被另一实例占用时，启动器会指出冲突；使用该实例的配置或选择其他端口。迁移旧版服务时，在 `/sessions` 全部 `running=false` 后停止旧网关和 worker，再运行新启动器。

代码仅从一个 Git checkout 运行。已有 EEGAgent 的数据、知识库、私有模型登记、虚拟环境与本机字体可保留原目录，通过本机配置中的路径引用；EFAgent 与 EEGAgent 是同一项目。

## 图片问答

`configs/conversation_models.json` 按接口返回的精确模型 ID 登记 `image_input`、验收日期与证据。当前 MiniMax-M3 已通过本机接口的四色象限定位测试；这项测试仅验证基础图片输入。接入其他模型时，使用实际接口完成图片定位、中文图表与追问验收，再在本机能力文件登记。

有图片的会话需要选择标有“看图”的模型。前端保留未发送草稿并打开模型选择器，网关在提交附件之前核对能力；模型由用户明确选择。

调试时仍可分别运行 `serve.ps1 -Component worker` 和 `serve.ps1 -Component gateway`，让服务在终端前台运行；Ctrl+C 停止对应进程。

## 工作区与资料

在网页上传文件，或挂接数据文件夹。将采集事实、视频配对与交付约定写入工作区根目录的 `EFAGENT.md`；兼容原有 `EEGAGENT.md` 和 `AGENTS.md`，按上述顺序选取。

仓库附带领域知识笔记和图谱。论文全文、下载缓存和用户文档保存在本地；可上传 PDF、Markdown 或文本供当前会话检索。项目资料的批量导入可使用：

```powershell
& .\scripts\knowledge.ps1 ingest --help
```

导入、关系审核和上下文补全完成后会发布知识索引版本，运行中的 worker 在下次检索时加载新内容。手动编辑资料后运行 `& .\scripts\knowledge.ps1 refresh`；有本机配置时在 `knowledge.ps1` 后添加 `-Config`。知识统计的 `revision` 与搜索结果的 `index_revision` 可用于核对更新。

## 文件与交付

工具读取本会话已提交附件、当前工作区、明确配置的数据目录和本会话产物；从输入框移除的暂存附件不会作为分析输入。脚本可通过 `eeg.call` 连续检查、处理并导出新结果。

来源内容版本绑定登记时的摘要。原始 EEG 被外部软件改写后，旧记录会要求重新 `inspect_record` 登记新版本，避免同一产物身份对应不同数值。

EDF 导出保留可精确表示的采样时钟与样本数；需要补齐记录时，在产物说明和同卡片的元数据 JSON 中列出有效点数、文件点数与尾部补零量。下载后可用独立 EDF reader 核对。`read_file` 读取超长单行时使用返回的 `next_offset` 和 `next_char_offset` 继续，两个游标应同时传回。

## 接入专用情绪模型

`configs/model_registry.yaml` 保留输入通道、采样率、窗口、预处理与标签契约，初始状态为 `pending` / `planned` / `research_only`。对话模型和这些分析模型分别配置。

1. 在自己的推理服务器准备模型代码、运行环境和权重。
2. 将 `scripts/remote/` 中对应适配器放在执行目录；适配器参数见各文件的 `--help`。
3. 复制 registry 到本地配置文件，填写权重 `file_uri`、真实 `sha256` 和 `remote.host/python/root/script/gpu`；SSH 主机名可来自本机 SSH config。
4. 核对训练时输入和标签契约，将已配置条目的 `status` 设为 `registered`。
5. 两个终端设置 `EFAGENT_MODEL_REGISTRY` 指向该文件，然后启动服务。

本地 registry 可命名为 `model_registry.local.yaml`，它已被 Git 忽略。推理通过 SSH/SCP 发送本次输入并取回预测、输入张量与方法记录。

`execution_ready` 表示执行配置可用；`evaluation` 单独保存划分协议、checkpoint 选择依据及独立评估状态。若 `checkpoint_selection: test_accuracy`，结果会标记 `test_selected`，仍可运行并保留方法记录。重新独立评估后再更新该证据；软件测试不会替代模型评估。

## 验证

访问 `/health` 检查网关和 worker 连接。开发检查：

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[test]"
& .\.venv\Scripts\python.exe -m pytest
cd gateway
go test ./...
```

前端检查与构建命令见根 README。图标与字体采用本地图标子集和系统字体，界面运行无需字体 CDN。
