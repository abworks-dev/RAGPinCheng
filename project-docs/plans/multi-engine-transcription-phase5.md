# 多引擎视频自动转录 Phase 5 — 隔离端到端与版本感知发布

- 状态：**R2 已批准；Phase 5A/5B 首轮远端 CI 失败，最小修复待验证；Phase 5C 未授权**
- 风险等级：**R2**（涉及转录版本审核/发布 API、后台索引编排、Parent/Child 与 Qdrant payload 兼容、检索可见性、管理端 UI 和 CI）
- 调查日期：2026-08-03（Asia/Shanghai）
- 代码调查基线：`origin/master@b323023aaf47e628379163470eb30c39f6d3554e`
- 代码实施基线：`origin/master@27d5c5c`
- 总体方案：[多引擎视频自动转录](multi-engine-auto-transcription.md)
- 架构决策：[0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)
- 前置计划：[Phase 1](multi-engine-transcription-phase1.md)、[Phase 2](multi-engine-transcription-phase2.md)、[Phase 3](multi-engine-transcription-phase3.md)

> 本计划是 Phase 5 的唯一详细实施基线。它首先补齐当前应用层缺失的“版本查看、审核、发布、候选索引与检索可见性”闭环，再分层验证隔离端到端。批准本计划只授权下文 Phase 5A/5B 的代码、测试、CI 和文档修改；不授权真实 ffmpeg、真实 ASR、GPU、Qdrant 服务、模型下载、生产数据、生产部署、索引重建或灰度。Phase 5C 的真实适配器资格验证必须另行提交环境、数据、资源、停止与回滚清单并再次审批。

## 1. 目标

在不创建自动转录专用检索、引用或播放器协议的前提下，建立以下可恢复闭环：

```text
MP4 + profile_id
→ 受控音频准备
→ remote Provider
→ ProviderCandidate / ProviderFailure
→ 唯一 pipeline + normalizer
→ Canonical Transcript
→ 确定性 Markdown
→ transcript version
→ 人工审核
→ publishing
→ 隔离候选索引
→ SQLite 正式 head 原子切换
→ 只检索当前正式版本
→ 现有引用协议
→ 现有视频播放器 seek
```

Phase 5 必须同时证明：

1. 未发布 Candidate 永远不会被检索；
2. 新 Candidate 的索引失败、发布失败或进程中断不会影响旧正式版本；
3. `media_transcript_heads.current_version_id` 是版本化自动转录的唯一正式可见性事实；
4. legacy 人工 Markdown、普通文档、旧索引与旧会话继续工作；
5. experimental Profile 必须经过实际人工审核，且不会因为转录成功自动发布或自动进入索引；
6. 普通文档索引与转录发布索引共用一个串行 BGE/parents 写入协调器，不并发争用；
7. 本阶段不把真实环境可运行等同于 Profile 资格批准或生产开放。

## 2. 本轮调查与工作区保护

### 2.1 工作区事实

主工作树当前位于 `codex/multi-engine-transcription-phase4@4eb2ee8`，并已有受保护的 `WORKLOG.md` 与用户端前端并行修改。本计划的代码事实来自干净 detached worktree：

```text
${LOCAL_USER_HOME}\AppData\Local\Temp\RAGPinCheng-phase4b-main-4eb2ee8
HEAD = origin/master@b323023aaf47e628379163470eb30c39f6d3554e
```

Phase 5 计划不得覆盖、整理、提交或删除主工作树中与本任务无关的修改。未来实施前必须再次基于最新 `origin/master` 建立干净分支或隔离 worktree；不得直接在当前带并行修改的工作树实施跨模块代码。

### 2.2 调查边界

本轮只读取代码、测试、配置和文档；没有运行测试、Docker Compose、ffmpeg、ASR、GPU、Qdrant、模型、真实媒体或生产数据。

## 3. 当前代码事实与调用链证据

### 3.1 Phase 4B 已接通转录前半段

- `api/routes_admin.py` 的 `POST /api/admin/media` 已区分人工模式与自动模式；自动模式允许只上传 MP4，并只接受服务端白名单 `profile_id` 与幂等键。
- `api/routes_transcription.py` 已提供：
  - `GET /api/admin/transcription/profiles`；
  - `GET /api/admin/transcription/jobs`；
  - `GET /api/admin/transcription/jobs/{job_id}`；
  - `POST /api/admin/transcription/jobs/{job_id}/cancel`；
  - `POST /api/admin/transcription/media/{media_id}/retry`。
- `api/transcription_service.py::create_pending_job()` 已解析 Profile、调用 `FfmpegMediaAudioPreparer`、冻结 execution config/snapshot 并创建应用任务。
- `api/transcription_service.py::run_job()` 已通过 `run_transcription_pipeline()` 形成唯一 Provider Candidate/Failure → normalizer → Canonical 流；随后调用 formatter，写入受控 artifact 与 `transcript_versions`。
- `api/transcription_worker.py` 已有串行队列、取消探针、pending 恢复和 running 重启失败关闭。
- `api/main.py:117-140` 同时启动普通索引 worker 与转录 worker；转录 worker 只在 `ASR_ENABLED` 且 token 存在时运行。

结论：上传、任务、取消、重试、恢复和 Candidate → Canonical → Markdown → version 已有实际应用接线，不应在 Phase 5 重写。

### 3.2 Phase 2 审核与发布内核存在，但应用层未接

