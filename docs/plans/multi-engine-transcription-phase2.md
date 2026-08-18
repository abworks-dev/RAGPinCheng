# 多引擎视频自动转录 Phase 2：任务、版本、发布与恢复详细实施计划

- 状态：**历史 Phase 2 计划；持久化、任务、版本和恢复代码已在后续提交中实施，真实生产数据操作仍需独立审批**
- 风险等级：**R2**
- 编写日期：2026-08-03（Asia/Shanghai）
- 批准日期：2026-08-03（Asia/Shanghai）
- 代码基线：`origin/master@c25c15115a6c4ddab6cdc11f7fdd8348d008b466`
- 前置阶段：Phase 1 已由 PR #1 合并，GitHub Actions `test-transcription-contracts` 为 124 passed、0 skipped
- 上位方案：[多引擎视频自动转录总体方案](multi-engine-auto-transcription.md)
- 前置契约：[Phase 1 详细实施计划](multi-engine-transcription-phase1.md)
- 关联决策：[0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)

> 本文件只规定 Phase 2 的本地持久化、任务/版本/审核/发布状态、兼容迁移、恢复和 publication-only 候选索引逻辑边界。批准本文件不授权真实 `app.sqlite` 迁移、生产部署、Qdrant 写入、API/UI 改造、真实 ASR、音频提取、模型/GPU/网络或真实数据操作。代码实施必须在本文件获明确批准后进行；任何生产迁移或真实索引操作仍须另行 R3 审批。

## 1. 目标

Phase 2 在 Phase 1 引擎无关契约之上建立可恢复、可审计、失败关闭的任务和版本持久化层，使后续 Phase 3～5 可以在不改变 Canonical、Provider 和人工 Markdown 既有边界的情况下：

1. 保存不可变的转录执行身份、Profile/Execution 快照和执行状态；
2. 将成功结果保存为候选 `transcript_version`，而不是覆盖当前正式稿；
3. 将转录成功、人工审核、发布、publication-only 候选索引和当前正式版本分别建模；
4. 在候选索引完成并通过全部 guard 后，用单个 SQLite 事务切换正式版本指针；
5. 在失败、重启、重复请求和并发请求下保持幂等、唯一约束和旧正式版本保护；
6. 通过添加式迁移、迁移前 SQLite backup、临时数据库测试和恢复验证兼容现有 `app.sqlite`；
7. 永久保留现有 MP4 + 人工 Markdown 上传、原始字节保存和索引路径。

Phase 2 交付的是**未接入 API、UI、真实 Provider 和真实 Qdrant 的持久化/工作流内核**。它不产生新的用户可见自动转录能力。

## 2. 明确不包含

本阶段不得新增、修改或执行：

- 管理端 API endpoint、请求/响应 DTO、CSRF 或前端 UI；
- `api/routes_admin.py`、`api/routes_media.py`、`frontend/**`；
- 现有通用索引 worker、队列或重试 API；
- `api/indexing.py`、`src/index.py`、`src/indexing_pipeline.py`、`src/retrieve.py` 的生产逻辑；
- Qdrant collection、payload、过滤条件、真实 embedding 或 `parents.sqlite`；
- FunASR、faster-whisper、FFmpeg、PyAV、torch、CUDA、模型下载或真实推理；
- 音频提取、真实媒体读取、生产机、SSH、网络或外部 API；
- 真实 `data/app.sqlite`、真实 `docs/`、真实 `media/` 或客户数据；
- Phase 3 独立 ASR 服务、Phase 4 API/UI、Phase 5 真实隔离端到端和 Phase 6 灰度；
- 删除、覆盖或自动回填现有人工 Markdown、历史索引、媒体或 retry artifacts；
- 新增第三方运行时依赖或数据库迁移框架。

## 3. 当前代码事实与调用链证据

### 3.1 `app.sqlite` 与迁移

当前 `api/db.py`：

- 使用标准库 `sqlite3`，数据库为 `data/app.sqlite`；
- `connect()` 固定启用 foreign keys、WAL 和 `synchronous=NORMAL`；
- `init_db()` 先执行单个 `SCHEMA`，再通过 `PRAGMA table_info` 和 `ALTER TABLE` 做内联、前向迁移；
- 当前没有迁移登记表、迁移版本、迁移 checksum、迁移前 SQLite backup 或迁移测试；
- 现有表包括 `users`、`auth_sessions`、`conversations`、`messages`、`index_jobs` 和 `media_assets`。

`api/main.py` 在应用 lifespan 启动时调用 `init_db()`。因此未来 Phase 2 迁移一旦接入，部署启动会修改真实数据库；本阶段只实现并在临时 SQLite 上验证，不执行部署。

### 3.2 通用 `index_jobs` 不是 publication index

当前 `api/indexing.py` 的 `index_jobs`：

- 服务 PDF、Markdown、Office 和人工 transcript 的通用文档索引；
- 运行状态实际包含 `pending`、`uploading`、`queued_mineru`、`parsing`、`chunking`、`summarizing`、`embedding`、`done`、`failed` 等；
- 启动恢复会重新入队 `pending`，并把其他非终态任务标记为失败；
- worker 直接调用 `src.indexing_pipeline.index_single()`。

Phase 1 `PublicationIndexStatus` 只有 `pending/parsing/chunking/embedding/done/failed`，且明确是 candidate transcript publication-only 领域。二者状态集合、身份绑定、恢复语义和职责均不相同，Phase 2 **不得扩展或复用现有 `index_jobs` 行来冒充 publication index job**。

### 3.3 当前媒体与人工 Markdown 路径

当前人工媒体调用链为：

```text
POST /api/admin/media
→ _validate_transcript_markdown(bytes)
→ media/<media_id>/original.mp4
→ docs/教学视频/<title>__<media-id-prefix>.md（原始人工 bytes）
→ media_assets(transcript_origin='uploaded', transcript_source_path=绝对路径)
→ index_jobs(media_id)
→ api.indexing worker
→ index_single(..., doc_type='transcript', media_id=...)
→ chunk_transcript
→ parents.sqlite + Qdrant
```

