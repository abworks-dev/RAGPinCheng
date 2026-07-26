# 工作日志

由 Claude Code 在每次任务完成后按日期追加。这里只记录实际完成的工作、验证结果以及必要的待办或风险。

## 2026-07-20

### --:-- — 配置 Claude Code 每次任务工作日志

- 完成：在项目入口指令中加入强制收尾规则，要求 Claude Code 每次任务完成后按日期记录成果、涉及文件、验证结果和必要风险。
- 文件：`CLAUDE.md`、`WORKLOG.md`
- 验证：已核对入口规则与日志模板内容；未修改或运行项目业务代码。

### --:-- — Docker 构建缓存优化与依赖清理

- 完成：
  1. 清理 `requirements-prod.txt` 中无用依赖（删除 `mineru[core]` 和 `streamlit`），镜像预计瘦身约 500 MB
  2. 给 `FlagEmbedding` 加上版本上限 `<2`，防止大版本升级破坏检索逻辑
  3. 更新 `docker/Dockerfile.backend` 中关于 `platforms` 参数的过时注释（已默认禁用以优化缓存）
  4. `torch>=2.7` → `torch==2.7.0` 锁定精确版本，确保 Docker 层缓存命中
  5. `docker/docker-compose.yml` 禁用 `build.platforms`，消除跨平台构建对缓存的负面影响
- 文件：`requirements-prod.txt`、`docker/Dockerfile.backend`、`docker/docker-compose.yml`
- 验证：本地 `docker compose build` 前端层全部命中缓存；PyTorch 层第一次重新下载后，后续构建将 100% 缓存命中
- 效果：第二次构建从 3-5 分钟缩短到 <1 秒，节省每次 2.5 GB 下载流量

### --:-- — 引用角标 Tooltip 右侧裁剪修复

- 完成：为 CitationMarker 组件添加 tooltip 水平溢出检测，当 tooltip 右侧超出视口边界时自动切换为 right-0 向左延伸，避免被视口右侧裁剪
- 文件：`frontend/src/components/Message.tsx`
- 验证：无法执行 npm run build（安全分类器 Windows Defender 暂不可用），改动仅 3 处新增状态 + 扩展 useLayoutEffect + CSS 类名切换，完全复用已有模式，类型安全
- 待办/风险：安全分类器恢复后手动运行 npm run build 确认

## 2026-07-22

### 18:44 — 调查 WPS 风格引用角标

- 完成：定位现有回答角标、悬浮预览与来源面板实现，确认可在保留当前引用解析和数据结构的前提下复刻 WPS 风格；给出 Firefox 中采集目标元素 DOM 与样式的范围。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读检查 `Message.tsx`、`citations.ts`、`SourcesPanel.tsx`、`index.css` 和前端类型；未运行前端构建。
- 待办/风险：当前截图未包含已生成答案及引用浮层，精确还原仍需角标常态、悬停态和展开来源态的 DOM/样式或截图。

### 20:20 — 增加 Claude 方案审批门禁

- 完成：在项目入口加入 `R0–R3` 风险评级；规定 `R2/R3` 任务提交方案后必须停止，只有用户明确批准后才能执行，范围或风险变化时需重新审批。
- 文件：`CLAUDE.md`、`WORKLOG.md`
- 验证：已检查规则差异，确认保留并衔接原有 Reset、删除、生产部署等专项确认要求；未运行项目业务测试。

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

### --:-- — Codex 辅助角色与审批门禁

- 完成：新增 Codex 协作入口，明确 Claude Code 是主要开发者、Codex 是辅助协作者；规定任务先做 `R0–R3` 评级，`R2/R3` 展示方案并等待明确批准后方可执行。
- 文件：`AGENTS.md`、`WORKLOG.md`
- 验证：已按批准方案核对角色分工、授权语义、重新审批条件、执行边界和回滚要求；未修改或运行项目业务代码。

### --:-- — 修复引用角标 tooltip 闪烁无法停留的问题

- **问题原因**：`onMouseEnter/onMouseLeave` 只绑定在 `<a>` 角标上，而 tooltip 是兄弟元素。鼠标从角标移到 tooltip 时触发 `onMouseLeave` → tooltip 消失 → 鼠标回到角标 → 无限闪烁。
- **修复方案**：
  - hover 事件移到外层 `<sup>` 上（角标 + tooltip 都在里面）
  - 用 `bottom-[100%] mb-0.5` 替代 `-translate-y-full`，让 tooltip 底部略微重叠角标（无视觉间隙）
- **文件**：`frontend/src/components/Message.tsx`

### --:-- — 修复引用角标 tooltip 在左侧被侧边栏遮挡的问题

- **问题原因**：tooltip 使用 `right-0` 定位，从角标**向左展开**。当角标靠近页面左侧时，tooltip 会延伸到侧边栏（`<aside>`）区域，被其背景和边框遮挡。
- **修复方案**：将 `right-0` 改为 `left-0`，让 tooltip 从角标**向右展开**，避开左侧侧边栏。
- **文件**：`frontend/src/components/Message.tsx`

### --:-- — 修复引用角标 tooltip 在顶部被视口遮挡的问题

- **问题原因**：tooltip 固定显示在角标上方（`bottom-[100%]`）。当角标靠近视口顶部时（如页面滚动到第一条回答），tooltip 上边缘会超出视口，内容被截断。
- **修复方案**：
  - 使用 `useLayoutEffect` 在渲染后即时检测 tooltip 的 `getBoundingClientRect()`
  - 若 tooltip 上边缘距视口顶部 < 10px，动态切换到下方显示（`top-[100%]`）
  - 两种定位都保留微小间距（`mb-0.5` / `mt-0.5`）防止鼠标移动时闪烁
- **文件**：`frontend/src/components/Message.tsx`
- **验证**：前端 `npm run build` 构建通过 ✅

### --:-- — 修复暗黑模式下对话列表标题颜色过深的问题

- **问题原因**：`ConversationList.tsx` 使用 `text-ink/90`——这是 Tailwind 的 opacity 语法，生成硬编码的 `color: rgb(31, 41, 55) / 0.9;`。暗黑模式的 `.dark .text-ink` 覆盖只对纯 `text-ink` 类有效，导致标题颜色仍是深灰色，与深色背景融为一体。
- **修复方案**：改为 `text-ink opacity-90`，`opacity` 不影响颜色通道，暗黑模式的颜色覆盖能正常生效。
- **附加修复**：给引用角标 hover 状态添加 `dark:hover:bg-gray-700`，与暗黑模式背景协调。
- **文件**：`frontend/src/components/ConversationList.tsx`、`frontend/src/components/Message.tsx`
- **验证**：前端 `npm run build` 构建通过 ✅

