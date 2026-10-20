# EFAgent

**EEG + Face 多模态分析 Agent。** 用自然语言处理脑电、人脸视频和图片，在同一工作区完成数据检查、信号清洗、逐窗特征、可视化与报告导出。

- 上传数据、粘贴图片，或挂接本地文件夹；通过 `@` 引用文件。
- 脑电预处理、ICA、频谱、差分熵、连接性与质量分析。
- EEG 与人脸视频配对分析，按已配置的模型契约执行远程情绪识别。
- 中文图表、过程反馈、产物预览与下载、会话恢复。
- 内置领域技能与知识笔记，支持资料检索和原文定位。

## 启动

需要 Python 3.11+、Go 1.22+ 和 PowerShell。处理视频还需要将 FFmpeg（含 `ffprobe`）加入 PATH。

```powershell
git clone https://github.com/khalil-wu/EF_Agent.git
cd EF_Agent
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[worker,toolbox,knowledge]"
```

首次在 PowerShell 中配置兼容 OpenAI Chat Completions 的对话模型：

```powershell
$env:EFAGENT_LLM_BASE_URL = "https://api.openai.com/v1"
$env:EFAGENT_LLM_MODEL = "your-model"
$env:EFAGENT_LLM_API_KEY = "your-api-key"
```

一个命令启动：

```powershell
& .\scripts\start.ps1
```

脚本在后台启动 worker 和网关，自动打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。已运行的服务会直接复用。数据、会话与产物保存在本地 `storage/`，启动日志位于 `storage/logs/`。

## 开发

仓库已包含构建好的前端。修改界面时，需要 Node.js 22.12+：

```powershell
cd frontend
npm ci
npm run check
npm test
npm run build
```

`gateway/` 是 Go 网关，`src/eegagent/` 是 Python 计算服务，`frontend/` 是界面源码，`skills/` 与 `knowledge/` 保存领域方法和知识笔记。内部包名与旧配置仍兼容 `eegagent` / `EEGAGENT_*`。

专用情绪模型通过自有权重和 SSH 执行器接入，配置见 [运行说明](docs/RUNBOOK.md)。工作区约定可参考 [EFAGENT.example.md](EFAGENT.example.md)。

## 许可证

[Apache-2.0](LICENSE)。第三方组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