`media_assets.status` 是上传/粗粒度索引状态，不等同于 Phase 1 的 Job、Review、Publication 或 PublicationIndex 状态。`media_assets.transcript_source_path` 当前保存人工稿路径；Phase 2 不更改其含义，不增加触发器，也不自动把既有人工文件转换为 Canonical。

### 3.4 当前索引不支持候选隔离

当前 `src/indexing_pipeline.index_single()`：

- 在 chunk 之前调用 `_purge_existing(source_path)`；
- `_purge_existing` 会先按 `source_path` 删除 Qdrant points 和 `parents.sqlite` rows；
- 若后续 chunk/embedding 失败，旧文档已经从索引删除；
- `index_children()` 的 Qdrant payload 当前没有 `transcript_version_id` 或 publication visibility；
- `src.retrieve` 只组合 category/code filters，不读取正式版本指针；
- `Parent/Child` 虽携带 `media_id`，Qdrant payload 当前未写入 `media_id`，检索结果主要从 `parents.sqlite` 回取。

因此现有 `index_single()` 不能用于候选版本发布，也不能证明旧正式索引保护。Phase 2 只建立独立 `PublicationIndexPort`、持久化 job/receipt 和 target identity，并用 fake adapter 验证逻辑隔离；真实 Qdrant candidate collection/payload/检索切换属于 Phase 5，不能在 Phase 2 声称已验证。

### 3.5 当前备份事实

`scripts/deploy-app.sh` 在部署时使用普通文件复制备份 `app.sqlite` 和 `parents.sqlite`。普通 `cp` 不能单独证明 WAL 数据库快照一致。Phase 2 迁移前备份必须使用 Python `sqlite3.Connection.backup()`，并在临时数据库上执行 `PRAGMA integrity_check` 和恢复验证；不修改或执行生产部署脚本。

### 3.6 当前测试事实

- Phase 1 已有纯 Python transcription 契约测试和 CI job；
- 当前没有 `api/db.py` migration、`transcription_jobs`、`transcript_versions`、发布事务或恢复专项测试；
- Phase 1 CI 命令匹配 `tests/test_transcription*.py`，因此命名符合该模式的 Phase 2 测试可进入现有 job，无需为了 CI 名称新增依赖。

## 4. 模块边界与依赖方向

采用最小端口与适配器分层，不重构现有 API/RAG：

```text
src/transcription/types/profile/canonical/policy   （Phase 1 稳定领域契约）
                    ↑
src/transcription/persistence.py                   （记录类型和 Store/Artifact/Index ports）
                    ↑
src/transcription/workflow.py                      （无数据库的状态/发布编排）
                    ↑
api/transcription_store.py                         （app.sqlite adapter）
api/transcription_artifacts.py                     （受控本地文件 adapter）
                    ↑
api/db_migrations.py + api/db_backup.py            （SQLite 基础设施）
```

依赖规则：

1. Phase 1 `candidate.py`、`provider_protocol.py`、`pipeline.py`、`normalizer.py`、`formatter.py` 不依赖 SQLite/API；
2. `workflow.py` 只依赖 Phase 1 契约和 persistence ports，不导入 `api.*`、Qdrant 或文件系统；
3. SQLite adapter 可以依赖 `src.transcription`，反向依赖禁止；
4. Store port 暴露高层原子操作，不把裸 connection、cursor、SQL 或通用 `transaction()` 泄漏给业务层；
5. Provider、formatter 和 normalizer 不访问 Store；
6. publication index port 不复用 `api.indexing.create_job/enqueue`；
7. API/UI/worker 直到 Phase 3/4 才接线。

## 5. Phase 2 冻结决策

### 5.1 独立 publication index 表

新增 `transcript_publication_index_jobs`，不修改通用 `index_jobs`。每个 publication job 必须绑定：

- `transcript_version_id`；
- `candidate_version_id`（与 version id 完全一致，显式保存用于 guard 回比）；
- Canonical SHA-256；
- Markdown SHA-256；
- `target_index_id`；
- 独立 attempt 和 `PublicationIndexStatus`。

### 5.2 正式版本指针使用独立 head 表

新增 `media_transcript_heads(media_id PRIMARY KEY, current_version_id UNIQUE, updated_at)`，而不是给 `media_assets` 增加循环外键列。优点：

- 迁移纯添加；
- 旧 `media_assets` 行无需回填；
- 没有 head 行时继续采用既有 legacy manual 路径；
- 单个媒体最多一个当前正式版本由主键保证；
- 一个版本不能成为其他媒体的 head，由 `UNIQUE(current_version_id)` 和事务内 media_id 回比保证。

`PublicationStatus.published` 表示该版本曾成功发布；是否**当前正式**只由 head 表决定。旧 published 版本保留历史状态，不新增 `superseded` 枚举，不修改 Phase 1 状态集合。

### 5.3 Canonical 存数据库，Markdown 存受控内容寻址文件

- Automatic version 的 `canonical_json` 保存 Phase 1 完整 Canonical JSON UTF-8 文本，并保存 `canonical_sha256`；加载时重新编码并严格 `CanonicalTranscript.from_json_dict()` 回比；
- Markdown bytes 保存到 `data/transcription_artifacts/markdown/<sha256-prefix>/<sha256>.md`；数据库只存 POSIX 相对路径、大小和 SHA-256；
- 路径由 hash 派生，禁止调用方提交任意绝对路径、`..`、URL 或自由目录；
- 文件使用同目录临时文件、flush/fsync 和原子 replace；目标已存在时必须逐字节 hash/size 相同，否则失败关闭；
- DB 事务失败后允许留下内容寻址、不可见的孤立文件；Phase 2 不自动删除，后续清理须另批；
- Candidate Markdown 不写入 `docs/`，避免被现有批量/通用索引误摄取；
- Manual version 可以引用已验证的 `docs/` 相对路径，`canonical_json/canonical_sha256` 为 null，不做 formatter round-trip。

### 5.4 既有人工稿不自动回填

