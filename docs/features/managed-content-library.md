# 受管资料库

## 状态

共享媒体目录在分类树中以“共享文件夹”显示。共享文件夹只读扫描 MP4，远程视频不可通过资料库移动、重命名、替换或删除，但其转录稿继续使用既有审核、发布和索引流程。

已实现。普通文档经过上传、提交、确认和发布后进入检索；系统管理员上传的 MP4 从同一资料上传入口进入资料库，初始状态为“待转录”，不会在上传时创建转录任务。管理员可在资料列表选择视频，或在“上传任务”按上传批次（含分散在子目录中的视频）选择转录方案后，视频才进入独立转录链路；正式发布后的转录稿继续绑定同一视频条目。“转录任务”作为资料管理的管理员子页签承载视频后续处理。

## 入口与调用链

- 页面：`frontend/src/pages/admin/AdminManagedContentPage.tsx`
- 转录任务子页：`frontend/src/pages/admin/AdminTranscriptionTasksPage.tsx`、`frontend/src/pages/admin/AdminMediaPage.tsx`
- 前端 API：`frontend/src/api/client.ts`
- HTTP 路由：`api/routes_content.py`
- 递归批量任务：`api/content_bulk_operations.py`
- 状态与事务：`api/content_store.py`
- 对象存储：`api/content_storage.py`
- 发布：`api/content_publication.py`
- 视频目录登记：`api/media_transcript_catalog.py`、`api/transcription_store.py`
- 检索可见性：`src/content_retrieval_visibility.py`
- Schema：`api/schemas.py`、`api/db_migrations.py`

文件夹与资料混合批量操作链路：

```text
勾选文件夹/资料 -> POST /bulk-operations/preflight
-> Schema 30 持久化目录树、资料快照、资格与用户选择
-> 逐项取消/审核或确认执行
-> 同步流程结果，或后台 ZIP64/强制删除任务 -> 轮询状态与逐项结果
```

- 批量选择支持任意数量的文件夹或资料；嵌套选择自动归并到最上层根目录，递归资料最多 5000 份。
- 影响范围按目录与完整路径展示。状态或权限不匹配的资料保留在树中并标明原因，不会被流程操作修改；可操作资料可逐项取消，确认/退回还可逐项处理。
- 目录移动只移动所选根目录，子文件夹和内部资料随目录保持原有归属；混合散选资料按其版本和状态独立移动，不会把目录内资料拆出。
- 长耗时打包和批量强制删除保存在 `content_bulk_operations` 及其目录/资料快照表中。服务重启后恢复排队任务；部署门禁会拒绝仍有 `queued`、`running` 或 `packaging` 任务时发布。

上传任务链路：

```text
multipart 上传 -> upload_batches + upload_batch_entries
-> GET /api/admin/content/upload-tasks -> 任务列表/状态筛选/分页
-> GET /api/admin/content/upload-tasks/{batch_id} -> 任务详情抽屉
```

- Schema 14 为 `upload_batches` 增加上传模式、目标目录和文件统计，并以结构化的 `upload_batch_entries` 保存每个文件的接收/跳过结果。
- `item.upload` 是上传任务页和接口的权限边界；普通用户只能查看自己创建的任务，全局管理员可查看全部任务。
- 浏览器使用 XHR 显示字节传输进度；网络传输达到 100% 后切换为“服务端处理中…”。当前同步上传接口不承诺跨刷新恢复实时进度，但任务历史和文件明细可跨刷新查询。
- 失败任务在原文件仍保留于当前页面时支持重试；刷新页面后需要重新选择原文件，服务端不保存可重试的浏览器临时文件。
- 上传前调用 `POST /api/admin/content/uploads/preflight` 检查文件名、大小写规范化后的同名资料和文件夹根目录冲突。冲突保留在当前处理窗口，不自动跳转上传任务页；无冲突文件会与处理后的文件一起提交，任务页继续提供历史记录和逐文件失败明细。
- 文件冲突默认选择跳过，也可另存为新资料（默认建议追加 ` (1)` 等后缀），或在已有资料没有活动发布/整理任务时作为其新草稿版本。文件夹根目录冲突默认不合并，支持合并到现有目录或整体重命名后重新预检；重命名不会改变文件夹内的相对层级。
- 批量上传按文件独立处理：跳过或处理冲突不会阻断同批无冲突文件；全部文件被跳过时批次标记为失败并保留原因。服务端上传阶段会再次校验冲突和版本，预检后发生变化的资料会跳过并返回 `content_upload_conflict_changed`。
- 系统管理员的文件选择、拖放和文件夹上传额外接受 MP4；普通账号的支持格式文案、文件选择和服务端接口均不开放 MP4。视频上传不再要求上传时选择方案，成功后在资料列表显示为“待转录”；上传批次仍记录视频的稳定幂等键和文件明细。
- Schema 31 为 `upload_batch_entries` 增加 `entry_kind`、`media_id`、`transcription_job_id` 和 `failure_code`。上传任务详情因此可区分普通文档和视频，并关联各自的后续对象；旧记录通过默认值继续视为普通文档。

