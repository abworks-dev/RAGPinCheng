---
paths:
  - "frontend/**/*"
---

# Frontend Rules

- API调用集中在`frontend/src/api/`，保持`credentials: include`和CSRF注入。
- 后端`SourceDTO`与SSE事件变化必须同步`types.ts`、stream reader和相关组件。
- `prep/token/done/error`都要有可恢复的UI状态；处理断开、重复提交和切换对话。
- 权限由后端强制，前端隐藏按钮不是安全控制。
- 保持Markdown、GFM、Math/KaTeX和引用解析兼容。
- Transcript当前只展示时间引用；媒体 URL、播放器字段和播放链路只能作为补充报告中的候选实现，不得写成现有能力。
- 修改后运行`npm run build`，报告TypeScript和Vite结果。