添加式 migration 不读取或 hash 真实 `docs/`，不为现有 `media_assets` 创建 version/head。没有 head 的旧媒体继续由现有 `transcript_source_path` 和索引行为提供服务。Phase 4 如需把人工稿登记为 version，必须调用独立 `register_manual_version()`，并保持原始 bytes 不变。

### 5.5 Phase 2 只实现逻辑候选索引端口

`PublicationIndexPort` 使用受控 target identity；每次失败后的 retry 都使用新 target，禁止复用可能半写入的候选目标：

```text
transcript-candidate-<canonical-lowercase-version-uuid>-a<attempt_number>
```

Fake adapter 必须证明：candidate target 与 live/old target 身份不同，失败不会返回 done receipt，formatter/artifact 不被读取。Phase 2 不创建真实 collection、不改 Qdrant payload、不更新检索过滤器，也不声称物理索引已原子切换。Phase 5 必须用隔离 collection/SQLite/目录完成真实证明。

### 5.6 无自动 DOWN migration

所有 migration 仅添加表/索引，不删除或重写旧数据。代码回滚时旧版本忽略新增表；若必须恢复数据库文件，只能使用迁移前 backup，并在真实环境另行 R3 审批。

## 6. 运行时记录契约

### 6.1 通用格式

- Job、version、publication job、candidate version 使用 Phase 1 的规范 lowercase UUID 文本；
- SHA-256 必须为 64 位小写十六进制；
- timestamp 使用 UTC Unix epoch 整数秒；bool 不得作为整数；
- JSON 使用 Phase 1 `canonical_json_bytes` 规则，无 BOM、无尾随换行；
- 数据库加载后必须经显式 `from_json_dict()` 重建，不能把任意 JSON string/dict 直接视为领域对象；
- 错误正文不得保存异常 repr、stack、路径、URL、密钥或 Provider 私有对象；`error_summary` 为预先脱敏的单行 UTF-8 文本，最大 1000 字符；
- 所有状态字段使用 Phase 1 枚举 `.value`，数据库 CHECK 与运行时 guard 双重验证。

稳定失败码集合：

- transcription job 可使用 Phase 1 `ProviderErrorCode`，以及 Phase 2 固定的 `worker_restarted`、`invalid_persisted_state`、`artifact_write_failed`；
- publication index 只允许 `index_adapter_failed`、`invalid_index_receipt`、`index_worker_restarted`、`index_integrity_error`；
- 新失败码必须修改本计划/契约和测试，不能把异常类名或自由字符串写入 code。

### 6.2 `TranscriptionJobRecord`

逻辑字段：

- `id`、`media_id`、`created_by`、`attempt_number`；
- `request_idempotency_key`、`execution_identity`；
- `profile_id`、`provider_key`、`profile_definition_version`、`config_hash`；
- 可空、成对出现的 `model_id/model_revision`；Phase 2 fake Profile 为 null，Phase 3 只能从服务端可信 Profile 提取，管理员请求不能填写；
- `profile_snapshot_json`、`execution_config_json`、`execution_fingerprint`；
- `audio_sha256`、`input_kind`、`input_size_bytes`、`total_ms`、`processed_ms`；
- `status`、可空 `stage`；
- 可空 `failure_error_code`、`failure_classification`、`error_summary`；
- 可空 `checkpoint_json`、`result_version_id`、`canonical_sha256`、`draft_markdown_rel_path`、`draft_markdown_sha256`；
- `created_at`、`started_at`、`finished_at`、`updated_at`。

Phase 2 的虚构 input fixture 把 `TranscriptionInputRef.content_sha256` 解释为已提取、待转录输入的 `audio_sha256`；本阶段不提取真实音频。

### 6.3 幂等与 attempt

总体方案中的执行身份冻结为：

```text
execution_identity = SHA-256(canonical JSON of
  media_id, audio_sha256, profile_id,
  profile_definition_version, provider_key,
  model_id, model_revision,
  provider_adapter_version, config_hash,
  execution_fingerprint)
```

但 execution identity **不是历史唯一键**，因为 retry 必须是新 attempt。规则：

- `request_idempotency_key` 是一次创建命令的服务端受控 UUID，数据库全局唯一；重复 key 返回同一 job；
- 同一 execution identity 可以有多个历史 attempt；
- `attempt_number` 在同一 media 内从 1 递增，由 `UNIQUE(media_id, attempt_number)` 保证；
- 同一 media 同时只能有一个 `pending/running` job，由 partial unique index 保证；
- retry 必须创建新 job id、新 request key、新 attempt_number，不能重开旧终态行；
- disabled/deprecated admission 在创建 retry 前由 Phase 1 resolver/guard 判定，Store 不自行放宽。

### 6.4 Checkpoint

Checkpoint 是 Phase 2 自有严格 JSON，不存 Provider 原始状态：

```json
{
  "schema_version": "transcription-checkpoint/1",
  "completed_stage": "validating_input|transcribing|normalizing|formatting",
  "processed_ms": 0,
  "canonical_sha256": null,
  "markdown_sha256": null,
  "result_version_id": null
}
```

- 严格拒绝未知字段；
- processed/total 非负且 processed <= total；
- hash/version 只有在对应阶段完成后允许出现；
- Provider checkpoint、chunk state、模型路径和任意 dict 不进入该结构；
- 真实引擎块级恢复属于 Phase 3。

### 6.5 `TranscriptVersionRecord`

字段：

- `id`、`media_id`、可空 `transcription_job_id`；
- `source = automatic | manual`；
- automatic 必填 `profile_snapshot_json`、`profile_id`、`provider_key`、`config_hash`；可空 `model_id/model_revision` 必须成对出现；manual 这些字段必须为 null；
- automatic 必填 `canonical_json`、`canonical_sha256`；manual 二者必须为 null；
- `markdown_storage_kind = managed_artifact | legacy_manual`；
- `markdown_rel_path`、`markdown_sha256`、`markdown_size_bytes`；
- `review_status`、可空 `reviewed_by/reviewed_at/review_note`；
- `publication_status`、可空 `published_at/supersedes_version_id`；
- `created_at`、`updated_at`。