统一入口只统一选择、目录预检、冲突处理、进度和上传任务记录，不合并两条领域流水线：

```text
普通文件 -> content_objects + content_items + content_versions
-> 提交确认 -> content_index_jobs -> content_item_heads

MP4 -> media_assets + media_transcript 目录壳（待转录）
-> 管理员选择方案 -> transcription_jobs
-> transcript_versions -> transcript_publication_index_jobs -> media_transcript_heads
-> content_items(media_transcript) 目录壳
```

视频上传不会创建普通 `content_versions`、`content_index_jobs` 或 `content_item_heads`，但会立即登记带有 `media_id` 的资料库目录壳，因此“待转录”视频不会丢失在列表外。选择方案后才创建 `transcription_jobs`；转录失败、取消和重试都继续更新同一视频条目。混合批次不是跨文件事务：每个文件独立提交并在上传任务中保留结果，已成功文件不会因同批后续失败回滚。

```text
上传文件 -> content_objects + content_items + content_versions
-> 提交确认 -> 审核 -> 发布任务 -> content_item_heads
-> PublishedContentSnapshot -> RAG 检索可见
```

视频转录稿沿用独立的发布和索引权威链路：

```text
media_assets + transcript_versions
-> transcript_publication_index_jobs -> media_transcript_heads
-> content_items(media_transcript) 目录壳 -> 资料库联合列表
```

只读外部媒体源使用独立虚拟目录树，不直接创建受管目录壳。共享视频完成现有审核、索引和发布事务后才作为 `media_transcript` 出现在本资料库；源扫描、缺失和不可达状态见 [外部媒体源](external-media-sources.md)。

Schema 16 为历史上已有正式 head 的未归档视频补建目录壳。目录壳只保存 `media_id`、标题和 `category_id`，不创建 `content_versions`、`content_publications`、`content_index_jobs`、`content_item_heads` 或对象副本，因此不会重复文件、发布状态、索引任务或 Qdrant points。现行统一上传在视频落盘后立即登记同类目录壳；正式 head 切换时更新同一条目录壳，失败时整笔事务回滚。

Schema 19 增加 `media_metadata_revisions` 和 `media_replacements`。媒体标题/源文件名修订复用当前正式 Markdown 创建待审核候选；替换视频作为新的媒体、转录和索引候选处理。两类候选都在审核、索引和发布成功前保留旧 `media_transcript_heads`、目录壳和检索可见内容；最终激活在一个 SQLite 事务内切换 head 与目录关联，失败整笔回滚。旧视频只在替换成功后标记归档，物理文件不在该事务中删除。

Schema 26 为媒体记录增加目标归档目录和规范化标题/源文件名，并以部分唯一索引保护新上传媒体的目录内身份。资料上传入口通过 `POST /api/admin/content/uploads/preflight` 对新 MP4 按 Unicode NFKC、去除首尾空格和不区分大小写规则检查资料标题与源文件名；冲突可逐项跳过或重命名后另存。已发布视频的替换仍在“转录任务”中使用原 `POST /api/admin/media/preflight` 和 `POST /api/admin/media` 契约，并允许唯一命中的正式资料进入候选替换流程。两条入口都会在实际上传时再次校验冲突，预检后状态变化或并发唯一冲突返回 `media_upload_conflict_changed`。尚未发布但未归档的媒体也占用所选目录内的标题和源文件名，但不能作为更新目标。替换候选在旧媒体归档后于同一事务接管规范化身份。

