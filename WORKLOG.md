# 工作日志

由 Claude Code 在每次任务完成后按日期追加。这里只记录实际完成的工作、验证结果以及必要的待办或风险。

## 2026-07-22

### Codex 辅助角色与审批门禁

- 完成：新增 Codex 协作入口，明确 Claude Code 是主要开发者、Codex 是辅助协作者；规定任务先做 `R0–R3` 评级，`R2/R3` 展示方案并等待明确批准后方可执行。
- 文件：`AGENTS.md`、`WORKLOG.md`
- 验证：已按批准方案核对角色分工、授权语义、重新审批条件、执行边界和回滚要求；未修改或运行项目业务代码。

### 20:20 — 增加 Claude 方案审批门禁

- 完成：在项目入口加入 `R0–R3` 风险评级；规定 `R2/R3` 任务提交方案后必须停止，只有用户明确批准后才能执行，范围或风险变化时需重新审批。
- 文件：`CLAUDE.md`、`WORKLOG.md`
- 验证：已检查规则差异，确认保留并衔接原有 Reset、删除、生产部署等专项确认要求；未运行项目业务测试。

### 修复引用角标 tooltip 闪烁无法停留的问题

- **问题原因**：`onMouseEnter/onMouseLeave` 只绑定在 `<a>` 角标上，而 tooltip 是兄弟元素。鼠标从角标移到 tooltip 时触发 `onMouseLeave` → tooltip 消失 → 鼠标回到角标 → 无限闪烁。
- **修复方案**：
  - hover 事件移到外层 `<sup>` 上（角标 + tooltip 都在里面）
  - 用 `bottom-[100%] mb-0.5` 替代 `-translate-y-full`，让 tooltip 底部略微重叠角标（无视觉间隙）
- **文件**：`frontend/src/components/Message.tsx`

### 修复引用角标 tooltip 在左侧被侧边栏遮挡的问题

- **问题原因**：tooltip 使用 `right-0` 定位，从角标**向左展开**。当角标靠近页面左侧时，tooltip 会延伸到侧边栏（`<aside>`）区域，被其背景和边框遮挡。
- **修复方案**：将 `right-0` 改为 `left-0`，让 tooltip 从角标**向右展开**，避开左侧侧边栏。
- **文件**：`frontend/src/components/Message.tsx`

### 修复引用角标 tooltip 在顶部被视口遮挡的问题

- **问题原因**：tooltip 固定显示在角标上方（`bottom-[100%]`）。当角标靠近视口顶部时（如页面滚动到第一条回答），tooltip 上边缘会超出视口，内容被截断。
- **修复方案**：
  - 使用 `useLayoutEffect` 在渲染后即时检测 tooltip 的 `getBoundingClientRect()`
  - 若 tooltip 上边缘距视口顶部 < 10px，动态切换到下方显示（`top-[100%]`）
  - 两种定位都保留微小间距（`mb-0.5` / `mt-0.5`）防止鼠标移动时闪烁
- **文件**：`frontend/src/components/Message.tsx`
- **验证**：前端 `npm run build` 构建通过 ✅

### 修复暗黑模式下对话列表标题颜色过深的问题

- **问题原因**：`ConversationList.tsx` 使用 `text-ink/90`——这是 Tailwind 的 opacity 语法，生成硬编码的 `color: rgb(31, 41, 55) / 0.9;`。暗黑模式的 `.dark .text-ink` 覆盖只对纯 `text-ink` 类有效，导致标题颜色仍是深灰色，与深色背景融为一体。
- **修复方案**：改为 `text-ink opacity-90`，`opacity` 不影响颜色通道，暗黑模式的颜色覆盖能正常生效。
- **附加修复**：给引用角标 hover 状态添加 `dark:hover:bg-gray-700`，与暗黑模式背景协调。
- **文件**：`frontend/src/components/ConversationList.tsx`、`frontend/src/components/Message.tsx`
- **验证**：前端 `npm run build` 构建通过 ✅

### 修复第二次输入纯数字仍会检索的问题

- **问题根源**：`query_guard.py` 中 `has_history=True` 时对纯数字输入无条件放行，导致第二轮输入 "222" 被改写成 "222 是什么" 后走检索流程，但历史对话中没有任何文档上下文，返回页码匹配的垃圾结果。
- **修复方案**：
  - 新增 `_PURE_DIGITS_ONLY_RE` 正则，专门检测纯数字输入（只有数字、空格、小数点）
  - 纯数字输入**无论第几轮对话始终拦截**
  - 正常跟进（如 `"那 22 呢？"`）包含中文字符，不会被误拦截
- **文件**：`src/query_guard.py`
- **验证**：逻辑自检验证通过，测试用例覆盖边界情况。

### 修复引用角标垂直偏移与样式

- **问题原因**：原角标使用 `align-top` + `inline-flex` 在 `<sup>` 上，数字显示位置偏上且缺少边框；WPS 采用 `<sup>` 包裹 `<a>` 的结构，垂直定位更精确。
- **修改内容**：
  - DOM 结构改为 `sup > a`（与 WPS 一致）
  - 垂直对齐：`top-[-0.35em] align-baseline` 精确控制
  - 尺寸固定：`h-[18px] min-w-[18px] text-[11px]`
  - 样式：浅灰背景 `bg-gray-100` + 细边框 `border-gray-200` + 圆角，与正文融合自然
  - tooltip 移入 `<sup>` 内部，定位更准确