`review_note` 与 `error_summary` 相同，只允许受控、脱敏、单行、最大 1000 字符；Phase 2 不提供 API 输入。

### 6.6 Artifact refs

新增 `transcript_version_artifacts` 只保存 Phase 1 `ArtifactReference`：

- `(version_id, artifact_id)` 主键；
- 有限 `kind`、SHA-256、size；
- 不保存 raw/debug/path/URL 或 artifact bytes；
- automatic version 可有零到多个；manual version 不接受 Provider artifacts。

## 7. SQLite Schema

### 7.1 Migration ledger

新增：

```sql
CREATE TABLE IF NOT EXISTS app_schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    applied_at  INTEGER NOT NULL
);
```

Phase 2 首个 migration 固定为 `version=1`、`name='multi_engine_transcription_phase2'`。Migration runner 必须按 version 排序，发现重复版本/名称、未知更高版本、缺口或已登记 migration 定义变化时失败关闭。

### 7.2 `transcription_jobs`

新增表和索引至少包含：

```sql
CREATE TABLE transcription_jobs (
    id                         TEXT PRIMARY KEY,
    media_id                   TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
    created_by                 INTEGER REFERENCES users(id) ON DELETE SET NULL,
    attempt_number             INTEGER NOT NULL CHECK (attempt_number > 0),
    request_idempotency_key    TEXT NOT NULL UNIQUE,
    execution_identity         TEXT NOT NULL,
    profile_id                 TEXT NOT NULL,
    provider_key               TEXT NOT NULL,
    model_id                  TEXT,
    model_revision            TEXT,
    profile_definition_version TEXT NOT NULL,
    config_hash                TEXT NOT NULL,
    profile_snapshot_json      TEXT NOT NULL,
    execution_config_json      TEXT NOT NULL,
    execution_fingerprint      TEXT NOT NULL,
    audio_sha256               TEXT NOT NULL,
    input_kind                 TEXT NOT NULL,
    input_size_bytes           INTEGER NOT NULL CHECK (input_size_bytes >= 0),
    total_ms                   INTEGER NOT NULL CHECK (total_ms > 0),
    processed_ms               INTEGER NOT NULL DEFAULT 0 CHECK (processed_ms >= 0 AND processed_ms <= total_ms),
    status                     TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    stage                      TEXT CHECK (stage IS NULL OR stage IN ('validating_input','transcribing','normalizing','formatting')),
    failure_error_code         TEXT,
    failure_classification     TEXT CHECK (failure_classification IS NULL OR failure_classification IN ('transient','permanent')),
    error_summary              TEXT,
    checkpoint_json            TEXT,
    result_version_id          TEXT,
    canonical_sha256           TEXT,
    draft_markdown_rel_path    TEXT,
    draft_markdown_sha256      TEXT,
    created_at                 INTEGER NOT NULL,
    started_at                 INTEGER,
    finished_at                INTEGER,
    updated_at                 INTEGER NOT NULL,
    UNIQUE(media_id, attempt_number),
    CHECK ((model_id IS NULL AND model_revision IS NULL) OR
           (model_id IS NOT NULL AND model_revision IS NOT NULL))
);

CREATE UNIQUE INDEX uq_transcription_jobs_one_active_media
ON transcription_jobs(media_id)
WHERE status IN ('pending','running');
```

`result_version_id` 在创建 `transcript_versions` 后通过应用事务和运行时回比保证引用；SQLite migration 中如采用 FK，必须使用可由临时数据库证明的建表顺序，禁止通过关闭 foreign keys 绕过。

### 7.3 `transcript_versions`

```sql
CREATE TABLE transcript_versions (
    id                       TEXT PRIMARY KEY,
    media_id                 TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
    transcription_job_id     TEXT UNIQUE REFERENCES transcription_jobs(id) ON DELETE RESTRICT,
    source                   TEXT NOT NULL CHECK (source IN ('automatic','manual')),
    profile_id               TEXT,
    provider_key             TEXT,
    model_id                 TEXT,
    model_revision           TEXT,
    config_hash              TEXT,
    profile_snapshot_json    TEXT,
    canonical_json           TEXT,
    canonical_sha256         TEXT,
    markdown_storage_kind    TEXT NOT NULL CHECK (markdown_storage_kind IN ('managed_artifact','legacy_manual')),
    markdown_rel_path        TEXT NOT NULL,
    markdown_sha256          TEXT NOT NULL,
    markdown_size_bytes      INTEGER NOT NULL CHECK (markdown_size_bytes >= 0),
    review_status            TEXT NOT NULL CHECK (review_status IN ('not_required','awaiting_review','review_approved','review_rejected')),
    reviewed_by              INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at              INTEGER,
    review_note              TEXT,
    publication_status       TEXT NOT NULL CHECK (publication_status IN ('not_published','publishing','published','publication_failed')),
    published_at             INTEGER,
    supersedes_version_id    TEXT REFERENCES transcript_versions(id) ON DELETE RESTRICT,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL,
    CHECK ((model_id IS NULL AND model_revision IS NULL) OR
           (model_id IS NOT NULL AND model_revision IS NOT NULL))
);
```

SQLite CHECK 无法完整表达 automatic/manual 跨字段条件；Store 在写入和加载时必须再次验证，并由负面测试覆盖直接 SQL 污染后的拒绝。

### 7.4 `transcript_version_artifacts`

```sql
CREATE TABLE transcript_version_artifacts (
    version_id      TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
    artifact_id     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL CHECK (size_bytes >= 0),
    PRIMARY KEY(version_id, artifact_id)
);
```

### 7.5 `transcript_publication_index_jobs`

```sql
CREATE TABLE transcript_publication_index_jobs (
    id                     TEXT PRIMARY KEY,
    transcript_version_id  TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
    candidate_version_id   TEXT NOT NULL,
    attempt_number         INTEGER NOT NULL CHECK (attempt_number > 0),
    canonical_sha256       TEXT,
    markdown_sha256        TEXT NOT NULL,
    target_index_id        TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN ('pending','parsing','chunking','embedding','done','failed')),
    error_code             TEXT,
    error_summary          TEXT,
    created_at             INTEGER NOT NULL,
    started_at             INTEGER,
    finished_at            INTEGER,
    updated_at             INTEGER NOT NULL,
    UNIQUE(transcript_version_id, attempt_number),
    UNIQUE(target_index_id)
);

CREATE UNIQUE INDEX uq_transcript_publication_index_one_active
ON transcript_publication_index_jobs(transcript_version_id)
WHERE status IN ('pending','parsing','chunking','embedding');
```

