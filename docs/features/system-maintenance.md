# 系统维护

- 状态：已实现
- 最后核对：2026-08-16

## 用户可观察能力

管理员可在独立的“系统维护”页面查看自动清理状态，配置历史对话保留期限，预览待清理数量，手动执行清理并查看最近运行记录。默认策略为启用自动清理并保留 30 天。

## 当前边界

### 已实现

- 历史对话自动清理可启用或停用，保留期限允许设置为 7 至 3650 天；
- 无持久化配置时使用“启用、30 天”的兼容默认值；
- 保存设置不立即删除数据，后台任务下一次检查时读取最新策略；
- 预览统计对话、关联消息和失效登录会话，不修改数据；
- 手动清理采用当前已保存的对话策略，即使自动清理已停用也可执行；
- 失效登录会话始终按认证自身的 `expires_at` 清理，不使用对话保留期限；
- 自动和手动运行保存脱敏统计，最多保留最近 200 条，管理页面展示最近 20 条；
- 缩短期限、停用自动清理和立即删除均使用带影响说明的确认对话框。

### 未实现

- 对话归档、软删除和页面内恢复；
- 数据库备份下载或恢复；
- 生产环境部署与真实数据清理。

## 入口与调用链

```text
AdminMaintenancePage
→ /api/admin/maintenance/*
→ api/maintenance.py
→ maintenance_settings / maintenance_runs
→ conversations / messages / auth_sessions
```

后台执行：

```text
api.main._sweeper_loop（每小时）
→ run_cleanup(trigger_source="automatic")
→ 读取最新设置
→ 清理符合条件的对话和失效登录会话
→ 写入脱敏运行统计
```

## 关键文件

- `api/maintenance.py`
- `api/db_migrations.py`
- `api/routes_admin.py`
- `api/schemas.py`
- `api/main.py`
- `frontend/src/pages/admin/AdminMaintenancePage.tsx`
- `frontend/src/api/client.ts`

## 数据契约

- `maintenance_settings` 是 `singleton_id = 1` 的单例策略；
- `conversation_retention_days` 范围为 7 至 3650；
- `maintenance_runs` 只保存触发方式、状态、当次策略、删除数量、时间和脱敏错误类型；
- 删除 `conversations` 时，外键级联删除关联消息、问题与回答版本；
- API 预览数量是请求时快照，执行响应中的实际删除数量才是最终结果。

## 不变量与安全边界

- 维护读取接口要求管理员权限，设置和清理接口同时要求管理员 CSRF；
- 设置保存和预览不得执行删除；
- 登录会话期限与对话保留策略独立；
- 运行记录不得保存对话标题、正文、用户身份、Cookie 或 CSRF Token；
- 已删除数据不能通过应用页面恢复，生产执行前依赖独立数据库备份流程。

## 验证

- 验证默认策略、设置边界、预览只读、自动清理开关、手动清理和外键级联；
- 验证匿名、普通用户、管理员和 CSRF 依赖；
- 验证维护页面加载、缩短期限确认、手动清理确认和运行记录；
- 前端执行相关单元测试、构建和管理后台多视口视觉检查。

## 相关决策

- 暂无独立 ADR；现有约束见根目录 `CLAUDE.md`。
