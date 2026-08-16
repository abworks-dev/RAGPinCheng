# 页面与功能清单

本清单把用户可见页面、管理后台 tab、权限边界、API、异步状态、功能事实文档和验收入口放在同一张可复核的索引中。功能事实仍以 `docs/features/` 为准，长期架构选择仍以 `docs/decisions/` 为准；本清单只负责页面级导航和验证覆盖。

## 状态记号

- `L`：loading；`E`：empty；`X`：error；`D`：disabled；`B`：busy；`S`：success；`P`：partial failure。
- `unit`、`visual` 和 `manual` 列必须指向实际存在的测试或验收入口；尚未建立的入口标记为“待补”。
- 管理后台 tab 的 URL 是目标嵌套路由；`/admin` 保留为按当前用户权限选择默认页的兼容入口。

## 页面索引

| 页面 / tab | 路由 | 业务职责 | 权限 | 主要 API / 契约 | 状态矩阵 | 关联文档 | unit test | visual spec | 人工验收入口 |
|---|---|---|---|---|---|---|---|---|---|
| 登录 | `/login` | 用户名密码登录、错误提示、提交中反馈 | 匿名 | `api.me`、`api.login`、AuthContext | L/X/B/S | [authentication](../features/authentication.md) | `LoginPage.test.tsx` | `auth-pages.spec.ts` | [USER_ACCEPTANCE](../USER_ACCEPTANCE.md)、[manual-regression](../../frontend/tests/manual-regression.md) |
| 注册 | `/register` | 创建账户、密码确认和基础校验 | 匿名 | `api.register` | X/B/S | [authentication](../features/authentication.md) | `RegisterPage.test.tsx` | `auth-pages.spec.ts` | [USER_ACCEPTANCE](../USER_ACCEPTANCE.md)、[manual-regression](../../frontend/tests/manual-regression.md) |
| 对话工作台 | `/` | 会话列表、SSE 问答、历史恢复、引用、反馈和预览 | 已登录普通用户 | `api.listConversations`、`api.getConversation`、`chatStream`、反馈与来源接口 | L/E/X/B/S/P | [chat-runtime](../features/chat-runtime.md)、[citations-and-sources](../features/citations-and-sources.md)、[feedback-management](../features/feedback-management.md) | `frontend/src/components/**.test.tsx`、`useChat.test.tsx` | `chat-page.spec.ts` | [USER_ACCEPTANCE](../USER_ACCEPTANCE.md)、[manual-regression](../../frontend/tests/manual-regression.md) |
| 用户管理 | `/admin/users` | 用户启停、角色、密码重置、资料权限和权限组 | 管理员 | `adminListUsers`、`adminPatchUser`、资料权限组 API | L/E/X/D/B/S/P | [authentication](../features/authentication.md)、[document-indexing](../features/document-indexing.md)、[admin visual contract](admin-ui-visual-contract.md) | `AdminUsersPage.test.tsx` | `admin-workflows.spec.ts`（权限入口） | [manual-regression](../../frontend/tests/manual-regression.md) |
| 对话管理 | `/admin/conversations` | 按用户/主题筛选并只读查看完整对话及版本历史 | 管理员 | `adminListAllConversations`、`adminGetConversation` | L/E/X/B/S | [chat-runtime](../features/chat-runtime.md) | `AdminConversationsPage.test.tsx`、`AdminConversationDetail.test.tsx` | `admin-conversations.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 资料库 | `/admin/content` | 上传、分类、确认、退回、发布、批量操作和资料预览 | `organize` / `review` / `publish`；管理员全量 | 受管 content、category、permission API | L/E/X/D/B/S/P | [document-indexing](../features/document-indexing.md)、[admin visual contract](admin-ui-visual-contract.md) | `AdminManagedContentPage.test.tsx` | `admin-workflows.spec.ts`、`admin-golden.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 分类设置 | `/admin/categories` | 分类树维护、启停和并发版本保存 | `manage_categories`；管理员全量 | `managedCategories`、分类创建/更新 API | L/E/X/D/B/S | [document-indexing](../features/document-indexing.md)、[admin visual contract](admin-ui-visual-contract.md) | `AdminCategoriesPage.test.tsx` | `admin-workflows.spec.ts`、`admin-golden.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 索引监控 | `/admin/index` | 旧目录索引与受管发布任务状态、筛选、预览、重试和安全删除 | 管理员 | `adminListIndexedDocuments`、`adminListIndexJobs`、受管任务 API | L/E/X/B/S/P | [document-indexing](../features/document-indexing.md)、[admin visual contract](admin-ui-visual-contract.md) | `AdminDocumentsPage.test.tsx` | `admin-workflows.spec.ts`、`admin-golden.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 视频媒体 | `/admin/media` | MP4 上传、人工/自动转录、任务恢复、版本审核和发布 | 管理员；具体操作受转录准入策略限制 | media、transcription job/profile、version API | L/E/X/D/B/S/P | [transcript-pipeline](../features/transcript-pipeline.md)、[admin visual contract](admin-ui-visual-contract.md) | `AdminMediaPage.test.tsx`、`useAdminMediaAssets.test.ts`、`useTranscriptionJobs.test.ts` | `media-page.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 概览 | `/admin/overview` | 管理统计、生产运行状态和维护策略摘要 | 管理员 | `adminOverviewApi.stats`、`adminOverviewApi.systemOverview`、`adminOverviewApi.maintenance` | L/X/S/P | [authentication](../features/authentication.md) | `AdminOverviewPage.test.tsx` | `admin-overview.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 系统维护 | `/admin/maintenance` | 对话保留策略、清理影响预览、手工清理和运行记录 | 管理员 | `adminMaintenanceApi.status`、`preview`、`runs`、`updateSettings`、`cleanup` | L/E/X/D/B/S | [authentication](../features/authentication.md) | `AdminMaintenancePage.test.tsx` | `admin-overview.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |
| 反馈 | `/admin/feedback` | 反馈筛选、分页、处理状态和备注 | 管理员 | `adminFeedback`、`adminPatchFeedback` | L/E/X/B/S/P | [feedback-management](../features/feedback-management.md) | `AdminFeedbackPage.test.tsx` | `feedback-page.spec.ts` | [manual-regression](../../frontend/tests/manual-regression.md) |

## 维护规则

1. 新页面或 tab 必须先在本清单增加一行，再提交源码和测试。
2. API、权限或状态机发生变化时，同时更新对应 feature 文档和本清单；纯样式调整只需更新视觉入口。
3. visual spec 使用合成数据，必须覆盖目标 viewport；golden 更新先生成候选截图，再人工审查后提交。
4. 页面清单不替代后端鉴权、功能事实文档或用户验收证据；实现和验证结果仍以 Git、CI 和用户验收为准。
