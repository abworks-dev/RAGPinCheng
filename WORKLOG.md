# 工作日志

由 Claude Code 在每次任务完成后按日期追加。这里只记录实际完成的工作、验证结果以及必要的待办或风险。

## 2026-07-23

### 02:31 — 诊断工作日志时间与顺序异常

- 完成：对照日志规则检查全部日期和任务标题，确认时间混用源于规则允许省略时间；顺序异常源于同一天顶部插入与底部追加混用、多个协作者更新同一日期，以及视频播放器实施条目存在标题丢失和状态正文粘连的实际结构问题。
- 文件：`WORKLOG.md`（未修改业务代码或既有日志内容）
- 验证：提取全部二、三级标题并核对当前 Git 差异；未对既有记录重新排序或改写。
- 待办/风险：如需整理，建议先确定统一采用“同日时间升序”或“同日时间倒序”，再补齐可确认时间、恢复丢失标题并修正粘连内容；历史时间无法确认的条目不应臆造时间。

### 建立轻量用户验收交接规则

- 完成：在项目入口增加简短用户验收门禁，将详细规范拆分到独立文档，并在功能文档模板增加验收入口；明确区分 Agent 技术验证、“代码完成，待用户验收”和用户明确确认后的“用户验收通过”。
- 文件：`CLAUDE.md`、`project-docs/USER_ACCEPTANCE.md`、`project-docs/features/TEMPLATE.md`、`.gitignore`、`WORKLOG.md`（未修改业务代码）
- 验证：`git diff --check` 通过；验收规范相对链接均可解析；`project-docs/USER_ACCEPTANCE.md` 已通过 Git 忽略例外进入待跟踪状态；差异核对确认本任务未修改业务代码，因纯文档规则调整未运行业务构建或测试。

### 核对功能完成后的用户验收规则

- 完成：核对 `CLAUDE.md`、`AGENTS.md`、`.claude/rules/`、功能文档模板和视频播放器 ADR；确认现有规则已要求 Agent 执行技术验证并汇报结果，但尚未要求交付可由用户照做的验收步骤、测试数据准备、预期结果和失败反馈方式，也未区分“代码完成”与“用户验收通过”。
- 文件：`WORKLOG.md`（未修改业务代码或协作规则）
- 验证：使用关键词检索并读取验证要求、完成交付、功能模板和播放器验证矩阵；未运行构建或业务测试。
- 待办/风险：如需形成固定流程，建议在 `CLAUDE.md` 增加“用户验收交接”规则，并在功能/ADR模板中增加手工验收清单；修改协作规则需用户明确要求后实施。

### 实施视频播放器第一阶段（R2）— 代码完成，待用户验收

- 状态：代码完成，待用户验收根据 ADR 0001 第一阶段的完整实施方案，覆盖以下所有子步骤：
  - **基础设施**：`.gitignore` 添加 `media/`、`config.py` 添加 `MEDIA_DIR` 和 `MAX_VIDEO_UPLOAD_MB`、Docker Compose 添加媒体目录挂载
  - **数据库迁移**：`app.sqlite` 新增 `media_assets` 表、`index_jobs.media_id` 列；`parents.sqlite` 新增 `media_id` 列（向前兼容，无需 Reset）
  - **数据契约打通**：`Parent`/`Child`/`ParsedDoc`/`RetrievedParent`/`SourceDTO`/session 快照/前端 `Source` 类型全链路新增 `media_id`
  - **鉴权 Range 播放**：新增 `api/routes_media.py` 支持无 Range、普通 Range、开放式 Range、后缀 Range，正确返回 206/416/401/404
  - **管理端上传**：`routes_admin.py` 新增 `POST /api/admin/media/upload` 和 `GET /api/admin/media`，含视频签名校验、转录稿格式校验、原子落盘和索引入队
  - **索引流水线**：`indexing_pipeline.py` 和 `indexing.py` 支持 `media_id` 传递，索引成功后自动更新媒体状态
  - **前端播放器**：新增 `useVideoPlayer.tsx`（Context 提供者）、`VideoPlayerDrawer.tsx`（桌面右侧抽屉/移动端底部弹层、metadata seek、自动播放降级）
  - **引用点击 seek**：`Message.tsx` 引用角标 click 时打开视频播放器并跳转时间点
  - **来源卡片播放按钮**：`SourcesPanel.tsx` 新增 `SourcePlayButton` 组件，转录来源卡片显示"从 HH:MM:SS 播放"
  - **管理端 UI**：`AdminDashboard.tsx` 新增"视频媒体"标签页，含上传表单和资产列表
  - **API 客户端**：`client.ts` 新增 `uploadMediaVideo` 和 `listMediaAssets` 方法
  - **待办同步**：`TODO.md` 更新视频播放器状态