- `api/transcription_store.py:385-408` 已实现 `review_version()`，只允许 `awaiting_review → review_approved/review_rejected`。
- `api/transcription_store.py:410-448` 已实现 `begin_publication()`，创建 `transcript_publication_index_jobs`，并固定 `target_index_id=transcript-candidate-{version_id}-a{attempt}`。
- `api/transcription_store.py:450-508` 已校验 index receipt 的 version/hash/target 身份和阶段转换。
- `api/transcription_store.py:509-580` 已在单个 app.sqlite 事务中执行 promotion guard、旧版本 supersede、唯一 head 切换和新版本 published。
- `src/transcription/workflow.py:159-212` 已实现 `begin_publication()`、`run_publication_index()` 与 `promote()` 编排。
- `api/transcription_store.py:715-770` 已识别 `promotion_ready`、`resume_publication_index`、`keep_publication_failed` 等恢复动作。
- 但是 `api/transcription_service.py:52-54,263-265` 仍注入 `_NoPublicationIndex`，任何真实候选索引调用都会以 `publication_not_connected` 失败。
- 当前 API、DTO 和管理端页面没有版本列表、Markdown 审阅、审核、发布或 publication job 查询入口。

结论：总体方案把历史版本、审核和发布列入 Phase 4，但最新 master 实际只完成 Phase 4B 的上传与任务 UI。Phase 5 若不显式补齐这些应用能力，就无法执行总体方案定义的端到端链路。

### 3.3 当前普通索引路径不能作为候选发布适配器

- `src/indexing_pipeline.py:71-100::_purge_existing()` 按 `source_path` 同时删除全局 Qdrant points 与 `parents.sqlite` parents。
- `src/indexing_pipeline.py:274-332::index_single()` 在 chunk 前无条件调用 `_purge_existing()`；源码注释明确承认后续失败会让旧文档从索引消失。
- `src/index.py:207-283::index_children()` 使用全局 `COLLECTION`，Point payload 只有 `parent_id`、文档元数据、`doc_type`、`start_time` 等；没有 `transcript_version_id` 或 candidate target。
- `src/index.py:140-171::store_parents()` 和 parents 表没有版本/候选身份列。
- `src/chunk.py:375,393` 的 transcript Parent/Child ID 基于标题、时间和文本；同一内容的不同 transcript version 可能复用 ID。

结论：不得把 `_NoPublicationIndex` 直接替换为 `index_single()`。这样会在候选构建开始时删除旧正式稿，违反失败隔离和旧正式版本保护。

### 3.4 当前检索不知道正式版本 head

- `src/retrieve.py` 固定查询全局 `COLLECTION`；过滤条件只覆盖 category/code 等业务条件。
- `src/retrieve.py:296-320` 从 child payload/parent row 恢复 `start_time` 和 `media_id`，但不读取 `transcript_version_id`。
- `media_transcript_heads` 位于 app.sqlite；当前检索完全不读取该表。
- 因此 Phase 2 的正式 head 目前只是持久化事实，不能控制真实检索可见性。

结论：Phase 5 必须建立 fail-closed 的版本可见性过滤；仅把 Candidate points 写入同一 collection 而不改检索会直接泄漏未发布内容。

### 3.5 人工 Markdown、引用与播放器协议可复用

- `src/chunk.py::_parse_transcript_turns()` 与 `chunk_transcript()` 已解析 formatter 产出的 speaker/time 格式，并把 `start_time`、`media_id` 贯通 Parent/Child。
- `src/indexing_pipeline.py::_build_transcript_doc()` 与 `index_single(..., media_id=...)` 已支持人工 transcript Markdown。
- `src/retrieve.py::RetrievedParent` 已包含 `doc_type`、`start_time`、`media_id`。
- 前端 `SourcesPanel.tsx`、`SourceWorkspace.tsx`、`VideoPlayerDrawer.tsx` 已使用现有 `media_id + start_time` 协议打开鉴权媒体并 seek。
- `tests/test_transcript_manual_regression.py` 已用人工和自动 formatter fixtures 验证真实 parser/chunker golden。

结论：Phase 5 只能扩展索引身份与可见性，不能创建自动转录专用 citation DTO、播放器路由或时间戳协议。

### 3.6 当前唯一 Profile 仍是 experimental

`src/transcription/profile_catalog.py:20-50` 只登记：

```text
profile_id    = funasr-sensevoice-zh-experimental-v1
qualification = experimental
admission     = enabled
release       = requires_review=true, auto_publish=false, auto_index=false
model_revision= 7bf452403abd7353a300cd760f7adae7701c92c1
```

因此：

- Phase 5A/5B 可以用该 Profile 验证审核门禁、显式发布和失败隔离；
- 总体方案要求的“至少一个 `qualification_approved` Profile 完成真实隔离验证”当前没有可用对象；
- Phase 5C 不能在计划中把 experimental Profile 静默提升为 approved，也不能自行新增 approved Profile。

### 3.7 当前测试与 CI 的能力边界

现有测试已覆盖：

- Phase 1 Canonical/normalizer/formatter/provider/profile；
- Phase 2 Store、migration、artifact、recovery 与 publication transaction；
- Phase 3 remote Provider、service contract、mock engine、scheduler；
- Phase 4 API、应用服务、worker、媒体输入、取消/重试；
- 人工 transcript parser/chunker golden。

`tests/test_transcription_publication_transaction.py` 已证明 fake `PublicationIndexPort` 下的 identity mismatch、失败保持旧 head、experimental review、disabled/deprecated guard 和 transaction rollback，但没有证明真实 Parent/Qdrant/retrieve 可见性。

`.github/workflows/ci.yml` 当前会运行 transcription contract suite、前端 build、ASR service contract 和 provider/GPU contract；没有独立 Phase 5 应用 E2E 或版本可见性 job。

## 4. 已确认的实质缺口

Phase 5 实施前必须把以下内容视为主任务，而不是邻近优化：

1. **应用发布缺口**：没有版本列表、Markdown 审阅、审核、发布和 publication job API/UI。
2. **PublicationIndexPort 缺口**：应用仍使用 `_NoPublicationIndex`。
3. **候选隔离缺口**：`index_single()` 会先删除旧 source，不能用于候选索引。
4. **版本身份缺口**：Parent、Child、Qdrant payload、parents.sqlite 都没有 transcript version/target 身份。
5. **检索可见性缺口**：检索不读取正式 head，Candidate 一旦写入现有 collection 就可能被召回。
6. **索引串行化缺口**：普通索引 worker 与未来 publication worker 不能各自并发调用 BGE 和 parents.sqlite。
7. **资格验证阻塞**：没有 `qualification_approved` Profile，真实 Phase 5C 不能满足生产资格前置标准。

