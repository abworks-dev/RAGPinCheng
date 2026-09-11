# 转录工作台：版本列表左置 + 批量删除旧版本（实施方案）

状态：待实施（用户已确认按推荐选项执行）
任务分支：`codex/transcript-version-batch-delete`
风险等级：R3（引入对真实转录版本的**删除**能力，涉及托管产物与索引数据）

## 1. 目标与当前依据

用户诉求（2026-09-11）：
1. 转录工作台抽屉里“转录版本”列表目前位于**顶部**，不直观；改为**左侧边**，便于快速翻页点选。
2. 增加**批量选择**，允许点选删除**旧版本**转录结果。

当前实现依据：

- 抽屉：`frontend/src/components/TranscriptionWorkbenchSheet.tsx` 渲染 `<TranscriptionVersionPanel embedded />`。
- 版本列表：`frontend/src/components/TranscriptionVersionPanel.tsx` 第 240–313 行，`<section aria-label="转录版本列表">` 位于面板顶部，`max-h-[18rem]` 滚动区，逐条 `<article>`；每条已有“校对内容/审核通过/拒绝/发布”动作。
- 后端：`api/routes_transcription.py` 只有 `GET /media/{media_id}/versions`、`/versions/{id}/markdown`、`/versions/{id}/timeline`、`/versions/{id}/review`、`/versions/{id}/return-to-review`、`/versions/{id}/publish`、`GET /publication-jobs/{id}`；**没有任何删除单个/多个版本的接口**。现有删除只发生在媒体级回收站清理（`api/content_trash_cleanup.py`，删除该媒体全部版本）。
- 约束：`transcript_versions` 被以下外键以 `ON DELETE RESTRICT` 引用 —— `media_transcript_heads.current_version_id`、`transcript_publication_index_jobs.transcript_version_id`、`transcript_versions.supersedes_version_id` / `derived_from_version_id`（`api/db_migrations.py`）。删除必须显式处理这些引用。

## 2. 已确认的决策（最保守选项）

1. **可删范围**：仅允许删除**从未发布过**的旧版本，即同时满足
   - 不是当前正式 head（不等于 `media_transcript_heads.current_version_id`）；
   - `publication_status = 'not_published'`；
   - `review_status != 'awaiting_review'`（待审核稿不可删）；
   - 没有其它版本以 `supersedes_version_id` 或 `derived_from_version_id` 指向它。
   已发布 / 发布中 / 发布失败 / 待审核 / 当前 head 一律**拒绝**并返回结构化原因。
2. **删除方式**：**永久删除**（版本行 + 该版本对应的托管 markdown 产物文件），并写入审计事件（actor、媒体、被删版本号/id、原因）。
3. **作用范围**：仅限**当前视频**的版本列表（不做跨视频批量）。
4. 前端必须二次确认，逐项展示不可删除原因，批量操作保留部分成功结果。

## 3. 接口设计

`POST /api/admin/transcription/media/{media_id}/versions/bulk-delete`（管理员 + CSRF）：

```
请求: { "version_ids": ["...", ...], "request_idempotency_key": "<uuid>" }
响应 202: {
  "items": [ { "version_id": "...", "status": "deleted"|"unavailable"|"conflict",
               "reason": "<产品化中文原因>" } ],
  "deleted_count": n, "skipped_count": m
}
```

规则：
- 逐项校验（媒体归属、上节 4 条可删条件），单项失败不阻塞其余项；
- 幂等：同一 `request_idempotency_key` 重复提交返回首次结果，不重复删除；
- 事务：每项独立事务，删除版本行 + 关联产物；任何异常项返回 `conflict` 与原因，不中断批量；
- 不修改 `media_transcript_heads`，不触碰其它版本；
- 审计事件记录到既有审计表（或既有 audit 约定），不新增 schema 变更。

## 4. 前端设计

- 抽屉主体改为两列：`grid gap-4 lg:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)]`。
  - 左列：版本列表（含序号、状态徽标、`当前正式版本` 标记、`校对/选择` 行为），`lg:sticky lg:top-0`、自身滚动，不再占用顶部空间。
  - 右列：原校对工作区与预览。
  - `<lg`（含 390px）：左列降级为顶部可折叠选择器（复用现有 Button/Select 组件），不横向溢出。
- 快速翻页：左列顶部“上一版/下一版”按钮 + 键盘 `Alt+ArrowUp/Alt+ArrowDown`（需 `focus-visible` 与 aria-label），选择后同步滚动定位并加载该版本预览。
- 批量选择：左列“批量选择”开关 → 每条出现 checkbox（统一组件，不用浏览器默认外观）；底部操作条显示“已选 N 个 · 删除所选 · 取消”；不可删项 checkbox 禁用并给出悬浮原因。
- 删除确认：Dialog 列出将删除的版本（序号/时间/来源）与将被永久删除的产物说明；确认后调用批量接口；结果以 `role="status"` 汇总“成功 X / 跳过 Y”，逐项原因在列表内展示；部分成功保留可重试项。
- 状态覆盖：loading / empty / error / busy / disabled / 成功反馈；批量操作保留部分成功与恢复入口。

## 5. 验证方式

- 后端：`tests/test_transcription_phase4_api.py` 新增用例 —— 可删旧版本被删；head/已发布/待审核/被引用版本被拒；部分成功；幂等键重复提交；非管理员与缺 CSRF 被拒。
- 前端：`npm run build`；`TranscriptionVersionPanel.test.tsx`、`TranscriptionWorkbenchSheet.test.tsx` 及 `npm run test:run`；新增批量选择、不可删禁用、确认流程、部分成功用例。
- 真实浏览器验收（合成数据）：`1280x720` 与 `390x844`，检查 `body` 无横向溢出、核心操作可见、勾选与确认可达、partial-failure 提示、键盘路径。
- 生产验收：由用户在抽屉中实际操作（选择 → 删除 → 确认），验收通过后更新 TODO 状态。

## 6. 风险与回滚

- 风险：误删旧版本（缓解：只允许 never-published、head 与待审核永不可删、二次确认、审计留痕、逐项原因）；
  发布/索引数据一致性（缓解：已发布/发布中/发布失败一律拒绝，不触碰索引任务行）。
- 回滚：整体 revert 提交；无 schema 迁移、无批量数据改写；部署前生产 `app.sqlite` 已有 workflow 自动备份（另可在删除操作前手动再备一次）。

## 7. 明确不做

- 不做跨视频批量删除；不做软删除/回收站；不允许删除已发布或待审核版本；不修改 `media_transcript_heads`；不新增数据库 schema；不改发布与索引流程。
