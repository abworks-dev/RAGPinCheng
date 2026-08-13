# 文档摄取与索引

- 状态：已实现
- 最后核对：2026-08-13

## 用户可观察能力

受管资料库功能开启后，“资料库”是普通资料唯一的网页上传、确认和发布入口，支持 PDF、Markdown、DOCX、XLSX 和 PPTX。具有独立资料权限的人员按“整理上传 → 负责人确认 → 管理员发布”处理普通资料；分类、业务资料、版本、审核、发布和正式 head 由 `app.sqlite` 管理，只有发布成功的当前版本进入正式检索。“索引监控”分别展示受管资料发布任务和兼容期旧目录索引任务，不再提供旧上传入口。教学视频转录稿仍走既有媒体链路。

## 当前边界

### 已实现

- PDF 经过 MinerU 解析为 Markdown；
- 非教学视频分类的 `.md` 按普通文档处理；
- `教学视频/` 下的 `.md` 按 Transcript 处理；
- Parent 写入 `parents.sqlite`，Child 写入 Qdrant；
- “索引监控”保留旧索引资料总览、搜索、分类/类型/状态筛选、分页、源文件预览、视频与转写稿预览、重试、重新索引与安全删除；
- `CONTENT_MANAGEMENT_ENABLED=true` 时旧 `/api/admin/upload` 在写文件前返回 `409`，避免绕过受管流程；
- 资料列表合并 Parent 索引与最近一次任务生命周期，处理中或失败但尚未进入 Parent 的资料也可见；
- 索引任务保留为折叠的辅助活动视图；
- 索引活动使用历史处理语义展示成功状态；源文件已删除时保留活动记录和删除记录入口，但不再提供无效重试；
- Office 文件上传、解析、预览与引用定位。
- 受管资料库添加式 Schema、七个首批一级分类、四级分类树、稳定 `category_key`、显示编号/名称和导入别名；
- `organize`、`review`、`publish`、`manage_categories`、`import_server` 独立权限，管理员默认拥有全部资料权限；
- 资料权限在用户管理页集中显示和配置；行操作中的“设置权限”可套用普通成员、BIM工程师、资料负责人、系统管理员预设，也可精确勾选个人权限；
- 权限组是可复用模板，自定义模板支持创建、复制、编辑和停用；套用时复制精确权限，后续修改模板不会改变既有用户；
- 网页多文件上传、服务器 `inbox/server/<batch_id>/` dry-run/apply 导入、审核发布状态机和串行候选索引；
- “资料库”支持状态汇总、搜索、分类/状态/来源筛选、分页、完整分类路径、文件预览/下载和资料详情；
- 确认、退回和发布支持单项或最多 20 份批量操作；批量结果逐项返回并展示失败原因，失败项可重试；
- 分类稳定标识由服务端生成且不在业务界面展示；分类页展示完整路径和直接资料数，含资料分类不得停用；
- SHA-256 内容寻址对象存储，物理对象可复用而业务资料记录保持独立；
- Parent、Child、parents.sqlite 和 Qdrant payload 携带 nullable 的 `content_item_id`、`content_version_id` 和 `category_key`；
- 检索按 `content_item_heads` 过滤受管版本，`compat` 模式继续允许旧的未版本化索引；
- 发布时生成保留原扩展名的工作副本供解析和 Office 预览，引用接口通过稳定 `content://` 身份回取正式对象；
- 受管 PDF 在 `data/parsed/managed/<content_version_id>/` 中使用独立解析缓存和临时目录，不依赖旧 `docs` 相对路径；MinerU 云解析结果原子写入 `document.md`，同一版本重试复用该缓存；
- MinerU 的上传和排队阶段在受管发布任务中折叠为合法的 `parsing` 状态；发布失败只通过有限错误码和脱敏中文说明暴露给管理页，完整异常保留在后端日志；
- 可重建的一至四级编号目录只读视图；视图是副本，不是分类或索引事实来源；
- 代码和普通环境默认关闭受管资料库；当前 Ubuntu 生产已显式设置 `CONTENT_MANAGEMENT_ENABLED=true`，并保持 `CONTENT_HEAD_ENFORCEMENT=compat`。
- 当前生产根目录为 `/data/business/ragpincheng/content`（容器内 `/app/content`）；2026-08-13 已将 117 份旧普通资料登记为 `legacy` 来源，并已对其中资料发起确认/发布。该批发布因受管 PDF 解析路径和任务状态兼容缺陷系统性失败，尚未建立正式 `content_item_heads`；本节描述的修复需在 R2 合并及独立 R3 部署后才会在生产生效。旧 `source/media` 继续保留既有链路。