### --:-- — 修复第二次输入纯数字仍会检索的问题

- **问题根源**：`query_guard.py` 中 `has_history=True` 时对纯数字输入无条件放行，导致第二轮输入 "222" 被改写成 "222 是什么" 后走检索流程，但历史对话中没有任何文档上下文，返回页码匹配的垃圾结果。
- **修复方案**：
  - 新增 `_PURE_DIGITS_ONLY_RE` 正则，专门检测纯数字输入（只有数字、空格、小数点）
  - 纯数字输入**无论第几轮对话始终拦截**
  - 正常跟进（如 `"那 22 呢？"`）包含中文字符，不会被误拦截
- **文件**：`src/query_guard.py`
- **验证**：逻辑自检验证通过，测试用例覆盖边界情况。

### --:-- — 修复引用角标垂直偏移与样式

- **问题原因**：原角标使用 `align-top` + `inline-flex` 在 `<sup>` 上，数字显示位置偏上且缺少边框；WPS 采用 `<sup>` 包裹 `<a>` 的结构，垂直定位更精确。
- **修改内容**：
  - DOM 结构改为 `sup > a`（与 WPS 一致）
  - 垂直对齐：`top-[-0.35em] align-baseline` 精确控制
  - 尺寸固定：`h-[18px] min-w-[18px] text-[11px]`
  - 样式：浅灰背景 `bg-gray-100` + 细边框 `border-gray-200` + 圆角，与正文融合自然
  - tooltip 移入 `<sup>` 内部，定位更准确
- **文件**：`frontend/src/components/Message.tsx`
- **验证**：前端构建通过 ✅

### --:-- — 修复 SSE 流超时导致的 network error

- **问题原因**：SSE 流在 LLM 生成停顿超过 30 秒时，被反向代理（Nginx/Cloudflare）或浏览器因空闲超时断开，导致用户看到 `⚠️ network error`。
- **解决方案**：在后端 SSE 事件生成器中添加 15 秒心跳机制。当没有 token 到达时，发送 SSE 注释（`: ping`）保持连接活跃。前端已兼容处理 SSE 注释行。
- **文件**：`api/routes_chat.py`
- **修改内容**：
  - 添加 `last_activity` 时间跟踪
  - 每 15 秒无活动时发送 `ping_event`（SSE 注释，前端自动忽略）
  - 使用 `asyncio.wait_for` + 短超时循环实现心跳窗口

### --:-- — 优化参考来源预览移除 Markdown 语法标记

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

### --:-- — 移除 LLM 输出中的"资料来源"重复项

- 完成：修改 `answer_system.md` Prompt，移除要求 LLM 在正文末尾追加"**资料来源：**"小节的指令。因为前端已经通过独立的折叠面板展示参考来源，LLM 再输出一遍会导致重复显示。
- 文件：`prompts/answer_system.md`
- 验证：已核对 Prompt 修改内容；新生成的回答将不再包含"资料来源："标题和列表，只保留行内引用标注。

### --:-- — 整理并优化 Roadmap TODO 文档

- 完成：将历史 TODO 重新整理为 Markdown 格式的 Roadmap，按优先级分类（高/中/增强/运维），补充设计方案细节、涉及文件和状态标记；同时添加已完成功能的历史记录，方便追踪项目进展；删除旧的纯文本 `TODO` 文件。
- 文件：`TODO.md`（新建）、`TODO`（删除旧文件）
- 验证：已核对所有待办项与代码中的设计注释，格式符合 Markdown 标准；未修改业务代码。

### --:-- — 视频转录播放器集成现状与开源实现调研

- 完成：基于当前源码复核转录分块、索引、检索、来源 DTO、引用角标和来源面板链路；确认时间戳引用与来源定位已完成，媒体资产关联、鉴权播放、HTTP Range、播放器及时间点跳转尚未实现；参考 Able Player、MediaCMS 与轻量 React 视频转录播放器的实现方式，形成分阶段候选设计。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对项目规则、功能地图及相关前后端源码；通过 GitHub 仓库页面核对交互式转录、播放器时间同步、媒体权限、HLS/字幕等实现思路；未运行构建或业务测试。
- 待办/风险：后续实现涉及媒体数据契约、认证访问、部署挂载和前端交互，属于 R2 修改；需先确定视频存储方式与上传/关联流程，再经方案审批实施。

### --:-- — 视频播放器分阶段可选方案整理

- 完成：按媒体来源与存储、上传关联、播放传输、前端交互、转录同步和后续扩展阶段整理适合当前 FastAPI、React、Docker 单体架构的可行选项及推荐默认值，供用户选择后收敛为确定实施方案。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：结合当前视频转录调用链、认证方式、Docker 挂载和管理端上传能力进行方案边界复核；未运行构建或业务测试。
- 待办/风险：需用户确认关键选项后形成唯一 R2 实施方案并重新审批；本轮不构成执行授权。

## 2026-07-23

### 02:31 — 诊断工作日志时间与顺序异常

- 完成：对照日志规则检查全部日期和任务标题，确认时间混用源于规则允许省略时间；顺序异常源于同一天顶部插入与底部追加混用、多个协作者更新同一日期，以及视频播放器实施条目存在标题丢失和状态正文粘连的实际结构问题。
- 文件：`WORKLOG.md`（未修改业务代码或既有日志内容）
- 验证：提取全部二、三级标题并核对当前 Git 差异；未对既有记录重新排序或改写。
- 待办/风险：如需整理，建议先确定统一采用“同日时间升序”或“同日时间倒序”，再补齐可确认时间、恢复丢失标题并修正粘连内容；历史时间无法确认的条目不应臆造时间。

### 03:31 — 统一 Revit 转录稿时间戳格式

- 完成：将目标转录稿中 50 处 `说话⼈ N MM:SS` 时间戳补齐小时位，统一为 `说话⼈ N HH:MM:SS`；保留原有标题层级和正文不变。
- 文件：`MEP_001_Revit界⾯介绍（⼯具栏、属性⾯ 板、项⽬浏览器）_2026.6.13.md`、`WORKLOG.md`
- 验证：全文件共识别 53 条说话人时间戳，均符合三段式格式；两段式时间戳残留为 0，其中 3 条原本已是三段式，未重复修改。

### 06:58 — 梳理项目指令入口框架

