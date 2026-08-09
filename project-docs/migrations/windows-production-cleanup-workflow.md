# Windows production cleanup workflow

统一入口为 `scripts/cleanup-production.ps1`，底层继续调用三个独立清理器：

- `${PRODUCTION_ASR_ROOT}`：ASR staging、qualification 和 wheel-cache；
- `${PRODUCTION_RUNTIME_ROOT}`：GPU runtime release、qualification、resolver 和 pip-cache；
- `${PRODUCTION_BACKUP_DIRECTORY}`：旧 `gpu-service-backup-*` 部署备份。

统一入口只负责目标选择、DryRun/Apply 模式、失败即停和汇总报告，不复制各清理器的路径安全与保留逻辑。

## 触发方式

- 每天北京时间 03:30 执行全量 DryRun；
- 每 30 分钟检查 D 盘占用率；
- 部署工作流在 `deploy-gpu` 和 `deploy-app` 都成功后，自动清理旧 `RAGBackups`；
- 手动触发可选择 `all`、`asr`、`runtime` 或 `backups`；
- 不自动注册或启用 Windows 计划任务。

## 磁盘阈值

| D 盘使用率 | 动作 |
| --- | --- |
| `< 80%` | 只记录 |
| `80% - 85%` | 只记录告警 |
| `>= 85%` | 执行全量 DryRun 并上传候选报告 |
| `>= 90%` | 具备自动清理旧 `RAGBackups` 条件；还要求 `PRODUCTION_AUTO_CLEANUP_ENABLED=true` |
| `>= 95%` | 只告警，不自动删除 ASR 或 Runtime 数据 |

磁盘压力模式不会自动 Apply ASR 或 GPU Runtime。要自动清理这些缓存，需要另行增加只包含 staging、wheel-cache、resolver 和 pip-cache 的细粒度目标。

## 并发和安全

- 清理工作流与 GPU 部署共享 `production-gpu-exclusive` 并发组；
- 手动删除必须同时确认 Apply 和生产清理；
- 部署失败不会触发部署后清理；
- 自动部署后只清理旧备份，保留最新 3 份；
- 当前模型、当前 Runtime、qualification 证据和非匹配备份目录不会被统一入口绕过；
- 每次运行上传 JSON 审计报告。

## 回滚

停用工作流或将 `PRODUCTION_AUTO_CLEANUP_ENABLED` 设为 `false` 即可停止磁盘压力自动清理。已删除的旧备份不能由脚本自动恢复；部署回滚依赖保留的最新备份。
