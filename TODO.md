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
- 完成标准：正式资料 head 在 `strict` 下可检索；无版本旧索引不可见；生产容器不再依赖旧 `source`；切换和物理归档均有独立恢复点。
- 依赖：受管资料生产迁移与 `strict` 切换已完成并留有 workflow 证据；真实来源链接验收和旧目录观察期尚未完成。
- 方案链接：`docs/plans/managed-content-library.md`、`docs/decisions/0003-managed-content-library.md`、`docs/migrations/managed-content-production-runbook.md`

### 查询拆分 Phase B 生产评测

- 状态：进行中
- 目标：在完整 ChatSession 链路量化查询拆分对比较型问题的回答质量、稳定性和成本，为是否灰度开启提供依据。
- 下一步：
  - [ ] 编写 `docs/plans/phase-b-comparison-greyout.md`，提交独立 R2 方案审批。
  - [ ] 扩充代表性 comparison 黄金集后，在真实环境对开关 on/off 运行完整 ChatSession A/B。
  - [ ] 收集回答质量、引用支持度、partial/no-answer、`final_sources → used_sources` 侧覆盖损失、延迟 P50/P95、Token 成本、timeout、fallback、错误率。
- 完成标准：普通黄金集不退化；比较集收益、延迟和成本有可复核结论；明确给出开启、继续关闭或补实验的建议。
- 依赖：Phase A 机制评测已完成；黄金集第二期扩展、Ubuntu 活索引和 GPU 服务；默认开关保持关闭。
- 方案链接：`scripts/run_eval_retrieval.py`、`docs/features/retrieval-pipeline.md`，以及待新增的 Phase B 方案。

### 查询拆分灰度决策

- 状态：未开始
- 目标：在 Phase A + Phase B（ChatSession 开关 A/B）均通过、且黄金集第二期扩样后，决定是否有限灰度开启。
- 下一步：
  - [ ] 等 Phase B 跑通后，提交独立灰度方案 + 回滚条件 + canary 流量比例 + 监控阈值 + 恢复方式。
- 完成标准：用户明确批准或否决灰度；如批准，具备监控指标、停止条件和回滚步骤。
- 依赖：`### 查询拆分 Phase B 生产评测` 完成；该项改变 RAG 行为，属于 R2。
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
- 依赖：第一期 79 题基线、comparison 评分和索引陈旧告警已经完成；Phase B 需要 20 至 30 道覆盖跨文档、弱规范码、多实体和 gate 边界的代表性 comparison 题。
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
  - [ ] 在干净环境完成 Phase 5 API、worker、候选索引、Qdrant Filter、前端测试和构建验证，再进行 scoped code review 与用户验收。
  - [ ] 为 Windows ASR 环境准备独立 R3 部署与隔离验收方案，覆盖备份、目录权限、固定依赖、网络限源、Token、回滚和 `ASR_ENABLED=false` 门禁。
  - [ ] Qwen3-ASR 完成 R2 代码审查与 CI 后，再提交统一 R3 qualification 方案；验证通过前保持 experimental/disabled。
- 完成标准：人工转录流程不退化；管理员只能选择服务端白名单 Profile；同一媒体可保留多个历史版本且只有 `app.sqlite` head 指向的版本进入正式检索；experimental Profile 强制审核；至少一个 `qualification_approved` Profile 完成隔离端到端验证后才讨论生产灰度。
- 依赖：Phase 1 至 5B 已形成代码链路但仍待完整验证；Windows 部署、真实引擎/GPU/Qdrant 和生产数据操作必须另按 R3 审批，单卡 GPU 保持 BGE 优先。
- 方案链接：`docs/plans/multi-engine-auto-transcription.md`、`docs/plans/multi-engine-transcription-phase5.md`、`docs/plans/multi-engine-transcription-phase5c-windows-asr-deployment.md`、`docs/plans/qwen3-asr-r2-r3-integration.md`、`docs/decisions/0002-multi-engine-transcription.md`

### 转录稿 Markdown 校对

- 状态：待用户验收
- 目标：允许管理员在转写工作台校对任一转录版本，以新草稿保存、重新审核并受控发布，不覆盖 ASR 或当前正式版本。
- 下一步：
  - [ ] 按 `docs/USER_ACCEPTANCE.md` 在非敏感测试视频上验收桌面双栏、移动编辑/预览切换、新草稿、未保存保护、审核发布和失败恢复。
- 完成标准：新修订可持久保存并可追溯基础版本和编辑人；并发冲突失败关闭；候选索引完成前旧正式 head 不变；用户确认目标 viewport 与核心操作可用。
- 依赖：schema 11 迁移、转录 artifact 存储、Phase 5 审核/候选索引/正式 head 流程。
- 方案链接：`docs/features/transcript-pipeline.md`

### 转录任务页转录方案展示与重新转录

- 状态：待用户验收
- 目标：转录任务页与转写工作台展示每条媒体所用转录方案（自定义方案被归档时提示“原转录配置已删除”）；提供单条与批量“重新转录”（弹窗选择方案，所选与原方案一致时提示但仍可继续）；行操作按钮全部常显，不可用时禁用并由 `disabled_actions` 悬浮提示。
- 下一步：
  - [ ] 生产验收：已转录/进行中任务的方案行与已删除方案标记、重新转录弹窗与同方案提示、批量重新转录、禁用按钮 tooltip，以及桌面与 390px 移动端布局。
- 完成标准：方案展示只出现在已选择方案进入转录/转录完成的记录；已归档方案显示删除标记；重新转录新稿不会覆盖当前正式 head，须重新审核发布；用户确认各状态与 viewport 可用。
- 依赖：`_media_action_state` 新增 `start_transcription`/`re_transcribe` 动作键；媒体/任务/版本 DTO 返回 `scheme_name`/`scheme_deleted`；复用既有 `start` 转录接口。
- 方案链接：`docs/features/transcript-pipeline.md`、`docs/features/managed-content-library.md`

---

## 最近完成摘要

后续新增摘要应尽量链接相关 commit、PR、workflow 或功能文档；详细事实不在本文件重复记录。

1. 当前回答来源支持批量复制和 Markdown 下载（2026-08-17，PR #462）。
2. 视频播放进度按用户和媒体恢复，明确时间引用仍优先（2026-08-17，PR #460）。
3. 项目文档目录统一为 `docs/`，legacy 资料默认路径迁至 `content/legacy-docs/`，生产部署验证通过（2026-08-14，PR #268，workflow `31802581728`）。
4. [受管知识资料库](docs/features/managed-content-library.md)生产基础能力启用，保持 `compat` 且未迁移旧资料（2026-08-11，PR #221/#222，workflow `31500815860`）。
5. [Qwen3-ASR / WhisperX 本地快速实验室](docs/plans/asr-local-development-lab.md)及双引擎 full 实测（2026-08-11）。
6. 三引擎共享 ASR qualification corpus 中性变量迁移（2026-08-10）。
7. faster-whisper 生产准入代码准备（2026-08-10）。
8. 管理端资料管理工作流 PR1，用户验收通过（2026-08-05）。
9. 多引擎视频自动转录架构决策与总体方案（2026-08-01）。
10. 查询拆分 Phase A 评测协议与指标实现（2026-07-31）。