## 列表与版本操作

- 列表标题区显示当前目录、资料数量和选择摘要；搜索框位于标题右侧，根目录和具体目录均可使用。搜索匹配资料名称、文件名、完整目录路径和上传相对路径；筛选层内可在“当前目录”和“全局搜索”之间切换，并继续提供状态、来源和类型筛选。根目录默认全局且不能切换到不存在的当前目录；进入具体目录后默认只搜索该目录，也可主动切换为全局搜索。全局结果不混入文件夹行，并在每份资料下显示完整目录路径。
- 当前目录的直接子文件夹与资料共用同一列表，文件夹始终排在分页资料之前；根目录未输入搜索或筛选条件时显示一级文件夹，进入目录后显示其直接子文件夹。文件夹不计入资料总数和分页，但可与资料混合勾选，选择摘要分别显示文件夹和资料数量；当前目录内的文件筛选不会隐藏文件夹。“资料”列排序分别对文件夹组和资料组排序，其他列只排序资料组。
- 文件夹行显示直接资料、直接子文件夹、递归子文件夹和递归资料统计。选中文件夹后，批量移动、提交审核、确认、退回、发布和下载递归作用于其子树；删除只接受文件夹根，普通删除沿用空目录约束，强制删除要求独立确认。
- 文件夹操作从左到右固定为打开、查看详情、重命名、调整编号、调整目录位置、下载和删除。资料操作从左到右固定为预览、查看详情、重命名、更新资料、调整分类、下载和删除；状态驱动的唯一下一步文字按钮仍位于工具操作前。
- 所有目录选择窗口复用层级目录树；具备 `category.manage` 权限时，可在已选上级目录下直接新建文件夹并自动选中新目录。
- “审核”打开专用窗口，展示资料、目录、文件、版本、来源和预览入口。确认通过的备注可选；退回修改的原因必填且最多 2000 字。提交期间窗口防止重复操作，请求失败时保留当前结果选择和输入。详情弹窗复用该审核入口，并显示最近审核人、时间、结果和审核备注或退回原因。
- “发布”和“重新发布”先打开确认窗口，确认后才创建索引任务；历史发布失败信息继续在详情和确认窗口中展示。
- PPTX 发布时通过 LibreOffice 生成同版本 `.preview.pdf` 派生产物。列表接口以预览文件的 PDF 签名为准返回 `preview_status`，缺失或损坏时不再开放预览按钮；发布负责人可在资料详情中单独重新生成预览，该操作不重新索引，也不改变资料发布状态。转换写入使用同目录临时文件和原子替换，失败不会破坏已有有效预览。
- 普通资料上传、文件夹上传和版本更新支持 `.xmind`。服务端在创建版本前校验 ZIP 路径、条目数、解压总量、压缩比、主题数、主题深度和文本长度，并兼容现代 `content.json` 与旧版 `content.xml`；不满足限制的文件按逐项失败返回且不创建资料版本。XMind 发布时按画布、中心主题和子主题转换为 Markdown 后复用普通文档 Parent/Child 索引流程，不修改已有索引。
- XMind 预览使用版本级 `item.view` 接口读取受管对象，返回受限的画布与主题树；资料列表、详情和审核入口均复用 `ResourcePreviewShell`，与 PDF、Office 和视频预览保持同一抽屉动效、焦点恢复和移动端全屏行为。主题树由本地只读思维导图画布渲染为中心主题、双向分支和连线，支持画布切换、平移、缩放与适配视图；原始资料不会发送到第三方渲染服务。预览不依赖发布后的 `parent_id`，因此草稿、待确认和已发布版本均可查看。
- PPTX 预览生成失败仍不阻断资料索引与发布。PDF 预览窗口显示中文失败原因；系统概览实际探测 LibreOffice `/health`，区分“运行正常”“服务异常”和“已停用”。
- 视频条目按独立生命周期展示“待转录、转录中、待发布、发布中、发布失败、已发布”等状态。转录完成但待审核、审核退回或审核处理中统一投影为“转录中”；只有审核通过且尚未点击发布才显示“待发布”，只有候选索引成功并完成正式 head 切换才显示“已发布”。待转录或可恢复失败状态显示“开始转录”，转录中只允许进入详情并由转录任务页取消，审核、发布和重试均在转录任务页完成；播放、下载在正式 head 发布前保持禁用。资料列表支持选中视频批量选择方案；上传任务按批次汇总视频数量和待转录数量，支持跨子目录批量预检，预检逐项显示可启动与跳过原因，部分成功后保留失败项供重试。独立“发布任务”页签展示普通资料和视频转录稿的发布队列、失败原因与重试入口。
- 视频转录稿固定展示转录、播放视频与转录稿、详情、重命名、更新视频资料、移动路径、下载和删除按钮，顺序与资料列表一致；下载窗口可选择原始 MP4、当前正式 Markdown 或两者的 ZIP。系统管理员可从操作菜单进入“转录任务”编辑当前正式转录稿或替换视频；普通发布负责人不会看到管理员操作。视频与转录稿共用资料回收站，归档后可恢复，永久删除受同一归档保护和管理员边界约束。
- 编辑媒体信息不会立即修改正式条目。候选稿必须重新审核、索引并发布，成功后标题、源文件名、目录壳和正式 head 同时生效；审核拒绝或索引/事务失败时旧名称和旧 head 保持不变。
- 替换视频必须从“转录任务”选择新的 MP4 和当前可用的服务端转录方案。重复请求以幂等键同时绑定上传者、标题、文件名、文件内容、方案和源视频；转录或发布失败不会归档旧视频。候选正式发布后，原目录壳原地关联新媒体并保留目录位置。
- 视频转录稿只展示 `media_transcript_heads` 指向的当前正式版本。产生较新的待处理稿时，旧正式稿继续在资料库和检索中可见，并显示“有新转录稿待处理”；待处理稿不会作为第二份资料出现。
- 历史视频默认进入 `05 培训资料`；新上传视频沿用资料上传所选目录。发布负责人可从资料列表移动正式视频目录壳，系统管理员也可从“转录任务”调整目录；移动不会改变视频文件、转录发布状态、正式 head 或 Qdrant 索引。同一活动目录内的新上传视频按资料标题和源文件名执行同名预检；不同目录仍允许同名。
- 重命名同时修改资料标题和源文件名；更新上传新对象，并可沿用原名称或使用上传文件名。两者都会新增递增的草稿版本，不覆盖历史版本或对象。
- 已发布资料生成新草稿时，旧 `content_item_heads` 保持有效，直到新版本发布成功，避免检索空窗。此期间不能移动或由仅有整理权限的账号删除该资料。
- 文件名按 Unicode NFKC 和不区分大小写形式规范化。同一活动目录下出现同名资料时返回结构化 `409`；用户确认替换后，冲突资料与新版本写入在同一事务中完成，冲突资料进入回收站并立即退出检索。