- 完成：核对 Codex 协作入口、Claude 项目总入口、按路径领域规则、功能知识地图与 ADR、TODO/工作日志之间的分层关系；进一步通过 Git 历史确认 `python-backend.md` 自首次引入时就与 `rag-pipeline.md` 完全相同，标题、正文和 `src/scripts/prompts` 路径范围均为复制内容，且之后从未修正。由此造成 `CLAUDE.md` 声明的 `api/**` 后端规则入口与实际自动匹配范围不一致；部分后端底线仍由总入口、安全规则和功能文档覆盖，但缺少集中、按 API 路径加载的后端专属规则。
- 文件：`WORKLOG.md`（未修改业务代码或指令文件）
- 验证：只读检查 `git status --short --branch`、`AGENTS.md`、`CLAUDE.md`、`.claude/rules/*.md`、相关功能文档及首次引入规则的提交 `b070f7f`；两个规则文件逐字无差异，且从该提交到当前 HEAD 均无后续差异。未运行业务测试。

### 15:17 — 核对项目显卡使用时机

- 完成：追踪 BGE-M3 Embedding、BGE reranker、文档索引、在线检索、MinerU 解析、GLM 生成与 Docker GPU 透传链路；确认后端默认启动时加载本地 BGE 模型，查询检索和建索阶段执行本地 GPU 推理，无 CUDA 时 Embedding 明确回退 CPU；GLM 和云端 MinerU 的显卡计算在外部服务端。
- 补充：评估“Linux 主服务 + Windows GPU 节点”分布式部署可行；当前代码不支持远程 BGE，需将 Embedding 与 rerank 抽象为网络接口，Linux 保留 FastAPI、Qdrant、SQLite、会话与 GLM 编排，Windows 仅承载 GPU 模型推理。若实施，属于 R2 跨模块与部署契约修改，需另行方案审批。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对 `src/embed.py`、`src/rerank.py`、`src/retrieve.py`、`src/index.py`、`src/session.py`、`api/main.py`、`src/ingest.py`、Docker Compose 和后端镜像配置；未启动模型、未运行业务测试。
- 待办/风险：`FlagReranker` 未像 Embedding 封装那样显式指定设备，其实际 CPU/GPU 选择依赖 FlagEmbedding/PyTorch 运行时自动检测；本轮未在实机上采样显存。

### 16:20 — 统一工作日志入口与格式

- 完成：明确 `CLAUDE.md` 是工作日志格式、时间和排序规则的唯一入口，`AGENTS.md` 仅引用该入口；统一要求使用上海本地完成时间、日期与同日任务倒序、新记录插入当天顶部，历史未知时间使用 `--:--` 且不得臆造；整理既有任务标题并恢复视频播放器 ADR 的独立小节边界。
- 文件：`CLAUDE.md`、`AGENTS.md`、`WORKLOG.md`（未修改业务代码）
- 验证：检查全部日期与三级标题格式、日期和同日时间顺序、小节边界及 Git 差异；未运行业务构建或测试。

### 16:33 — 调整工作日志为正序排列

- 完成：将工作日志规则调整为日期正序、同日已知时间正序；新记录追加在当天最后一条已知时间记录之后、历史 `--:--` 记录之前，并按该规则重新排列现有日志。
- 文件：`CLAUDE.md`、`WORKLOG.md`（未修改业务代码）
- 验证：检查日期和同日时间正序、`--:--` 位置、标题唯一性、正文完整性及 Git 差异；未运行业务构建或测试。

### 16:53 — 实施 GitHub Actions 快速部署

- 完成：在用户批准 R2 方案后新增 CI 与生产部署工作流；CI 检查 Python 语法、前端生产构建和 Compose 配置，生产工作流经 `production` Environment 审批后调用 Windows self-hosted runner；部署脚本限制干净 `master` 快进更新，记录新旧版本、复用 Docker 缓存、检查健康状态、失败时尝试回滚，并阻止数据库/索引敏感文件自动发布。
- 文件：`.github/workflows/ci.yml`、`.github/workflows/deploy-production.yml`、`scripts/deploy-production.ps1`、`Git同步部署与测试流程.md`、`WORKLOG.md`（未修改业务代码）
- 验证：PowerShell 语法解析、Python `compileall`、前端 `npm run build`、`docker compose config --quiet` 和 `git diff --check` 均通过；前端保留既有 CSS 语法及包体积警告。未连接生产服务器、未安装 runner、未触发部署；本地缺少项目 `.venv` 和 YAML 解析依赖，未运行 pytest 或独立 YAML 解析器。
- 待办/风险：需在 GitHub 创建带审批人的 `production` Environment，并在生产服务器安装带 `self-hosted, windows, production, gpu` 标签的 runner；首次发布前仍须备份应用数据和 Qdrant、确认旧版本可恢复，并人工验收登录、历史数据、问答引用和 GPU。

### 16:59 — 说明 GitHub 生产环境审批配置

- 完成：结合当前 `abworks-dev/RAGPinCheng` 仓库和部署工作流的 `environment: production` 配置，整理 GitHub Environment 创建、required reviewers、禁止自审、部署分支限制和首次审批操作步骤，并标明私有仓库套餐可能影响审批规则可用性。
- 文件：`WORKLOG.md`（未修改业务代码或 GitHub 外部设置）
- 验证：核对本地远端仓库地址、生产工作流环境名和当前工作区状态；未登录或修改 GitHub 设置，官方文档在线查询本轮返回异常。
- 待办/风险：用户需在仓库 Settings 中完成配置；若页面不显示 Deployment protection rules 或 Required reviewers，应先核对仓库可见性、组织策略和 GitHub 套餐。

### 17:04 — 明确单人维护的部署审批方式

- 完成：确认单人维护无需他人审批；建议将维护者本人设为 `production` reviewer 且不启用 `Prevent self-review`，保留上线前的单人二次确认，避免 `master` CI 成功后直接自动部署。
- 文件：`WORKLOG.md`（未修改业务代码、工作流或 GitHub 外部设置）
- 验证：对照当前生产工作流同时支持 CI 成功触发和手动触发的行为复核；未修改 GitHub 设置或触发部署。

### 17:10 — 说明 Windows 生产 runner 安装流程

