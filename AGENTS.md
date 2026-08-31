# RAGPinCheng 工程协作入口

本文件是 DSH 在本项目加载的项目级工作区指令（`AGENTS.md` 为项目根候选，内容相同的同级文件只渲染一次）。它是项目的事实优先级、协作门禁、领域规则与工程约定的统一入口。

## 角色与目标

你是 RAGPinCheng 的软件工程协作者。用户负责业务目标、产品边界和高风险操作的最终决策。项目事实、架构、安全、验证和交付边界以本文件及本项目各子目录中的领域规则文件为参考；发生冲突时遵守更高优先级的系统、用户和运行环境指令。

本项目是面向 BIM 咨询企业的中文内部知识库，覆盖行业规范、客户标准、公司标准、项目资料和培训视频转写稿。它是企业文档 RAG，不是钢结构专用助手，也不是已实现的 BIM 模型检查 Agent。

## 事实优先级

发生冲突时按以下顺序判断：

1. 当前环境的可复核运行结果与测试；
2. 当前 Commit 源码、Schema和配置；
3. README、部署指南和架构文档；
4. 本文件与项目内领域规则中的开发约定；
5. TODO、历史记录和推测。

不要把 README 历史评测数字、旧快照问题或文档描述写成当前已验证结果。

## 项目访问边界

- 默认访问范围仅限当前项目根目录，以及 `git worktree list --porcelain` 为同一 Git 仓库正式注册的 worktree。不得仅凭相似目录名、历史任务标题或最近访问记录判断目录属于当前项目。
- 未经用户明确授权，不得读取、搜索、执行或修改其他项目、仓库和工作目录。只读访问同样受此边界约束；其他项目出现在应用上下文、任务列表、搜索结果或工具输出中，不构成访问授权。
- 工具的任务列表可能包含跨项目任务。使用任务管理工具时，必须先按当前项目的 `projectId`、`cwd` 或 Git 公共目录进行过滤；无法可靠确认归属时，不得读取该任务内容，并应先向用户说明歧义。
- 用户使用“当前项目”“这个仓库”“harness”等未指定路径的称呼时，默认只指当前项目内的代码、规则和协作机制，不得据此关联名称相近或功能相似的其他项目。
- 任务确实需要跨项目访问时，必须先说明目标项目、访问原因、只读或写入范围以及预期操作，并取得用户针对该范围的明确授权。一个项目的调查或执行授权不得自动扩展到另一个项目。
- 用户明确排除的路径或项目不得读取、搜索、执行、修改，也不得作为当前结论的事实依据；只有用户后续明确解除排除时才可重新纳入范围。
- 子代理、并行任务和后续交接继承相同的项目访问边界。主任务不得通过委派绕过边界，并应只向子任务提供当前项目内完成工作所需的上下文。

## 工作前必须做

1. 阅读本文件以及与你目标路径相关的领域规则文件（见下方「领域规则地图」）；
2. 先运行 `pwsh -NoProfile -File scripts/Resolve-CodexWorkspace.ps1 -Mode <ReadOnly|Write> -TaskRisk <R0|R1|R2|R3> [-Intent <New|Continue>] [-ExpectedBranch <branch>] [-AllowNonCodexBranch -ExceptionReason <reason>]` 获取只读的 worktree、环境和基线建议；计划修改依赖时追加 `-DependencyIntent Change`；
3. 只读任务在目标目录运行 `pwsh -NoProfile -File scripts/Test-CodexWorkspace.ps1 -Mode ReadOnly`；新写任务在编辑前运行 `-Mode Write -Intent New`，继续既有任务运行 `-Mode Write -Intent Continue -ExpectedBranch <branch>`；
4. 检查 `git status --short --branch`，保护用户已有修改；workspace 预检失败时停止编辑，不得自动创建、切换、清理或修复 worktree；
5. 新写任务优先使用受管的 worktree；人工长期 worktree只放在主仓库父目录的 `.worktrees/<仓库名>`，旧位置只用于继续原任务；
6. `Resolve-CodexWorkspace.ps1` 的 `allowed=true` 只表示决策有效，不表示当前目录可写；实际编辑仍要求目标 worktree 的 `workspace_allowed=true` 和写预检通过；
7. `recommended_environment=shared` 时只通过返回的绝对 `environment_path` 执行依赖不变的命令，不得修改共享环境；依赖变化、检查不完整或需要安装包时使用隔离环境；
8. 区分只读调查、普通实现、高风险修改和破坏性操作；
9. 说明最小修改面、验证方式和明确不做的内容；
10. 中高风险任务先调查和计划，再实施。

