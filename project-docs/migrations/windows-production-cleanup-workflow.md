# Windows production cleanup workflow

统一入口为 `scripts/cleanup-production.ps1`，底层继续调用三个独立清理器：

- `D:\ServiceData`：ASR staging、qualification 和 wheel-cache；
- `D:\RAGPinCheng\runtime`：GPU runtime release、qualification、resolver 和 pip-cache；
- `D:\RAGBackups`：旧 `gpu-service-backup-*` 部署备份。

统一入口只负责目标选择、DryRun/Apply 模式、失败即停和汇总报告，不复制各清理器的路径安全与保留逻辑。

## 运行模式

- 定时触发每天执行 DryRun，不删除生产数据；
- 手动触发可选择 `all`、`asr`、`runtime` 或 `backups`；
- 手动删除必须同时勾选 `apply` 和 `confirm_production_cleanup`；
- 每次运行报告上传为 GitHub Actions artifact；
- 不自动注册或启用 Windows 计划任务。

## 生产前置条件

生产 Windows runner 必须使用 `D:\RAGPinCheng` 的已部署代码，并存在三个目标根目录中被选择的目录。生产执行前先查看 DryRun 报告，再单独确认 Apply。

## 回滚与边界

清理失败会停止后续目标。已删除的缓存、历史运行结果和旧部署备份不能由脚本自动恢复；当前模型、当前 runtime、非匹配备份目录和各清理器明确保护的路径不会被统一入口绕过。
