# 反馈处理工作流

## 当前状态

已实现。用户提交的回答与来源反馈保持为追加式 JSONL 原始记录；管理员可以在管理面板中筛选、搜索、分页并维护处理状态。

## 调用链

```text
FeedbackBar / SourcesPanel
→ POST /api/feedback
→ data/feedback.jsonl（原始反馈）

AdminFeedbackPage
→ GET /api/admin/feedback（筛选、统计、分页）
→ PATCH /api/admin/feedback/{feedback_id}（CSRF 管理员）
→ app.sqlite / feedback_workflow（处理状态）
```

## 数据边界

- 新反馈写入 UUID `feedback_id`；历史记录根据行位置和原始内容生成稳定兼容 ID，不重写原文件。
- 原始评价与管理状态分离：`rating=down` 表示用户认为回答需改进，`status` 表示管理员处理进度。
- 状态为 `pending`、`in_progress`、`resolved`、`archived`。
- 完成记录必须选择处理结果；归档可恢复，不提供永久删除接口。
- 工作流表保存处理人、备注、更新时间和完成时间。管理员变更接口同时要求管理员权限和 CSRF。

## 关键入口

- `api/feedback.py`
- `api/routes_admin.py`
- `api/db_migrations.py`
- `api/schemas.py`
- `frontend/src/pages/admin/AdminFeedbackPage.tsx`

## 验证

- `python -m pytest tests/test_feedback_workflow.py tests/test_transcription_db_migrations.py -q`
- `cd frontend && npm run test:run`
- `cd frontend && npm run build`

## 已知边界

- 原始反馈仍按 JSONL 全量读取，适用于当前内部知识库规模；若记录量显著增长，应评估将原始反馈一并迁入数据库。
- 状态更新采用最后写入生效，当前未提供多人同时编辑的版本冲突提示。
