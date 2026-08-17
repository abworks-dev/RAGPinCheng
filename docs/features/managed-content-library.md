# 受管资料库

## 状态

已实现。资料经过上传、提交、确认和发布后进入检索；资料管理页按当前受控目录展示资料，支持上传、拖放确认、新建目录、搜索、状态/来源筛选、批量流程操作、单项六图标操作，以及移入回收站和恢复。回收站后新增“上传任务”页，集中展示当前账号的上传历史和当前浏览器内的传输进度。

## 入口与调用链

- 页面：`frontend/src/pages/admin/AdminManagedContentPage.tsx`
- 前端 API：`frontend/src/api/client.ts`
- HTTP 路由：`api/routes_content.py`
- 状态与事务：`api/content_store.py`
- 对象存储：`api/content_storage.py`
- 发布：`api/content_publication.py`
- 检索可见性：`src/content_retrieval_visibility.py`
- Schema：`api/schemas.py`、`api/db_migrations.py`

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

```text
上传文件 -> content_objects + content_items + content_versions
-> 提交确认 -> 审核 -> 发布任务 -> content_item_heads
-> PublishedContentSnapshot -> RAG 检索可见
```

## 列表与版本操作

- 列表标题区显示当前目录、资料数量和选择摘要；搜索框位于标题右侧，状态与来源筛选收纳在搜索框展开层内，目录范围始终跟随当前地址栏；选择两份及以上资料时，“新建目录”切换为“批量操作”。
- 当前目录的直接子文件夹与资料共用同一列表，文件夹始终排在分页资料之前；根目录显示一级文件夹，进入目录后显示其直接子文件夹。文件夹不计入资料总数、分页、勾选和批量操作，文件筛选也不会隐藏文件夹；“资料”列排序分别对文件夹组和资料组排序，其他列只排序资料组。
- 批量移动和批量删除以 `item_id + expected_version_id` 进行并发校验，返回逐项成功或失败；确认、退回和发布沿用版本级批量接口。选择两份及以上资料时，批量操作菜单还支持一次下载最多 20 份资料的 ZIP 压缩包。
- 每份资料提供查看、移动、下载、重命名、更新和删除六个固定图标操作。查看详情不再承载下载；提交、确认、退回和发布保留在详情弹窗中。
- 重命名同时修改资料标题和源文件名；更新上传新对象，并可沿用原名称或使用上传文件名。两者都会新增递增的草稿版本，不覆盖历史版本或对象。
- 已发布资料生成新草稿时，旧 `content_item_heads` 保持有效，直到新版本发布成功，避免检索空窗。此期间不能移动或由仅有整理权限的账号删除该资料。
- 文件名按 Unicode NFKC 和不区分大小写形式规范化。同一活动目录下出现同名资料时返回结构化 `409`；用户确认替换后，冲突资料与新版本写入在同一事务中完成，冲突资料进入回收站并立即退出检索。

新增或扩展的管理接口：

- `POST /api/admin/content/bulk-move`
- `POST /api/admin/content/bulk-archive`
- `POST /api/admin/content/bulk-download`
- `POST /api/admin/content/items/{item_id}/rename`
- `POST /api/admin/content/items/{item_id}/versions`

## 权限

- 入口与查看：`workspace.view` 控制资料工作台入口；`item.view` 控制资料列表、详情和预览；`item.download` 控制单份附件下载和批量 ZIP 下载；`category.view` 控制分类树和路径。
- 资料整理：`item.upload`、`item.submit`、`item.move_draft`、`item.archive_draft` 分别控制上传、提交、移动草稿/退回资料和将其移入回收站。
- 确认与发布：`item.review` 控制确认和退回，`item.move_review` 控制移动待确认资料，`item.publish` 控制发布，`item.archive_published` 控制将已确认、发布失败或已发布资料移入回收站。
- 回收站：`trash.view` 控制查看，`trash.restore` 独立控制恢复。发布负责人默认可查看但不能恢复。
- 分类与目录：`category.manage` 控制分类维护，`folder.request` 控制目录申请，`folder.review` 控制目录审批。
- 运维入口：`import.server` 控制服务器批次导入，`index.view` 控制索引任务页面和 API。
- 批量下载要求 `item.download`、登录 Cookie 和 CSRF token；每批 1–20 个唯一版本，未归档资料的对象文件总量默认不超过 1 GiB。文件缺失、资料已归档或超过总量上限时整批失败，不生成残缺压缩包；临时 ZIP 在响应完成后清理。
- 权限节点存在显式前置依赖。用户管理页勾选动作权限时自动补齐入口和查看权限，取消前置权限时自动移除依赖动作；后端拒绝保存缺少前置权限、重复或未知节点的组合。
- 系统预设组包括普通成员、资料浏览者、BIM工程师、资料负责人、发布负责人、分类管理员和系统管理员；权限管理和权限组维护仅允许全局管理员执行。
- 管理员通过现有管理员回退拥有全部 19 个资料权限节点。
- 所有修改请求都要求登录 Cookie 和 CSRF token；前端按钮可见性不是授权边界。

## 回收站语义

`DELETE /api/admin/content/items/{item_id}` 接收当前 `expected_version_id`，用于避免将已被其他人更新的资料移至回收站。

“移至回收站”是可恢复的逻辑删除：

1. 设置 `content_items.archived_at`，资料从默认列表、状态计数和分类计数中消失；
2. 已发布资料将当前 `content_publications` 标记为 `withdrawn`，并删除对应 `content_item_heads`；
3. 检索快照只接纳未归档资料的发布头，因此归档后不再进入 RAG 结果；
4. 归档版本的文件 HTTP 入口返回 404；
5. 保留 `content_objects`、`content_versions`、审核、发布、索引和审计记录，不物理删除共享对象或 Qdrant points。

正在发布或仍有活动索引任务的资料返回 `409`。版本不一致返回 `409`，权限不足返回 `403`，不存在或已归档返回 `404`。

## 恢复边界

`GET /api/admin/content/trash` 要求 `trash.view`；`POST /api/admin/content/items/{item_id}/restore` 额外要求 `trash.restore`、当前版本号和 CSRF token。草稿、退回和待确认资料恢复原状态；已确认、发布失败或已发布资料统一恢复为“已确认”。恢复不会重建 `content_item_heads`，不会自动进入检索，必须重新发布。当前阶段不提供永久删除、自动到期清理或对象文件清理。

## 验证入口

- API 与权限：`tests/test_content_library_api.py`
- 状态、对象与检索可见性：`tests/test_content_library_foundation.py`
- 页面交互：`frontend/src/pages/admin/AdminManagedContentPage.test.tsx`
- 浏览器布局：`frontend/tests/visual/admin-workflows.spec.ts`
- Golden：`frontend/tests/visual/admin-golden.spec.ts`