Automatic version 必须有 canonical hash；manual version 的 publication job 允许 canonical hash 为 null，但 Markdown hash 必填。

### 7.6 `media_transcript_heads`

```sql
CREATE TABLE media_transcript_heads (
    media_id           TEXT PRIMARY KEY REFERENCES media_assets(media_id) ON DELETE RESTRICT,
    current_version_id TEXT NOT NULL UNIQUE REFERENCES transcript_versions(id) ON DELETE RESTRICT,
    updated_at         INTEGER NOT NULL
);
```

Store 加载 head 时必须验证 version.media_id 与 head.media_id 相等；发现交叉媒体污染立即失败关闭。

## 8. 状态转换和原子操作

### 8.1 Job

允许转换：

```text
pending → running(validating_input)
running(stage A) → running(stage A 或下一固定 stage)
running → succeeded | failed | cancelled
pending → cancelled
```

禁止：

- 终态回到 pending/running；
- 跳过或倒退 stage；
- pending 带 started_at/stage；
- succeeded 没有 result_version/hash/path；
- failed 没有稳定 failure code/classification；
- cancelled 伪装成 Provider failure；
- job succeeded 自动改变 review/publication/index/head。

状态更新使用 `UPDATE ... WHERE id=? AND status=? AND updated_at=?` 或等价 compare-and-swap；受影响行数不是 1 时报告并发冲突，不静默覆盖。

### 8.2 成功结果事务

`record_transcription_success()` 顺序：

1. 严格验证 input、execution、snapshot、Canonical 和 Markdown bytes/hash；
2. 确认 job 正处于 `running/formatting` 且 execution identity 一致；
3. 先把 Markdown 写入内容寻址 artifact store；
4. `BEGIN IMMEDIATE`；
5. 插入一个 automatic candidate version 和 artifact refs；
6. experimental/requires_review 初始化为 `awaiting_review`，否则为 `not_required`；
7. version publication 初始化为 `not_published`；
8. job 写入 result_version/hash/path 并变为 `succeeded`；
9. commit。

任何失败不得留下 succeeded job 或半个 version；同一 job 最多一个 version，由 `UNIQUE(transcription_job_id)` 保证。内容寻址 orphan 不可见且不自动删除。

### 8.3 失败结果事务

`record_transcription_failure()`：

- 只接受严格 `ProviderFailure` 或 Phase 2 自有稳定失败码；
- job 变为 failed，保存 classification 和脱敏摘要；
- 不创建 version、Markdown 或 index job；
- retry 另建新 attempt。

### 8.4 Review

- automatic requires_review → `awaiting_review`；不需审核 → `not_required`；
- manual version 的初始状态在注册命令中显式传入，但只能是 `not_required` 或 `awaiting_review`，不能伪造 approved；
- `awaiting_review → review_approved | review_rejected`；
- 终态 review 不原位反转；修改稿创建新 version；
- reviewer 必须是受控 user id；Phase 2 只持久化，不实现管理员鉴权 API；
- `review_gate_satisfied` 继续使用 Phase 1 定义。

### 8.5 Begin publication

`begin_publication()`：

1. 加载 version、job snapshot 和调用方提供的当前 Profile；
2. 计算 Phase 1 `effective_release_policy`；
3. 检查 review gate、deprecated explicit admin 和 disabled；
4. version `not_published/publication_failed → publishing`；
5. 创建新的 publication index attempt，绑定 version/hash/target；
6. 不修改 head，不访问 Qdrant。

### 8.6 Publication index receipt

Port 返回的 receipt 必须严格回比：

- index job id；
- candidate/transcript version id；
- canonical/Markdown SHA-256；
- target index id；
- status；
- receipt schema version。

不匹配、未知字段、done 但 hash 缺失、失败 receipt 伪装 done 均拒绝。index failed 只把 index job 标为 failed，并把候选 version 标为 publication_failed；head 和旧 published version不变。

### 8.7 Promote 事务

`promote_published_version()` 必须是单个 `BEGIN IMMEDIATE` SQLite 事务：

1. 重新读取当前 version、review、publication、index job 和 head；
2. 重新运行 Phase 1 `promote_allowed`；
3. 确认 publication=`publishing`、index=`done`；
4. 逐字节回比 candidate id、Canonical/Markdown hash 和 target identity；
5. 读取旧 head；
6. 将新 version 的 `supersedes_version_id` 设为旧 head（如有）；
7. insert/update `media_transcript_heads`；
8. 将新 version 标为 `published` 并写 `published_at`；
9. commit。

事务失败、进程在 commit 前退出、CAS 冲突、disabled、hash/target 不匹配时 head 不变。Phase 2 测试证明的是 SQLite 指针原子性和逻辑 target 隔离；不声称 Qdrant 与 SQLite 存在分布式原子事务。

## 9. Recovery 语义

Phase 2 实现可单测的 `audit_and_recover_transcription_state()`，但不接入 `api.main` 启动：

| 状态 | 恢复动作 |
| --- | --- |
| pending job | 保持 pending，返回 `resume_pending` action |
| running job | 标记 failed=`worker_restarted`，保留 checkpoint，未来 retry 新建 attempt |
| succeeded job + version | 保持，不推进审核/发布 |
| running job 但已存在 result version | 视为数据库污染并失败关闭，不猜测 succeeded |
| publishing + active index | 保持 publishing，返回 `resume_publication_index` action |
| publishing + index done + head 未切换 | 返回 `promotion_ready`，不自动 promote；必须重新提供当前 Profile/管理员语义 |
| publication_failed | 保持，允许未来显式新 index attempt |
| head 指向不存在/跨媒体/non-published version | 完整性错误，禁止自动修复 |