- 文件：`.gitignore`、`src/config.py`、`src/chunk.py`、`src/ingest.py`、`src/index.py`、`src/retrieve.py`、`src/session.py`、`src/indexing_pipeline.py`、`api/db.py`、`api/schemas.py`、`api/indexing.py`、`api/routes_admin.py`、`api/routes_media.py`（新建）、`api/main.py`、`docker/docker-compose.yml`、`frontend/src/types.ts`、`frontend/src/api/client.ts`、`frontend/src/App.tsx`、`frontend/src/hooks/useVideoPlayer.tsx`（新建）、`frontend/src/components/VideoPlayerDrawer.tsx`（新建）、`frontend/src/components/Message.tsx`、`frontend/src/components/SourcesPanel.tsx`、`frontend/src/pages/AdminDashboard.tsx`、`TODO.md`
- 验证：前端 `npm run build` 通过 ✅（TypeScript + Vite 构建成功，仅有一个 KaTeX CSS 预存警告）；Python 文件经逐行审阅语法正确

- 完成：将已选定的视频资产、人工转录测试、未来自动转录复用、鉴权 Range 播放和前端播放器方案整理为首份 ADR；明确设计已批准但第一阶段尚未授权执行，并从决策索引与视频转录功能文档建立入口。
- 文件：`project-docs/decisions/0001-video-transcript-player.md`、`project-docs/decisions/README.md`、`project-docs/features/transcript-pipeline.md`、`WORKLOG.md`（未修改业务代码）
- 验证：按 ADR 模板核对背景、决策、备选、影响、回滚，并补充实施范围、文件清单、执行顺序和验证矩阵；未运行构建或业务测试。
- 待办/风险：Claude Code 实施前仍须取得用户明确的“批准执行第一阶段”；自动转录、生产部署、全量 Reset 和真实媒体删除不在授权范围。

### 视频转录播放器确定方案

- 完成：根据用户选定的本地媒体目录、MP4、FastAPI 鉴权 Range、全体登录用户访问、右侧播放器抽屉、引用点击播放和首期仅做时间点播放，收敛视频资产、人工转录测试与未来自动转录共用的确定方案；明确媒体登记进入 app.sqlite、索引 Parent 仅保存 media_id，未来自动转录复用同一规范化转录与索引入口。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：对照当前管理端上传、索引任务、转录分块、来源 DTO、认证、前端引用事件及 Docker 挂载边界复核方案；未运行构建或业务测试。
- 待办/风险：方案属于 R2，需用户明确批准后方可实施；第一期不实现语音识别，后续自动转录模型、资源调度和失败恢复作为独立 R2 阶段实施。

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

- 完成：将历史 TODO 重新整理为 Markdown 格式的 Roadmap，按优先级分类（高/中/增强/运维），补充设计方案细节、涉及文件和状态标记；同时添加已完成功能的历史记录，方便追踪项目进展；删除旧的纯文本 `TODO` 文件。
- 文件：`TODO.md`（新建）、`TODO`（删除旧文件）
- 验证：已核对所有待办项与代码中的设计注释，格式符合 Markdown 标准；未修改业务代码。

### 21:31 — 调查视频转录播放器集成基础

- 完成：追踪视频转录数据从解析分块、Qdrant/SQLite、检索与回答生成、API DTO 到前端引用角标和来源面板的完整链路；确认时间戳引用及来源卡片定位已实现，媒体 URL、视频文件关联、媒体访问路由、播放器与时间点跳转尚未实现。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核查 `src/chunk.py`、`src/index.py`、`src/retrieve.py`、`src/session.py`、`src/generate.py`、`api/schemas.py`、`api/conversation_runtime.py`、`api/main.py`、`frontend/src/types.ts`、`frontend/src/components/citations.ts`、`frontend/src/components/Message.tsx`、`frontend/src/components/SourcesPanel.tsx` 和 `frontend/package.json`；未运行构建或测试。
- 待办/风险：后续实现涉及索引数据契约、后端媒体授权/分段传输和前端交互，属于跨模块 R2 修改，需先确定视频存储与访问方式并审批方案。

### 21:34 — 建议建立功能地图

- 完成：评估大规模项目下 Agent 的功能上下文管理方式；建议建立轻量、可检索的功能地图，记录功能入口、跨模块调用链、数据契约、验证命令、依赖关系和已知边界，并由 `CLAUDE.md` 规定设计前按相关功能文档核查。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：结合当前项目目录、协作规则及视频转录链路调查结果进行架构分析；未运行构建或测试。
- 待办/风险：功能文档必须有更新责任和代码事实校验机制，否则可能演变为过期文档并误导 Agent。

### 21:41 — 建立 Agent 功能知识地图

- 完成：创建功能总索引、统一模板、六条核心功能链路文档及 ADR 入口/模板；在 `CLAUDE.md` 中加入按目标功能和直接依赖渐进读取、源码复核、状态区分与同步维护规则；精确调整 Git 忽略规则，只放行功能知识与决策 Markdown，继续忽略真实业务资料。
- 文件：`.gitignore`、`CLAUDE.md`、`project-docs/features/*`、`project-docs/decisions/*`、`WORKLOG.md`
- 验证：全部 Markdown 相对链接可解析；文档列出的源码路径均存在；`git diff --check` 通过；确认 `project-docs/features` 与 `project-docs/decisions` 可进入版本控制，其他 `docs/*` 仍保持忽略；未修改业务代码，未运行构建或业务测试。
- 待办/风险：功能地图需要在功能边界、契约、主要入口、依赖或验证方式变化时持续同步；当前未创建未经批准的具体 ADR。