新增或扩展的管理接口：

- `POST /api/admin/content/uploads/preflight`
- `POST /api/admin/content/uploads`（普通资料与管理员 MP4 的统一上传入口）
- `POST /api/admin/transcription/media/{media_id}/start`
- `POST /api/admin/transcription/bulk-start/preflight`
- `POST /api/admin/transcription/bulk-start`（支持 `media_ids`、`upload_batch_id`、`category_id + recursive` 三种范围）
- `GET /api/admin/content/publication-jobs`、`POST /api/admin/content/publication-jobs/{job_id}/retry`（普通资料与视频转录稿的统一发布任务列表和视频失败重试）
- `POST /api/admin/content/bulk-move`
- `POST /api/admin/content/bulk-archive`
- `POST /api/admin/content/bulk-restore`
- `POST /api/admin/content/bulk-download`
- `POST /api/admin/content/bulk-operations/preflight`
- `GET /api/admin/content/bulk-operations/{run_id}`
- `PATCH /api/admin/content/bulk-operations/{run_id}/selection`
- `POST /api/admin/content/bulk-operations/{run_id}/items/{item_id}/review`
- `POST /api/admin/content/bulk-operations/{run_id}/execute`
- `POST /api/admin/content/bulk-operations/{run_id}/cancel`
- `GET /api/admin/content/bulk-operations/{run_id}/archive`
- `GET /api/admin/content/categories/{category_id}/delete-preview`
- `DELETE /api/admin/content/categories/{category_id}`
- `POST /api/admin/content/items/{item_id}/rename`
- `POST /api/admin/content/items/{item_id}/versions`
- `GET /api/admin/content/items/{item_id}/media-download?part=video|transcript|all`
- `POST /api/admin/transcription/media/{media_id}/metadata-revisions`
- `POST /api/admin/media/preflight`
- `POST /api/admin/media`（替换时额外提交 `replacement_source_media_id`，且必须使用自动转录 Profile）

