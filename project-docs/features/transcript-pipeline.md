# 视频转录链路

- 状态：人工上传/播放链路与多引擎 Phase 5A/5B 应用闭环已实现；faster-whisper R2 已合并，R3 依赖资格失败且准入保持关闭
- 最后核对：2026-08-05

## 用户可观察能力

教学视频转录稿可以被索引和检索，回答能够显示带时间戳的视频引用，点击引用定位到来源卡片并打开视频播放器。管理员既可继续上传 MP4+Markdown 转录稿对，也可上传单个 MP4 并选择服务端白名单 Profile 启动自动转录任务。

Phase 5A/5B 已接通版本列表、只读 Markdown 预览、人工审核、显式发布、候选索引与正式 head 检索过滤。转录成功、审核通过、发布中和正式检索可见仍是独立状态；真实 ASR/GPU/Qdrant 端到端尚未运行。

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

### 已接线：多引擎转录 Phase 2～5B

- Phase 2 的 `transcription_jobs`、`transcript_versions`、artifact refs、publication-only index jobs 和正式版本 head 已由应用层 Store/服务使用；
- Phase 3 remote Provider 仍只返回严格 `ProviderCandidate | ProviderFailure`，由 `pipeline.py` 独占 normalizer/Canonical 结果流；
- 管理端单 MP4 + `profile_id` 上传、任务状态/取消/恢复已接入应用 API 和后台 worker；人工 MP4+Markdown 路径保持独立；
- 管理端按媒体 lazy 加载版本历史，可预览不可变 Markdown、提交审核备注、批准/拒绝并显式发布；人工版本不提供可用的自动发布动作；
- 管理端媒体列表使用真实审核枚举计算唯一当前阶段，独立展示索引状态，并提供处理中、待审核、发布处理中和失败快捷筛选；筛选暂时只作用于最近加载的 100 条；
- Remote Provider 的服务请求身份绑定应用任务、媒体与执行指纹；同一应用任务网络重试保持稳定，同一媒体新建应用重试任务生成新身份；
- 转录任务 API 返回安全的结构化失败 `code/message/retryable`；服务请求身份冲突与契约不匹配分别处理，前端不再直接展示 Provider 技术摘要；
- publication adapter 只走 `chunk_document → store_parents → index_children`，不调用 purge、reset 或普通 `index_single`；
- Parent、Child 和 Qdrant payload 添加 nullable `transcript_version_id` / `publication_target_id`，legacy stable ID 算法保持不变；
- 正式可见性唯一读取 `app.sqlite.media_transcript_heads.current_version_id`；Qdrant recall 和 Parent expansion 使用同一快照，损坏/缺失 head 对 versioned transcript fail closed，legacy/普通文档继续可见；
- 普通索引与 publication 索引共用现有单 worker/单队列，publication job 支持幂等恢复与失败状态持久化。
- Profile catalog 现包含已启用的 experimental SenseVoice，以及准入关闭的
  experimental faster-whisper 和 Qwen3-ASR；三者复用同一 Remote Provider 与唯一
  Candidate → Canonical 结果流；
- ASR service 注册三个固定 service Profile；faster-whisper 或 Qwen3-ASR
  缓存/依赖缺失时仅相应 Profile 不可用，不阻止现有 SenseVoice 服务启动；
- faster-whisper adapter 固定
  `dropbox-dash/faster-whisper-large-v3-turbo@0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`
  与 CUDA FP16 参数，但 R2 未安装依赖、下载模型或运行推理。
- Qwen3-ASR adapter 固定
  `Qwen/Qwen3-ASR-0.6B@5eb144179a02acc5e5ba31e748d22b0cf3e303b0`
  与
  `Qwen/Qwen3-ForcedAligner-0.6B@c7cbfc2048c462b0d63a45797104fc9db3ad62b7`，
  仅允许 Windows Transformers/CUDA BF16 候选参数；R2 未安装依赖、下载模型或
  运行推理，application Profile 保持 disabled。

SenseVoice 已完成独立生产短媒体验收。Qwen3-ASR 已具备统一 R3 仓库资格工具，
但真实 workflow 尚未取得 PASS，因此没有 Windows、CUDA、依赖、模型、质量或资源
资格结论。faster-whisper 的固定 8 个非敏感 Windows
TTS 样本已生成并通过严格 Manifest 校验，但生产 R3 workflow 在组合依赖解析阶段
得到 `dependency_preparation_failed`；模型、CUDA、质量和资源门禁均未运行，因此
其 R2 接线不构成运行或生产资格结论。

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

自动转录与版本发布结果流：