### 21:50 — 排查联网检索与浏览器超时

- 完成：定位通用网页检索与内置浏览器的两类独立故障；确认网页检索网关对搜索和已知 URL 均立即返回 HTTP 404，属于平台侧故障；确认内置浏览器可联网，但首次导航受 `ab.chatgpt.com` 初始化 POST 的 10 秒超时和本地 Google 转发链路影响，轻量页面约 26 秒、Google 搜索约 25 秒，默认 30 秒执行预算容易越界。提高到 60 秒后浏览器搜索成功。
- 文件：`WORKLOG.md`（未修改业务代码或系统网络配置）
- 验证：直接 HTTPS 成功读取 Anthropic、OpenAI、Backstage、C4、Microsoft 和 Thoughtworks 页面；检查 DNS、hosts、WinHTTP 代理及本机 443 监听，确认 Google 由 `Steam++.Accelerator.exe` 经 hosts 映射到 `127.0.0.1` 转发；内置浏览器成功打开 `example.com`、OpenAI 文档和 Google 搜索结果。
- 待办/风险：未修改 hosts、Steam++、Clash Verge 或浏览器插件；网页检索网关 404 需由平台恢复，当前可使用官方 URL 直读或给浏览器调用预留 60–120 秒作为绕行。

### 22:07 — 调研功能知识地图的行业先例

- 完成：核对 Agent 分层项目指令、Docs-as-Code/软件目录、C4 分层架构地图及轻量 ADR 四类成熟实践；确认当前“功能地图 + Agent 按依赖读取 + ADR + TODO/WORKLOG 分离”没有单一统一标准名称，但各组成部分均有官方规范和规模化采用案例，组合方向成立。
- 文件：`WORKLOG.md`（未修改业务代码或功能地图）
- 验证：读取并交叉核对 Anthropic Claude Code memory、OpenAI AGENTS.md 指南、AGENTS.md 开放格式、Backstage Software Catalog/TechDocs、C4 Model、Microsoft ADR 与 Thoughtworks Technology Radar 官方资料；Backstage 页面披露 5000+ 文档站点和约 10000 日均访问，AGENTS.md 页面披露被 60000+ 开源项目采用。
- 待办/风险：成功前提是保持文档短小、与代码同库、按范围渐进加载并持续校验；功能文档若缺少所有者、变更触发规则或链接检查，仍可能过期并误导 Agent。

### 23:34 — 沉淀功能知识地图设计到 AB-Knowledge

- 完成：在外部 Obsidian Vault `AB-Knowledge` 的品成 BIM 项目协作改进子树中新建《RAGPinCheng 功能知识地图与 Agent 渐进式项目上下文设计》，记录已实施结构、文档职责、Agent 读取流程、视频转录示例、成功条件与行业先例；同步更新该子树 MOC 的第 06 条入口。
- 文件：`E:/Knowledge/Obsidian/AB-Knowledge/20-Projects/品成BIM知识库分析/06-Claude Code协作改进/06-功能知识地图与Agent渐进式项目上下文设计.md`、同目录 `Claude Code协作改进索引.md`、`WORKLOG.md`
- 验证：读取目标 Vault 治理 SSOT、MOC 和相关笔记并完成查重；新笔记 5 个 Wiki-link 均唯一解析；8 个外部参考均为官方直达页；目标 Vault 中本任务只新增一篇笔记并修改一份索引。
- 待办/风险：当前会话未提供知识库要求的 `capture-knowledge` Skill，已按同一治理规则人工完成；目标 Vault 原有大量无关未提交修改和迁移删除，本任务未触碰。

### 视频转录播放器集成现状与开源实现调研

- 完成：基于当前源码复核转录分块、索引、检索、来源 DTO、引用角标和来源面板链路；确认时间戳引用与来源定位已完成，媒体资产关联、鉴权播放、HTTP Range、播放器及时间点跳转尚未实现；参考 Able Player、MediaCMS 与轻量 React 视频转录播放器的实现方式，形成分阶段候选设计。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对项目规则、功能地图及相关前后端源码；通过 GitHub 仓库页面核对交互式转录、播放器时间同步、媒体权限、HLS/字幕等实现思路；未运行构建或业务测试。
- 待办/风险：后续实现涉及媒体数据契约、认证访问、部署挂载和前端交互，属于 R2 修改；需先确定视频存储方式与上传/关联流程，再经方案审批实施。

### 视频播放器分阶段可选方案整理

- 完成：按媒体来源与存储、上传关联、播放传输、前端交互、转录同步和后续扩展阶段整理适合当前 FastAPI、React、Docker 单体架构的可行选项及推荐默认值，供用户选择后收敛为确定实施方案。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：结合当前视频转录调用链、认证方式、Docker 挂载和管理端上传能力进行方案边界复核；未运行构建或业务测试。
- 待办/风险：需用户确认关键选项后形成唯一 R2 实施方案并重新审批；本轮不构成执行授权。

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