### 未实现

- 标签和可编辑的资料版本历史；
- 统一跨 SQLite 与 Qdrant 的索引事务。
- 117 份已迁移普通资料的分类确认、分批审核发布、观察期切换和旧目录清理；
- 将既有视频转录状态机改写为统一内容版本状态机；视频继续由 `media_assets`、`transcript_versions` 和 `media_transcript_heads` 管理。

旧资料迁移提供离线清点、确定性规划和固定 T9 恢复点的只读 T10 预检：预检会重新核对候选普通资料的大小、SHA-256 和活动分类，但不写数据库或内容根目录。规划器将 `.preview.pdf`、`.preview.xlsx` 标为生成的派生产物，T10 apply 也会拒绝旧计划或手工计划中的同类文件。受控 staging/apply CLI 已实现，apply 会登记为 `legacy` 来源并停在 `awaiting_review`；真实执行仍须独立 R3 批准，不由代码存在或预检通过自动授权。

## 入口与调用链

```text
管理员上传 / 批量构建
→ doc_type 分类
→ index_single
→ ParsedDoc
→ chunk_document / chunk_transcript
→ Parent + Child
→ store_parents(parents.sqlite)
→ index_children(Qdrant)
```

受管普通资料：

```text
网页上传或 inbox/server 批次
→ objects/sha256 + app.sqlite content item/version
→ 提交确认 → 审核批准 → 发布任务
→ published/<item>/<version>/<original_filename>
→ index_managed_content(显式业务身份和分类)
→ Parent + Child 候选
→ content_item_heads 原子切换
→ 检索按同一 head 快照过滤 Child 和 Parent
```

## 关键文件

- `api/routes_admin.py`
- `api/indexing.py`
- `src/ingest.py`
- `src/indexing_pipeline.py`
- `src/chunk.py`
- `src/index.py`
- `scripts/build_index.py`
- `api/routes_content.py`
- `api/content_store.py`
- `api/content_storage.py`
- `api/content_publication.py`
- `api/content_import.py`
- `api/content_view.py`
- `src/content_retrieval_visibility.py`
- `scripts/import_content_batch.py`
- `scripts/inventory_legacy_content.py`
- `scripts/plan_legacy_content_migration.py`
- `scripts/legacy_content_migration.py`
- `scripts/preflight_legacy_content_t10.py`
- `scripts/stage_legacy_content_t10.py`
- `scripts/apply_legacy_content_t10.py`
- `scripts/rebuild_content_view.py`
- `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- `frontend/src/pages/admin/AdminCategoriesPage.tsx`

## 数据契约

- `ParsedDoc → Parent/Child`；
- Child 必须保存稳定 `parent_id`；
- Parent SQLite 与 Qdrant Child 使用确定性 ID 关联；
- 管理列表的 `document_id` 由规范化源路径哈希派生，不新增持久化字段；列表和删除请求均使用该不透明句柄，不向浏览器返回物理路径或 `content://` 对象路径；
- 管理列表只为已有 Parent 的资料返回按 `parent_id` 排序的代表性 `preview_parent_id`，仅用于复用现有源文件预览接口；该句柄在当前索引快照内确定，重新索引后允许变化；同一资料仅有一个非空 `media_id` 时返回媒体关联并复用视频与转写稿抽屉，缺失或混杂关联时关闭视频预览；
- 管理页内预览支持 PDF、DOCX、XLSX 和 PPTX：PPTX 依赖已生成的 `.preview.pdf`，XLSX 优先使用 `.preview.xlsx`；Markdown 可被索引但当前不提供管理页内渲染；源文件或转换产物缺失、浏览器解析失败时显示脱敏的可关闭错误状态；
- 主列表状态取同一源路径的最新索引任务，并保留 `is_indexed` 区分是否已有可检索版本；
- 已完成但已无 Parent 索引的历史任务只保留在索引活动中，不再生成资料条或计入可检索统计；
- 索引任务 DTO 提供 `source_exists`，仅表达源文件当前是否仍为普通文件，不暴露额外路径信息；
- 删除源文件结果区分 `not_requested`、`deleted`、`missing` 和 `failed`；`missing` 作为幂等成功，`failed` 必须向管理员明确提示；
- `data/parents.sqlite` 是可重建索引状态，`data/app.sqlite` 不是。
- 受管资料的分类身份使用数据库 ID 和稳定 `category_key`；显示编号、显示名称与物理目录名可以调整；
- `content_objects` 允许 SHA-256 物理去重，`content_items` 不做跨项目强制合并；
- `content_item_heads.current_version_id` 是普通受管资料正式可见性的唯一事实；候选索引完成前不切换；
- `CONTENT_ROOT/views/current` 和 `inbox` 仅为导入/导出视图，不得被索引器当作事实来源。