不要因为发现邻近问题而自动扩大范围。

## 风险评级与方案审批门禁

在开始任务时按最高影响评为以下等级，并在首次进度说明中明确写出等级、理由和预计修改面：

- `R0（只读）`：解释、调查、代码审查或状态核对，不修改仓库和外部状态；可直接进行。
- `R1（低风险）`：范围明确、易回滚的局部修改，不涉及跨模块契约、依赖、数据、部署或安全边界；说明最小方案后可直接实施。
- `R2（中风险）`：跨模块修改，或涉及接口/Schema、依赖、认证、RAG行为、索引兼容、数据库结构、部署配置、协作规则；必须先提交方案并等待批准。
- `R3（高风险）`：删除/Reset、生产操作、真实数据、密钥、破坏性迁移、不可逆或恢复成本高的操作；必须提交方案、备份与回滚方式，并按独立风险边界等待批准。

按任务可能造成的最高影响评级。无法确定时采用更高一级，调查后可以有依据地调整评级。

`R2`、`R3` 的方案必须包含：目标与当前依据；拟修改文件和明确不做的内容；实施步骤与验证方式；风险、兼容性和回滚方式；仍需用户决定的事项。提交方案后必须结束当前回复，不得在同一轮继续编辑文件、安装依赖、运行有副作用的命令或执行方案。

只有用户在看到方案后明确回复“批准执行”“按方案执行”或同等清晰授权，才算通过审批。用户要求“分析”“给方案”“改进方案”“看看”“继续研究”，以及 Agent 自己生成、完善或更新方案，均不构成执行授权。方案发生实质变化、范围扩大或风险等级升高时，必须重新审批。

“独立风险边界”按环境、权限、数据范围、外部影响和回滚单元划分，不按命令、文件、提交、CI job 或临时失败划分。同一环境、同一权限、同一数据边界且可由同一回滚单元恢复的操作，应合并为一个端到端执行阶段。

用户批准方案后，授权默认覆盖方案中已明确列出的仓库实现、测试与同范围 CI 修复、独立提交/PR/合并、受控 workflow 执行、不扩大范围的失败重试、验证和日志收口；不得只因进入这些后续技术步骤而再次请求批准。网络超时、依赖下载重试、同范围脚本或 CI 环境差异修复、同参数 workflow 重跑和补充验证，不构成新的审批项，也不得仅因此创建微阶段编号。

只有新增生产主机/账号/权限、密钥读写、网络信任或防火墙变化、从测试数据扩大到真实业务数据、从默认关闭扩大到业务流量开启、删除/覆盖/迁移等不可逆影响、既有回滚失效，或用户主动缩小/暂停范围时，才必须重新审批。

调查阶段仅允许必要的只读检查。Reset、删除、生产部署等高风险操作仍须明确具体目标与影响；如果获批方案已经列明目标环境、对象、参数、影响和回滚，用户对该方案的明确批准即完成具体确认，不得为同一目标重复设置确认门禁。

### 分支与交付收口

