# 共享目录同步与夜间发布

## 状态

代码完成待验证，待用户验收，默认关闭（`SHARED_SYNC_ENABLED=false`）。在已配置的只读共享根上，把共享目录中的普通文档、受管附件和多格式文件按“日间只登记、夜间窗口自动导入并发布、发现删除或替换立即退出检索、可恢复、当夜清理”的规则同步进知识库。同步只读取共享目录，永不写入、移动、重命名或删除共享端文件。真实 SMB 挂载、凭据、生产目录规模和夜间窗口吞吐尚未验收。

与既有 [外部媒体源](external-media-sources.md) 的关系：外部媒体源负责“扫描登记 + 手动或按源配置的自动导入”，导入后停在待发布，由管理员确认；本功能在同一 `external_media_sources` / `external_media_entries` 之上增加**定时窗口、自动发布、撤回恢复、代次防护和同步审计日志**。两者的扫描结果共用同一张条目表，不重复登记。

## 入口与调用链

- 页面：`frontend/src/pages/admin/AdminSharedSyncPage.tsx`
- 前端 API：`frontend/src/api/admin/sharedSync.ts`、`frontend/src/api/client.ts`
- HTTP 路由：`api/routes_shared_sync.py`（`/api/admin/shared-sync`）
- 调度与窗口：`api/shared_sync_scheduler.py`
- 撤回/恢复/清理/日志骨架：`api/shared_sync.py`
- 扫描与条目协调：`api/external_media.py`
- 文档与附件导入：`api/external_document_import.py`、`api/external_document_types.py`
- 发布与代次校验：`api/content_publication.py`、`api/content_store.py`
- 可见性：`src/content_retrieval_visibility.py`、`src/session.py`、`src/retrieve.py`、`api/routes.py`、`api/content_attachments.py`
- Schema：`api/db_migrations.py` migration 48
- 配置：`src/config.py`

```text
只读共享根（EXTERNAL_MEDIA_ROOTS）
-> external_media_sources（category_kind='shared_folder'、document_import_mode）
-> 周期/手动 reconcile（记录 sync_generation 与条目可用性）
-> 全天：缺失/替换 -> content_head_withdrawals + 删除 content_item_heads
-> 全天：重新可用且指纹一致 -> 按撤回记录直接恢复原 head（不重新解析）
-> 夜间窗口：候选稳定 -> 导入（对象存储 / 附件原样存储）
-> 夜间窗口：create_publication_job(source_generation, shared_entry_id)
-> 发布：_promote 事务内校验代次、可用性、撤回与清理占用后才切 head
-> 夜间窗口：仍缺失且过宽限期 -> 清理知识库副本（Qdrant/parents/published 对象/版本行）
-> shared_sync_runs / shared_sync_events（保留 30 天）
```

## 时间语义（Asia/Shanghai）

| 时段 | 阶段 | 行为 |
|---|---|---|
| `07:00–24:00` | `day` | 周期扫描登记；缺失/替换立即撤回；恢复立即生效；新增与改动只登记为待发布 |
| `00:00–01:00` | `quiet` | 只扫描登记，不做导入和发布，也不做清理 |
| `01:00–06:30` | `window` | 扫描成功后：稳定候选导入、自动提交发布、仍缺失资料当夜清理 |
| `06:30` 之后 | 回到 `day` | 不再启动新任务；未完成任务保留，不谎报完成 |

窗口跨午夜由半开区间判断（`_in_window`），`WINDOW_START == WINDOW_END` 视为全天。撤回与恢复不受窗口限制。

## 数据与状态

- `external_media_entries` 增加 `sync_generation` 与 `withdrawn_at`。源内容变化或可用性翻转时 `sync_generation += 1`；任务记录创建时的代次。
- `content_index_jobs` 增加 `source_generation` 与 `shared_entry_id`，把发布任务绑定到具体的共享条目与代次。
- `content_head_withdrawals` 记录撤回指针（资料、版本、发布、来源、相对路径、指纹、代次、原因、状态、清理批次、时间戳）。`reason` 为 `shared_missing | shared_replaced | manual`，`state` 为 `withdrawn | restored | cleanup_claimed | purged`。唯一索引 `uq_content_head_withdrawal_active` 保证同一版本不会出现两条未终结的撤回。
- `shared_sync_runs` 记录每个源每次同步的触发方式（`manual | nightly`）、状态、11 个计数、错误码、租约令牌与时间窗口。唯一索引 `uq_shared_sync_run_active` 保证同一源同时只有一个 `running` 运行。
- `shared_sync_events` 记录逐文件动作：`observed | coalesced | skipped | imported | published | withdrawn | restored | purged | failed`，含资料/版本/上一版本、错误码与结构化详情。路径经过截断与 UNC 前缀剥离，不写入正文或凭据。
- `shared_sync_candidates` 保存候选的首次/最近观测时间、观测次数和状态（`waiting | ready | coalesced | importing | imported | failed`），唯一键为 `(source_id, relative_path, fingerprint)`。

六条不变量：

1. 同一资料最多一个可见 head；
2. 已撤回版本不得进入新检索、会话携带、来源预览或附件下载；
3. 旧代次任务永不提交发布；
4. 清理确认前不删除恢复所需的 head 与索引；
5. 清理占用与恢复互斥且幂等；
6. 源不可达不等于全量删除。