## 权限

- 入口与查看：`workspace.view` 控制资料工作台入口；`item.view` 控制资料列表、详情和预览；`item.download` 控制单份附件下载和批量 ZIP 下载；`category.view` 控制分类树和路径。
- 资料整理：`item.upload`、`item.submit`、`item.move_draft`、`item.archive_draft` 分别控制上传、提交、移动草稿/退回资料和将其移入回收站。
- 确认与发布：`item.review` 控制确认和退回，`item.move_review` 控制移动待确认资料，`item.publish` 控制发布和重新生成当前已发布 PPTX 的预览，`item.archive_published` 控制将已确认、发布失败或已发布资料移入回收站。
- 视频转录稿：`item.view` 控制资料库查看，已登录用户按当前正式 head 读取播放和转录预览；`item.download` 控制原视频、正式转录稿和组合 ZIP 下载；`item.publish` 同时允许只调整视频目录壳。首期 MP4 上传、转录任务页、转录校对、媒体信息修订、替换、取消/重试和失败视频删除均沿用全局系统管理员边界，后端不以 `item.upload` 代替该限制。视频归档要求 `item.archive_published`，恢复要求 `trash.restore`；资料库接口仍拒绝对视频条目执行普通文档重命名、更新或发布。
- 回收站：`trash.view` 控制查看，`trash.restore` 独立控制恢复，`trash.purge` 控制永久删除，`trash.policy_manage` 控制自动清理策略和运行记录。后两项仅默认授予系统管理员。
- 分类与目录：`category.manage` 控制分类维护和普通目录删除，`category.force_delete` 独立控制不可恢复的目录树强制删除，`folder.request` 控制目录申请，`folder.review` 控制目录审批。强制删除还依赖 `trash.purge`，仅默认授予系统管理员。
- 运维入口：`import.server` 控制服务器批次导入，`index.view` 控制索引任务页面和 API。
- 旧版文件级批量下载仍要求 `item.download`、登录 Cookie 和 CSRF token，每批 1–20 个唯一版本且默认不超过 1 GiB。新的文件夹递归打包使用持久化任务，默认最多 10 GiB，生成 ZIP64 并保留所选根目录、子文件夹和空目录；生成前要求归档盘仍有任务原始大小加 2 GiB 保留空间，生产部署额外要求内容盘至少有 12 GiB 可用。前端显示排队、已处理字节和百分比，可在生成期间取消；完成文件保留 6 小时并支持 HTTP Range 下载，过期任务由每小时清理或启动恢复清除。视频单项和组合下载继续沿用原 1 GiB 边界。
- 权限节点存在显式前置依赖。用户管理页勾选动作权限时自动补齐入口和查看权限，取消前置权限时自动移除依赖动作；后端拒绝保存缺少前置权限、重复或未知节点的组合。
- 系统预设组包括普通成员、资料浏览者、BIM工程师、资料负责人、发布负责人、分类管理员和系统管理员；权限管理和权限组维护仅允许全局管理员执行。
- 管理员通过现有管理员回退拥有全部 20 个资料权限节点。
- 所有修改请求都要求登录 Cookie 和 CSRF token；前端按钮可见性不是授权边界。

## 回收站语义

