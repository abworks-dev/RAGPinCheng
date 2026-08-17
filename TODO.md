# 品成 BIM 知识库 — 功能待办

本文件只记录未来工作与最近完成摘要。已完成事实与详细验证以 Git commit/PR、checks 和 workflow run 为准；当前功能事实在 `docs/features/` 维护，大型候选方案放在 `docs/plans/`，已批准的长期架构决策放在 `docs/decisions/`。

统一状态：`未开始` / `待审批` / `进行中` / `代码完成待验证` / `待用户验收` / `已完成` / `已取消`。

---

## 当前优先事项

### 受管知识资料库

- 状态：待用户验收
- 目标：以数据库分类和受管对象存储替代物理目录事实来源，建立整理、确认、发布、正式索引和只读目录视图工作流。
- 下一步：
  - [ ] 验收“资料库 / 分类设置 / 索引监控”统一界面，以及非敏感 PDF/Markdown 样本的三权工作流、发布检索、引用预览和只读视图。
- 完成标准：只有已确认并发布的版本进入正式检索；网页上传和后台编号目录均能受控导入；旧视频转录链路不退化；用户确认验收通过。
- 依赖：R2 契约已于 2026-08-11 批准；生产备份、迁移、切换和旧目录归档另按 R3 审批。
- 方案链接：`docs/plans/managed-content-library.md`、`docs/decisions/0003-managed-content-library.md`

### 受管知识资料库生产迁移

- 状态：进行中
- 目标：在独立备份和可回滚前提下完成普通资料受管迁移、旧索引下线、媒体存储解耦和旧 `source` 目录退役。
- 下一步：
  - [ ] 使用一条已发布规范完成真实问答检索和来源文件链接验收，确认引用可打开且不再依赖旧 `source`。
  - [ ] source 解耦后观察 1 至 2 周，再独立决定旧 `source/docs`、`source/media` 的物理归档或删除；不得删除整个生产仓库目录。
- 完成标准：116 个正式普通资料 head 与未来正式 transcript head 在 `strict` 下可检索；无版本旧索引不可见且受控下线；生产容器不再依赖 `source/docs`、`source/media`；切换和物理归档均有独立恢复点。
- 依赖：T11 已于 2026-08-14 删除 19,981 个旧普通资料 Parent 和 38,387 个 Qdrant Point；T12-B 数据 workflow `31767567546` 已按冻结摘要 `a36bbef…c53a` 完成独立备份、4 条旧媒体归档、2 个 transcript head、44 个 Parent 和 104 个 Point 的精确下线。App-only workflow `31769119451` 已将生产切换到 `strict`，并通过 41,191 个 Point、SQLite 完整性、受管配置、GPU、后台页面和无旧 `source` 容器挂载核验。
- 方案链接：`docs/plans/managed-content-library.md`、`docs/decisions/0003-managed-content-library.md`、`docs/migrations/managed-content-production-runbook.md`

### 查询拆分 Phase A 生产评测

- 状态：进行中
- 目标：量化查询拆分对比较型问题的检索收益、稳定性和成本，为是否灰度开启提供依据。
- 阶段 1：Phase A（已完成）
  - 已完成：在 Ubuntu 活索引运行 Phase A 2×2 评测，保存 ITT、applied-only、5 个 contrast、gain/loss 和错误状态结果（2026-07-31，commit `d57fe1c` + 两个 bug fix `e3c80d5`/`7626d1c`）。
  - 已完成：机制层验证：4 题 comparison 跑通；transitions 守恒；4-样本警告触发；负 delta 触发 WARN 不改 exit code。结论：拆分确有正向收益（k5 +0.500、k8 +0.250 `delta_all_sides_hit_rate`），但 4 题、同一文档对、均带强规范编号，不构成统计决策。
- 阶段 2：ChatSession 开关 A/B（未跑，Phase B 核心）
  - [ ] 真实环境开关运行完整 ChatSession：on 路径走 `_fresh_retrieve`（gate + rewrite + guard + carry + context packaging + generate），off 路径走原 `_fresh_retrieve(开关关)`。
  - [ ] 收集回答质量、引用支持度、partial/no-answer、`final_sources → used_sources` 侧覆盖损失、延迟 P50/P95、Token 成本、timeout、fallback、错误率。
  - [ ] 写 `docs/plans/phase-b-comparison-greyout.md` 作为独立 R2 方案（待新写）。
  - [ ] 显式声明结果**不**构成灰度依据的边界（样本不足、in-process 风险、query rewrite/guard 干扰等）。
