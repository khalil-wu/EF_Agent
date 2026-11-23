# EFAgent 运行说明

安装与启动见根目录 README。网关默认监听 `127.0.0.1:8080`，Python worker 监听 `127.0.0.1:8100`。

## 配置

`scripts/start.ps1`、`serve.ps1` 和 `knowledge.ps1` 共用 `environment.ps1`，自动加载 `scripts/settings.local.ps1`，并读取当前终端的环境变量。也可用 `-Config` 指定配置文件。已有 `EEGAGENT_*` 配置仍可使用，同名 `EFAGENT_*` 优先。本机配置文件不要提交到 Git。

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
| `EFAGENT_RUN_TIMEOUT_MINUTES` | 单轮分析运行上限（分钟），默认 30 |

设置好环境变量后运行 `& .\scripts\start.ps1`，在后台启动两个进程并打开网页。`-NoBrowser` 仅启动服务。相同构建和配置复用进程；变化后只重启受影响的组件，网关会按源码增量构建。存在运行中的分析时保留原进程，分析结束后再次运行命令更新。两个进程继承同一组配置，启动日志位于 `storage/logs/`。

`/health` 提供应用、组件、构建、配置标识、源码目录、数据目录与 PID。端口被另一实例占用时，启动器会指出冲突；使用该实例的配置或选择其他端口。迁移旧版服务时，在 `/sessions` 全部 `running=false` 后停止旧网关和 worker，再运行新启动器。

代码与本机数据统一放在一个 EFAgent 目录。启动脚本自动加载 `scripts/settings.local.ps1`，其中指定数据、私有知识库、模型登记与字体路径。从 EEGAgent 目录迁移时可为旧路径创建目录联接，保留历史会话中的文件引用。EFAgent 与 EEGAgent 是同一项目。

### macOS / Linux

`scripts/start.sh` 与 PowerShell 启动器等价：`./scripts/start.sh`（可选 `start`/`stop`/`status` 与 `--no-browser`、`--config <file>`）自动加载 `scripts/settings.local.sh`（参考 `settings.local.sh.example`），按需构建前端与网关后拉起 worker 和网关，日志与 PID 位于 `storage/logs/`。默认 Python 为 `.venv-macos/bin/python`，缺失时退回 `.venv/bin/python`。

## 图片问答

`configs/conversation_models.json` 按接口返回的精确模型 ID 登记 `image_input`、验收日期与证据。当前 MiniMax-M3 已通过本机接口的四色象限定位测试；这项测试仅验证基础图片输入。接入其他模型时，使用实际接口完成图片定位、中文图表与追问验收，再在本机能力文件登记。

有图片的会话使用标有“看图”的模型。发送时前端会切换到已登记的看图模型，切换失败时保留草稿；网关在提交附件之前核对模型能力。

调试时仍可分别运行 `serve.ps1 -Component worker` 和 `serve.ps1 -Component gateway`，让服务在终端前台运行；Ctrl+C 停止对应进程。

## 工作区与资料

在网页上传文件，或挂接数据文件夹。将采集事实、视频配对与交付约定写入工作区根目录的 `EFAGENT.md`；兼容原有 `EEGAGENT.md` 和 `AGENTS.md`，按上述顺序选取。其他机器或旧路径下生成的存储记录在读取时自动对应当前存储目录；文件夹位于存储目录之外的工作区需重新挂接。

仓库附带领域知识笔记和图谱。论文全文、下载缓存和用户文档保存在本地；可上传 PDF、Markdown 或文本供当前会话检索。项目资料的批量导入可使用：

```powershell
& .\scripts\knowledge.ps1 ingest --help
```

导入、关系审核和上下文补全完成后会发布知识索引版本，运行中的 worker 在下次检索时加载新内容。手动编辑资料后运行 `& .\scripts\knowledge.ps1 refresh`；有本机配置时在 `knowledge.ps1` 后添加 `-Config`。知识统计的 `revision` 与搜索结果的 `index_revision` 可用于核对更新。

### 向量检索与重排

配置 `EFAGENT_EMBED_BASE_URL`、`EFAGENT_EMBED_API_KEY`、`EFAGENT_EMBED_MODEL` 后启用远程向量检索（OpenAI 兼容 `/embeddings`）；`EFAGENT_EMBED_DIMENSIONS` 与 `EFAGENT_EMBED_QUERY_INSTRUCTION` 可选。`EFAGENT_RERANK_BASE_URL`、`EFAGENT_RERANK_MODEL` 启用远程重排，`EFAGENT_RERANK_MODE` 取 `blend`（默认，融合检索名次与重排名次）或 `replace`。

