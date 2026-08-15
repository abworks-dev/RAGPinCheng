# 受管资料库

## 状态

已实现。资料经过上传、提交、确认和发布后进入检索；资料管理页支持筛选、批量流程操作，以及移入回收站和恢复。

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