- 完成标准：Phase A 协议层✅ + Phase B 真实链路数据齐；普通黄金集不退化；比较集收益、延迟和成本有可复核结论；明确给出开启、继续关闭或补实验的建议。
- 依赖：Ubuntu 活索引、GPU 服务、当前 79 题黄金集、`### 黄金集第二期扩展`（用于 A/B 跑出代表性数据）；默认开关保持关闭。
- 方案链接：`scripts/run_eval_retrieval.py`、`docs/features/retrieval-pipeline.md`、本节阶段 2 产出的 Phase B 计划文件（待写）

### 查询拆分灰度决策

- 状态：未开始
- 目标：在 Phase A + Phase B（ChatSession 开关 A/B）均通过、且黄金集第二期扩样后，决定是否有限灰度开启。
- 下一步：
  - [ ] 等 Phase B 跑通后，提交独立灰度方案 + 回滚条件 + canary 流量比例 + 监控阈值 + 恢复方式。
- 完成标准：用户明确批准或否决灰度；如批准，具备监控指标、停止条件和回滚步骤。
- 依赖：`### 查询拆分 Phase A 生产评测` 全部完成；`### 黄金集第二期扩展` 完成后扩样；该项改变 RAG 行为，属于 R2。
- 方案链接：待 Phase B 完成后新增到 `docs/plans/`。

### Office 安全与故障恢复补齐

- 状态：进行中
- 目标：补齐 Office 处理链路中尚未覆盖的安全、资源控制和派生产物清理能力。
- 下一步：
  - [ ] 检测 Office 外部链接及嵌入对象，并明确拒绝或清洗策略。
  - [ ] 为 Docling/openpyxl 解析增加统一超时和可终止边界。
  - [ ] 增加磁盘空间阈值检查与可观察告警。
- 完成标准：恶意或超限输入可控失败；任务不会无限占用解析资源；删除后无已知派生产物残留。
- 依赖：现有签名校验、zip bomb、宏检测、上传大小限制、串行索引队列和 LibreOffice 超时已经实现；删除属于高风险操作，真实数据验证需单独确认。
- 方案链接：`docs/plans/office-document-support-plan.md`

### Office 发布与运维收口

- 状态：进行中
- 目标：补全已部署 Office 能力的生产说明、可观测性和回滚约定。
- 下一步：
  - [ ] 记录镜像体积与 CPU、内存、磁盘资源影响。
  - [ ] 基于已实现的部署级 Office 功能开关设计分阶段生产灰度策略。
- 完成标准：新环境可以按文档部署并检查 Office 链路；故障时能停用新解析且不影响 PDF、Markdown 和转录稿。
- 依赖：Python/前端依赖、LibreOffice 独立容器、Compose 和 `LIBREOFFICE_URL` 已落地。
- 方案链接：`docs/plans/office-document-support-plan.md`

### Office 自动化测试与用户验收矩阵

- 状态：进行中
- 目标：把当前 XLSX 专项测试和人工冒烟扩展为完整的 Office 回归保护。
- 下一步：
  - [ ] 形成覆盖匿名用户、普通用户、管理员、失败重试和兼容预览的用户验收矩阵。
- 完成标准：Office 专项自动化测试通过、前端构建通过，并完成一轮非敏感样本用户验收。
- 依赖：现有 `tests/test_xlsx_converter.py` 只覆盖部分 XLSX 转换行为；真实生产资料不得作为普通测试样本。
- 方案链接：`docs/plans/office-document-support-plan.md`、`docs/USER_ACCEPTANCE.md`

---

## 产品与体验待办

### 资料完全删除残留修复

- 状态：待用户验收
- 目标：完全删除资料后不再显示历史完成任务生成的幽灵资料条，并在源文件删除失败时明确告警。
- 下一步：
  - [ ] 使用一份可清理的非敏感测试资料验证“同时删除源文件”后资料条消失；如可构造文件占用或权限失败，确认弹窗保留“索引已移除、源文件未删除”提示。
- 完成标准：已删除资料不再显示或计入可检索统计，处理中/失败任务仍可见，源文件删除结果不再被静默误报。
- 依赖：无需数据库迁移或索引重建；不得用真实业务资料执行破坏性验收。
- 方案链接：`docs/plans/admin-document-management-pr1.md`、`docs/USER_ACCEPTANCE.md`

### 跨父文档多轮综合

- 状态：未开始
- 目标：让多轮对话能够利用多个历史命中文档，而不局限于少量上一轮片段。
- 下一步：
  - [ ] 先调查当前 carry、rewrite 和预算链路，定义跨文档软提示及证据展开边界。
  - [ ] 提交 R2 实施方案和多轮黄金集验证设计。
- 完成标准：跨文档追问可稳定召回相关证据，且普通单轮与 no-answer 不退化。
- 依赖：会改变 ChatSession 与 RAG 行为，属于 R2。
- 方案链接：待调查后新增。

