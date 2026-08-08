# Windows GPU ASR 存储清理

> 适用路径：`${PRODUCTION_DATA_ROOT}`  
> 默认模式：只读预览（DryRun）  
> 生产实际删除：必须显式传入 `-Apply`，并在执行前复核候选清单。

## 目标

清理 Windows GPU 主机上的过期 ASR 临时数据，主要包括：

- `model-preparation\*\staging`
- `qualification` 下带时间命名的历史运行目录
- `wheel-cache` 中过期或超过容量上限的文件

以下目录永不作为自动清理目标：

- `RAGPinCheng-ASR\models`
- `RAGPinCheng-ASR-WhisperX\models`
- 无法识别时间或状态的 qualification 目录
- 活跃标记、锁文件、近期修改的目录

## 保留策略

| 类型 | 默认策略 |
|---|---|
| 正式模型 | 永久保留 |
| `staging` | 7 天 |
| `qualification` 历史运行 | 30 天，且保留最新 3 次 |
| 每个 `wheel-cache` | 30 天；超过 8 GB 时从最旧文件开始回收 |
| 活跃或状态不明路径 | 跳过，不自动删除 |

`wheel-cache` 的 8 GB 限制按每个缓存目录分别计算。删除 wheel 后，后续重新创建环境可能需要重新下载依赖。

## 脚本

脚本位置：

```text
scripts\cleanup-asr-storage.ps1
```

### 1. 生产机 DryRun

```powershell
Set-Location C:\RAGPinCheng

.\scripts\cleanup-asr-storage.ps1 `
  -RootPath '${PRODUCTION_DATA_ROOT}' `
  -AuditPath '${PRODUCTION_DATA_ROOT}\cleanup-audit\dryrun.json'
```

DryRun 不删除文件。先检查输出中的 `Candidates`、`Skipped paths` 和预计释放空间。

### 2. 生产删除前复核

```powershell
.\scripts\cleanup-asr-storage.ps1 `
  -RootPath '${PRODUCTION_DATA_ROOT}' `
  -Apply `
  -WhatIf `
  -AuditPath '${PRODUCTION_DATA_ROOT}\cleanup-audit\apply-preview.json'
```

`-Apply -WhatIf` 仍然只预览，用于确认 PowerShell 的实际删除目标。

### 3. 执行删除

仅在确认没有 ASR 下载、模型准备或 qualification 任务运行时执行：

```powershell
.\scripts\cleanup-asr-storage.ps1 `
  -RootPath '${PRODUCTION_DATA_ROOT}' `
  -Apply `
  -Confirm `
  -AuditPath '${PRODUCTION_DATA_ROOT}\cleanup-audit\apply.json'
```

脚本不会备份后再删除大文件。需要保留历史 qualification 证据时，应先把对应目录复制到独立备份介质。

## 定时任务建议

第一周只配置每天低峰期 DryRun，观察候选目录是否符合预期；不要直接配置 `-Apply`。

建议任务参数：

```text
Program:
  powershell.exe

Arguments:
  -NoProfile -ExecutionPolicy Bypass -File C:\RAGPinCheng\scripts\cleanup-asr-storage.ps1
  -RootPath ${PRODUCTION_DATA_ROOT}
  -AuditPath ${PRODUCTION_DATA_ROOT}\cleanup-audit\scheduled-dryrun.json
```

连续观察一周且候选清单稳定后，再单独批准启用带 `-Apply` 的任务。任务账号需要能读取和删除 ASR 缓存，但不应获得不必要的仓库或业务数据权限。

## 回滚与故障处理

- 删除正式模型不会发生；模型服务应继续使用现有模型目录。
- 删除的 wheel 缓存可以通过重新安装依赖重新生成。
- 删除的 staging 或 qualification 目录不能由脚本自动恢复，需从备份恢复或重新运行任务。
- 发现误删候选时，立即停用计划任务，保留审计 JSON，并根据审计路径从备份恢复。
- 不要使用 `docker system prune` 或删除 Docker Volume 作为本清理流程的一部分。

## 验证

清理后检查：

```powershell
Get-ChildItem '${PRODUCTION_DATA_ROOT}' -Force -Directory
Get-PSDrive D
```

并验证：

1. ASR 服务进程仍在运行；
2. 正式模型目录仍完整；
3. 新的转录任务可以启动；
4. 现有 BGE/GPU 服务健康检查正常；
5. 审计 JSON 与实际删除路径一致。