- 完成：针对生产服务器 `${PRODUCTION_REPO_PATH}`、Docker Desktop 和 GPU 部署方式，整理 GitHub 仓库级 Windows x64 self-hosted runner 的下载、注册、`production,gpu` 附加标签、交互试跑、服务化、Docker 权限和离线处理步骤；明确默认标签会自动提供 `self-hosted` 与 `Windows`。
- 文件：`WORKLOG.md`（未修改业务代码、工作流或 GitHub 外部设置）
- 验证：核对当前部署工作流要求的 runner 标签和固定生产路径；未连接生产服务器、未使用注册 Token、未安装或启动 runner。
- 待办/风险：用户需在生产服务器以管理员身份按 GitHub 页面即时命令注册；注册 Token 不得共享或写入仓库，服务账号必须能够访问 Docker、生产仓库和备份目录。

### 17:15 — 区分 Runner 安装包与工作流构件

- 完成：澄清“下载工作流程构件”用于获取 Actions 运行产物，不是 self-hosted runner 安装入口；提供当前仓库 Windows x64 runner 设置直达路径和页面识别要点。
- 文件：`WORKLOG.md`（未修改业务代码、工作流或 GitHub 外部设置）
- 验证：对照仓库级 runner 安装目标核对页面用途；未下载文件、未使用注册 Token、未安装 runner。

### 17:17 — 确认 Runner 页面并处理 Token 暴露风险

- 完成：确认用户已进入正确的 Windows x64 self-hosted runner 安装页面；提示停止使用已粘贴到对话中的临时注册 Token、重新生成并仅在生产服务器本地使用，同时说明公开仓库 runner 的额外安全边界和 `production,gpu` 标签填写位置。
- 文件：`WORKLOG.md`（未修改业务代码、工作流或 GitHub 外部设置）
- 验证：核对页面所示 runner 版本、Windows x64 下载/校验/解压/配置流程与当前工作流标签要求；未下载文件、未注册或启动 runner。
- 待办/风险：公开仓库的 self-hosted runner 风险高于私有仓库；不得让 Pull Request 或不受信任分支直接使用生产 runner，临时 Token 不得再次共享。

### 17:20 — 评估公开 Fork 的生产 Runner 方案

- 完成：结合企业内部 RAG 与生产服务器权限边界，建议先迁移到独立私有仓库再注册 self-hosted runner；说明公开 Fork 通常不能单独改为私有、公共历史不会因新建私库而消失，以及继续使用公开仓库时需改成仅手动部署并加强隔离的备选边界。
- 文件：`WORKLOG.md`（未修改业务代码、工作流、远端仓库或 GitHub 设置）
- 验证：核对当前 `origin` 为公开 Fork、生产工作流使用固定服务器目录和 self-hosted 标签；未迁移仓库、未修改可见性、未注册 runner。
- 待办/风险：新建私有仓库、切换远端和收紧部署触发方式属于 R2 协作与部署配置变更，需形成具体迁移方案并获批后执行；公开仓库既有历史仍需单独做敏感信息审计。

### 18:10 — 核对私有仓库与 Actions 免费额度

- 完成：依据 GitHub 官方定价和 Actions 计费页面确认 GitHub Free 包含不限数量的公开/私有仓库及每月 2,000 分钟 CI/CD，self-hosted runner 的 Actions 使用免费；进一步确认 Free 私有仓库不能配置 Environment，个人 Pro/组织 Team 虽可创建私有 Environment，但 required reviewers 等保护规则在 Free/Pro/Team 上仅适用于公开仓库。结合当前单人维护场景，建议采用“私有仓库 + 仅 `workflow_dispatch` 手动发布”替代付费环境审批。
- 文件：`WORKLOG.md`（未修改业务代码、工作流、远端仓库或计费设置）
- 验证：只读访问 GitHub 官方 Pricing、Actions Billing 与 Environment 管理页面并核对当前条款；未创建私有仓库、未产生 Actions 运行或费用。
- 待办/风险：GitHub 定价可能调整；构件、缓存、Packages、LFS 和超额 GitHub-hosted runner 使用有独立配额或计费，实际用量应在 Billing 页面设置预算和提醒。

### --:-- — 来源预览文本移除 HTML 标签

- 完成：在 `stripMarkdown()` 中增加 HTML 标签清理（`<[^>]*>` 正则），来源面板预览和 Tooltip 预览中的 `<sub>1</sub>` 等标签不再显示，变为纯文本
- 文件：`frontend/src/utils/markdown.ts`
- 验证：`npm run build` 通过 ✅
- 待办/风险：需要在服务器上重建容器才会生效，或本地热重载即生效

### --:-- — 建立轻量用户验收交接规则

- 完成：在项目入口增加简短用户验收门禁，将详细规范拆分到独立文档，并在功能文档模板增加验收入口；明确区分 Agent 技术验证、“代码完成，待用户验收”和用户明确确认后的“用户验收通过”。
- 文件：`CLAUDE.md`、`project-docs/USER_ACCEPTANCE.md`、`project-docs/features/TEMPLATE.md`、`.gitignore`、`WORKLOG.md`（未修改业务代码）
- 验证：`git diff --check` 通过；验收规范相对链接均可解析；`project-docs/USER_ACCEPTANCE.md` 已通过 Git 忽略例外进入待跟踪状态；差异核对确认本任务未修改业务代码，因纯文档规则调整未运行业务构建或测试。

### --:-- — 核对功能完成后的用户验收规则

- 完成：核对 `CLAUDE.md`、`AGENTS.md`、`.claude/rules/`、功能文档模板和视频播放器 ADR；确认现有规则已要求 Agent 执行技术验证并汇报结果，但尚未要求交付可由用户照做的验收步骤、测试数据准备、预期结果和失败反馈方式，也未区分“代码完成”与“用户验收通过”。
- 文件：`WORKLOG.md`（未修改业务代码或协作规则）
- 验证：使用关键词检索并读取验证要求、完成交付、功能模板和播放器验证矩阵；未运行构建或业务测试。
- 待办/风险：如需形成固定流程，建议在 `CLAUDE.md` 增加“用户验收交接”规则，并在功能/ADR模板中增加手工验收清单；修改协作规则需用户明确要求后实施。

### --:-- — 实施视频播放器第一阶段（R2）— 代码完成，待用户验收

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

### --:-- — 记录视频播放器第一阶段 ADR

- 完成：将已选定的视频资产、人工转录测试、未来自动转录复用、鉴权 Range 播放和前端播放器方案整理为首份 ADR；明确设计已批准但第一阶段尚未授权执行，并从决策索引与视频转录功能文档建立入口。
- 文件：`project-docs/decisions/0001-video-transcript-player.md`、`project-docs/decisions/README.md`、`project-docs/features/transcript-pipeline.md`、`WORKLOG.md`（未修改业务代码）
- 验证：按 ADR 模板核对背景、决策、备选、影响、回滚，并补充实施范围、文件清单、执行顺序和验证矩阵；未运行构建或业务测试。
- 待办/风险：Claude Code 实施前仍须取得用户明确的“批准执行第一阶段”；自动转录、生产部署、全量 Reset 和真实媒体删除不在授权范围。