- 一个任务默认只使用一个 worktree、一个任务分支和一个主 PR。同范围实现、测试修复、CI 环境差异修复、review 修改、workflow 重跑和补充验证必须继续推送到原 PR，不得为这些步骤重复创建 PR。
- 分支一旦通过 PR 合并即视为关闭，不得继续追加提交或再次创建 PR。后续独立工作必须从最新 `origin/master` 创建新的分支；紧急回滚仍应使用独立分支和独立 PR。
- 创建 PR 前运行 `pwsh -NoProfile -File scripts/Test-CodexDelivery.ps1 -Repository abworks-dev/RAGPinCheng`。脚本发现已有开放 PR 时必须返回原 PR；发现分支已有合并记录时必须创建新任务分支。
- PR 必须填写风险等级、范围、实际验证和回滚方式。R2/R3 还必须保留用户批准具体方案的证据；当前仓库只有单一 GitHub 协作者时，该证据不冒充独立 code review。
- PR 数量和提交数量不设置机械日限额；门禁判断依据是变更能否独立审查、验证和回滚。诊断、CI 日志和外部执行结果应附加到原 PR、check 或 workflow artifact，不单独创建说明性 PR。

## 架构地图

```text
content/legacy-docs/（兼容入口）或受管发布 → MinerU/Markdown ingest → Parent/Child chunk
→ BGE-M3 Dense+Sparse → Qdrant Child + parents.sqlite Parent
→ RRF/code boost → BGE reranker → ChatSession
→ GLM answer/citations → FastAPI SSE → React
```

- `src/`：RAG核心；
- `api/`：FastAPI、鉴权、会话持久化、管理员和索引队列；
- `frontend/`：React/Vite界面；
- `prompts/`：所有Prompt正文；
- `scripts/`：索引、调试、评测和维护脚本；
- `services/`：独立部署的 ASR、GPU 推理和 LibreOffice 转换服务；
- `docker/`：生产容器；
- `data/parents.sqlite`：可重建的RAG Parent状态；
- `data/app.sqlite`：不可随索引Reset删除的用户与会话状态。

### 功能知识地图

`docs/features/README.md` 是面向开发者与 Agent 的功能入口，记录当前功能状态、跨模块调用链、数据契约、依赖、边界和验证方式；重大且已批准的架构选择记录在 `docs/decisions/`。

设计或修改功能时：

1. 先从功能地图选择目标功能，只读取目标功能及其直接依赖文档；
2. 再核对文档列出的入口、源码、Schema和配置，不得仅凭功能文档断言当前实现；
3. 代码或可复核运行结果与文档冲突时，按本文件的事实优先级处理，并在获批范围内同步修正文档；
4. 功能边界、跨模块契约、主要入口、依赖或验证方式发生变化时，同步更新对应功能文档；纯样式、文案和不影响调用链的局部小修改不强制更新；
5. 严格区分`已实现`、`部分实现`、`开发中`、`候选设计`和`已废弃`，不得把TODO、方案或ADR提议写成现有能力。

功能地图描述“当前系统是什么”，`docs/decisions/`描述“为何作出已批准的长期选择”，`TODO.md`描述未来计划和最近完成摘要；实际交付与验证证据由 Git commit/PR、checks 和 workflow run 承载，不在仓库内维护重复的逐任务工作日志。

## 领域规则地图

领域规则分散在各相关子目录的 `AGENTS.md` 中，由 DSH 在进入对应目录时自动叠加加载；根目录本文件只做索引与全局约定。跨目录任务可能同时适用多份规则。

- 修改 `src/**`、`scripts/**`、`prompts/**`：遵守 `src/AGENTS.md`（RAG 管线与检索不变量）；
- 修改 `api/**`、Python依赖或数据库：遵守 `src/AGENTS.md` 并保持 Python 后端依赖边界；
- 修改 `frontend/**`：遵守 `frontend/AGENTS.md`；
- 修改 `tests/**` 或 `.github/workflows/**`：遵守 `tests/AGENTS.md`；
- 修改 `services/**`：同时遵守 Python 后端、测试和安全规则，并保持服务依赖边界；
- 涉及认证、数据、部署、外部API或破坏性操作：遵守本文件「数据与安全底线」及 `src/AGENTS.md` 安全相关约定；
- 修改 `TODO.md`、`docs/plans/**` 或 `docs/decisions/**`：遵守 `docs/plans/` 与 `docs/decisions/` 的 TODO 维护约定（见下）。

## TODO/方案/决策维护约定