## 依赖与下游消费者

- 依赖 MinerU、BGE-M3、Qdrant 和配置目录；
- 下游为检索、回答引用、管理员资料管理和黄金集。

## 不变量与安全边界

- 表格、公式和标题上下文不得被错误拆散；
- 表格摘要只增强 Child 检索文本，不修改 Parent 原始证据；
- 真实业务资料送往外部 MinerU 前必须确认授权；
- Reset、资料删除和运行中存储操作必须按专项规则确认。
- 服务器 apply 导入只允许位于 `CONTENT_ROOT/inbox/server` 下的批次；dry-run 不写数据库或对象存储；
- T10 旧资料预检使用 SQLite `mode=ro` 并只生成脱敏聚合摘要；真实 apply 必须匹配已批准 plan 指纹、候选数、执行身份和确认词；
- 受管对象路径拒绝越界和符号链接；网页变更必须同时通过 Cookie 鉴权、资料权限和 CSRF；
- 资料权限和权限组模板仅允许全局管理员维护；`manage_categories` 不能授予自身或他人的确认、发布权限；
- 新能力在代码和普通环境中默认关闭；生产已经显式启用基础能力。真实资料迁移、启用严格 head、移动旧目录或清理旧索引仍属于独立 R3。

## 验证

- 对目标类型执行局部索引与检索冒烟；
- 涉及 Chunk、ID、Embedding 或 Payload 时运行固定黄金集并说明重建要求；
- 管理 API 变化验证权限、失败状态、重试和持久化。
- 资料列表聚合变化验证最新任务选择、未索引任务可见性、服务端筛选/分页以及错误信息脱敏。
- 受管资料验证匿名/无权限/整理/确认/发布矩阵、CSRF、分类并发版本、multipart、多版本 head、对象去重、路径边界、后台 dry-run、只读视图和索引 payload。

## 已知限制

- 索引没有统一事务覆盖 SQLite 与 Qdrant 两种存储，失败恢复依赖现有任务状态与重试流程。
- 当前资料 ID 来自部署内源路径的稳定哈希；跨部署移动源目录后不保证保持相同 ID。
- 该路径身份限制只适用于旧索引；受管资料使用稳定业务 ID。当前生产功能开关已启用，但仍以 `compat` 模式保留旧未版本化索引，旧库尚未迁移。
- 只读目录视图使用文件副本以避免权限修改污染正式对象，重建时需要额外临时磁盘空间。

## 相关决策

- PR1 实施说明：`project-docs/plans/admin-document-management-pr1.md`。
- 受管资料库决策：[0003 — 数据库分类与受管内容资料库](../decisions/0003-managed-content-library.md)。
- R2 实施方案：[受管知识资料库实施方案](../plans/managed-content-library.md)。
- 生产运行与迁移：[受管知识资料库生产运行与旧资料迁移手册](../migrations/managed-content-production-runbook.md)。

