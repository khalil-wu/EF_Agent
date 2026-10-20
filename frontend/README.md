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

字体优先级保留原配置。本机字体定义可放入 `bridge/fonts.local.css`，字体文件放入 `bridge/fonts/`；构建时追加本地字体定义并复制字体文件。这些本地文件不纳入 Git，公开构建和本机字体设置分别维护。

POST 发送与 GET 续接使用同一个 SSE 消费器。快照先恢复持久消息，再消费 `live.events`，从 `live.run_id/live.sequence` 继续 GET 订阅；切换会话会取消客户端连接，不中断服务端任务。旧游标 409 时重新读取快照。真实的失败和用户停止保留已输出文本。

上游 Chat Completions 不能在第一个 token 前确定正文属于过程还是最终答案。UI 使用 pending 状态连续展示；工具调用确定后更新 commentary，完成后确定 final_answer/interrupted。接口返回的 reasoning_content 通过独立 reasoning_delta 事件流式展示，界面只保留最新 600 字符的三行预览。第一段正文（包括 commentary）、工具调用、等待用户输入或本轮结束，会同步清除推理状态；同一条消息的晚到推理不重新打开。仍在推理阶段但停更 5 秒时，预览淡出；下一步模型请求可重新显示新推理。过期事件在续接时不重放到界面。推理文字不写入对话消息、导出回复或模型上下文。

模型失败或用户停止会立即结束本轮文字和工具进度；`session_saved` 负责持久化结束，不延长生成状态。首个回复 token 前失败的会话重新打开时仍保留失败卡片；用户重试后，新请求独立显示真实生成进度。

测试覆盖事件顺序、终态与重试、SSE 关闭、游标恢复、插话归属、折叠选择、图表去重与真实引用；DOM 测试验证增量更新和完成时保留原段落、引用、图片及文本选择。
