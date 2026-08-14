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

## 实现门禁

- 修改前先识别现有设计tokens、共享UI组件和同类页面；优先复用，不为单页复制视觉系统。管理后台同时遵守`docs/design/admin-ui-visual-contract.md`（存在时）。
- 新页面必须先定义桌面端和390px移动端布局。页面主体不得横向溢出；核心操作不得依赖隐藏的横向滚动，宽表只能在表格自身容器滚动，并在窄屏保留可发现的对象身份和操作。
- `file`、`select`、`checkbox`、`radio`等原生控件必须使用统一组件，或在同一改动中提供基于tokens的完整、可复用样式；不得直接交付浏览器默认外观。
- 操作型页面及受影响的异步区域必须覆盖`loading`、`empty`、`error`、`busy`、`disabled`和成功反馈；批量操作还要保留部分成功、逐项失败原因和恢复入口。
- 不向普通业务人员直接暴露内部ID、内部键、请求ID、UUID等技术字段；确有业务、审计或排障需要时，使用产品化中文标签、解释和受控详情。
- 熟悉且低歧义的图标操作使用现有图标组件；图标按钮必须有稳定尺寸、可访问名称和tooltip，业务关键或危险操作不得只靠图标表达。所有控件须有可见`focus-visible`、正确语义/ARIA和键盘路径，动态结果按需使用`status`、`alert`或`aria-live`。
- 为控件、网格和动态区域定义稳定尺寸或响应式约束。长文本、最长单词、动态状态和加载内容必须换行、截断并提供完整值，或预留空间；不得溢出、遮挡相邻内容或造成无必要的布局位移。

## 验证门禁

- 新页面或重大视觉修改必须使用合成、脱敏数据在真实浏览器验收，最低覆盖`1280x720`和`390x844`；管理后台按视觉契约补充其更严格的viewport和场景。
- 每个viewport必须检查`body`横向溢出、核心操作可见性、文字/浮层遮挡、表格滚动边界，以及正常、焦点、loading、empty、error、busy、disabled和成功状态。
- 修改后运行`npm run build`、相关unit test（至少仓库的`npm run test:run`或更小且可说明的相关集合），以及仓库提供的视觉检查命令。不得用build替代unit test或浏览器验收。
- 没有可用视觉工具、命令或fixture时，必须逐项报告未验证内容和原因，不得声称视觉、响应式或浏览器验收通过。
- 用户可见变化完成技术验证后，按`docs/USER_ACCEPTANCE.md`提供验收交接；未经用户确认不得声称用户验收通过。