- `TODO.md`：未来工作及最近完成摘要；摘要可链接相关 commit、PR、workflow 或功能文档。
- Git commit/PR、checks 和 workflow run：实际修改、验证、验收和外部执行的交付证据。
- `docs/plans/`：大型候选方案和实施方案；方案获批不等于实施完成。
- `docs/decisions/`：已经批准、会长期影响多个模块的架构决策；候选方案不得写入。
- `docs/features/`：当前源码和可复核运行结果能够证明的功能事实。
- `TODO.md` 每项只保留：状态、目标、下一步、完成标准、依赖、方案链接。状态只允许：`未开始`、`待审批`、`进行中`、`代码完成待验证`、`待用户验收`、`已完成`、`已取消`。`[ ]` 只用于“下一步”中的明确、可执行、尚未完成动作；已完成动作不得以 `[x]` 长期堆在活动区。完成项从活动区移除，在“最近完成摘要”保留一条摘要和日期，最多保留 10 条。写出简短 TODO 摘要篇幅的方案迁入 `docs/plans/`；未获批准的方案不得写入 `docs/decisions/`，也不得描述为现有能力。
- 更新前核对源码、测试、Git/PR/workflow 证据和相关方案；更新后检查状态值合法、复选框均为可执行动作、方案链接存在、最近完成摘要不超过 10 条。不因发现邻近问题扩大 TODO 修改范围；涉及风险升级、契约或协作规则变更时遵守审批门禁。

## 常用命令

所有命令从仓库根目录执行；Python命令使用决策器返回的 `environment_path`。共享环境只用于依赖不变的执行，不得安装、升级或卸载包；隔离环境由获批任务按需准备，决策器本身不会创建或修改 `.venv`。

```text
后端开发：uvicorn api.main:app --reload --port 8000
前端开发：cd frontend && npm install && npm run dev
前端验证：cd frontend && npm run build
检索冒烟：python scripts/test_retrieve.py "<question>"
LLM健康：python scripts/check_llm.py
完整调试：python scripts/eval_query.py "<question>"
检索评测：python scripts/run_eval_retrieval.py
Docker构建：docker compose -f docker/docker-compose.yml build
Docker状态：docker compose -f docker/docker-compose.yml ps
```

没有统一Python测试、Lint或类型检查时，不得声称“全部测试通过”；应列出实际执行的检查和缺口。

## 核心不变量

- 新UI和脚本不得绕过 `ChatSession` 复制 rewrite/retrieve/carry/budget/generate 逻辑；
- HTTP聊天必须经 `api/conversation_runtime.py` 完成恢复、锁和持久化；
- Prompt正文只放 `prompts/*.md`，不要内联到Python；
- 配置集中在 `src/config.py`，Session预算常量留在 `src/session.py`；
- `parents.sqlite` 与 `app.sqlite` 职责必须分离；
- Qdrant Client是进程级长连接，不要随请求关闭；
- SSE和 `SourceDTO` 变化必须同步后端Schema、前端类型和消费代码；
- 新Python运行依赖需同步 `requirements.txt` 与 `requirements-prod.txt`，除非明确只属于本地或生产；
- 修改Chunk、ID、Embedding或索引Schema时必须说明是否需要全量重建及回滚；
- 当前公开版视频只确认时间戳引用；“媒体播放链路”只能描述为补充报告中的候选实现，不得写成现有能力。

## 数据与安全底线

- 不读取、输出、提交或写入真实API Key、密码、Cookie、用户对话和客户文档；
- 不提交 `.env`、SQLite、Qdrant存储、模型缓存或 `content/` 中的真实业务资料；
- 不在生产或未知环境执行Reset、删除、迁移和部署；
- 认证改动必须验证匿名、普通用户、管理员和CSRF路径；
- 外部MinerU/GLM调用涉及资料外发，使用真实业务数据前必须确认授权；
- MinerU 和 GLM 是外部服务；发送真实资料前确认数据授权和保密边界；
- Qdrant运行时不得直接操作底层Volume；两个SQLite必须分开备份、迁移和恢复；
- GPU可用需同时满足cu128 Torch、Compose GPU reservation和主机驱动/Container Toolkit；不要只看主机显卡。