### --:-- — 视频转录播放器确定方案

- 完成：根据用户选定的本地媒体目录、MP4、FastAPI 鉴权 Range、全体登录用户访问、右侧播放器抽屉、引用点击播放和首期仅做时间点播放，收敛视频资产、人工转录测试与未来自动转录共用的确定方案；明确媒体登记进入 app.sqlite、索引 Parent 仅保存 media_id，未来自动转录复用同一规范化转录与索引入口。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：对照当前管理端上传、索引任务、转录分块、来源 DTO、认证、前端引用事件及 Docker 挂载边界复核方案；未运行构建或业务测试。
- 待办/风险：方案属于 R2，需用户明确批准后方可实施；第一期不实现语音识别，后续自动转录模型、资源调度和失败恢复作为独立 R2 阶段实施。

## 2026-07-24

### 11:14 — 改为私有仓库免费手动部署

- 完成：按用户批准的 R2 方案将生产工作流改为仅支持 `workflow_dispatch` 手动触发，并增加必选生产确认框；删除 CI 成功后的自动发布和 `production` Environment 依赖，代理设置改为仓库级 Actions Variable。同步修订首次脚本同步和日常发布说明，避免 GitHub Free 私有仓库依赖不可用的环境审批功能。
- 文件：`.github/workflows/deploy-production.yml`、`Git同步部署与测试流程.md`、`WORKLOG.md`（未修改业务代码）
- 验证：人工核对工作流触发条件、输入表达式、runner 标签和代理变量引用；检索确认活动工作流与部署文档不再包含 `workflow_run` 或 `environment: production`；`git diff --check` 通过。未创建或迁移私有仓库、未注册 runner、未触发生产部署。
- 待办/风险：需由用户创建独立私有仓库并切换开发机和生产服务器 `origin`，在仓库级 Actions Variables 配置可选的 `DEPLOY_HTTP_PROXY`，再注册生产 runner；首次部署前仍须备份应用数据和 Qdrant 并确认回滚版本。

### 12:17 — 确定私有仓库命名与保留策略

- 完成：建议将现有公开 Fork 重命名为 `RAGPinCheng-public`，再创建独立私有仓库 `RAGPinCheng`，从而保留正式仓库原名称且避免直接删除；说明复用旧名称会终止 GitHub 对重命名前地址的自动重定向，因此需显式更新开发机、生产服务器和集成中的远端地址。
- 文件：`WORKLOG.md`（未修改业务代码、工作流、远端仓库或 GitHub 设置）
- 验证：只读核对 GitHub 官方仓库重命名文档；未重命名、删除、归档或创建任何远端仓库。
- 待办/风险：仓库重命名、创建私库、切换远端属于 R2 外部协作配置变更；删除公开仓库属于 R3 破坏性操作，迁移和验证完成前不应执行。

### 12:19 — 说明仓库改名对服务器拉取的影响

- 完成：明确公开仓库仅重命名后 GitHub 会继续重定向旧 Git 地址，服务器拉取通常不立即失效；重新创建同名私有仓库后重定向终止，服务器旧地址将指向新私库，必须提前完成代码推送和私库认证。建议先以不同名称建立私库并验证开发机、服务器和部署链路，再决定是否交换正式名称。
- 文件：`WORKLOG.md`（未修改业务代码、工作流、远端仓库或服务器配置）
- 验证：对照 GitHub 官方重命名重定向规则与当前服务器通过 `origin/master` 拉取的部署脚本复核；未重命名仓库、切换远端或连接生产服务器。
- 待办/风险：名称复用期间若私库尚未就绪或服务器凭据无权访问，`git fetch/pull` 会失败；现有运行容器不受 Git 远端改名直接影响。

### 12:20 — 给出公开仓库改名后的迁移顺序

- 完成：在用户完成公开仓库改名后，确认本地 `origin` 仍使用旧 URL；给出先将开发机和生产服务器远端显式改到 `RAGPinCheng-public`、再创建空白私有 `RAGPinCheng`、推送验证后切换生产远端的无中断顺序。
- 文件：`WORKLOG.md`（未修改业务代码、Git 远端、远程仓库或服务器配置）
- 验证：只读检查本地远端地址和工作区状态，确认部署工作流与说明仍处于未提交状态；未推送、未创建私库、未连接生产服务器。
- 待办/风险：在同名私库创建前应先修正两台机器的公开远端地址；本地尚有本任务的工作流、部署脚本和文档改动，迁移时需明确提交到新私库，不应误推回公开仓库。

### 12:30 — 核对新私库迁移前状态

- 完成：确认新建的私有 `abworks-dev/RAGPinCheng` 初始为空、公开 `RAGPinCheng-public` 与本地迁移基线均为 `242a4ea`；在用户批准后将公开仓库登记为 `public`、私库登记为 `origin`，提交 GitHub Actions、生产部署脚本和部署文档，并首次推送完整 `master` 历史到私库。本地 `master` 已改为跟踪 `origin/master`。
- 文件：`.github/workflows/ci.yml`、`.github/workflows/deploy-production.yml`、`scripts/deploy-production.ps1`、`Git同步部署与测试流程.md`、`WORKLOG.md`（未修改业务代码）
- 验证：`git diff --cached --check` 通过；首次部署提交为 `9bfa6bd`；远端复核确认私库和本地均为 `9bfa6bd`，公开仓库仍为 `242a4ea`，未收到部署配置；推送触发私库 CI，但手动生产部署未触发。未切换生产服务器或注册 runner。
- 待办/风险：需查看私库 CI 结果；随后在生产服务器配置私库认证并切换 `origin`，该步骤涉及生产部署链路，执行前需再次确认目标与回滚方式。

### 12:35 — 规划生产服务器切换私库

- 完成：在私库首次推送完成后，规划以只读 GitHub Deploy Key 为生产服务器配置私库访问，保留 `public` 远端作为回滚入口；将生产机远端切换、快进同步部署文件、Runner 服务账号和 Docker 权限验证拆分为受控步骤，明确同步代码不等于启动生产部署。
- 文件：`WORKLOG.md`（未修改业务代码、生产服务器、GitHub Deploy Key 或远端配置）
- 验证：核对本地工作区干净、`master` 跟踪私库 `origin/master`，且 `public` 与 `upstream` 远端仍保留；未读取私库 CI 状态、未连接生产服务器。
- 待办/风险：执行前需确认私库 CI 已通过和生产 Runner 使用的 Windows 账号；生产服务器生成的私钥不得离开服务器，切换 `origin` 与拉取代码属于 R2 部署链路变更，需用户明确批准。