恢复 action 是严格枚举/record，不执行 Provider、网络、Qdrant、文件删除或真实 worker。

## 10. Migration、backup 与恢复

### 10.1 Migration runner

- 新增 `api/db_migrations.py`，使用标准库；
- `api/db.py:init_db()` 首先以只读 schema inventory 判断现有 base/legacy/Phase 2 DDL 是否有任一 pending；不得为了判断 pending 先创建 migration ledger 或执行 `SCHEMA`；
- 现有数据库有 pending DDL 时，必须先完成并验证 backup，随后才打开写事务执行 base SCHEMA、legacy `media_id` 补列和 Phase 2 runner；新空库无需 backup；
- runner 可显式接收 temp db path/connection，测试不得 monkeypatch真实 `APP_DB_PATH`；
- 每个 migration 使用 `BEGIN IMMEDIATE`，逐条 `execute`，禁止用会隐式 commit 的不透明脚本破坏事务；
- migration 前后分别运行 `PRAGMA foreign_key_check`，完成后运行 `PRAGMA integrity_check`；
- 已应用 migration 重跑为 no-op；部分失败 rollback，不登记版本；
- 发现数据库登记了当前代码未知的更高 migration，应用启动失败关闭。

### 10.2 Migration 前 backup

- 新增 `api/db_backup.py`；
- 只有存在任一 pending base/legacy/Phase 2 migration 且源数据库已存在时创建 backup；新空库不备份；
- 使用只读源 connection 的 `backup()` API，不用普通 `copy`；
- backup 名称包含 UTC timestamp、旧 schema version 和随机 UUID，避免覆盖；
- backup 完成后打开目标运行 `integrity_check`，失败则删除**本次未发布的临时 backup**并停止 migration；
- final backup 文件使用原子 rename；已发布 backup 不自动删除；
- tests 只操作 `tmp_path`；本阶段不对真实 `data/` 执行。

### 10.3 恢复

Phase 2 提供并测试 `verify_backup(path)` 和临时数据库恢复流程，但不提供自动 restore、不替换运行中的 app.sqlite。真实恢复步骤必须停服务、保留失败库、验证 backup、原子替换并另行 R3 审批。

## 11. 人工 Markdown 兼容边界

必须持续满足：

- `api.routes_admin._validate_transcript_markdown` 未修改；
- `_classify_doc_type(..., '教学视频')` 行为未修改；
- MP4 + Markdown 原始 bytes 仍按当前路径落盘；
- 当前 `media_assets`/`index_jobs`/`api.indexing` 路径未接入新 Store；
- 人工稿不依赖 Profile Registry、Provider、Canonical 或 `ASR_ENABLED`；
- 人工稿不注册 `ManualTranscriptProvider`；
- legacy media 无 head 时保持当前读取/播放/检索行为；
- `register_manual_version()` 只为未来 API 提供未接线能力，接受受控相对路径和预计算 hash，不读取/改写文件正文；
- Phase 2 不 backfill、不 reindex、不改现有人工稿状态。

## 12. 拟新增和修改文件

### 12.1 本次计划编写

只新增：

```text
docs/plans/multi-engine-transcription-phase2.md
```

并按协作规则保留结构化交付摘要。本轮不修改任何代码、TODO、数据库或 CI。

### 12.2 未来获批代码实施范围

拟新增：

```text
api/db_backup.py
api/db_migrations.py
api/transcription_artifacts.py
api/transcription_store.py

src/transcription/persistence.py
src/transcription/workflow.py

tests/fixtures/transcription/phase2-legacy-app-schema.sql
tests/test_transcription_phase2_types.py
tests/test_transcription_db_migrations.py
tests/test_transcription_artifacts.py
tests/test_transcription_store.py
tests/test_transcription_workflow_persistence.py
tests/test_transcription_publication_transaction.py
tests/test_transcription_recovery.py
tests/test_transcription_phase2_manual_regression.py
tests/test_transcription_phase2_static_boundaries.py
```

拟修改：

```text
api/db.py
src/transcription/__init__.py
tests/test_transcription_static_boundaries.py
tests/transcription_fixture_helpers.py
docs/features/transcript-pipeline.md
TODO.md
任务最终回复与 workflow artifact
```

实施时确认 Phase 1 static DAG/fixture 扫描会主动收集所有新增 `src/transcription/*.py` 和 transcription fixtures，因此必须最小扩展这两个既有测试文件的 Phase 2 模块白名单、临时 SQLite fixture 和 `.sql` fixture 类型；不降低 Phase 1 的 Provider、真实依赖、人工路径或 protected-path 禁止规则。

`.github/workflows/ci.yml` 仅在现有 `test-transcription-contracts` 未能自动收集 Phase 2 测试时最小修改；当前 glob 已覆盖，默认不修改。

### 12.3 明确保护、不修改

```text
api/main.py
api/indexing.py
api/routes_admin.py
api/routes_media.py
api/schemas.py
frontend/**
src/chunk.py
src/index.py
src/indexing_pipeline.py
src/retrieve.py
src/generate.py
data/**
docs/**
media/**
requirements.txt
requirements-prod.txt
docker/**
scripts/deploy-app.sh
```

若实施调查证明必须修改任一保护路径、真实索引 Schema、API/UI 或依赖，属于范围实质变化，停止并重新审批。

## 13. 测试计划

### 13.1 类型和严格加载

`test_transcription_phase2_types.py`：

- Record、checkpoint、recovery action、index request/receipt 正向 round-trip；
- 根和所有嵌套对象额外字段拒绝；
- UUID/SHA/status/timestamp/bool/Path/Enum/dataclass/bytes/tuple 等负面；
- automatic/manual 跨字段不变量；
- error/review text 单行、长度和脱敏边界。

### 13.2 Migration 与 backup

`test_transcription_db_migrations.py` 使用 `tmp_path`：

- 空库初始化；
- 当前 legacy schema fixture → Phase 2；
- 重复 migration no-op；
- 中途注入失败后无半表/无 migration row；
- 未知未来 migration、版本缺口、重复定义失败；
- foreign key/integrity check；
- SQLite backup API、backup integrity、从 backup 恢复到另一临时路径；
- 不访问真实 `APP_DB_PATH`。