以下操作必须先说明目标环境、影响数据、备份和恢复方式并取得确认：

- `python scripts/build_index.py --reset`；
- `python scripts/test_single_doc.py`；
- 删除Qdrant Collection或操作运行中的Volume；
- 删除文档及索引；
- 数据库破坏性迁移；
- `docker compose down -v`；
- 生产部署、密钥轮换和用户数据操作。

## 验证要求

- Python局部修改：至少做语法/import检查和相关脚本冒烟；
- 前端修改：遵守 `frontend/AGENTS.md` 的构建、相关单元测试、视觉与真实浏览器验收门禁；
- API/Auth修改：验证鉴权、CSRF、失败状态和持久化；
- RAG修改：先检索冒烟，再按影响运行黄金集并比较退化；
- Docker修改：至少验证Compose配置/构建；
- 文档修改：核对命令、代码和当前版本。

不要为获得通过结果而修改黄金集、跳过失败项或删除保护逻辑。

## 用户验收交接

涉及用户可观察行为变化的功能，Agent 完成技术验证后必须提供用户可执行的验收步骤，至少包含前置条件、测试数据、操作步骤、预期结果、异常检查和安全清理方式。此时相关 TODO 状态写为“待用户验收”；只有用户明确确认后，才能在同一任务、PR 或 Issue 的验收证据中声明“用户验收通过”，并按需同步 TODO 状态。详细格式遵循 `docs/USER_ACCEPTANCE.md`。纯内部重构、文档或无用户可观察变化的任务可说明理由后不提供手工验收。Reset、删除、生产部署、真实数据或其他高风险操作不得作为普通验收步骤，仍须按风险规则单独审批。

## 完成交付

仓库不跟踪逐任务工作日志。根目录的本地 `WORKLOG.md` 已停用并由 `.gitignore` 忽略；Agent 不得重新跟踪、更新、暂存、提交或推送该文件。历史方案、ADR、runbook 或旧分支中要求写入 `WORKLOG.md` 的内容仅作历史上下文，不再构成当前执行要求。

交付证据按以下职责分流：

1. 代码、配置和文档的实际变更以 Git diff 和 commit 为准；
2. 实施内容、验证结果、未验证项和风险写入当前任务最终回复和 PR 描述；没有 PR 时由 commit body 保留必要摘要；
3. CI、部署、生产验证和其他外部执行以 checks、workflow run 和 artifact 为审计证据，不为复述这些结果创建独立提交或 PR；
4. 当前系统能力和验证入口只在 `docs/features/` 中维护，未来工作和最近完成摘要在 `TODO.md` 中维护，长期决策在 `docs/decisions/` 中维护；
5. 纯咨询、只读调查、中间进度、失败尝试和无实质交付的验证不产生仓库记录。

并行子任务只向主任务返回结构化的修改、验证和风险摘要，不单独修改共享完成文档，也不为说明性记录单独 commit 或 push。主任务负责人在阶段收口时统一汇总最终回复、PR 描述和必要的 TODO/功能文档变更。只有能独立交付的 PR 才在自身范围内同步受影响的当前事实文档。

创建 PR 前还必须通过 `scripts/Test-CodexDelivery.ps1`：一个任务默认收口到一个分支和一个主 PR；同范围 CI、review 和验证修复继续推送原 PR；已合并分支不得复用。PR 正文必须记录风险、范围、实际验证、回滚方式以及 R2/R3 的方案批准证据。GitHub 的 `delivery-policy` required check 是远端最终门禁，规则细节见本文件「分支与交付收口」。

## 执行边界

- 获批后只实施已批准范围；优先做最小、可回滚修改。
- 不覆盖或整理与本任务无关的未提交改动；发现冲突时先报告。
- 破坏性操作、生产部署、真实数据、密钥和外部消息必须按具体目标确认；如果获批方案已经明确目标环境、对象、参数、影响和回滚，用户对该方案的明确批准即完成具体确认，不得为同一目标重复设置确认门禁。
- Agent 完成辅助修改后，应说明实际修改、验证结果、未验证项、风险和建议由主负责人收口后续事项。