```text
单 MP4 + profile_id
→ 应用转录任务/worker
→ audio preparation port
→ independent asr_service create/upload/start/poll/result
→ RemoteAsrProvider
→ ProviderCandidate | ProviderFailure
→ pipeline.py → normalize_candidate → CanonicalTranscript
→ deterministic Markdown + candidate transcript version
→ 管理员审核
→ publication-only candidate index
→ SQLiteTranscriptionStore.promote（单事务 head 切换）
→ 检索按同一 head snapshot 过滤 Qdrant Child 与 parents.sqlite Parent
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
- `api/transcription_store.py`、`api/transcription_artifacts.py`、`api/transcription_publication.py`（版本、artifact 与发布应用服务）
- `src/transcription/persistence.py`、`src/transcription/workflow.py`（Phase 2 领域端口与编排）
- `src/transcription/asr_service_contract.py`、`provider_registry.py`、`remote_provider.py`、`runtime_ports.py`（Phase 3 后端契约）
- `src/transcription/profile_catalog.py`（Phase 3 experimental Profile catalog）
- `src/transcription_retrieval_visibility.py`（Phase 5 SQLite head 只读快照，位于 Phase 1 纯契约核心之外）
- `api/routes_transcription.py`、`api/indexing.py`（转录/版本 API 与共享 worker）
- `frontend/src/components/TranscriptionVersionPanel.tsx`（版本审阅与发布）
- `asr_service/`（Phase 3 独立服务、存储、调度和 engine adapter）
- `asr_service/engines/faster_whisper.py`、`requirements-faster-whisper.txt`
  （准入关闭的可选引擎 adapter 与隔离依赖声明）
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
| `transcript_version_id` | transcript version | 是 | immutable 候选/历史版本身份；进入 Parent/Child 与 Qdrant payload |
| `current_version_id` | `media_transcript_heads` | 是 | SQLite 当前正式版本指针；legacy media 可以没有 head |
| `application_job_id` | `transcription_jobs.id` | 否 | 仅经 `ProviderRuntimePorts` 传递，参与服务请求身份；不进入 InputRef 或 Canonical |

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
- 新媒体和版本字段是向前兼容的 nullable 列，不需要索引 Reset；legacy stable ID 保持。
- Phase 2 新表为添加式迁移；不删除旧表、不回填人工稿、不触发索引 Reset。
- `app.sqlite` current head 是唯一正式可见性事实；versioned transcript 在 head DB 损坏/缺失时 fail closed，legacy/普通文档继续可见。
- experimental Profile 不能自动发布或自动索引；人工 Markdown 路径不经过 Provider。
- `published` 只在候选索引成功并完成正式 head 原子切换后成立；不存在“已发布、稍后再手动索引”的稳定状态。

## 验证

- 当前链路：转录解析、时间戳索引、检索命中、回答引用、前端匹配；
- 播放链路：匿名 401、登录用户 200/206、未知媒体 404、路径穿越 404；
- Phase 2：临时 SQLite migration/backup、Store 事务、artifact hash、publication head、recovery、人工稿不回填和静态依赖边界；
- Phase 3/4：纯 Python service/remote、应用任务/worker、mock engine、存储恢复、取消/恢复和静态依赖边界；
- Phase 5：Store/事务/manual/visibility/index metadata/static 本地 29 项通过；版本管理定向前端 31 项通过；API、worker、candidate index、Qdrant Filter 与完整前端 build 由独立 CI job 验证；
- Phase 5C 真实 ffmpeg/ASR/GPU/Qdrant E2E 未运行。
- 管理流程加固：Provider/应用/API 定向 40 项通过；变基到最新 master 后 Provider/应用身份定向 31 项与前端定向 34 项通过，前端 production build 通过；远端 CI、真实服务和生产回归未执行。
- faster-whisper R2：无 FastAPI、无真实引擎的 ASR/Provider/应用回归
  187 项通过；Phase 1 核心契约与静态边界 189 项通过；PR #34 的 7 个远端 CI
  检查全部成功并已合并。真实依赖、模型、CUDA 和质量资格仍待 R3。
- faster-whisper R3：统一资格 workflow、隔离编排、固定模型准备、严格 8 样本
  Manifest、质量/资源门禁和无真实引擎测试已在独立分支实现，尚待 scoped review、
  远端 CI、合并及真实 Windows CUDA 执行；当前不能据此认定 faster-whisper 可用。

## 已知限制

- 第一阶段只支持 MP4 格式，不支持其他视频容器；
- 播放器只做时间点 seek，不做完整交互式转录同步高亮；
- SenseVoice 短媒体自动转录已完成生产验收，但候选稿发布/Qdrant 正式可见性 E2E
  尚未执行；faster-whisper 真实依赖、模型、CUDA 和推理均未运行；
- 当前唯一允许新建任务的自动 Profile 仍是 experimental SenseVoice；faster-whisper
  experimental Profile 可见但 admission 为 disabled；尚无 `qualification_approved` Profile；
- 支持范围播放但无 HLS 自适应码率。
- 媒体快捷筛选是最近 100 条的客户端筛选，不是服务端全库查询；独立转写工作台基础版仍待后续 PR。

## 相关决策

- [0001 — 视频转录播放器与媒体资产流水线](../decisions/0001-video-transcript-player.md) 第一阶段已实施完成。
- [0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)；实施基线见 [Phase 2](../plans/multi-engine-transcription-phase2.md)、[Phase 3](../plans/multi-engine-transcription-phase3.md) 与 [Phase 5](../plans/multi-engine-transcription-phase5.md) 详细计划。
- [转录管理流程加固](../plans/transcription-admin-workflow-hardening.md) 记录 Phase 5 后续 PR 1/PR 2 的独立范围。
- [faster-whisper Provider 接入](../plans/faster-whisper-provider-integration.md)
  记录 R2 代码边界与后续 R3 资格门禁。
- [faster-whisper R3 统一资格验证](../plans/faster-whisper-r3-unified-qualification.md)
  是后续依赖、模型、CUDA、质量与资源实测的唯一执行基线。
