# Windows GPU Runtime 自动清理

## 当前状态

已实现清理脚本和计划任务安装器；本地尚未连接生产机、注册计划任务或执行删除。

适用根目录固定为：

```text
D:\RAGPinCheng\runtime
```

本方案不处理 `D:\ServiceData`，后者由独立的 ASR 存储清理方案负责。

## 清理范围

| 路径 | 默认策略 |
|---|---|
| `releases\<release-id>` | 30 天后清理，保留当前 release 和最新 2 个已验证 release |
| `qualification\<run-id>` | 30 天后清理，保留最新 3 次 |
| `resolver\<run-id>-<attempt>` | 14 天后清理 |
| `pip-cache` 文件 | 30 天后清理，并限制到 8 GB |
| `cleanup-audit` 文件 | 保留 90 天 |
| `wheel-seed` | 永不自动删除 |

旧 release 只会在其 manifest 明确标记为 `qualified` 且 `validated` 时进入候选。缺失、损坏或状态未知的 manifest 会跳过。当前 `current-release.json` 指向的 release 永远受保护。

## 安全门禁

- 默认 DryRun；只有 `-Apply` 才允许删除。
- 支持 `-WhatIf`、`-Confirm` 和 JSON 审计报告。
- 根目录必须精确等于 `D:\RAGPinCheng\runtime`。
- 拒绝 reparse point 和越出 runtime 根目录的路径。
- 活跃标记、最近 24 小时修改的目录和活动 runtime 进程会被跳过。
- 单次默认最多删除 20 GB，超出时直接失败。
- 不停止生产 GPU 服务，不删除当前 release，不删除 `wheel-seed`。

## 首次启用

先在生产机执行只读预览：

```powershell
Set-Location D:\RAGPinCheng

.\scripts\cleanup-gpu-runtime.ps1 `
  -RuntimeRoot D:\RAGPinCheng\runtime `
  -AuditPath D:\RAGPinCheng\runtime\cleanup-audit\manual-dryrun.json
```

复核候选清单后，再执行 PowerShell 的二次预览：

```powershell
.\scripts\cleanup-gpu-runtime.ps1 `
  -RuntimeRoot D:\RAGPinCheng\runtime `
  -Apply `
  -WhatIf `
  -AuditPath D:\RAGPinCheng\runtime\cleanup-audit\apply-preview.json
```

连续观察至少 7 天，确认候选没有正在使用的 release 或资格任务后，才可单独批准 `-Apply`。

计划任务默认仍为 DryRun：

```powershell
.\scripts\install-gpu-runtime-cleanup-task.ps1 `
  -RepositoryPath D:\RAGPinCheng `
  -RuntimeRoot D:\RAGPinCheng\runtime `
  -StartTime 03:30 `
  -WhatIf
```

只有完成 DryRun 观察并取得单独生产删除确认后，才允许使用 `-EnableApply` 注册自动删除任务。

## 验证与回滚

清理前后检查：

```powershell
Get-ScheduledTask -TaskName RAGPinCheng-GPU -ErrorAction SilentlyContinue
Get-Content D:\RAGPinCheng\runtime\current-release.json
Get-PSDrive D
```

随后验证 GPU 服务 `/health`、`/model-info`、embedding 和 rerank 冒烟。

删除的旧 release 不提供脚本内自动恢复；常规回滚依赖保留的当前/最近 release。超出保留窗口的版本需要从已验证 runtime lock、模型缓存和 qualification 证据重新构建。
