# 架构决策记录

本目录用于记录已经讨论并批准、会长期影响多个模块的架构选择。它不替代功能现状文档、TODO 或 Git/PR/workflow 交付证据。

只有在决策真正获批后才创建记录。使用 [TEMPLATE.md](TEMPLATE.md)，文件名建议为 `NNNN-short-title.md`。候选方案继续留在对应功能文档的“未实现”或“相关决策”部分，不提前写成既定结论。

## 决策索引

- [0001 — 视频转录播放器与媒体资产流水线](0001-video-transcript-player.md)：历史设计记录；核心播放器链路已实现，当前状态见视频转录与引用功能文档。
- [0002 — 多引擎视频自动转录与管理员选择](0002-multi-engine-transcription.md)：已批准架构；当前实现状态见视频转录功能文档。
- [0003 — 数据库分类与受管内容资料库](0003-managed-content-library.md)：已批准架构；生产切换和迁移状态见受管资料功能与迁移文档。
- [0004 — Git worktree 创建位置与兼容策略](0004-worktree-location-policy.md)：Codex 受管目录优先，人工长期 worktree 集中到仓库外统一目录，旧位置仅允许继续。
- [0005 - 只读外部媒体源与本地转录产物分离](0005-read-only-external-media-sources.md)：共享原视频保持只读，扫描身份、转录产物和正式发布状态由应用独立管理。
- [0006 - 统一资料上传与视频转录任务分流](0006-unified-managed-upload-routing.md)：文件选择与上传任务统一，普通文档和视频继续进入独立领域流水线；首期视频操作仅限系统管理员。