`DELETE /api/admin/content/items/{item_id}` 接收当前 `expected_version_id`，用于避免将已被其他人更新的资料移至回收站。

“移至回收站”是可恢复的逻辑删除：

1. 设置 `content_items.archived_at`，资料从默认列表、状态计数和分类计数中消失；
2. 已发布资料将当前 `content_publications` 标记为 `withdrawn`，并删除对应 `content_item_heads`；
3. 检索快照只接纳未归档资料的发布头，因此归档后不再进入 RAG 结果；
4. 归档版本的文件 HTTP 入口返回 404；
5. 保留 `content_objects`、`content_versions`、审核、发布、索引和审计记录，不物理删除共享对象或 Qdrant points。

视频转录稿也使用同一回收站，但归档仅隐藏目录壳并把当前媒体标记为 `archived`，不撤销正式 head 或立即删除索引，因而可原样恢复。活动转录或发布索引任务会阻止归档和永久删除。

正在发布或仍有活动索引任务的资料返回 `409`。版本不一致返回 `409`，权限不足返回 `403`，不存在或已归档返回 `404`。

## 目录删除语义

分类管理和资料管理的目录条目复用同一删除确认窗口。预览接口返回完整目录路径、目录版本、子目录数、普通资料数、视频转录稿数、上传任务数和索引任务数；执行接口再次校验目录版本，避免预览后目录状态发生变化。

普通删除要求目标目录树内没有资料、视频转录稿、上传任务或索引任务。删除父目录时一并删除其空子目录，并按剩余兄弟目录的现有顺序重新生成连续编号。系统一级分类不能删除。

系统管理员可在普通删除被内容阻止时切换为强制永久删除。操作必须同时输入预览返回的完整目录路径并勾选不可恢复确认；服务端要求 `category.force_delete`，不能通过前端显隐绕过。强制删除会：

1. 将目录树内仍在运行的索引和分类调整任务标记为失败，防止 worker 在删除后继续提交；
2. 清除普通资料（包括回收站资料）、版本、发布记录、上传批次和索引任务；
3. 删除对应 Qdrant points、`parents.sqlite` 行和发布副本，仅在没有其他版本引用时删除对象文件；
4. 删除目录树并重新生成同级目录的连续编号；
5. 在 `category_force_delete_runs` 和资料审计事件中记录结果，外部存储清理不完整时标记为 `partial` 并向操作者返回明确错误。

系统一级分类和包含视频转录稿的目录树始终禁止强制删除；视频完整删除继续由“转录任务”的独立生命周期负责。上传路径必须位于受管内容根目录内，符号链接、路径逃逸和过宽目录均被拒绝。单文件夹兼容接口仍在 HTTP 请求内同步执行；多选强制删除进入持久化单线程后台队列，逐个根目录提交，并在服务重启后从未完成根继续恢复。取消只阻止后续根目录，不能回滚已经完成的永久删除。

## 恢复边界

`GET /api/admin/content/trash` 要求 `trash.view`；`POST /api/admin/content/items/{item_id}/restore` 额外要求 `trash.restore`、当前版本号和 CSRF token。草稿、退回和待确认资料恢复原状态；已确认、发布失败或已发布资料统一恢复为“已确认”。恢复不会重建 `content_item_heads`，不会自动进入检索，必须重新发布。

恢复默认回到原目录，也可选择其他活动目录；原目录停用时必须改选目录。同一活动目录仍按 Unicode NFKC 和不区分大小写规则禁止同名资料。发生冲突时可改选目录，或在同时具备冲突资料对应归档权限时确认替换。替换会将冲突资料移入回收站，并与当前资料恢复在同一 SQLite 事务内完成；任一版本、权限、活动索引或分类调整校验失败时整笔回滚。

`POST /api/admin/content/bulk-restore` 每批接受至少 1 份资料，可分别恢复到原目录，或统一恢复到一个活动目录。每份资料独立事务提交并返回逐项结果；同名冲突不会在批量操作中自动替换，失败项继续留在回收站，供单项恢复处理。每个成功项继续写入独立的 `content.restored` 审计事件。