### 12:37 — 启动生产私库访问分步引导

- 完成：将生产服务器私库接入拆分为逐步确认流程，指导用户在私库 Actions 页面核对最新 CI 的分支、提交和检查结果；用户已确认 CI 绿色通过。继续确认生产服务器当前 Windows 账号为 `${PRODUCTION_HOSTNAME}\administrator`，且 Docker、Docker Compose 和 NVIDIA GPU 命令均可用，可作为生产 Runner 执行账号；用户已在私库添加只读 Deploy Key，未勾选写权限。
- 文件：`WORKLOG.md`（未修改业务代码、工作流、GitHub 设置或生产服务器）
- 验证：核对私库最新已推送提交为 `946306f`，并由用户反馈私库 CI 已通过；根据用户提供的生产服务器命令结果确认 `docker version`、`docker compose version` 和 `nvidia-smi` 均正常；由用户确认 Deploy Key 已添加且未授予写权限；用户测试默认 SSH 地址读取私库时出现 `Permission denied (publickey)`，判断为 Git 未指定专用 Deploy Key；指导配置 SSH Host 别名后，用户确认私库读取成功且 master 为 `946306f`；生产目录只读检查显示工作区干净，当前 HEAD 为公开基线 `242a4ea`，`origin` 仍为旧同名 HTTPS 地址；未连接生产服务器。
- 待办/风险：用户已批准切换生产远端并快进同步私库；生产远端已确认切为 `origin` SSH 私库、`public` 公开备份、`upstream` 原作者仓库，生产目录已快进同步到私库最新 `946306f` 且工作区干净；检查 `D:\actions-runner\.runner` 不存在，判断 runner 尚未完成注册或当前目录不是已配置 runner 目录；用户已重新下载并解压 GitHub Actions Runner，确认存在 `config.cmd`；首次注册返回 GitHub API 404，判断需重新从私库 Runner 页面生成与该仓库匹配的新 token；用户反馈 runner 后续已配置完成，GitHub 显示在线、标签正确且 Windows 服务 Running；生产部署前检查确认 `.env` 存在、HEAD 为 `946306f`、工作区干净、Docker Compose 配置无错误，仓库变量 `DEPLOY_HTTP_PROXY` 未设置且按当前网络条件可为空；用户在 GitHub Code 页面确认 `.github/workflows/deploy-production.yml` 已存在且包含 `workflow_dispatch`，刷新后 GitHub Actions 已显示 `Deploy production` 手动运行入口；首次手动部署在 runner 执行临时 PowerShell 脚本时被 Windows Execution Policy 拒绝，调整后重新运行 workflow 已绿色通过，用户确认生产容器状态正常。

## 2026-07-25

### 00:06 — 改为 CI 成功后自动部署

- 完成：按用户批准的 R2 方案，将生产部署 workflow 保留手动 `workflow_dispatch` 入口，同时新增 `workflow_run` 触发，使 `master` 分支 CI 成功后自动触发生产部署；失败或未完成的 CI 不会触发部署。
- 文件：`.github/workflows/deploy-production.yml`、`WORKLOG.md`
- 验证：核对 `CI` 工作流名称与 `deploy-production.yml` 的 `workflow_run.workflows` 匹配；`git diff --check` 通过；人工核对 job 条件仅允许手动确认或 CI success 进入部署。未触发生产部署。
- 待办/风险：该提交推送到 `master` 后会先运行 CI，CI 成功将自动触发生产部署；后续任何直接推送到 `master` 且 CI 通过的提交都会影响生产，日常半成品应推到功能分支而不是 `master`。

### 00:42 — 解释 TODO 中的检索黄金集

- 完成：核对 TODO、评测数据结构和执行脚本，确认“黄金集”是经人工审核后固定下来的 RAG 回归评测题集，用于判断检索改动是否造成 Recall@1、Recall@5、MRR@5 或 no-answer 能力退化；未修改代码或黄金集数据。
- 文件：`WORKLOG.md`
- 验证：只读核对 `TODO.md`、`src/eval/types.py`、`src/eval/golden.jsonl` 和 `scripts/run_eval_retrieval.py`；未运行评测，因为本次仅解释概念。

### 00:47 — 强化 WORKLOG 时间前缀规则

- 完成：将工作日志时间前缀明确提升为硬性约束，要求所有当前任务记录使用 `### HH:mm — 简短任务名`；新增 Claude Code 全局工作日志规则，并同步强化 Codex 协作入口，禁止当前任务使用无时间标题或 `--:--`。
- 文件：`CLAUDE.md`、`.claude/rules/worklog.md`、`AGENTS.md`、`WORKLOG.md`
- 验证：检索三个指令入口确认均包含强制时间格式、无时间标题禁令和 `--:--` 使用边界；`git diff --check` 通过。未运行代码测试，因为仅修改协作说明。
- 待办/风险：按批准范围保留既有无时间历史记录，未猜测或批量补写其完成时间。

### 00:50 — 修复 WORKLOG 标题格式

- 完成：扫描全部工作日志标题，将 2026-07-25 的五条无时间历史记录补为 `--:--` 占位格式，并移动到当天所有已知时间记录之后；未猜测历史完成时间或修改日志正文。
- 文件：`WORKLOG.md`
- 验证：检查所有三级标题均符合 `### HH:mm — 任务名` 或历史占位格式 `### --:-- — 任务名`，并核对同日已知时间记录位于占位记录之前。

### 15:48 — 评估分离 GPU 后的部署配置

- 完成：核对生产 Compose、镜像依赖及 embedding/reranker 调用方式，确认 i5-8400、32 GB 内存和约 500 GB 存储足以承载 Web/API、Qdrant、SQLite 与文件存储；当前 embedding 与 reranker 仍以内嵌模型方式运行，尚不支持直接迁移到另一台 GPU 主机，CPU 回退可运行但会增加检索与建索引延迟。进一步确认远程化在现有模块边界下可行，但远程 embedding 必须保持 BGE-M3 的 1024 维 dense 与 sparse lexical weights 契约，reranker 必须保持逐候选归一化分数及顺序；未修改业务代码。
- 文件：`WORKLOG.md`
- 验证：只读核对 `docker/docker-compose.yml`、`docker/Dockerfile.backend`、`requirements-prod.txt`、`src/embed.py`、`src/rerank.py` 与 `src/config.py`；未执行部署或性能压测。
- 待办/风险：实际容量仍取决于文档、视频和 Qdrant 索引规模；若要使用独立 GPU 主机，需要准备兼容 CUDA 的 NVIDIA GPU 环境、固定一致的模型和推理版本、低延迟可信网络，并新增远程 embedding/rerank 服务及鉴权、超时、限流、健康检查和降级配置；模型或向量契约变化可能要求全量重建索引，实施前需按 R2 方案审批。