## 5. 推荐架构与依赖方向

### 5.1 分层

```text
管理端 API/UI
    ↓ 只提交 version_id、审核决定或发布命令
TranscriptionPublicationApplicationService
    ↓
TranscriptionPersistenceWorkflow
    ├─ SQLiteTranscriptionStore（review / begin / receipt / promote）
    ├─ ArtifactStore（按 hash 读取 Markdown bytes）
    └─ PublicationIndexPort
           ↓
QdrantTranscriptPublicationIndexAdapter
    ├─ 版本化 transcript chunk
    ├─ parents.sqlite candidate rows
    └─ Qdrant candidate points

Chat/RAG retrieve
    ├─ 普通/legacy 文档：保持现状
    └─ 版本化 transcript：PublishedTranscriptVisibilityPort
           ↓
       app.sqlite media_transcript_heads
```

依赖规则：

- `src/transcription/` 继续拥有领域契约和端口，不依赖 FastAPI、React、Qdrant service 进程或具体 ASR 引擎。
- `api/` 拥有 app.sqlite Schema/写事务、artifact、管理命令、worker 配置和 PublicationIndexPort 的应用适配器。
- `src/chunk.py`、`src/index.py`、`src/indexing_pipeline.py` 只增加可选的版本索引元数据；普通文档和 legacy transcript 调用不传时保持原行为。
- `src/transcription_retrieval_visibility.py` 拥有可见性端口及基于共享 `APP_DB_PATH` 的只读 SQLite adapter；该 Phase 5 模块位于 Phase 1 纯契约核心包之外，只读取并校验 head/version，不执行 Schema 初始化或写事务，也不导入 `api`。`src/retrieve.py` 默认使用该 adapter，并允许测试注入同一端口。
- Provider/normalizer/formatter 不参与审核、发布、Qdrant 或可见性判断。
- 不按 Provider 名称、模型名或 `profile_id` 在索引/检索核心中分支。

### 5.2 唯一正式可见性事实

对版本化自动转录，唯一权威为：

```text
media_transcript_heads.current_version_id
```

不得同时引入第二个可写“published=true”事实作为检索真相。Qdrant/parents 中只保存不可变的 `transcript_version_id` 与 `publication_target_id`；当前是否可见在每次检索开始时由 app.sqlite 正式 head 快照派生。

理由：

- app.sqlite promotion 已能在一个事务中切换唯一 head；
- 如果再异步翻转 Qdrant payload 或 parents 标记，会产生跨数据库双写和不可避免的中间不一致；
- 读取 head 后构造 Qdrant filter，可让 Candidate 预先完成索引但在 promotion 前保持不可见，promotion 事务提交后下一次检索自然切换。

### 5.3 Qdrant/parents 兼容字段

仅为版本化 transcript 增加两个 nullable 字段：

```text
transcript_version_id: UUID | null
publication_target_id: transcript-candidate-{version_id}-a{attempt} | null
```

规则：

- 两者必须同时为空或同时非空；
- 非空时 `doc_type` 必须为 `transcript`；
- `publication_target_id` 中的 version 必须等于 `transcript_version_id`；
- legacy 人工 transcript、普通 Markdown/PDF/Office 文档继续写 null/缺失字段；
- 新 version 的 Parent/Child stable ID 必须把 `transcript_version_id` 纳入 namespace，避免跨版本覆盖；legacy ID 算法不变；
- candidate retry 的 attempt target 可以不同，但相同 version 内容的 Parent/Child ID 保持以 version 为界稳定；重复 attempt 使用 upsert，receipt 仍绑定本次 target；
- 不把 version/target 字段加入 `embed_text`，不改变向量语义与排序。

### 5.4 候选索引写入规则

新增专用 candidate 入口，不复用普通 `index_single()` 的 purge 语义：

1. 由 Store 加载 `TranscriptVersionRecord` 和 publication request；
2. 由 ArtifactStore `load_verified()` 校验 Markdown size/SHA-256；
3. 从受控 `media_assets` 读取标题和 media identity；
4. 构造逻辑 source identity：`docs/教学视频/_media/{media_id}.md`，只用于索引元数据，不要求磁盘存在，不暴露 artifact 绝对路径；
5. 在受控临时目录把已校验 bytes 物化为 UTF-8 Markdown，构造 `ParsedDoc`；
6. 调用新的 `index_transcript_candidate()`：只 chunk/upsert 当前 version，不调用 `_purge_existing()`；
7. status callback 按 `parsing/chunking/embedding/done/failed` 写入现有 publication index job；transcript Markdown 的 parsing 可直接进入 chunking；
8. 成功 receipt 必须原样回显 request 的 version/hash/target；失败 receipt 使用既有结构化错误码，不泄漏路径、token 或底层异常；
9. candidate index 成功后才允许调用 Store `promote()`；失败时旧 head 和旧 version points/parents 均不变。

### 5.5 检索可见性规则

每次 retrieval 开始时读取一个不可变的 published-head snapshot，并把以下逻辑合并到 dense、sparse、code boost 等所有 Qdrant Prefetch：

```text
可见 = 没有 transcript_version_id
    OR transcript_version_id ∈ 当前 published head 集合
```

额外规则：

- 无 head 时，所有版本化 Candidate 隐藏；
- app.sqlite 不可读、head 行损坏或 version/media 不匹配时，对版本化 transcript fail closed，但普通文档和 legacy 无 version transcript 仍可用；
- Parent expansion 后再次校验 parent 的 `transcript_version_id` 属于同一 head snapshot，防止 Qdrant payload 与 parents.sqlite 串线；
- `RetrievedParent` 可内部携带 nullable `transcript_version_id` 用于一致性检查，但不新增前端引用字段；
- category/code filter 必须与可见性 filter 做 AND 合并，不能让任何 recall 分支绕过；
- Candidate points 可能长期保留用于历史版本；清理策略不属于本阶段，不自动删除。

