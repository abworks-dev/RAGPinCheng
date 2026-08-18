# RAGPinCheng Git 同步、部署与测试流程

本文描述代码交付与生产部署的边界。生产部署不是 `git push` 后自动发生的
普通副作用；必须由受保护的 GitHub Actions workflow、完整 master SHA、
environment 审批、备份和健康检查共同完成。

## 架构与服务

```text
浏览器 -> Ubuntu backend/React -> Qdrant + SQLite
                              -> Windows GPU service
                              -> LibreOffice service
```

ASR、受管资料迁移、索引下线、目录归档和生产清理均有独立 workflow，不应
塞入普通应用部署步骤。

## 日常开发

```powershell
git switch master
git pull --ff-only origin master
git switch -c codex/<short-name>

# 修改后运行与变更范围匹配的测试
git diff --check
git add <files>
git commit -m "<type>: <summary>"
git push -u origin codex/<short-name>
```

生产分支是否允许直接更新由仓库保护规则和当前任务授权决定；不要在生产
主机上创建临时 feature 分支或用未审计的 `git checkout` 覆盖工作树。

## CI

推送或 PR 是否触发 CI 以 `.github/workflows/ci.yml` 的实际配置为准。提交
交付时记录实际运行的 job、失败项和未运行项，不把历史 workflow 名称或旧的
“全部通过”文字复制到新报告中。

## 生产应用部署

当前应用部署使用手动 workflow，例如 `Deploy Production App + Content/ASR
Manual`。执行前必须：

1. 从 `master` 选择完整 40 位 commit SHA；
2. 由 workflow 校验 commit、环境、锁和生产部署互斥锁；
3. 备份 `app.sqlite`、`parents.sqlite`、受管内容和相关运行状态；
4. 按 workflow 构建 backend，执行 Compose health、数据库完整性、GPU 契约、
   managed-content 和索引可见性检查；
5. 所有检查通过后才宣布部署完成。

不要在服务器上直接运行 `git pull && docker compose up` 代替受控 workflow。
普通 IT 操作见 [部署指南_IT.md](部署指南_IT.md)。

## GPU 与 ASR

GPU 和 ASR 服务由各自的 production workflow 管理。GPU runtime 必须绑定
validated lock、源码 fingerprint 和 qualification evidence；ASR Profile 的
准入、模型准备、promotion 和回滚不能由普通应用部署隐式触发。

相关入口：

- `deploy-production-app-manual.yml`；
- `deploy-production-emergency.yml`；
- `deploy-asr-production.yml`；
- [GPU 部署与运行时说明](GPU_DEPLOYMENT.md)；
- [Ubuntu 应用与 Windows GPU 迁移手册](../migrations/ubuntu-app-windows-gpu-runbook.md)。

## 数据、索引与回滚

禁止在普通部署中执行以下操作：

- `docker compose down -v`；
- Qdrant collection 删除或 volume 操作；
- `build_index.py --reset`；
- 真实资料删除、数据库破坏性迁移或旧目录物理归档。

应用回滚使用 workflow 保存的上一版本和备份，不在生产主机直接切换未知
commit。受管资料迁移、索引下线和 source 解耦见
[生产迁移 Runbook](../migrations/managed-content-production-runbook.md)。

## 验收

部署后至少核对：

- `/api/health` 和 GPU `/health`；
- 登录、会话恢复、普通问答、引用打开和失败状态；
- Office/ASR/受管资料功能只在对应开关和服务启用时验收；
- 数据库、索引和 Qdrant 状态未发生未批准变化。

用户可观察功能按 [用户验收交接规范](../USER_ACCEPTANCE.md) 记录，不建立重复
的仓库工作日志。
