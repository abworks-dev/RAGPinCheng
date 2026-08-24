# Windows GPU ASR 存储生命周期

> 默认模式：只读预览（DryRun）
> 生产实际删除：必须显式传入 `-Apply`，并在执行前复核 JSON 候选清单。
> 本次迁移不批量删除已有历史目录。

## 管理范围

脚本使用现有的明确根目录变量，不再依赖不存在的 `PRODUCTION_ASR_ROOT`：

- `PRODUCTION_ASR_DATA_ROOT`：部署 dependency run、失败 staging 和证据备份；
- `PRODUCTION_ASR_PROGRAM_ROOT`：只用于固定生产根身份校验，不清理当前 app/venv；
- `PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT`：`runs\<run_id>`；
- `PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT`：`runs\<run_id>`；
- `PRODUCTION_WHISPERX_ROOT`：`qualification\runs\<run_id>`。

以下内容永不作为自动收缩目标：正式模型、当前 app/venv、共享 `wheel-cache`、配置、
app/venv 回滚备份，以及 qualification 中的 `reports`、`evidence`、`logs`、`state`、
`config`、manifest 和 verdict。

`dependency-runs\candidate-<workflow_run_id>` 在候选识别阶段只做只读分类，不进入清理候选。
Inventory 会同时核对候选 ID、`releases\<id>`、`config\releases\<id>`、release manifest、
`release-state\active.json` 和 activation rollback state。活动、回滚引用、闭包不完整、身份冲突、
非法命名或无法测量的候选均保持保护状态；只有后续独立 R3 方案获批后，才可能讨论删除。

## 保留策略

| 类型 | 默认策略 |
| --- | --- |
| 成功 qualification | artifact 上传后只收缩本次 run 的可重建重资产 |
| 失败 qualification | 完整保留 24 小时，之后只收缩可重建重资产 |
| 已收缩 qualification run | 30 天，且保留最新 3 次 |
| 成功部署 dependency run | 部署成功后只删除当前 commit 对应的精确目录 |
| 失败或遗留 dependency run | 7 天 |
| `failed-staging-*` / `stale-staging-*` | 7 天，且保留最新 2 次 |
| 正式模型、共享 wheel cache、当前程序和回滚备份 | 不自动删除 |

qualification 的可重建重资产白名单固定为：`venv`、`wheelhouse`、
`shared-wheel-seed`、`model-staging`、`spool`、`temp`。新增目录不会自动进入白名单。

超过 30 天的完整 qualification run 在删除前，会把小型证据复制到
`${PRODUCTION_ASR_DATA_ROOT}\cleanup-evidence-backup\<engine>\<run_id>`，并生成包含
SHA-256 的 `inventory.json`。如果目标备份目录已存在，脚本失败关闭并要求人工复核。

## 单次运行收缩

`scripts\compact-asr-run.ps1` 只接受一个确定的 workflow run ID 或部署 commit SHA。
工作流始终上传审计 JSON；只有任务成功且环境变量
`PRODUCTION_ASR_RUN_COMPACTION_ENABLED=true` 时才传入 `-Apply`。变量缺失或为其他值时
仅执行 DryRun，失败 qualification 也只执行 DryRun。

首次上线保持该变量未设置或设为 `false`。合并后先执行一次受控 qualification，检查
compaction artifact 中的根目录、候选路径和字节数；确认无误后再将变量设为 `true`，并
再次运行一项 qualification 验证磁盘差值。不要用该开关处理历史目录。

## 周期清理

统一入口 `scripts\cleanup-production.ps1` 会调用 `scripts\cleanup-asr-storage.ps1`。手工
预览示例：

```powershell
.\scripts\cleanup-asr-storage.ps1 `
  -DataRoot $env:PRODUCTION_ASR_DATA_ROOT `
  -ProgramRoot $env:PRODUCTION_ASR_PROGRAM_ROOT `
  -FasterWhisperQualificationRoot $env:PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT `
  -Qwen3AsrQualificationRoot $env:PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT `
  -WhisperXRoot $env:PRODUCTION_WHISPERX_ROOT `
  -AuditPath (Join-Path $env:RUNNER_TEMP 'asr-cleanup-dryrun.json')
```

DryRun 后必须复核：每个候选是否位于上述固定根目录、是否匹配 run ID/commit 命名、
`Kind` 是否属于策略、总量是否低于删除上限、`Skipped` 是否包含正在使用的目录。

`.github/workflows/cleanup-production-operations.yml` 每天执行一次完整 DryRun，并每小时
两次检查 `D:` 盘压力。只有同时满足以下条件时，压力检查才会自动执行 ASR Apply：

- 使用率处于 `backup-apply` 档位（默认 90% 至 95%，`critical` 档位失败关闭）；
- 仓库变量 `PRODUCTION_AUTO_CLEANUP_ENABLED=true`；
- 同一次 workflow run 的 DryRun 成功生成 ASR batch manifest；
- Apply 下载该 preview artifact，并使用 workflow 输出的精确 SHA-256；
- 候选在 Apply 前重新测量且与 manifest 完全一致。

Preview artifact 使用 `production-cleanup-<run_id>-<run_attempt>`，Apply 审计使用独立的
`production-cleanup-<run_id>-<run_attempt>-apply`，避免覆盖审批证据，也允许 workflow
rerun 生成新的不可变 artifact。Preview 将精确 artifact 名称和 manifest SHA-256 一并
输出给 Apply；手工 Apply 未提供新名称时仍兼容旧的 `production-cleanup-<run_id>`。
默认单候选 20 GB、单批 18 GB 上限继续生效；超限时整体失败，不扩大清理范围。

磁盘压力流程严格按 `pressure-dryrun`、`auto-clean-asr`、`auto-clean-backups` 串行，
避免多个复用 workflow 同时等待 `production-gpu-exclusive` concurrency group 时互相替换。

该流程只清理明确配置的 qualification、dependency 和 failed staging 受管根目录。
它不会递归删除 GitHub runner 通用 `_work`/`_temp`、正式模型、共享 `wheel-cache`、
active candidate 或 rollback backup，因为这些目录无法仅凭年龄可靠判断是否被其他任务使用。
qualification 中可重建的 `venv`、`wheelhouse`、`model-staging`、`spool` 和 `temp`
已覆盖本次磁盘不足的主要来源，并继续接受进程和 active marker 检查。

## 回滚与验证

停止后续收缩：将 `PRODUCTION_AUTO_CLEANUP_ENABLED` 和
`PRODUCTION_ASR_RUN_COMPACTION_ENABLED` 设为 `false`。前者关闭磁盘压力自动 Apply，
但保留周期 DryRun；后者关闭单次 qualification 成功后的收缩。仓库回滚可恢复旧
workflow 和脚本，但不能恢复已经删除的可重建目录。

删除的 venv、wheelhouse、spool 和 temp 需通过重新运行 qualification/部署重建；删除的
完整历史 run 只能从 `cleanup-evidence-backup`、workflow artifact 或外部备份恢复证据。
不要运行 `docker system prune`、删除 Docker Volume、正式模型或共享 wheel cache。

每次受控验证检查：

1. 审计 JSON 的 `mode`、`target_root`、候选和 `Deleted` 与实际一致；
2. `reports/evidence/logs/state/config` 仍存在，正式模型和共享 wheel cache 未变化；
3. ASR 服务健康，新的转录或下一次 qualification 能正常启动；
4. `Get-PSDrive D` 显示空间变化与审计字节数合理一致。