### 5.6 审核与发布应用命令

新增管理员端点：

```text
GET  /api/admin/transcription/media/{media_id}/versions
GET  /api/admin/transcription/versions/{version_id}/markdown
POST /api/admin/transcription/versions/{version_id}/review
POST /api/admin/transcription/versions/{version_id}/publish
GET  /api/admin/transcription/publication-jobs/{index_job_id}
```

契约：

- 全部 read 需要 admin；mutation 还需要 CSRF；普通用户和匿名失败关闭。
- version list 只返回受控 DTO，不返回 `canonical_json` 全文、artifact 相对/绝对路径、Provider token 或底层配置。
- Markdown preview 只返回经 hash 校验的 UTF-8 文本；自动 version 从 artifact 读取，legacy manual version 仅在现有受控 docs 相对路径下读取。
- review body 只允许 `approved: bool` 与可选单行 `review_note`；reviewed_by 来自当前管理员，不接受客户端用户 ID。
- publish body 不允许 `profile_id`、target index、attempt、policy override、模型参数或 `explicit_admin_action`。成功通过 admin+CSRF 的该命令本身即是显式管理员动作。
- publish 是状态幂等：已 `publishing` 返回现有最新 job；已作为当前 head 的 published version 返回当前结果；`publication_failed` 才创建递增 attempt；并发冲突返回 409，不创建双 job。
- experimental version 未实际 `review_approved` 时发布返回 409/422，绝不自动审核。
- index 完成后 worker 调用 `promote()`；API 请求不在 HTTP handler 中同步执行 embedding。

### 5.7 索引 worker 串行化与恢复

不新增与普通索引并行的第二个 BGE worker。最小方案是扩展 `api/indexing.py` 的单队列：

```text
IndexWorkItem = DocumentIndexJob(id: int)
              | TranscriptPublicationJob(id: UUID)
```

- 普通文档继续走现有 `_run_one(document job)`；
- publication item 通过配置的 callback 调用 publication application service；
- 队列 FIFO、单 consumer，不并发写 parents.sqlite 或调用 embedding；
- `api/main.py` 启动 worker 前配置 publication runner；
- 启动恢复把 `resume_publication_index` 重新入同一队列，把 `promotion_ready` 直接尝试 promote；
- 重复入队必须由 persisted status/identity guard 去重；
- shutdown 中断不得写 done receipt，不得 promote；重启后从现有 publication job 恢复；
- ASR 关闭不影响已生成 version 的人工审核和显式发布；publication 依赖的是索引/BGE，不依赖 remote ASR token。

## 6. 分层实施范围

### 6.1 Phase 5A — 无外部资源的应用闭环

在 CI 中使用：

- 临时 app.sqlite；
- 临时 media/artifact/docs/parents 目录；
- fake media audio preparer；
- fake remote Provider；
- fake PublicationIndexPort 或内存 candidate index；
- FastAPI TestClient 与前端 mock API。

验证上传 → 转录 version → preview → 审核 → publishing → fake index → promote 的状态、权限、幂等、取消、恢复和失败关闭。

Phase 5A 不运行 ffmpeg、ASR service、GPU、Qdrant、真实 embedding 或真实媒体。

### 6.2 Phase 5B — 版本感知索引/检索代码与离线契约验证

实现版本元数据、candidate 专用写入、published-head filter 和 Parent 二次校验；测试使用 fake Qdrant client/fake encoder/临时 parents.sqlite，不启动 Qdrant 服务。

验证：

- Candidate 已写入但 promotion 前不可检索；
- promotion 后只新 version 可检索；
- 新 Candidate index/receipt/promotion 任一失败仍检索旧 version；
- legacy transcript 与普通文档仍可检索；
- retry 不产生重复可见版本；
- citation 仍只使用 `doc_type/start_time/media_id`；
- UI 仍通过现有播放器 seek。

### 6.3 Phase 5C — 真实适配器资格验证（不由本计划的一般代码批准自动授权）

未来单独审批后，使用完全隔离资源执行：

- 独立 Qdrant collection；
- 独立 app.sqlite、parents.sqlite、media、artifact、docs 和临时目录；
- 固定非生产测试 MP4；
- 受控 ffmpeg；
- 独立 ASR service 与固定 model revision；
- BGE/GPU 资源观测；
- 固定问题集评估 Recall@1、Recall@5、MRR、no-answer、引用媒体、引用时间与播放器 seek。

Phase 5C 执行前必须明确：目标主机、数据分类、Profile 资格候选、模型/revision、collection 名、目录、GPU 预算、最长运行时间、停止条件、清理与保留策略。没有 `qualification_approved` Profile 时只能形成 experimental 证据，不能声称完成生产资格门禁。

## 7. 精确拟修改文件

以下是未来获批实施的最大允许修改集。若实施调查发现必须新增表、依赖、服务或改动列表外核心模块，必须停止并重新审批。

### 7.1 新增后端文件

| 文件 | 职责 |
|---|---|
| `api/transcription_publication.py` | Publication application service；版本列表/preview/review/publish；实现受控 `PublicationIndexPort` adapter；加载 version/artifact/media 并生成结构化 receipt。 |
| `src/transcription_retrieval_visibility.py` | 在 Phase 1 纯契约核心包之外定义 published-head snapshot/port、严格 UUID 与一致性校验，并实现基于共享 `APP_DB_PATH` 的只读 SQLite adapter；只读 join `media_transcript_heads`/`transcript_versions`，不初始化或写入 app.sqlite。 |

### 7.2 修改后端文件

