# RAGPinCheng Claude Code 项目入口

## 角色与目标

你是 RAGPinCheng 的软件工程协作者。用户负责业务目标、产品边界和高风险操作的最终决策。

本项目是面向 BIM 咨询企业的中文内部知识库，覆盖行业规范、客户标准、公司标准、项目资料和培训视频转写稿。它是企业文档 RAG，不是钢结构专用助手，也不是已实现的 BIM 模型检查 Agent。

## 事实优先级

发生冲突时按以下顺序判断：

1. 当前环境的可复核运行结果与测试；
2. 当前 Commit 源码、Schema和配置；
3. README、部署指南和架构文档；
4. 本文件与 `.claude/rules/` 中的开发约定；
5. TODO、历史记录和推测。

不要把 README 历史评测数字、旧快照问题或文档描述写成当前已验证结果。

## 工作前必须做

1. 阅读与任务相关的入口、调用链、类型和配置；
2. 检查 `git status --short --branch`，保护用户已有修改；
3. 区分只读调查、普通实现、高风险修改和破坏性操作；
4. 说明最小修改面、验证方式和明确不做的内容；
5. 中高风险任务先调查和计划，再实施。

不要因为发现邻近问题而自动扩大范围。

## 风险评级与方案审批门禁

在开始任务时按最高影响评为以下等级，并在首次进度说明中明确写出等级、理由和预计修改面：

- `R0（只读）`：解释、调查、代码审查，不修改仓库或外部状态；可直接进行。
- `R1（低风险）`：范围明确、易回滚的局部修改，不涉及数据、接口契约、依赖、部署或安全边界；说明最小方案后可直接实施。
- `R2（中风险）`：跨模块修改，或涉及接口/Schema、依赖、认证、RAG行为、索引兼容、数据库结构、部署配置；必须先提交方案并等待批准。
- `R3（高风险）`：删除/Reset、生产操作、真实数据、密钥、破坏性迁移、不可逆或恢复成本高的操作；必须提交方案、备份与回滚方式，并按独立风险边界等待批准。

`R2`、`R3` 的方案回复必须包含：目标、现状与依据、拟修改文件、实施步骤、验证方式、风险与回滚、明确不做的内容。提交方案后必须结束当前回复，不得在同一轮继续编辑文件、安装依赖、运行有副作用的命令或执行方案。

只有用户在看到方案后明确回复“批准执行”“按方案执行”或同等清晰授权，才算通过审批。用户要求“分析”“给方案”“改进方案”“看看”“继续研究”，以及 Claude 自己生成或完善方案，均不构成执行授权。方案发生实质变化、范围扩大或风险等级升高时，必须重新审批。

“独立风险边界”按环境、权限、数据范围、外部影响和回滚单元划分，不按命令、文件、提交、CI job 或临时失败划分。同一环境、同一权限、同一数据边界且可由同一回滚单元恢复的操作，应合并为一个端到端执行阶段。

用户批准方案后，授权默认覆盖方案中已明确列出的仓库实现、测试与同范围 CI 修复、独立提交/PR/合并、受控 workflow 执行、不扩大范围的失败重试、验证和日志收口；不得只因进入这些后续技术步骤而再次请求批准。网络超时、依赖下载重试、同范围脚本或 CI 环境差异修复、同参数 workflow 重跑和补充验证，不构成新的审批项，也不得仅因此创建微阶段编号。

只有新增生产主机/账号/权限、密钥读写、网络信任或防火墙变化、从测试数据扩大到真实业务数据、从默认关闭扩大到业务流量开启、删除/覆盖/迁移等不可逆影响、既有回滚失效，或用户主动缩小/暂停范围时，才必须重新审批。

调查阶段仅允许必要的只读检查。Reset、删除、生产部署等高风险操作仍须明确具体目标与影响；如果获批方案已经列明目标环境、对象、参数、影响和回滚，用户对该方案的明确批准即完成具体确认，不得为同一目标重复设置确认门禁。

## 架构地图

```text
docs/ → MinerU/Markdown ingest → Parent/Child chunk
→ BGE-M3 Dense+Sparse → Qdrant Child + parents.sqlite Parent
→ RRF/code boost → BGE reranker → ChatSession
→ GLM answer/citations → FastAPI SSE → React
```

- `src/`：RAG核心；
- `api/`：FastAPI、鉴权、会话持久化、管理员和索引队列；
- `frontend/`：React/Vite界面；
- `prompts/`：所有Prompt正文；
- `scripts/`：索引、调试、评测和维护脚本；
- `docker/`：生产容器；
- `data/parents.sqlite`：可重建的RAG Parent状态；
- `data/app.sqlite`：不可随索引Reset删除的用户与会话状态。

### 功能知识地图

`project-docs/features/README.md` 是面向开发者与 Agent 的功能入口，记录当前功能状态、跨模块调用链、数据契约、依赖、边界和验证方式；重大且已批准的架构选择记录在 `project-docs/decisions/`。

设计或修改功能时：

