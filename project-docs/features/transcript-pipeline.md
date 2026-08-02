# 视频转录链路

- 状态：人工上传/播放链路已实现；多引擎 Phase 2 持久化内核代码完成待远端 CI，尚未接线或部署
- 最后核对：2026-08-03

## 用户可观察能力

教学视频转录稿可以被索引和检索，回答能够显示带时间戳的视频引用，点击引用定位到来源卡片并打开视频播放器。管理员可上传 MP4+Markdown 转录稿对，系统自动绑定、索引并提供认证播放。

Phase 2 没有改变上述用户可观察行为。当前新增的是未接 API/UI、真实 Provider、worker 或 Qdrant 的任务、版本、审核、发布与恢复内核。

## 当前边界

### 已实现

- `教学视频/` 分类下 `.md` 被识别为 `doc_type=”transcript”`；
- 按”说话人 + HH:MM:SS”解析发言段落；
- Parent 和 Child 携带 `start_time` 和 `media_id`；
- `doc_type`、`start_time`、`media_id` 经检索、生成、`SourceDTO` 和前端完整传递；
- 前端显示 `🎬 @HH:MM:SS` 并能定位来源卡片；
- 后台管理端上传 MP4 + Markdown 转录稿：校验文件格式、大小、编码和时间戳标记；
- 视频文件落盘到 `media/<media_id>/original.mp4`，转录稿落盘到 `docs/教学视频/`；
- 自动建立 `media_assets` 登记、索引入队、索引完成后状态自动更新为 `ready`；
- 后端鉴权 HTTP Range 播放（支持无 Range、普通 Range、开放式 Range、后缀 Range）；
- 前端单实例播放器抽屉（桌面右侧、移动端底部弹层），支持 metadata seek 和自动播放降级；
- 引用角标点击自动打开视频播放器并跳转对应时间点；
- 来源卡片显示”从 HH:MM:SS 播放”按钮；
- 旧会话和未关联转录正常降级（无播放按钮，不报错）；
- 管理端”视频媒体”标签页展示上传表单和资产列表（含状态、大小、错误信息）。

### 未实现（第二阶段）

- 视频字幕轨道生成与同步；

### 已实现但未接线：多引擎转录 Phase 2 内核

- 添加式 SQLite migration ledger，以及 migration 前 `sqlite3.Connection.backup()`、完整性验证和只恢复到新路径的工具；
- 独立 `transcription_jobs`、`transcript_versions`、artifact refs、publication-only index jobs 和正式版本 head；
- 幂等 request key、递增 attempt、同媒体单 active job、严格 checkpoint 和稳定失败码；
- Canonical JSON 严格加载与 hash 回比，自动 Markdown 使用受控内容寻址文件；
- 审核、候选索引 receipt、单个 `BEGIN IMMEDIATE` head promote、supersedes 和恢复 action；
- fake `PublicationIndexPort` 只验证逻辑隔离，不写真实 Qdrant；
- legacy 人工稿不 backfill，无 head 时继续现有路径。

这些模块尚未接入新 endpoint、管理员 UI、真实 Provider 或索引 worker，也没有在真实 `data/app.sqlite` 上执行迁移。

## 入口与调用链

```text
管理端上传 MP4 + .md 或文件系统
→ POST /api/admin/media (校验、落盘、登记)
→ media_assets (app.sqlite) + docs/教学视频/ + media/<media_id>/
→ index_jobs (media_id 关联)
→ _build_transcript_doc(media_id)
→ chunk_transcript
→ Parent/Child(doc_type, start_time, media_id)
→ parents.sqlite (media_id 列) + Qdrant
→ RetrievedParent(media_id)
→ <source media_id=... time=... type="transcript">
→ SourceDTO(media_id)
→ 视频引用角标 (点击 → 播放器 seek)
→ 来源卡片 (播放按钮)
→ GET /api/media/{media_id} (鉴权 Range 播放)
```

Phase 2 未接线的内部结果流：

```text
Phase 1 Canonical Transcript + deterministic Markdown bytes
→ LocalTranscriptionArtifactStore（内容寻址）
→ SQLiteTranscriptionStore.record_success（candidate version）
→ review guard
→ fake PublicationIndexPort request/receipt
→ SQLiteTranscriptionStore.promote（单事务 head 切换）
```