### 13.3 Artifact store

`test_transcription_artifacts.py`：

- LF UTF-8 Markdown bytes 写入内容寻址路径；
- 重复相同 bytes 幂等；
- 同 hash 目标内容不一致失败；
- 路径穿越、绝对路径、URL、CRLF/BOM 契约按调用方 bytes 精确处理；
- 原子临时文件失败不发布 final；
- 不删除已发布 artifact。

### 13.4 Store 与约束

`test_transcription_store.py`：

- job 创建、重复 request key 返回同一行；
- 同 media 并发 active job 只有一个成功；
- retry 新 attempt；
- 非法状态转换和 CAS 冲突；
- 严格 snapshot/execution/canonical 加载；
- 成功 transaction 只生成一个 version；
- 失败 transaction 不生成 version；
- 直接 SQL 污染被 Store 加载拒绝；
- foreign key、RESTRICT 和 head media 回比。

### 13.5 Workflow、审核和发布

`test_transcription_workflow_persistence.py` 与 `test_transcription_publication_transaction.py`：

- experimental、qualification-approved、deprecated、disabled 矩阵；
- succeeded 不推进 review/publication/index/head；
- review approved/not-required 门禁；
- rejected 不能发布；
- publication index 独立表和 target identity；
- pending/parsing/chunking/embedding/failed 不能 promote；
- done receipt 的 candidate/hash/target 任一不匹配不能 promote；
- publish transaction 前/中/commit 前注入失败，旧 head 不变；
- 成功 promote 只切换一个 head，记录 supersedes；
- 重复 promote 幂等或明确冲突，不产生第二 head；
- 旧 published version 保留只读历史。

### 13.6 Recovery

`test_transcription_recovery.py`：

- pending/running/succeeded/failed/cancelled；
- checkpoint 合法/非法；
- running 重启失败关闭；
- publishing active/done/failed；
- promotion-ready 不自动 promote；
- head 污染不自动修复；
- recovery 不调用 Provider/Qdrant/network/subprocess。

### 13.7 人工回归与静态边界

`test_transcription_phase2_manual_regression.py` 复用 Phase 1 人工 fixture，真实调用 parser/chunker，并确认人工路径文件 hash/diff 未改变。

`test_transcription_phase2_static_boundaries.py` 检查：

- 模块 DAG；
- core 不导入 `api`/sqlite/Qdrant；
- Store/backup/migration 不导入真实 ASR/媒体依赖；
- Phase 2 不调用网络、GPU、subprocess、真实媒体或真实数据库；
- protected paths 不在修改集；
- publication store 不引用通用 `index_jobs`/`api.indexing`；
- 不出现 `ManualTranscriptProvider`；
- tests/fixture helper 也纳入禁止导入扫描。

## 14. 完成标准与验收映射

以下 24 项全部满足才可标记 Phase 2 代码完成：

1. Phase 1 core 保持无 SQLite/API/Qdrant 依赖，新增模块 DAG 无环。
2. `app_schema_migrations` 有序、幂等、失败回滚、未来版本失败关闭。
3. migration 前 SQLite backup 使用 `Connection.backup()`，backup 可通过 integrity/restore 测试。
4. `transcription_jobs` 保存完整执行身份、快照、进度、状态、checkpoint、结果引用和时间。
5. 同 media 只有一个 active job，数据库并发测试证明约束生效。
6. 重复 request idempotency key 返回同一 job；retry 创建新 id/key/attempt。
7. Job、Stage、Review、Publication、PublicationIndex 继续分型，不复用通用 index job 状态。
8. checkpoint 严格、版本化、不泄漏 Provider 原始状态。
9. job 状态/stage 只按固定转换，终态不可重开。
10. 成功结果在单事务中产生一个 automatic candidate version 并把 job 标为 succeeded。
11. ProviderFailure 不生成 version/Markdown/index job，失败摘要脱敏。
12. Canonical JSON bytes/hash 和 Markdown bytes/path/hash 加载时重新校验。
13. Managed Markdown 内容寻址、原子发布、路径安全且不写 `docs/`。
14. Automatic/manual version 跨字段规则严格；manual 不伪装 Provider/Canonical。
15. 既有人工媒体不自动 backfill，无 head 时 legacy 路径不退化。
16. `transcript_publication_index_jobs` 与通用 `index_jobs` 完全分离。
17. index request/receipt 绑定 candidate/hash/target，任一不匹配失败关闭。
18. index done 前永远不能 promote。
19. promote 使用单个 `BEGIN IMMEDIATE` 事务切换 head 并记录 supersedes。
20. publish/index/事务失败时旧 head 和旧 published version 保持不变。
21. experimental/deprecated/disabled 和 effective policy 继续使用 Phase 1 guard。
22. recovery 对中断状态给出唯一 action，不自动调用 Provider、Qdrant 或 promote。
23. tests 只使用 temp SQLite、temp directories 和 fake index port，不访问真实资源。
24. CI 收集全部 Phase 2 测试，无新增运行时依赖，protected paths diff 为空。

主要映射：

| 完成标准 | 主要测试 |
| --- | --- |
| 1、7、16、24 | `test_transcription_phase2_static_boundaries.py` |
| 2、3 | `test_transcription_db_migrations.py` |
| 4～6、9～12 | `test_transcription_store.py`、`test_transcription_workflow_persistence.py` |
| 8、12、14 | `test_transcription_phase2_types.py` |
| 13 | `test_transcription_artifacts.py` |
| 15 | `test_transcription_phase2_manual_regression.py` |
| 17～21 | `test_transcription_publication_transaction.py` |
| 22 | `test_transcription_recovery.py` |
| 23 | 所有 Phase 2 tests + deny-I/O static fixture |

## 15. 实施顺序

代码实施另获批准后：