## 频繁变更防护

- 扫描指纹只用于快速发现；发布判定使用复制后的内容哈希。
- 候选需连续两次观测一致且间隔不少于 `SHARED_SYNC_STABILITY_SECONDS`（默认 120 秒）才视为稳定。
- 同一路径的旧指纹候选标为 `coalesced`，同条目连续变化只保留最新候选。
- 忽略 `~$*`、`.~lock*`、`*.tmp`、`*.laccdb`、`*.dwl`、`*.dwl2`、`*.bak`、`.DS_Store`、`Thumbs.db`、`desktop.ini`（`api/external_media.py` 的 `is_ignored_share_file`，扫描阶段即跳过，不登记条目）。
- 导入用 `ContentStorage.ingest_path`，复制前后各核对一次源 `stat`；复制后若源已变化则丢弃本次副本并标记 `external_file_changed`，不登记版本。
- 解析固定使用本地不可变副本。
- 代次校验在 `_promote` 的 `BEGIN IMMEDIATE` 事务内进行，冲突则作废该任务并只清理本任务产物。

## 格式范围

与受管资料库对齐：普通文档 9 种（PDF、Word、Excel、PowerPoint、Markdown、XMind），受管附件 12 种（`jpg/jpeg/png/dwg/dxf/rvt/che/nwd/nwc/ifc/skp/max`）。附件走附件注册，不生成 Parent/Child、不写 Qdrant、不调用 MinerU/LibreOffice，单文件上限 `SHARED_SYNC_ATTACHMENT_MAX_MB`（默认 1024）。RVT、DWG 不做原生解析，只提供文件信息与下载；IFC、DXF 由前端既有查看器预览。

## 安全与兼容边界

- 共享端只读；所有删除与清理只作用于知识库副本（Qdrant 点、parents 行、published 对象、版本与资料行）。
- 共享 ACL 不等同于应用权限；同步导入的资料仍按受管资料库的权限、分类与可见性规则生效。
- 撤回后 `_managed_source` 增加归档与撤回守卫，并移除旧的“同名文件递归兜底”下载路径；附件预览对无草稿权限用户返回 404。
- migration 48 全为添加式，不需要重建 Qdrant，也不改变既有 doc_type 白名单，因此无需 Reset。
- 调度为单实例租约；多应用副本部署前需要分布式租约。
- `SHARED_SYNC_ENABLED=true` 时由本调度器独占扫描与导入：既有外部媒体扫描循环（`run_due_external_scans`）直接返回，避免同一共享目录被两个循环重复扫描并在窗口外发布。
- 01:00 全量扫描不等于全量重新解析：只有稳定差异才重新导入与索引。

## 配置

```dotenv
SHARED_SYNC_ENABLED=false
SHARED_SYNC_TIMEZONE=Asia/Shanghai
SHARED_SYNC_QUIET_START=00:00
SHARED_SYNC_WINDOW_START=01:00
SHARED_SYNC_WINDOW_END=06:30
SHARED_SYNC_STABILITY_SECONDS=120
SHARED_SYNC_MAX_FILES_PER_RUN=200
SHARED_SYNC_CONCURRENCY=1
SHARED_SYNC_ATTACHMENT_MAX_MB=1024
SHARED_SYNC_PURGE_GRACE_MINUTES=0
SHARED_SYNC_LOG_RETENTION_DAYS=30
SHARED_SYNC_POLL_SECONDS=60
SHARED_SYNC_LEASE_MINUTES=30
```

## 验证

- `tests/test_shared_folder_sync.py`：缺失撤回与同指纹恢复、替换撤旧头且新版本待发布、代次 CAS 拒绝旧任务、清理占用后拒绝恢复、清理只删知识库副本、清理保留其他版本、30 天日志保留、租约互斥与超时回收、窗口相位（含跨午夜）、候选两次观测与合并、RVT 附件登记发布不解析、日间只扫描夜间才发布、扫描失败不当作删除、锁文件/临时文件不登记、导入前源已变化则拒绝登记。
- `tests/test_external_media_sources.py`、`tests/test_content_library_foundation.py`、`tests/test_routes_source_preview.py`、`tests/test_content_index_metadata.py`：既有扫描、发布与预览回归。
- `frontend/src/pages/admin/AdminSharedSyncPage.test.tsx`：阶段与来源渲染、恢复、立即同步、加载失败重试。
- `frontend/tests/visual/shared-sync.spec.ts`：1440×900 / 1280×720 / 768×1024 / 390×844 四档视口的阶段、来源、撤回、事件、空状态、错误状态与无横向溢出。

## 已知限制

- 不保证整个目录原子切换；同一目录下的多个文件按各自稳定时间分别发布。
- 无法识别所有保存中间态；持续写入的文件会一直停在候选状态，直到出现稳定版本。
- 跟随移动/重命名：目录镜像有显式策略，单文件按新路径作为新身份，旧路径按缺失撤回。
- 夜间窗口容量不足时只告警与保留待办，不承诺全量完成。
- 真实 SMB、长时断网、大规模目录和生产窗口吞吐待用户验收。

## 相关决策

- [0005 - 只读外部媒体源与本地转录产物分离](../decisions/0005-read-only-external-media-sources.md)