### 视频播放器交互增强

- 状态：待用户验收
- 目标：提升长视频转录阅读、续播和移动端体验。
- 下一步：
  - [ ] 使用非敏感测试视频验收移动端底部弹层的关闭、播放、转录滚动和横屏行为。
- 完成标准：移动端核心操作不被遮挡。
- 依赖：视频播放器第一阶段和精确命中时间跳播已完成。
- 方案链接：`docs/decisions/0001-video-transcript-player.md`

### 反馈质量仪表盘

- 状态：未开始
- 目标：把现有反馈数据转化为可操作的检索质量视图。
- 下一步：
  - [ ] 定义管理员趋势、文档/章节错误率和检索失败聚合指标。
  - [ ] 设计失败案例聚类及定期质量报告的隐私边界。
- 完成标准：管理员可定位高频错误来源，并形成可复核的改进清单。
- 依赖：`api/feedback.py` 已提供数据收集基础；涉及用户反馈数据时需遵守安全规则。
- 方案链接：待设计后新增。

---

## 评测与质量待办

### 黄金集第二期扩展

- 状态：未开始
- 目标：覆盖当前第一期规范类题集之外的公司流程、BIM 操作、培训视频和输入边界。
- 下一步：
  - [ ] 设计公司流程/BIM 操作类“怎么做”问题的评分方式。
  - [ ] 增加培训视频转录问题和纯数字、纯代码、无意义输入等边界用例。
  - [ ] 在实际召回验证基础上继续补充内容对称的比较题。
- 完成标准：新增题集经过人工审核，索引指纹已冻结，评分口径与检索黄金集、生成质量评测边界清晰。
- 依赖：第一期 79 题基线、comparison 评分和索引陈旧告警已经完成。
- **Phase B 前置关系**：`### 查询拆分 Phase A 生产评测` 阶段 2 真实链路 A/B 需要在 20-30 道代表性 comparison 题（含跨文档、无强规范码、多实体、gate 真假阳阴）上跑才有意义；当前第一期 4 题同池 GB50189↔GB55015、均带强规范编号，Phase B 跑在这 4 题上只会重复 Phase A 的"机制层信号"，得不出"代表性数据"。**本条目视为 Phase B 的强制前置**，不先做则 Phase B 的 A/B 价值有限。
- 方案链接：`docs/golden-set-staleness-guard.md`

---

## 待审批候选方案

### 分层查询增强（MQE + 受控 HyDE）

- 状态：待审批
- 目标：改善术语错配、模糊现象和培训视频类问题的召回，同时保护精确查询和 no-answer。
- 下一步：
  - [ ] 查询拆分灰度结论明确后，重新核对方案并提交独立 R2 审批。
- 完成标准：MQE、HyDE 分阶段验证，固定黄金集证明收益，延迟和成本可接受，精确查询不退化。
- 依赖：查询拆分 Phase A 与灰度决策；不得替换原始查询通道。
- 方案链接：`docs/plans/layered-query-enhancement.md`

### 多引擎视频自动转录

- 状态：代码完成待验证
- 目标：建立引擎无关的转录、审核、版本发布和索引流水线，让管理员只能从服务端白名单 Profile 中启动自动转录，同时永久保留人工 Markdown 路径。
- 下一步：
  - [ ] 由远端 CI 在干净环境验证 Phase 5 API、worker、candidate index、Qdrant Filter 组合、完整前端测试与构建，要求新增 job 零失败、零跳过且既有 jobs 不退化。
  - [ ] 将转录管理流程加固 PR 1 交由 scoped code review、远端 CI 和用户验收；PR 2 独立工作台须重新按 R2 审批后再实施。
  - [ ] CI 通过后进行 scoped code review 和用户验收；不得把本地缺依赖的测试写成已通过。
  - [ ] 如需执行 R3B，逐项审批 Windows 目录/ACL、独立 venv 依赖安装、固定 revision 模型离线准备、防火墙限源、Token 写入和 Scheduled Task 注册；默认保持不执行。
  - [ ] R3B 通过后另行审批 R3C，仅用非敏感短媒体和 experimental Profile 做隔离端到端验收；不得自动开放正式 Profile、自动发布、自动索引或生产灰度。
  - [ ] 任何真实 GPU qualification 继续按引擎逐项审批，列明目标 workflow、完整 master
    SHA、样本准备状态和副作用；共享语料迁移通过不自动授权推理或 Profile admission。
  - [ ] 同 SHA R3 通过后另行提交生产 R3 执行方案，逐项覆盖维护窗口、`asr.env`/应用/venv
    备份、Windows 本机双 Profile 验证、Ubuntu 跨节点验证、跨节点失败后的自动回滚和应用侧
    `ASR_ENABLED=false` 门禁；未经批准不得执行。
  - [ ] Qwen3-ASR R2 代码通过 scoped review、干净环境 CI 并合并后，按统一 R3
    一次性方案审批并执行单一 workflow；内部依次通过依赖/许可证/CUDA、双模型、
    真实推理、8 样本质量与资源门禁，任一前置门禁失败即停止，Profile 保持 disabled。
    首次 workflow `30970277613` 已在 `pip_download` 失败关闭；先完成固定 source
    run/SHA 的上下文安全结构化；只读取诊断 workflow `30972780438` 的三个固定本地文件，
    对剩余 2 条相同上下文记录仅提取规范化 requested/owner requirement 关系。取得严格
    脱敏 v3 JSON 后停止，不得运行 pip、修改 pin/freeze、服务或 Profile admission。
