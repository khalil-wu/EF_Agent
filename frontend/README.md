# EFAgent 对话前端

对话区域使用 React 19、TypeScript、React Markdown 和 remark-gfm，Vite 生成本地静态包。Lucide 图标由本地子集提供，不依赖 CDN。

```powershell
cd frontend
npm install
npm run check
npm test
npm run build
```

`src/transcript-store.ts` 是对话数据唯一所有者：消息、工具、产物以稳定 ID 更新。`src/transcript.tsx` 独占 `#chat` 的 DOM。已完成段落、引用按钮和图像由 React 保留，流式输出只更新对应内容，不再每帧重写整个 Markdown DOM。

`bridge/app.js` 管理网关连接、输入器、逐题提问和右侧资料面板，`bridge/app.css` 与 `bridge/index.html` 是外层布局源文件。它们不得清空或修改 React 对话区域的子树。构建会把这些文件同步到 `gateway/internal/server/static`；该目录里的 `app.js`、`app.css`、`index.html` 和 `transcript-ui.js` 是发布产物，修改前端请改这里的源文件后统一构建。

POST 发送与 GET 续接使用同一个 SSE 消费器。快照先恢复持久消息，再消费 `live.events`，从 `live.run_id/live.sequence` 继续 GET 订阅；切换会话会取消客户端连接，不中断服务端任务。旧游标 409 时重新读取快照。真实的失败和用户停止保留已输出文本。

上游 Chat Completions 不能在第一个 token 前确定公开文本属于过程还是最终答案。UI 明确使用 pending 状态连续展示；工具调用确定后更新 commentary，完成后确定 final_answer/interrupted。私有 reasoning 不在公开 SSE 中。同步 Python 工具目前提供真实开始、运行和结束事件，不虚构百分比或 stdout 流。

模型失败或用户停止会立即结束本轮文字和工具进度；`session_saved` 负责持久化结束，不延长生成状态。首个回复 token 前失败的会话重新打开时仍保留失败卡片；用户重试后，新请求独立显示真实生成进度。

测试覆盖事件顺序、终态与重试、SSE 关闭、游标恢复、插话归属、折叠选择、图表去重与真实引用；DOM 测试验证增量更新和完成时保留原段落、引用、图片及文本选择。