可运行 python -m eegagent.knowledge audit --strict 离线检查知识卡片引用的文档、章节和原句是否仍能定位；缺失解析文档、章节或原句时会报告，而不是悄悄改引用。原论文、上游代码和私有资料并非全部随仓库分发，新机器需按许可导入；原文件丢失或摘要不符时只提供已保存的解析摘录。文本能定位不代表科学结论自动验证。

启用远程检索会把文档摘录与提问发送给所配置的接口提供方；密钥通过本机配置加载到 worker，不进入前端或版本库。直接运行 Python 命令时需使用 `EEGAGENT_*` 变量名；`EFAGENT_*` 仅由启动脚本映射。

向量保存在内存 NumPy 矩阵中并缓存于 `storage/knowledge_index/`，不需要外部向量数据库；首次构建在后台进行，`GET /knowledge` 的 `dense_ready` 为 true 前检索保持关键词+图谱。上传文档在解析后后台向量化。接口故障自动回退关键词检索，冷却约 60 秒后重试。

扫描 PDF 的 OCR 由 `EFAGENT_OCR`（`off` 关闭）与 `EFAGENT_OCR_LANG`（默认使用已安装的 `chi_sim`、`eng`）控制。macOS 通过 `brew install tesseract` 安装，另需将 `chi_sim.traineddata` 放入 `$(brew --prefix)/share/tessdata/`。Windows 需安装 Tesseract 并将 `TESSDATA_PREFIX` 指向含语言包的 tessdata 目录。未安装 OCR 时仍可解析原生文字，扫描页会标记为待 OCR；单份文档最多尝试 OCR 60 页。

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

## 本地安全边界

本应用是单用户本地应用：网关与 worker 只应监听回环地址，并拒绝非本机 `Host`、跨来源 `Origin` 与跨站 `Sec-Fetch-Site: cross-site` 请求（403），用于抵御浏览器跨域调用与 DNS 重绑定。这不提供容器级隔离——同一用户下的恶意 Python 包、浏览器扩展或本地进程不在防护范围内；不要把服务绑定到局域网或公网地址。`run_python` 子进程会剥离 `EEGAGENT_*`/`EFAGENT_*` 中的 LLM、嵌入与重排配置及常见凭据变量。

## 验证

访问 `/health` 检查网关和 worker 连接。验证协议与验收口径见 [docs/VALIDATION.md](VALIDATION.md)。干净环境装全量依赖并自检：

```bash
python -m pip install -e ".[worker,toolbox,knowledge,test]"
./scripts/start.sh doctor      # 先加载 settings.local.sh 再体检；--json / --strict
```

直接运行 `python -m eegagent.doctor` 检查的是当前 shell 环境（`EFAGENT_*` 别名按启动器规则覆盖同名 `EEGAGENT_*`）；`start.sh doctor` 会先应用本机配置再执行同一检查。Windows 等价命令：`. .\scripts\environment.ps1; & $env:EEGAGENT_PYTHON -m eegagent.doctor`。doctor 只做本地检查，不访问网络也不输出配置值；FFmpeg/Tesseract 缺失为警告，不影响纯 EEG 使用。

开发检查：

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[test]"
& .\.venv\Scripts\python.exe -m pytest
cd gateway
go test ./...
```

macOS / Linux 下的等价命令：

```bash
cd gateway && go vet ./... && go test ./...
env -u PYTHONSTARTUP .venv-macos/bin/python -m pytest tests -q --no-header -p no:cacheprovider
cd frontend && npm run check && npm test && npm run build
```

`gateway/internal/server/static/` 下的 `app.js`、`app.css`、`index.html`、`transcript-ui.js` 是 `npm run build` 的发布产物，修改 `frontend/src` 与 `frontend/bridge` 后统一构建。仓库根目录的 `EFAGENT.md` / `EEGAGENT.md` / `AGENTS.md` 会作为应用级约定进入对话模型的系统提示，开发说明不要放在这些文件里。

前端检查与构建命令见根 README。图标与字体采用本地图标子集和系统字体，界面运行无需字体 CDN。