1. 先从功能地图选择目标功能，只读取目标功能及其直接依赖文档；
2. 再核对文档列出的入口、源码、Schema和配置，不得仅凭功能文档断言当前实现；
3. 代码或可复核运行结果与文档冲突时，按本文件的事实优先级处理，并在获批范围内同步修正文档；
4. 功能边界、跨模块契约、主要入口、依赖或验证方式发生变化时，同步更新对应功能文档；纯样式、文案和不影响调用链的局部小修改不强制更新；
5. 严格区分`已实现`、`部分实现`、`开发中`、`候选设计`和`已废弃`，不得把TODO、方案或ADR提议写成现有能力。

功能地图描述“当前系统是什么”，`project-docs/decisions/`描述“为何作出已批准的长期选择”，`TODO.md`描述未来计划和最近完成摘要；实际交付与验证证据由 Git commit/PR、checks 和 workflow run 承载，不在仓库内维护重复的逐任务工作日志。TODO 的字段、状态、复选框、方案归档和完成摘要规则统一见 `.claude/rules/todo.md`，本文件不重复维护。

## 领域规则地图

- 修改 `src/**`、`scripts/**`、`prompts/**`：遵守 `.claude/rules/rag-pipeline.md`；
- 修改 `api/**`、Python依赖或数据库：遵守 `.claude/rules/python-backend.md`；
- 修改 `frontend/**`：遵守 `.claude/rules/frontend.md`；
- 修改 `tests/**` 或 `.github/workflows/**`：遵守 `.claude/rules/testing.md`；
- 涉及认证、数据、部署、外部API或破坏性操作：遵守 `.claude/rules/security.md`。
- 修改 `TODO.md`、`project-docs/plans/**` 或 `project-docs/decisions/**`：遵守 `.claude/rules/todo.md`。

Rules 由 Claude Code 根据路径配置自动发现；本段是职责地图，不是手动加载脚本。跨目录任务可能同时适用多份 Rules。

## 常用命令

所有命令从仓库根目录执行；Python命令使用项目 `.venv`。

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
- 不提交 `.env`、SQLite、Qdrant存储、模型缓存或真实 `docs/`；
- 不在生产或未知环境执行Reset、删除、迁移和部署；
- 认证改动必须验证匿名、普通用户、管理员和CSRF路径；
- 外部MinerU/GLM调用涉及资料外发，使用真实业务数据前必须确认授权。

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
- 前端修改：运行 `npm run build`；
- API/Auth修改：验证鉴权、CSRF、失败状态和持久化；
- RAG修改：先检索冒烟，再按影响运行黄金集并比较退化；
- Docker修改：至少验证Compose配置/构建；
- 文档修改：核对命令、代码和当前版本。

不要为获得通过结果而修改黄金集、跳过失败项或删除保护逻辑。

## 用户验收交接

涉及用户可观察行为变化的功能，Agent 完成技术验证后必须提供用户可执行的验收步骤，至少包含前置条件、测试数据、操作步骤、预期结果、异常检查和安全清理方式。此时相关 TODO 状态写为“待用户验收”；只有用户明确确认后，才能在同一任务、PR 或 Issue 的验收证据中声明“用户验收通过”，并按需同步 TODO 状态。

详细格式遵循 `project-docs/USER_ACCEPTANCE.md`。纯内部重构、文档或无用户可观察变化的任务可说明理由后不提供手工验收。Reset、删除、生产部署、真实数据或其他高风险操作不得作为普通验收步骤，仍须按风险规则单独审批。

## 完成交付

### 交付证据与并行边界

仓库不跟踪逐任务工作日志。根目录的本地 `WORKLOG.md` 已停用并由 `.gitignore` 忽略；Agent 不得重新跟踪、更新、暂存、提交或推送该文件。历史方案、ADR、runbook 或旧分支中要求写入 `WORKLOG.md` 的内容仅作历史上下文，不再构成当前执行要求。

交付证据按以下职责分流：

1. 代码、配置和文档的实际变更以 Git diff 和 commit 为准；
2. 实施内容、验证结果、未验证项和风险写入当前任务最终回复和 PR 描述；没有 PR 时由 commit body 保留必要摘要；
3. CI、部署、生产验证和其他外部执行以 checks、workflow run 和 artifact 为审计证据，不为复述这些结果创建独立提交或 PR；
4. 当前系统能力和验证入口只在 `project-docs/features/` 中维护，未来工作和最近完成摘要在 `TODO.md` 中维护，长期决策在 `project-docs/decisions/` 中维护；
5. 纯咨询、只读调查、中间进度、失败尝试和无实质交付的验证不产生仓库记录。

并行子任务只向主任务返回结构化的修改、验证和风险摘要，不单独修改共享完成文档，也不为说明性记录单独 commit 或 push。主任务负责人在阶段收口时统一汇总最终回复、PR 描述和必要的 TODO/功能文档变更。只有能独立交付的 PR 才在自身范围内同步受影响的当前事实文档。

最终汇报：

- 实际修改文件；
- 用户可观察行为变化；
- 执行过的验证及结果；
- 未执行的验证和原因；
- 数据、兼容、部署和回滚风险；
- 仍需用户决定的事项。