| 文件 | 修改内容 |
|---|---|
| `src/config.py` | 将 `APP_DB_PATH` 作为共享只读配置入口；默认仍为 `data/app.sqlite`，不新增环境必填项。 |
| `api/db.py` | 复用共享 `APP_DB_PATH`；连接、migration 和事务行为不变。 |
| `src/ingest.py` | `ParsedDoc` 增加 nullable `transcript_version_id`、`publication_target_id`；默认 null，普通 ingest 行为不变。 |
| `src/chunk.py` | Parent/Child 增加相同 nullable 字段；versioned transcript stable ID 纳入 version；legacy/普通文档算法保持字节级兼容。 |
| `src/index.py` | parents.sqlite 添加式增加两列；store/fetch 与 Qdrant payload 传递字段；允许注入测试 client/encoder 的最小 seam，不改变生产默认。 |
| `src/indexing_pipeline.py` | 新增 `index_transcript_candidate()`，不调用 `_purge_existing()`；普通 `index_single()` 保持现状。 |
| `src/retrieve.py` | 所有 recall 分支合并 published-head visibility filter；Parent expansion 二次校验；legacy/普通文档兼容。 |
| `api/transcription_store.py` | 增加按 media 列版本、读取最新 publication job、状态幂等发布辅助查询和恢复所需查询；不改变现有表结构与 promotion 事务。 |
| `api/transcription_service.py` | 删除应用运行时对 `_NoPublicationIndex` 的唯一依赖，或把 version 持久化与 publication service builder 清晰分离；Provider pipeline 不变。 |
| `api/indexing.py` | 单队列支持普通 index job 与 publication job；callback 配置、状态保护、恢复与 shutdown fail-closed。 |
| `api/transcription_worker.py` | 只补充恢复动作向共享 index queue 的交接；不让 ASR worker执行 embedding。 |
| `api/routes_transcription.py` | 新增版本/preview/review/publish/publication job 路由；沿用 admin、CSRF 和错误脱敏。 |
| `api/schemas.py` | 新增严格 version、review、publication DTO；未知字段拒绝；不暴露路径和自由执行配置。 |
| `api/main.py` | 配置 publication runner，并在普通 index worker 启动后恢复 publication work；关闭顺序保持安全。 |

### 7.3 新增或修改前端文件

| 文件 | 修改内容 |
|---|---|
| `frontend/src/components/TranscriptionVersionPanel.tsx`（新增） | 版本历史、Markdown preview、审核决定、显式发布与 publication 状态；不实现任意模型参数或 Markdown 编辑器。 |
| `frontend/src/components/TranscriptionVersionPanel.test.tsx`（新增） | 权限假设、审核/发布按钮门禁、失败与 polling 状态。 |
| `frontend/src/pages/admin/AdminMediaPage.tsx` | 在现有上传/任务区接入版本面板；保留人工上传和 retry/cancel。 |
| `frontend/src/pages/admin/AdminMediaPage.test.tsx` | 补充 version/review/publish 集成回归。 |
| `frontend/src/api/client.ts` | 新增上述 API 方法；CSRF 规则沿用现有 mutation helper。 |
| `frontend/src/api/client.test.ts` | 请求 path/body、未知执行控制不发送、错误映射。 |
| `frontend/src/types.ts` | 新增受控 TranscriptVersion/PublicationJob 类型；不引入 Provider 私有类型。 |
| `frontend/src/hooks/useTranscriptionJobs.ts` | 仅在需要时合并 publication polling；不得创建第二套无限轮询器。 |
| `frontend/src/hooks/useTranscriptionJobs.test.ts` | polling 停止条件、页面卸载、terminal 状态。 |

### 7.4 新增或修改测试/fixture

| 文件 | 职责 |
|---|---|
| `tests/test_transcription_phase5_application_e2e.py`（新增） | 全 fake/临时资源的上传到 promote 闭环。 |
| `tests/test_transcription_publication_index_adapter.py`（新增） | artifact hash、logical source、version IDs、receipt、失败脱敏、无 purge。 |
| `tests/test_transcription_retrieval_visibility.py`（新增） | Candidate 隐藏、head 切换、旧 head 保护、legacy/普通文档兼容、所有 recall 分支过滤。 |
| `tests/test_transcription_phase5_api.py`（新增） | admin/普通用户/匿名/CSRF、DTO、preview、review、publish 幂等和冲突。 |
| `tests/test_transcription_phase5_worker.py`（新增） | 单队列串行、恢复、重复入队、shutdown 和 promotion-ready。 |
| `tests/test_transcript_index_metadata.py`（新增） | Parent/Child/parents/Qdrant payload nullable 字段与跨版本 ID。 |
| `tests/test_transcription_publication_transaction.py` | 保留既有测试并补充应用 adapter receipt 与恢复组合；不删除原 guard 测试。 |
| `tests/test_transcript_manual_regression.py` | 增加 legacy 无 version 字段仍保持 golden 的断言。 |
| `tests/test_transcription_static_boundaries.py` | 禁止 Provider 名称分支、禁止真实 ASR/ffmpeg/GPU/网络依赖进入 Phase 5A/5B 测试。 |
| `tests/fixtures/transcription/phase5/`（新增） | 小型固定 Markdown、版本/head/检索结果 golden；不放真实客户媒体或模型输出。 |

### 7.5 CI 与文档

| 文件 | 修改内容 |
|---|---|
| `.github/workflows/ci.yml` | 增加独立 `test-transcription-phase5` job；只安装已声明依赖，运行 fake/临时资源测试，不启动 Qdrant/ASR/GPU/ffmpeg。 |
| `project-docs/features/transcript-pipeline.md` | 实施完成后更新实际应用发布、versioned index 和检索可见性事实；不得提前写成已实现。 |
| `project-docs/plans/multi-engine-auto-transcription.md` | 仅在实施完成后最小更新阶段状态和链接，不改总体架构。 |
| `TODO.md` | 按实际审批/实施状态最小更新下一步与本计划链接。 |
| `WORKLOG.md` | 按每次实际完成的调查/实施/验证记录；不把未运行的真实 E2E 写成成果。 |

