# Windows production cleanup workflow

统一入口为 `scripts/cleanup-production.ps1`，底层继续调用三个独立清理器：

- `${PRODUCTION_ASR_DATA_ROOT}`、`${PRODUCTION_ASR_PROGRAM_ROOT}` 及三个引擎的明确
  qualification 根：ASR run 重资产、遗留 dependency run 和失败 staging；
- `${PRODUCTION_RUNTIME_ROOT}`：GPU runtime release、qualification、resolver 和 pip-cache；
- `${PRODUCTION_BACKUP_DIRECTORY}`：旧 `gpu-service-backup-*` 部署备份。

统一入口只负责目标选择、DryRun/Apply 模式、失败即停和汇总报告，不复制各清理器的路径安全与保留逻辑。

## 触发方式

- 每天北京时间 03:23 执行全量 DryRun；
- 每小时第 7、37 分钟检查 D 盘占用率，避免与夜间 DryRun 同分钟触发；
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

- 清理、ASR 部署及三个 qualification 工作流共享 `production-gpu-exclusive` 并发组；
- 手动删除必须同时确认 Apply 和生产清理；
- 部署失败不会触发部署后清理；
- 自动部署后只清理旧备份，保留最新 3 份；
- 当前模型、当前 Runtime、共享 wheel cache、qualification 小型证据和非匹配备份目录
  不会被统一入口绕过；
- 每次运行上传 JSON 审计报告。

ASR 周期清理默认仍为 DryRun。成功 qualification 或部署的单次精确收缩由
`PRODUCTION_ASR_RUN_COMPACTION_ENABLED` 控制；该变量只有精确值 `true` 且前序任务成功
时才会 Apply，其他情况只生成审计。首次上线不得用它批量处理历史目录。

## 回滚

停用工作流、将 `PRODUCTION_AUTO_CLEANUP_ENABLED` 设为 `false`，并将
`PRODUCTION_ASR_RUN_COMPACTION_ENABLED` 设为 `false`，即可停止自动删除。已删除的旧备份
和可重建运行目录不能由脚本自动恢复；部署回滚依赖保留的最新备份。