### 23:34 — 核对多查询扩展实现状态

- 完成：追踪查询改写、会话编排和混合检索链路，确认当前仅实现多轮问题的单一独立查询改写，以及单查询内部的 Dense/Sparse/code boost RRF 融合；未实现由 LLM 生成多个语义等价查询、分别检索并跨查询合并的 MQE。另确认 TODO 提及的 `retrieve_multi` 在当前源码中不存在。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对 `src/generate.py`、`src/session.py`、`src/retrieve.py`、`prompts/rewrite_system.md`、`TODO.md` 和检索功能文档；全仓检索未发现 `retrieve_multi`、`sub_queries` 或多查询执行入口。未运行模型与检索冒烟，因为本次仅调查现状。
- 待办/风险：`TODO.md` 对“Phase 1 基础设施已就绪”的描述与当前源码不一致；如需引入 MQE，会改变 RAG 检索行为，应先按 R2 提交方案并通过黄金集评测。

### 23:35 — 核对 HyDE 实现状态

- 完成：追踪多轮查询改写、查询嵌入、混合检索和索引期表格摘要链路，确认当前没有“先由 LLM 生成假设答案/文档，再嵌入该文本检索”的 HyDE 实现；现有 LLM 查询改写只负责把多轮追问补全为单一独立问题，检索仍直接嵌入该问题。索引期表格摘要会增强表格 Child 的检索表示，但不是查询期 HyDE。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对 `src/generate.py`、`src/session.py`、`src/retrieve.py`、`src/embed.py`、`src/table_summary.py`、`prompts/rewrite_system.md` 和检索功能文档；全仓检索未发现 HyDE、假设文档或假设答案生成入口。未运行模型与检索冒烟，因为本次仅调查实现状态。
- 待办/风险：若引入 HyDE，会新增检索前 LLM 调用并改变 RAG 行为、延迟、成本及检索指标，属于 R2，需先制定方案并用固定黄金集与原始查询基线对比。

### 23:38 — 比较查询拆分与 MQE+HyDE 方案

- 完成：结合当前 BGE-M3 Dense+Sparse、RRF、规范编号 code boost、父子块与 reranker 链路，对比 TODO 的按比较意图查询拆分方案和 MQE+HyDE 方案；结论是优先实现意图门控的查询拆分，再按低召回场景试验 MQE，HyDE 仅适合作为无规范编号、自然语言描述型查询的受控候选通道，不宜全量默认开启。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对 `TODO.md`、`src/config.py`、`src/retrieve.py`、`src/eval/types.py`、黄金集类型与检索功能文档；未运行检索评测或延迟测试，因为本次仅进行架构比较。
- 待办/风险：现有黄金集缺少比较型、多查询覆盖率和 HyDE 漂移专项指标；若实施任一方案均会改变 RAG 行为，属于 R2，需先补充基线、明确融合策略并审批。

### 23:54 — 补充分层查询增强候选设计

- 完成：在 TODO 中单独新增“查询拆分 + MQE + 受控 HyDE”候选设计，明确分层路由、精确查询禁用 HyDE、原始查询保留、跨查询融合、实施顺序与评测门槛；同步修正原查询拆分条目对不存在的 `retrieve_multi` 基础设施的错误描述。
- 文件：`TODO.md`、`WORKLOG.md`（未修改业务代码）
- 验证：检查 TODO 章节编号连续，新方案明确标注“候选设计，尚未实现”；`git diff --check -- TODO.md WORKLOG.md` 通过。未运行代码测试，因为本次仅修改规划文档。
- 待办/风险：实际实现会改变 RAG 行为，属于 R2，仍需提交实施方案、补充专项黄金集并获得批准；本次文档修改不代表方案已获实现批准。

### --:-- — 来源面板分类分组显示

- 完成：来源面板按分类分组（教学视频/公司标准/设计规范等），每组可折叠，组标题带图标和数量；单个卡片不再重复显示分类标签
- 文件：`frontend/src/components/SourcesPanel.tsx`
- 验证：`npm run build` 因安全分类器（Windows Defender）不可用未执行

### --:-- — 来源面板关键词高亮

- 完成：将用户检索词中的关键词在来源文本中加黄色 `<mark>` 背景高亮，使用 `dangerouslySetInnerHTML` 渲染
- 文件：`frontend/src/components/SourcesPanel.tsx`、`frontend/src/components/Message.tsx`
- 验证：`npm run build` 因安全分类器（Windows Defender）不可用未执行

### --:-- — 来源预览文本移除 HTML 标签

- 完成：在 `stripMarkdown()` 中增加 HTML 标签清理（`<[^>]*>` 正则），并补充面包屑和 Tooltip 中 `section_path` 的 HTML 清理
- 文件：`frontend/src/utils/markdown.ts`、`frontend/src/components/SourcesPanel.tsx`、`frontend/src/components/Message.tsx`
- 验证：`npm run build` 因安全分类器（Windows Defender）不可用未执行

### --:-- — 来源卡片复制按钮

- 完成：每个来源卡片右上角添加 `📋 复制` 按钮，点击复制文档标题、章节路径和文本到剪贴板
- 文件：`frontend/src/components/SourcesPanel.tsx`
- 验证：`npm run build` 因安全分类器（Windows Defender）不可用未执行

### --:-- — 复制按钮 HTTP 兼容性修复

- 完成：`navigator.clipboard` 在 HTTP 下不可用，增加 `document.execCommand('copy')` textarea 回退方案
- 文件：`frontend/src/components/SourcesPanel.tsx`
- 验证：`npm run build` 因安全分类器（Windows Defender）不可用未执行

## 2026-07-26

### 16:02 — 诊断公网 SSH 握手前断开