## 8. 明确不修改或不实施

Phase 5A/5B 明确不做：

- 不修改 Canonical Transcript、ProviderCandidate、ProviderFailure、normalizer 或 formatter 既有契约；
- 不接入第二个真实 ASR Provider，不做 faster-whisper retry；
- 不修改 Profile 资格，不新增 `qualification_approved` Profile；
- 不安装、下载、部署或真实运行 ffmpeg、FunASR、faster-whisper、模型、CUDA 或 GPU；
- 不启动或访问真实 Qdrant、BGE service、ASR service、网络或生产服务；
- 不处理真实/客户媒体，不复制生产 app.sqlite、parents.sqlite、collection 或 artifacts；
- 不重建、reset、删除或清理现有索引、版本、媒体或 artifacts；
- 不新增运行时第三方依赖；
- 不修改现有 citation DTO、SSE answer 协议、媒体 Range API 或播放器 seek 协议；
- 不实现 transcript 富文本编辑器、时间轴同步高亮、字幕导出或批量发布；
- 不把 legacy 人工 Markdown 回填为 versions，不迁移现有人工稿 bytes；
- 不改变普通文档 `index_single()` 的 purge 语义；该风险属于独立索引改造，不在本阶段顺带处理；
- 不实施候选/历史索引垃圾回收；
- 不执行生产部署、灰度或 Phase 6。

## 9. 数据与迁移策略

### 9.1 app.sqlite

- 不新增表或列；复用 Phase 2 已存在的 `transcript_versions`、`transcript_publication_index_jobs`、`media_transcript_heads`。
- 只增加 Store 查询/API 应用接线。
- promotion 仍由 `SQLiteTranscriptionStore.promote()` 的单事务负责。

### 9.2 parents.sqlite

`_init_parents_db(reset=False)` 添加式补充：

```sql
ALTER TABLE parents ADD COLUMN transcript_version_id TEXT NULL;
ALTER TABLE parents ADD COLUMN publication_target_id TEXT NULL;
```

- 不 reset、不回填、不删除旧 rows；
- legacy rows 的 null 表示“不受 version head 过滤”；
- 临时数据库测试验证旧 schema 升级与重复启动幂等；
- production migration/真实数据操作不在 Phase 5A/5B 执行范围。

### 9.3 Qdrant

- 不新建生产 collection，不改向量维度、distance 或 sparse 配置；
- payload 增加 nullable 字段，无全量重建要求；
- 真实隔离验证必须使用独立 collection 名；
- 现有 points 缺少字段时按 legacy 可见处理。

## 10. 测试矩阵

| 场景 | 正向断言 | 负向/边界断言 | 层级 |
|---|---|---|---|
| 上传到 version | fake MP4/profile 创建 job，fake Provider 产出 version | 未知 profile、无 CSRF、取消、Provider failure 不产 version | Phase 5A API/E2E |
| version list/preview | 同媒体多个 version 排序稳定，preview hash 一致 | 跨媒体 ID、artifact hash mismatch、路径逃逸 fail closed | API/application |
| experimental review | 实际管理员 approve 后允许 begin publication | awaiting/rejected 不能发布；客户端不能伪造 reviewed_by | API/Store |
| publish 幂等 | publishing 重放返回同 job；failed 生成 attempt+1 | 并发双 publish、自由 target/profile/policy 字段拒绝 | API/application |
| candidate index | version metadata 写入 parents/points，receipt 身份一致 | 调用 `_purge_existing`、hash/target mismatch、私有异常泄漏失败 | adapter/static |
| stable IDs | 同 version 重跑 ID 相同；不同 version ID 不同 | legacy fixture ID/bytes 变化即失败 | chunk golden |
| visibility | promotion 前 Candidate 不可检索；后只新 head 可检索 | 无 head、损坏 head、非 head version 全隐藏 | retrieve |
| old head protection | 新 index/receipt/promote 失败仍检索旧 head | 新 Candidate 不得挤掉或污染旧结果 | E2E/retrieve |
| all recall branches | dense/sparse/code boost/multi-query 均应用同一 filter | 任一 Prefetch 漏 filter 即测试失败 | retrieve/static |
| parent consistency | child version 与 parent/head snapshot 一致才返回 | child/parent version 串线直接丢弃并记录受控告警 | retrieve |
| legacy manual | 无 version 字段的人工 transcript 继续 parser/chunk/retrieve | 不依赖 ASR flag/Profile Registry | regression |
| ordinary docs | PDF/Markdown/Office 缺 version 字段继续可见 | version filter 不改变 category/code 语义 | retrieve regression |
| citations/player | 结果仍带 media_id/start_time，现有 seek 正确 | 不新增自动转录 citation 字段；旧会话缺 media 正常 | backend/frontend |
| index serialization | document/publication FIFO 串行，最大并发 1 | shutdown/duplicate/resume 不双跑、不提前 promote | worker |
| recovery | pending index resume；done-but-not-promoted 恢复 promote | running 中断不写 done；identity mismatch 保持 publishing/failed closed | worker/Store |
| external isolation | suite 在 socket/subprocess/GPU/Qdrant/ASR deny fixture 下通过 | 哨兵自测可捕获禁用调用 | static/runtime |
| frontend | version/review/publish 状态与按钮唯一 | 非 terminal 轮询卸载、错误、409、无权限 | Vitest/build |

## 11. 判定唯一的完成标准

Phase 5A/5B 只有同时满足以下全部条款才可标记“代码完成待验证”：

