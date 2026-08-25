# 外部媒体源

## 状态

已实现，默认关闭。服务端配置至少一个外部根别名后，管理员可以在分类管理中创建带共享标记的只读共享文件夹，递归扫描其中的 MP4，并将视频加入现有自动转录队列。共享原视频不能在应用中移动、重命名或删除；失败任务可以受控清理本地状态后重新入队。真实 SMB 挂载、凭据、服务账号权限和生产业务数据尚未配置或验收。

## 入口与调用链

- 页面：`frontend/src/pages/admin/AdminMediaPage.tsx`
- 分类创建入口：`frontend/src/pages/admin/AdminCategoriesPage.tsx`
- 前端 API：`frontend/src/api/admin/media.ts`、`frontend/src/api/client.ts`
- HTTP 路由：`api/routes_external_media.py`
- 扫描与协调：`api/external_media.py`
- 路径与存储解析：`api/media_storage.py`
- Schema：`api/db_migrations.py` migration 27
- 配置：`src/config.py`

```text
只读 SMB/网络目录（由宿主机挂载）
-> EXTERNAL_MEDIA_ROOTS_JSON 根别名
-> external_media_sources
-> 周期/手动 reconcile
-> external_media_entries + media_assets(storage_kind=external)
-> 管理员首次确认或后续自动入队
-> 本地 prepared-audio-v1.wav
-> 既有 Profile/Provider/Canonical 转录
-> 人工审核 -> 索引候选 -> 发布 head
-> content_items(media_transcript) 目录壳
```

## 数据与状态

- `external_media_sources` 保存显示名、根别名、相对目录、目标发布目录、默认转录方案、扫描间隔、自动入队开关和最近扫描摘要。
- `external_media_entries` 保存相对路径、父目录、文件名、大小、修改时间、指纹、可用性和媒体绑定。文件状态为 `available | missing | superseded`。
- `external_media_scan_runs` 保存手动或周期扫描的新增、变更、缺失和入队计数。
- 外部条目树不是受管分类树。未发布的视频不会创建 `content_items`；发布后仍由现有媒体正式 head 决定资料库和检索可见性。
- 源不可达时只更新源与 scan run 的失败状态，不改写条目可用性；下一次成功扫描才协调文件级变化。
- 共享媒体在任务创建前失败、仅有 `media_assets.status=failed` 时仍可重试；后端从 `external_media_sources.default_scheme_id` 解析方案并创建任务，前端不传入或推断方案。
- 既有失败/取消任务复用任务保存的运行配置、方案和参数快照。单项与批量重试均以后端能力和逐项结果为准。

## 安全与兼容边界

- API 不接受绝对路径或 UNC 路径，只接受已配置根别名和不含 `..`、反斜杠、绝对前缀的相对路径。
- 扫描跳过符号链接和 reparse point，并限制单源最大文件数。播放和 ASR 准备前再次核对大小与修改时间，文件身份变化时 fail closed。
- 浏览器只接收逻辑源名和相对路径，管理工作台视频仍经管理员预览端点鉴权代理并支持 200/206/416；公共媒体端点保持既有受管媒体边界。
- 外部原视频永不进入上传删除或回收站物理删除。只有不存在活动转录/索引任务、转录版本、正式 head、发布索引历史和活动替换时，失败对象才允许清理；清理只删除本地任务与派生缓存，把媒体重置为 `uploaded` 以便重新入队，并保留 `external_media_entries` 与共享原文件。事务内移动出的缓存先使用 `.cleanup-pending-*`；数据库提交后再转换为 `.cleanup-*`，提交失败则恢复后重新执行完整清理。只有已提交残留由服务端优先投影为独立的 `finalize_failed_cleanup` 幂等收尾动作；即使媒体已重新入队或再次失败，该动作也只删除旧暂存缓存，不修改当前媒体目录、任务或状态，收尾刷新后才重新投影普通失败清理能力。
- 仅支持 MP4。根挂载的只读属性、SMB 凭据、网络 ACL、容量和可用性监控不由应用管理。
- migration 27 为添加式，不需要 Qdrant 或 Parent 全量重建。

## 配置

```dotenv
EXTERNAL_MEDIA_ROOTS_JSON={"training-share":"/mnt/training-videos"}
EXTERNAL_MEDIA_MAX_FILES_PER_SOURCE=10000
EXTERNAL_MEDIA_SCAN_POLL_SECONDS=60
```

Compose 只透传这些变量，不自动添加宿主机挂载。生产启用前必须单独确认只读 mount、服务账号最小权限和回滚方式。

## 验证

- `tests/test_external_media_sources.py`：新增、幂等重扫、变更、缺失、恢复、源离线、路径逃逸、文件上限、外部身份解析和符号链接跳过。
- `tests/test_media_library_access.py`、`tests/test_admin_media_preview_route.py`：现有登录、发布可见性与 Range 回归。
- `tests/test_transcription_media_input.py`、`tests/test_transcription_application.py`：本地音频准备、内容哈希和转录应用回归。
- `tests/test_content_trash_cleanup.py`：既有回收站清理回归；外部媒体额外由预检阻止永久删除。
- `tests/test_transcription_phase4_api.py`：共享来源任务创建前失败重试、既有任务配置复用、单项/批量重试与清理安全边界。
- `frontend/src/pages/admin/AdminMediaPage.test.tsx`：默认关闭、特殊源浏览、勾选与显式批量入队，以及无任务失败项的重试、共享清理提示和批量部分失败反馈。

## 已知限制

- 不使用 SMB watcher 作为事实来源；动态更新延迟由源扫描间隔和全局 poll 间隔共同决定。
- 外部文件在旧正式版本发布后被原地替换时，旧转录 head 可继续检索，但旧视频字节不能恢复预览。
- 失败媒体的数据库补偿与本地缓存清理当前使用应用进程内互斥；多进程 worker 或允许低信任主体写入 `MEDIA_DIR` 的部署仍需文件锁或持久化 ownership，目录 reparse 检查与后续文件操作之间也存在本地文件系统 TOCTOU 残余风险。
- 当前扫描和入队为单实例协调；多应用副本部署前需要增加分布式租约。
- 真实 SMB、长时断网、大规模目录性能和生产 ASR 吞吐尚待用户环境验收。

## 相关决策

- [0005 - 只读外部媒体源与本地转录产物分离](../decisions/0005-read-only-external-media-sources.md)
- [0002 - 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)