- 完成标准：人工转录流程不退化；管理员只能选择服务端白名单 Profile；同一媒体可保留多个历史版本且只有 `app.sqlite` head 指向的版本进入正式检索；experimental Profile 强制审核；至少一个 `qualification_approved` Profile 完成隔离端到端验证后才讨论生产灰度。
- 依赖：Phase 1～4B 已形成契约、持久化、remote Provider 与应用上传/worker/UI 前半段；Phase 5A/5B 已实现版本审阅、发布、候选索引和检索可见性，待远端 CI；Windows ASR R3A 仓库实施及 PR #8 远端 CI 已通过，但不等于生产部署完成；R3B/R3C、真实引擎/GPU/Qdrant 和生产数据均未执行；真实环境操作另按 R3 逐项审批，单卡 GPU 保持 BGE 优先。
- 方案链接：`docs/plans/multi-engine-auto-transcription.md`、`docs/plans/multi-engine-transcription-phase1.md`、`docs/plans/multi-engine-transcription-phase2.md`、`docs/plans/multi-engine-transcription-phase3.md`、`docs/plans/multi-engine-transcription-phase5.md`、`docs/plans/multi-engine-transcription-phase5c-windows-asr-deployment.md`、`docs/plans/transcription-admin-workflow-hardening.md`、`docs/plans/faster-whisper-provider-integration.md`、`docs/plans/faster-whisper-r3-unified-qualification.md`、`docs/plans/qwen3-asr-r2-r3-integration.md`、`docs/plans/shared-asr-qualification-corpus-migration.md`、`docs/plans/asr-local-development-lab.md`、`docs/decisions/0002-multi-engine-transcription.md`、`docs/plans/funasr-auto-transcription.md`

### 转录稿 Markdown 校对

- 状态：待用户验收
- 目标：允许管理员在转写工作台校对任一转录版本，以新草稿保存、重新审核并受控发布，不覆盖 ASR 或当前正式版本。
- 下一步：
  - [ ] 按 `docs/USER_ACCEPTANCE.md` 在非敏感测试视频上验收桌面双栏、移动编辑/预览切换、新草稿、未保存保护、审核发布和失败恢复。
- 完成标准：新修订可持久保存并可追溯基础版本和编辑人；并发冲突失败关闭；候选索引完成前旧正式 head 不变；用户确认目标 viewport 与核心操作可用。
- 依赖：schema 11 迁移、转录 artifact 存储、Phase 5 审核/候选索引/正式 head 流程。
- 方案链接：`docs/features/transcript-pipeline.md`

---

## 最近完成摘要

后续新增摘要应尽量链接相关 commit、PR、workflow 或功能文档；详细事实不在本文件重复记录。

1. 当前回答来源支持批量复制和 Markdown 下载（2026-08-17，PR #462）。
2. 视频播放进度按用户和媒体恢复，明确时间引用仍优先（2026-08-17，PR #460）。
3. 项目文档目录统一为 `docs/`，legacy 资料默认路径迁至 `content/legacy-docs/`，生产部署验证通过（2026-08-14，PR #268，workflow `31802581728`）。
4. [受管知识资料库](docs/features/document-indexing.md)生产基础能力启用，保持 `compat` 且未迁移旧资料（2026-08-11，PR #221/#222，workflow `31500815860`）。
5. [Qwen3-ASR / WhisperX 本地快速实验室](docs/plans/asr-local-development-lab.md)及双引擎 full 实测（2026-08-11）。
6. 三引擎共享 ASR qualification corpus 中性变量迁移（2026-08-10）。
7. faster-whisper 生产准入代码准备（2026-08-10）。
8. 管理端资料管理工作流 PR1，用户验收通过（2026-08-05）。
9. 多引擎视频自动转录架构决策与总体方案（2026-08-01）。
10. 查询拆分 Phase A 评测协议与指标实现（2026-07-31）。