1. 建立 Phase 2 static boundary 和 legacy schema fixture；
2. 实现 persistence records、checkpoint、index request/receipt 和 ports；
3. 实现 SQLite backup 与 migration runner；
4. 添加 Phase 2 tables/indexes，完成空库/legacy/retry/rollback 测试；
5. 实现 SQLite Store 的 job/idempotency/attempt/CAS；
6. 实现内容寻址 artifact store；
7. 实现成功/失败 version 事务；
8. 实现 review/begin-publication/index receipt；
9. 实现 atomic promote 和失败注入测试；
10. 实现 recovery audit/actions；
11. 补齐人工 Markdown 回归和 protected path 检查；
12. 更新 feature/TODO，并在 PR/workflow 中保留验证证据；
13. 运行全部 Phase 1+2 tests、migration/backup tests、compile/diff/static checks；
14. 停止，等待 Phase 3 独立审批，不接 API/UI/真实引擎。

## 16. 验证命令

实施后至少运行：

```text
python -m pytest tests/test_transcription_phase2_types.py -v
python -m pytest tests/test_transcription_db_migrations.py -v
python -m pytest tests/test_transcription_artifacts.py -v
python -m pytest tests/test_transcription_store.py -v
python -m pytest tests/test_transcription_workflow_persistence.py -v
python -m pytest tests/test_transcription_publication_transaction.py -v
python -m pytest tests/test_transcription_recovery.py -v
python -m pytest tests/test_transcription_phase2_manual_regression.py -v
python -m pytest tests/test_transcription_phase2_static_boundaries.py -v
python -m pytest tests/test_transcription*.py tests/test_transcript_manual_regression.py -v
python -m compileall -q src/transcription api/db.py api/db_backup.py api/db_migrations.py api/transcription_store.py api/transcription_artifacts.py
git diff --check
git status --short --branch
```

额外自动检查：

- 所有 SQLite tests 的 path 均位于 pytest `tmp_path`；
- 测试结束后真实 `data/app.sqlite` 的存在性、mtime、size 和 hash（若存在）不变；
- `PRAGMA foreign_key_check` 无行，`PRAGMA integrity_check` 精确为 `ok`；
- protected paths diff 为空；
- 无 Qdrant/network/subprocess/ASR import/call；
- migration backup/restore 只作用于临时路径；
- Phase 1 124 项基线不退化，新增 Phase 2 tests 无 skip。

## 17. 风险、兼容性与回滚

### 17.1 迁移风险

`init_db()` 在应用启动时执行，错误 migration 会阻止服务启动。缓解：migration 前 backup、单事务、legacy fixture、重复启动和失败注入测试。真实部署仍须独立审批。

### 17.2 SQLite 与文件系统非原子

SQLite 不能与 Markdown 文件写入形成单个事务。采用内容寻址先写文件、再提交 DB；DB 失败只可能留下不可见孤立内容，不会产生成功 job/version。Phase 2 不自动清理。

### 17.3 SQLite 与 Qdrant 非原子

Phase 2 不实现真实 Qdrant，因此只证明 SQLite head 和逻辑 candidate target。真实 candidate/live 隔离、检索过滤和物理 promote 必须在 Phase 5 证明；禁止把 fake adapter 结果描述为生产索引原子性。

### 17.4 当前索引是破坏性替换

现有 `_purge_existing(source_path)` 会先删旧索引。Phase 2 明确不复用该路径。若后续要求直接接现有 worker，必须重新设计并重新审批。

### 17.5 人工稿兼容

自动 backfill 会读取真实文件并改变版本语义，因此本阶段禁止。旧媒体无 head 继续走 legacy 路径；未来显式导入不能改写原始 bytes。

### 17.6 回滚

代码回滚：恢复 `api/db.py`，删除未来新增 Phase 2 模块/测试入口；旧代码忽略新增表。

数据库回滚：默认不执行 DOWN、不删除新增表/行。若 migration 尚未部署，无数据库回滚。若已部署并必须恢复，停服务后使用迁移前 backup，另行 R3 审批并保留失败库。

文件回滚：managed artifacts 默认保留，不自动删除。任何清理须列出精确 hash/path 并单独审批。

## 18. Blocker 与停止条件

出现以下任一情况，停止实施并提交计划修订：

- 必须修改 API/UI、现有索引 worker、Qdrant/retrieval、`src/chunk.py` 或运行依赖；
- SQLite 无法以添加式 migration 表达 Schema 或 foreign keys；
- legacy migration 必须读取/修改真实人工文件；
- migration 无法在 backup 失败时阻止写入；
- Store 无法在单事务中保证 version/job 或 head/publication 一致；
- 需要新增 Phase 1 状态枚举或改变 Canonical/formatter bytes；
- 测试必须访问真实 `app.sqlite`、Qdrant、网络或生产；
- 现有人工 Markdown 回归失败；
- protected path 出现非零 diff；
- CI 需要新增第三方依赖才能运行。

## 19. 历史冻结选择（当前实现以源码和 feature 文档为准）

批准本计划即表示接受以下五项：

1. publication index 使用独立 `transcript_publication_index_jobs`，不扩展通用 `index_jobs`；
2. 当前正式版本使用独立 `media_transcript_heads`，不改 `media_assets` 指针列；
3. Canonical 存 SQLite 严格 JSON，automatic Markdown 存 `data/transcription_artifacts` 内容寻址文件；
4. 不自动 backfill 既有人工稿，legacy media 无 head 时继续当前路径；
5. Phase 2 只实现 fake `PublicationIndexPort` 和 SQLite 原子 head；真实 Qdrant candidate/live 隔离与检索验证留到 Phase 5。

若任一项不接受，需先修改本计划，不能在实施时临场另选。

## 20. 审批门禁

用户已于 2026-08-03 明确批准 Phase 2 R2 代码实施。当前实现已完成本地临时 SQLite、文件、事务、人工回归和静态边界验证；远端 CI 仍须在完整既有依赖环境复跑后才能把 Phase 2 标记为验证完成。

本次批准仍不授权：

- 真实数据库迁移或生产部署；
- API/UI/worker/Qdrant 接线；
- 真实 ASR、媒体、模型、GPU 或网络；
- 删除/恢复数据或 artifacts；
- Phase 3～Phase 6。