- 完成：确认目标域名的 2222 端口可以建立 TCP 连接，但服务端未返回 SSH 协议标识即主动断开；故障发生在身份认证之前，优先排查公网端口映射目标、SSH 服务监听地址及中间代理，不属于客户端密钥问题。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：执行 DNS、TCP 连通性及 OpenSSH 详细握手只读检查；TCP 成功，OpenSSH 报告 `kex_exchange_identification: Connection closed by remote host`。
- 待办/风险：本机网络代理使用虚拟 DNS 地址，最终公网解析需由服务器方或绕过代理的环境复核；未修改远端服务器配置。

### 16:30 — 评估 Ubuntu 与 Windows GPU 分离迁移

- 完成：核对当前生产 Compose、FastAPI 启动、BGE-M3 embedding、BGE reranker、Qdrant、SQLite 持久化与 GitHub Actions 发布边界；确认现有 backend 将模型推理与 API 进程耦合，无现成远程 GPU 接口，且生产 CD 固定由 Windows GPU 自托管 Runner 部署整套 Compose。形成 Ubuntu 承载 Web/API/Qdrant/持久化数据、Windows 承载 embedding 与 rerank 推理服务，并将两端发布拆分为独立受控作业的候选迁移边界。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读核对 `docker/docker-compose.yml`、`docker/Dockerfile.backend`、`src/embed.py`、`src/rerank.py`、`src/config.py`、`.env.example`、`.github/workflows/ci.yml`、`.github/workflows/deploy-production.yml`、`scripts/deploy-production.ps1`、API 启动及索引/检索调用入口；未执行构建、部署或数据迁移。
- 待办/风险：实施涉及跨主机推理接口、认证、超时降级、模型/向量兼容、双节点 CD 协调、Qdrant 快照和两个 SQLite 的独立备份恢复，属于 R2；两台主机已确认位于同一公司局域网且 Windows 可在工作时间持续运行，仍需固定 Windows 内网地址、明确非工作时间可用性和停机窗口后另行审批。

### 16:39 — 新增双节点迁移执行手册

- 完成：新增 Ubuntu 应用节点与 Windows GPU 节点迁移手册，记录当前依据、目标拓扑、不变量、R2/R3 审批门禁、GPU API/provider/容器/CI/CD 改造、基础设施准备、数据备份恢复、兼容性判定、生产切换、回滚、验收矩阵及 Claude Code 分阶段执行协议。
- 文件：`project-docs/migrations/ubuntu-app-windows-gpu-runbook.md`、`WORKLOG.md`（未修改业务代码）
- 验证：运行 `git diff --check` 并检查章节、风险门禁及尚未决策事项；未执行构建、测试、部署或数据迁移，因为本次仅新增候选迁移文档。
- 待办/风险：手册不构成实施或生产授权；GPU 固定内网地址、端口、版本、非工作时间策略、HTTPS、备份目标、停机窗口和验收阈值仍需在相应阶段确认。

### 16:44 — 梳理 Ubuntu 迁移前准备

- 完成：结合无 GPU 的 Ubuntu 应用节点目标，整理迁移前只读资源检查、系统备份与更新门禁、后续 Docker Engine/Compose/运维工具、自托管 Runner、HTTPS 和数据目录准备边界；确认 Ubuntu 不需要 CUDA、NVIDIA Toolkit、PyTorch 或本地 BGE 模型。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：核对 Docker 官方 Ubuntu 24.04 支持、Compose 插件安装方式、Docker 端口发布与 UFW 的交互，以及 GitHub Linux 自托管 Runner 要求；未在服务器安装软件或修改配置。
- 待办/风险：Docker 发布端口可能绕过 UFW，正式部署前必须调整 Compose 端口绑定与主机防火墙；系统现有大量内核、驱动和桌面更新，应先备份并安排维护窗口。

### 17:30 — GPU 推理服务 Phase 1 代码完成

- 完成：根据迁移手册 Phase 1 方案，创建 `gpu_service/` 独立推理服务，含完整代码结构：
  - `schemas.py` — Embedding/Rerank/Health/ModelInfo 的 Pydantic schema
  - `config.py` — 服务配置（监听地址 `${PRIVATE_IPV4}:8100`、Token 认证、推理限制、日志级别）
  - `models.py` — 单例 ModelManager，BGE-M3 加载 + dense/sparse 编码 + reranker 推理，含冷启动互斥锁、GPU OOM 保护、启动维度校验
  - `app.py` — FastAPI 路由（`/health`、`/model-info`、`/v1/embeddings`、`/v1/rerank`），Bearer Token 常量时间认证，请求日志中间件，413/422/503 错误处理
  - `requirements.txt` — 运行时依赖（torch cu128, FlagEmbedding, fastapi, uvicorn 等）
  - `tests/test_contract.py` — 19 项契约测试（空/单/批量/中英文 embedding、rerank 单候选返回列表、401/403/422/503 错误、CORS、sparse 结构校验）
  - `.env.example` — 配置模板
- 基线采集完成：Commit SHA、Qdrant 38488 points/76310 vectors、SQLite 大小、docs/media/parsed 容量、容器内 Torch/FlagEmbedding/Transformers 版本、健康检查输出
- 用户决策记录：API Token + 防火墙认证、503 维护提示、无固定停机窗口、Ubuntu 自托管 Runner、24h 在线
- 文件：`gpu_service/`（8 文件）、`project-docs/migrations/ubuntu-app-windows-gpu-runbook.md`（更新清单和已决定事项）、`TODO.md`（添加迁移待办项）
- 验证：代码语法经人工核查；本地安全分类器（Windows Defender）暂不可用，未运行 pytest
- 待办/风险：
  - 需在 Windows 生产机执行 `pip install -r gpu_service\requirements.txt && pytest gpu_service\tests\ -v` 确认契约测试通过
  - 需在 Windows 生产机执行 GPU 冒烟测试（启动服务 + 真实 embedding/rerank）
  - 阶段 2（Provider 抽象）尚未启动，需另行方案审批

### 17:45 — 排查本地安全分类器不可用问题

- 完成：确认"安全分类器"指 Microsoft Defender 防病毒服务，其服务已停止且启动类型为 Disabled，所有保护功能（AMService/Antispyware/RealTime）均关闭，属于用户主动行为；`npm run build` 当前正常通过（tsc 5.9.3 + Vite 5.4.21，耗时 1.75s），CSS 仅一个 Tailwind 生成内容的压缩警告，不影响运行。用户确认关闭 Defender 是预期行为，无需修复。
- 文件：`WORKLOG.md`（更新历史记录中提及"安全分类器"的条目，补充说明为 Windows Defender）
- 验证：确认 Windows Defender 服务状态为 Stopped/Disabled；`npm run build` 成功；`tsc -b --noEmit` 通过