## 关键文件

- `src/ingest.py`
- `src/indexing_pipeline.py`
- `src/chunk.py`
- `src/index.py`
- `src/retrieve.py`
- `src/generate.py`
- `api/schemas.py`
- `api/routes_media.py`（新增）
- `api/routes_admin.py`（媒体上传）
- `api/indexing.py`（media_id 传递）
- `api/db.py`、`api/db_migrations.py`、`api/db_backup.py`（Phase 2 添加式 Schema 与备份）
- `api/transcription_store.py`、`api/transcription_artifacts.py`（Phase 2 未接线适配器）
- `src/transcription/persistence.py`、`src/transcription/workflow.py`（Phase 2 领域端口与编排）
- `frontend/src/components/citations.ts`
- `frontend/src/components/SourcesPanel.tsx`（播放按钮）
- `frontend/src/components/Message.tsx`（引用 click seek）
- `frontend/src/components/VideoPlayerDrawer.tsx`（新增）
- `frontend/src/hooks/useVideoPlayer.tsx`（新增）

## 数据契约

| 字段 | 来源 | 可选 | 说明 |
|------|------|------|------|
| `doc_type=”transcript”` | 上传/分类推导 | 否 | 触发转录分块逻辑 |
| `start_time` | 转录稿解析 | 是 | 引用时间戳 HH:MM:SS |
| `media_id` | 上传时生成 UUID | 是 | 关联 media_assets 表，驱动播放器 |
| `media_url`（API 动态构造） | 由 media_id 生成 | 是 | 不存储，动态构造 `/api/media/{media_id}` |
| `transcript_version_id` | Phase 2 Store | 是 | 候选/历史版本身份；尚未进入现有索引 payload |
| `current_version_id` | `media_transcript_heads` | 是 | SQLite 当前正式版本指针；legacy media 可以没有 head |

## 依赖与下游消费者

- 依赖文档索引、检索、回答生成、引用与来源面板；
- 依赖认证（`require_user`）和媒体存储（`media/` 目录挂载）；
- 依赖 `media_assets` 表（`app.sqlite`）和 `media_id` 列迁移。

## 不变量与安全边界

- 不得向客户端暴露服务器绝对路径（`storage_rel_path` 对外隐藏）；
- 视频访问必须经过 `require_user` 鉴权和路径穿越防护（`safe_join`）；
- `media_id` 不存在或状态不是 `ready` 时返回 404；
- 非法 Range 返回 416；
- `media_id` 不进入 Embedding 文本，不影响检索排序；
- 旧会话缺少 `media_id` 时正常降级（无播放按钮，不报错）；
- 新媒体字段是向前兼容的 nullable 列，不需要索引 Reset。
- Phase 2 新表为添加式迁移；不删除旧表、不回填人工稿、不触发索引 Reset；真实迁移和恢复仍需独立审批。

## 验证

- 当前链路：转录解析、时间戳索引、检索命中、回答引用、前端匹配；
- 播放链路：匿名 401、登录用户 200/206、未知媒体 404、路径穿越 404；
- Phase 2：临时 SQLite migration/backup、Store 事务、artifact hash、publication head、recovery、人工稿不回填和静态依赖边界；
- 前端变化运行 `npm run build`，RAG 字段变化运行检索冒烟和相关黄金集。

## 已知限制

- 第一阶段只支持 MP4 格式，不支持其他视频容器；
- 播放器只做时间点 seek，不做完整交互式转录同步高亮；
- 无自动语音识别，转录稿需人工上传；
- Phase 2 尚无 API/UI/worker/真实 Provider/Qdrant 接线，不产生自动转录用户能力；
- 支持范围播放但无 HLS 自适应码率。

## 相关决策

- [0001 — 视频转录播放器与媒体资产流水线](../decisions/0001-video-transcript-player.md) 第一阶段已实施完成。
- [0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)；Phase 2 实施基线见[详细计划](../plans/multi-engine-transcription-phase2.md)。