1. 版本历史、preview、review、publish 和 publication job API 均有 admin/CSRF 测试。
2. 前端能查看自动转录 version 的 Markdown，并明确执行 approve/reject 与 publish；不暴露自由引擎参数。
3. experimental version 未 `review_approved` 时所有 publish 路径均失败。
4. publish HTTP handler 不执行 embedding，只创建/复用 persisted publication job 并入队。
5. 普通 index job 与 publication job 的实测最大并发为 1。
6. 唯一真实 `PublicationIndexPort` adapter 不调用 `_purge_existing()` 或 `index_single()`。
7. Artifact bytes 在 chunk 前按 size/SHA-256 校验；失败不写 done receipt。
8. versioned transcript 的 Parent、Child、parents row 和 point payload 均带相同 version/target identity。
9. 不同 version 的 Parent/Child ID 不碰撞；legacy golden ID/内容不变化。
10. promotion 前 Candidate 即使已写入 candidate index，也无法由任何 recall 分支返回。
11. promotion 提交后下一次检索只返回新 head；旧 version 保持历史但不可见。
12. candidate index、receipt、promotion 或进程恢复任一失败时，旧 head 仍可检索。
13. app.sqlite head 是唯一可写可见性事实；没有第二个 payload `published` 开关。
14. Qdrant filter 与 Parent 二次校验使用同一个不可变 head snapshot。
15. app.sqlite/head 损坏时版本化 transcript fail closed；普通文档和 legacy transcript 不受影响。
16. 现有 `media_id + start_time` citation/player 协议不变，自动稿引用可以使用现有播放器 seek。
17. 人工 MP4 + Markdown 上传、parser/chunker golden 和非 ASR 环境回归通过。
18. 普通 PDF/Markdown/DOCX/XLSX/PPTX 的无 version 索引/检索行为通过相关回归。
19. Phase 5A/5B tests 不访问网络、真实 ffmpeg、subprocess、ASR、GPU、Qdrant service 或真实媒体。
20. 新后端代码与测试不导入 FunASR/faster-whisper/torch/PyAV 等真实引擎依赖。
21. 不新增运行时第三方依赖；lockfile 变化只能来自既有前端依赖的确定性安装，不能夹带新包。
22. 新 parents.sqlite 列为添加式、nullable、幂等；不要求 reset 或回填。
23. CI 新 job 在干净环境零失败、零跳过；现有 transcription/manual/asr_service/provider/frontend jobs 不退化。
24. 功能文档只记录已由代码和测试证明的 Phase 5A/5B 事实；真实 Phase 5C 未运行必须明确标注。

Phase 5 完整隔离验证只有在另行批准并完成 Phase 5C 后才能标记完成；仅完成 1～24 不得声称真实 ffmpeg/ASR/GPU/Qdrant E2E 已验证。

## 12. 验证命令

### 12.1 Phase 5A/5B 实施后本地/CI 可运行

```powershell
python -m compileall -q api src tests
python -m pytest `
  tests/test_transcription_phase5_application_e2e.py `
  tests/test_transcription_publication_index_adapter.py `
  tests/test_transcription_retrieval_visibility.py `
  tests/test_transcription_phase5_api.py `
  tests/test_transcription_phase5_worker.py `
  tests/test_transcript_index_metadata.py -v
python -m pytest tests/test_transcription*.py tests/test_transcript_manual_regression.py -v
python -m pytest tests/test_retrieve.py tests/test_indexing_pipeline.py -v
```

若实际仓库不存在最后两个历史测试文件，实施时不得凭空把命令写入 CI；应以 `rg --files tests` 核验后的真实相关测试名替换，并在 PR 说明。

```powershell
Set-Location frontend
npm ci
npm run test -- --run
npm run build
```

```powershell
git diff --check
git status --short --branch
```

### 12.2 静态边界检查

- AST 扫描 Phase 5A/5B 新代码与测试不导入真实 ASR/媒体/GPU 包；
- 扫描 index/retrieve 核心不存在 Provider/profile 名称分支；
- 扫描 candidate adapter 不调用 `_purge_existing`、`index_single`、collection reset/delete；
- 扫描 API response 不包含 `storage_rel_path`、artifact root、token、model path 或自由 config；
- 扫描所有 Qdrant Prefetch 均合并 visibility filter。

### 12.3 本轮明确不运行

本轮不执行上述测试，也不执行：

```text
ffmpeg / ffprobe
asr_service
FunASR / faster-whisper
nvidia-smi / CUDA / torch
Qdrant service / Docker Compose
真实 embedding/rerank
build_index --reset
生产 app.sqlite / parents.sqlite / collection
真实或客户 MP4
```

## 13. Phase 5C 指标协议（未来单独审批）

固定测试集至少包含：

- 两个不同 transcript version，其中旧版和新版含可区分答案；
- 一个 no-answer 问题；
- 一个命中视频中段的时间引用问题；
- 一个人工 transcript 媒体；
- 一个普通非 transcript 文档；
- 一个失败 ASR/失败 candidate index 场景；
- 一个长音频 checkpoint/恢复场景。

指标定义在执行计划中冻结：

- Recall@1、Recall@5：按期望 parent/version identity；
- MRR：首个正确 published version parent 的倒数排名；
- no-answer：不得由 Candidate 或旧 superseded version 造成伪命中；
- 引用正确性：`media_id`、标题、doc_type 与当前 head 一致；
- 跳播时间：前端计算秒数等于命中 child 的 `start_time`；
- 失败隔离：失败后重复查询仍返回旧 head；
- 资源：BGE health、ASR 串行、GPU 峰值、停止信号和超时；
- 恢复：长音频中断后按既有 checkpoint 语义恢复或结构化失败。

不得修改现有企业 RAG 黄金集来“制造通过”。Phase 5 专用小型隔离集与现有生产黄金集分开保存、分开解释。

## 14. 兼容性与风险

### 14.1 跨库一致性

风险：app.sqlite、parents.sqlite 和 Qdrant 无法组成单一事务。

控制：Candidate 数据先作为不可见版本写入；检索只信 app.sqlite head。promotion 前不可见，promotion 后下一请求切换，不需要跨库翻转第二个 published 标记。

### 14.2 大型 head allowlist

风险：当前正式 version 数增长后，Qdrant `MatchAny` filter 可能变大。

