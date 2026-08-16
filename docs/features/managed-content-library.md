# 受管资料库

## 状态

已实现。资料经过上传、提交、确认和发布后进入检索；资料管理页按当前受控目录展示资料，支持上传、拖放确认、新建目录、筛选、批量流程操作、单项六图标操作，以及移入回收站和恢复。

## 入口与调用链

- 页面：`frontend/src/pages/admin/AdminManagedContentPage.tsx`
- 前端 API：`frontend/src/api/client.ts`
- HTTP 路由：`api/routes_content.py`
- 状态与事务：`api/content_store.py`
- 对象存储：`api/content_storage.py`
- 发布：`api/content_publication.py`
- 检索可见性：`src/content_retrieval_visibility.py`
- Schema：`api/schemas.py`、`api/db_migrations.py`

```text
上传文件 -> content_objects + content_items + content_versions
-> 提交确认 -> 审核 -> 发布任务 -> content_item_heads
-> PublishedContentSnapshot -> RAG 检索可见
```

## 列表与版本操作

- 列表顶部显示当前目录、资料数量和选择摘要；选择两份及以上资料时，“新建目录”切换为“批量操作”。
- 批量移动和批量删除以 `item_id + expected_version_id` 进行并发校验，返回逐项成功或失败；确认、退回和发布沿用版本级批量接口。
- 每份资料提供查看、移动、下载、重命名、更新和删除六个固定图标操作。查看详情不再承载下载；提交、确认、退回和发布保留在详情弹窗中。
- 重命名同时修改资料标题和源文件名；更新上传新对象，并可沿用原名称或使用上传文件名。两者都会新增递增的草稿版本，不覆盖历史版本或对象。
- 已发布资料生成新草稿时，旧 `content_item_heads` 保持有效，直到新版本发布成功，避免检索空窗。此期间不能移动或由仅有整理权限的账号删除该资料。
- 文件名按 Unicode NFKC 和不区分大小写形式规范化。同一活动目录下出现同名资料时返回结构化 `409`；用户确认替换后，冲突资料与新版本写入在同一事务中完成，冲突资料进入回收站并立即退出检索。

新增或扩展的管理接口：

- `POST /api/admin/content/bulk-move`
- `POST /api/admin/content/bulk-archive`
- `POST /api/admin/content/items/{item_id}/rename`
- `POST /api/admin/content/items/{item_id}/versions`

## 权限

- `organize`：上传、提交，以及将 `draft`、`rejected` 状态资料移至回收站。
- `review`：确认、退回待确认资料，以及恢复回收站资料。
- `publish`：发布，以及将已确认、发布失败或已发布资料移至回收站；可查看回收站但不能恢复。
- 管理员通过现有管理员回退拥有全部资料权限。
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

`GET /api/admin/content/trash` 向资料负责人和系统管理员显示回收站；`POST /api/admin/content/items/{item_id}/restore` 要求当前版本号和 CSRF token。草稿、退回和待确认资料恢复原状态；已确认、发布失败或已发布资料统一恢复为“已确认”。恢复不会重建 `content_item_heads`，不会自动进入检索，必须重新发布。当前阶段不提供永久删除、自动到期清理或对象文件清理。

## 验证入口

- API 与权限：`tests/test_content_library_api.py`
- 状态、对象与检索可见性：`tests/test_content_library_foundation.py`
- 页面交互：`frontend/src/pages/admin/AdminManagedContentPage.test.tsx`
- 浏览器布局：`frontend/tests/visual/admin-workflows.spec.ts`
- Golden：`frontend/tests/visual/admin-golden.spec.ts`