回收站复用资料库的列表交互：桌面端在表格首列逐项选择或全选当前页，移动端在每条资料标题前选择，列表标题显示已选数量；翻页或改变筛选会清空选择。选择两份及以上资料后才显示“批量恢复”，恢复位置在弹窗内选择；随后调用 `POST /api/admin/content/bulk-restore/preflight` 检查版本、目标目录、活动索引任务和同名冲突，确认窗口只提交检查通过的资料。冲突项不自动替换，继续通过单项恢复处理。

回收站默认保留期为 90 天，并在到期前 7 天标记“即将到期”；Schema 20 将策略保存到 `content_trash_settings`，自动清理默认关闭。列表返回 `purge_eligible_at`、`retention_status` 和 `retention_days_remaining`，并使用同一份数据库策略计算筛选和提示。保留状态及数量、原目录、归档人员和归档日期范围收纳在搜索框的筛选浮层内；归档时间通过“移入回收站”表头排序，保留期限显示在对应资料行内。原目录显示归档事件中的路径快照，目录后续改名、移动或停用不会改变历史含义；恢复仍使用目录 ID，并要求目标目录处于启用状态。

系统管理员可对 1–20 份已选普通文档或视频执行永久删除。`POST /api/admin/content/trash/purge/preflight` 检查归档状态、当前版本、活动索引/分类调整任务，以及视频的活动转录、发布、待审核修订、媒体信息修订、替换任务和所有文件路径边界；`POST /api/admin/content/trash/purge` 再次执行相同检查。普通资料要求输入精确的“永久删除 N 份资料”，含视频的批次要求输入服务端返回的“永久删除 N 份资料（含 M 个视频）”。

普通文档清理逐版本删除 Qdrant points、`parents.sqlite` 行和发布产物，仅在无其他版本引用时删除对象文件。视频清理覆盖同一目录壳关联的已完成替换谱系，删除 MP4/准备音频、全部转录 Markdown、Qdrant `transcript_version_id` points、`parents.sqlite` 行，以及媒体、转录、发布和替换记录；共享转录产物仍有其他版本引用时不会删除。两类清理都保留脱敏后的运行、逐项结果和原归档审计快照。永久删除完成后只能通过事先备份的 `app.sqlite`、`parents.sqlite`、媒体/转录文件和 Qdrant points 恢复。

`PUT /api/admin/content/trash/settings` 配置保留天数、到期提醒天数、单批上限和自动清理开关。独立的每小时任务使用 SQLite 租约避免多实例重复执行，只在开关启用时批量处理已超期普通文档；媒体永久删除始终要求管理员手动选择和媒体专用确认语句。迁移、应用启动和生产部署本身不会执行清理。`GET /api/admin/content/trash/purge-runs` 返回最近的手动/自动清理记录。

`POST /api/admin/content/trash/export` 按当前筛选导出带 UTF-8 BOM 的 CSV 到期处置清单，要求 `trash.view`、登录 Cookie 和 CSRF，并写入 `content.trash_exported` 审计事件。导出不改变资料状态，不代表清理审批或执行永久删除。

`GET /api/admin/content/items/{item_id}/audit-events` 返回资料移入回收站和恢复的产品化操作记录。活动资料要求 `item.view`，回收站资料要求 `trash.view`；接口只返回操作类型、人员、时间、目录快照、状态和冲突处理结果，不暴露内部 metadata 或存储路径。永久删除审计通过清理运行接口查看，源资料删除后仍保留标题、文件名、归档时目录和清理结果快照。

## 验证入口

- API 与权限：`tests/test_content_library_api.py`
- 视频目录登记与访问：`tests/test_transcription_publication_transaction.py`、`tests/test_media_library_access.py`
- 视频下载、媒体信息修订与替换事务：`tests/test_media_library_video_actions.py`、`tests/test_content_library_api.py`
- 状态、对象与检索可见性：`tests/test_content_library_foundation.py`
- 页面交互：`frontend/src/pages/admin/AdminManagedContentPage.test.tsx`、`frontend/src/pages/admin/AdminMediaPage.test.tsx`
- 浏览器布局：`frontend/tests/visual/admin-workflows.spec.ts`
- Golden：`frontend/tests/visual/admin-golden.spec.ts`