控制：Phase 5 先以正确性为目标并记录 head 数量/过滤构造耗时；不提前引入 per-media collection 或 alias。超过经实测确定的阈值时另立索引可见性优化计划。

### 14.3 legacy 缺字段语义

风险：旧 points 缺 version 字段，错误 filter 可能隐藏全库或误放 Candidate。

控制：明确 `missing version = legacy visible`，`present version = 必须命中 head`；同时对 parents 做二次校验并加入兼容 golden。

### 14.4 synthetic source identity

风险：自动 artifact 不位于 `docs/`，而现有索引 metadata 使用 `source_path`。

控制：使用稳定逻辑 identity `docs/教学视频/_media/{media_id}.md`，实际 bytes 仍从受控 artifact 读取；不把临时路径或 artifact 路径暴露给客户端。后台管理列表/删除对该 identity 的行为必须有测试，不得尝试删除不存在的 docs 文件后误报成功。

### 14.5 worker 竞争

风险：普通 index worker 与 publication index 并行会争用 BGE/GPU 和 parents.sqlite。

控制：统一到现有单 consumer 队列，不创建第二个 embedding worker。

### 14.6 UI 误导

风险：`transcription succeeded` 被管理员误认为已发布。

控制：UI 分开显示转录、审核、发布、候选索引四类状态；成功 version 默认仍是“待审核/未发布”。

### 14.7 experimental 资格误读

风险：隔离 E2E 成功被错误表述为 Profile 已批准生产。

控制：Profile qualification 不由 E2E 自动修改；Phase 5C 报告只能提供资格证据，资格变更需独立审批和 catalog 修改。

## 15. 回滚

代码回滚按层执行：

1. 前端隐藏 version/review/publish 面板，保留人工上传、任务只读、取消和 retry；
2. 保留新增 nullable parents 列，不做破坏性 down migration；
3. 停止 publication item 入队，已生成 version 与 index job 保持只读；
4. retrieval visibility 代码若回滚，必须同时禁止/隐藏所有 versioned Candidate 写入，不能让 Candidate points 在无过滤的旧检索中可见；
5. `ASR_ENABLED=false` 仅停止新自动转录，不删除版本、artifact 或已发布 head；
6. Profile 可单独设为 disabled，不影响 legacy 人工路径；
7. 不自动删除 Qdrant points、parents rows、媒体、Markdown 或历史版本。

关键安全要求：**不能只回滚检索 filter 而保留 Candidate 写入**。安全回滚最小单元是“candidate writer + visibility reader”一起关闭或一起恢复。

## 16. 实施顺序

1. 在最新 master 的干净 `codex/` 分支/worktree 复核状态与测试文件名。
2. 先写 version metadata、parents migration 与 stable ID tests。
3. 实现 candidate 专用 index 入口和 adapter tests，证明无 purge。
4. 实现 published-head visibility port、Qdrant filter 和 Parent 二次校验。
5. 用离线 fake index/retrieve tests 证明 Candidate 隐藏和旧 head 保护。
6. 补 Store 查询、publication application service 和 API DTO/路由。
7. 扩展单 index worker 支持 publication item、恢复和 promotion。
8. 接管理端 version/review/publish UI 与 polling。
9. 完成 Phase 5A fake application E2E、权限、幂等、恢复和人工稿回归。
10. 增加独立 CI job，运行全部后端/前端回归。
11. 更新 feature/TODO/WORKLOG，只记录已验证事实。
12. scoped code review；独立提交/PR；CI 在最新 master 基线验证。
13. 停止。不得自动运行 Phase 5C 或进入 Phase 6。

## 17. 已确认决定

用户已于 2026-08-03 明确批准 Phase 5 R2 代码实施，并同意以下四项决定：

1. Phase 5 吸收 Phase 4 未完成的版本历史、Markdown 审阅、审核和发布 API/UI；
2. 唯一版本可见性方案采用“同一 collection + immutable version payload + `app.sqlite.media_transcript_heads.current_version_id` 读取过滤”；
3. 普通索引与 publication 索引共用现有单 worker/单队列；
4. Phase 5A/5B 代码批准不包含 Phase 5C 的真实环境执行。

上述批准仍不授权真实 ffmpeg、ASR、GPU、Qdrant、生产数据、部署、索引重建或灰度。

## 18. 实施与验证状态

- Phase 5A/5B 已实现版本列表、不可变 Markdown 预览、审核/发布 API 与管理端 UI、publication-only 候选索引、单 worker 串行编排、SQLite 正式 head 和检索可见性过滤。
- Candidate 索引继续写入既有 collection，但携带 immutable `transcript_version_id` / `publication_target_id`；正式可见性只读取 `app.sqlite` head，legacy/普通文档缺少版本字段时保持可见。
- 本地无外部资源验证已通过：Python Store/事务/manual/visibility/index metadata/static 共 29 项；Phase 5 定向前端共 31 项；`compileall` 与 `git diff --check` 通过。
- 本地环境缺少 `qdrant_client`、FastAPI 等既有测试依赖，相关 API、worker、candidate index 与 Qdrant Filter 组合测试交由新增的 `test-transcription-phase5` CI job 在干净环境验证；CI 通过前状态保持“代码完成待验证”。
- 首轮远端 `test-transcription-contracts` 收集 289 项并出现 9 个失败：Phase 5 SQLite visibility adapter 误入 Phase 1 纯契约核心包、获批索引/检索修改后的静态保护哈希未同步，以及 Phase 5 review 测试误用无需审核的 approved Profile fixture。最小修复已将 adapter 移至 `src/transcription_retrieval_visibility.py`、同步获批基线并改用 experimental Profile；标准命令通道暂不可用，修复仍待本地/远端复验。
- Phase 5C 未授权、未执行；未安装或真实运行 ffmpeg/ffprobe、ASR、GPU、Qdrant、embedding/rerank，未访问生产数据，未新增运行时第三方依赖。