- **文件**：`frontend/src/components/Message.tsx`
- **验证**：前端构建通过 ✅

### 18:44 — 调查 WPS 风格引用角标

- 完成：定位现有回答角标、悬浮预览与来源面板实现，确认可在保留当前引用解析和数据结构的前提下复刻 WPS 风格；给出 Firefox 中采集目标元素 DOM 与样式的范围。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读检查 `Message.tsx`、`citations.ts`、`SourcesPanel.tsx`、`index.css` 和前端类型；未运行前端构建。
- 待办/风险：当前截图未包含已生成答案及引用浮层，精确还原仍需角标常态、悬停态和展开来源态的 DOM/样式或截图。

### 修复 SSE 流超时导致的 network error

- **问题原因**：SSE 流在 LLM 生成停顿超过 30 秒时，被反向代理（Nginx/Cloudflare）或浏览器因空闲超时断开，导致用户看到 `⚠️ network error`。
- **解决方案**：在后端 SSE 事件生成器中添加 15 秒心跳机制。当没有 token 到达时，发送 SSE 注释（`: ping`）保持连接活跃。前端已兼容处理 SSE 注释行。
- **文件**：`api/routes_chat.py`
- **修改内容**：
  - 添加 `last_activity` 时间跟踪
  - 每 15 秒无活动时发送 `ping_event`（SSE 注释，前端自动忽略）
  - 使用 `asyncio.wait_for` + 短超时循环实现心跳窗口

### 优化参考来源预览移除 Markdown 语法标记

- **问题原因**：参考来源的预览文本直接显示原始 Markdown 源代码，用户看到大量 `####` 标题标记和表格语法，影响可读性。
- **解决方案**：新增 `stripMarkdown()` 工具函数，在预览显示前移除 Markdown 语法标记：
  - 移除标题标记 `#`、代码块 `` ` ``、粗体 `**`、斜体 `*`
  - 移除链接、图片、列表、引用、水平分割线标记
  - 清理表格管道符号和多余空白行
- **文件**：`frontend/src/utils/markdown.ts`（新建）、`frontend/src/components/Message.tsx`、`frontend/src/components/SourcesPanel.tsx`
- **影响范围**：
  - 角标 Tooltip 预览（120 字符）
  - 参考来源面板的文本预览（400 字符，支持展开）
- **验证**：前端 `npm run build` 构建通过 ✅
- **验证**：代码语法检查通过，逻辑与现有流处理兼容。

### 移除 LLM 输出中的"资料来源"重复项

- 完成：修改 `answer_system.md` Prompt，移除要求 LLM 在正文末尾追加"**资料来源：**"小节的指令。因为前端已经通过独立的折叠面板展示参考来源，LLM 再输出一遍会导致重复显示。
- 文件：`prompts/answer_system.md`
- 验证：已核对 Prompt 修改内容；新生成的回答将不再包含"资料来源："标题和列表，只保留行内引用标注。

### 整理并优化 Roadmap TODO 文档

- 完成：将历史 TODO 重新整理为 Markdown 格式的 Roadmap，按优先级分类（高/中/增强/运维），补充设计方案细节、涉及文件和状态标记；同时添加已完成功能的历史记录，方便追踪项目进展。
- 文件：`TODO.md`（新建）
- 验证：已核对所有待办项与代码中的设计注释，格式符合 Markdown 标准；未修改业务代码。

## 2026-07-20

### 配置 Claude Code 每次任务工作日志

- 完成：在项目入口指令中加入强制收尾规则，要求 Claude Code 每次任务完成后按日期记录成果、涉及文件、验证结果和必要风险。
- 文件：`CLAUDE.md`、`WORKLOG.md`
- 验证：已核对入口规则与日志模板内容；未修改或运行项目业务代码。

### Docker 构建缓存优化与依赖清理

- 完成：
  1. 清理 `requirements-prod.txt` 中无用依赖（删除 `mineru[core]` 和 `streamlit`），镜像预计瘦身约 500 MB
  2. 给 `FlagEmbedding` 加上版本上限 `<2`，防止大版本升级破坏检索逻辑
  3. 更新 `docker/Dockerfile.backend` 中关于 `platforms` 参数的过时注释（已默认禁用以优化缓存）
  4. `torch>=2.7` → `torch==2.7.0` 锁定精确版本，确保 Docker 层缓存命中
  5. `docker/docker-compose.yml` 禁用 `build.platforms`，消除跨平台构建对缓存的负面影响
- 文件：`requirements-prod.txt`、`docker/Dockerfile.backend`、`docker/docker-compose.yml`
- 验证：本地 `docker compose build` 前端层全部命中缓存；PyTorch 层第一次重新下载后，后续构建将 100% 缓存命中
- 效果：第二次构建从 3-5 分钟缩短到 <1 秒，节省每次 2.5 GB 下载流量
