# 生产部署拓扑

本文档记录当前仓库能够确认的生产部署边界。主机地址、磁盘路径、私有 Compose 覆盖文件、生产 `.env` 和令牌不写入仓库，统一由 GitHub Environment `production-asr` 的 Variables/Secrets 提供。

## 节点职责

| 节点 | GitHub Actions Runner 标签 | 运行服务 |
| --- | --- | --- |
| Ubuntu 应用节点 | `self-hosted, Linux, X64, ubuntu, production, app` | `backend`（FastAPI + 构建后的 React 前端）、`qdrant`、`libreoffice` |
| Windows GPU/ASR 节点 | `self-hosted, Windows, X64, asr-production` | ASR HTTP 服务、GPU Embedding、GPU Rerank、模型缓存与运行时 |

Ubuntu 节点的服务定义位于 `docker/docker-compose.yml`。前端不单独部署 Node/Nginx 容器，由 `backend` 镜像同时提供 `/api/*` 和前端页面。

## 发布入口

- 应用节点：`.github/workflows/deploy-production-app-manual.yml`
- Windows GPU/ASR 节点：`.github/workflows/deploy-asr-production.yml`
- Windows ASR 激活与 Ubuntu 联调：`.github/workflows/activate-asr-production.yml`

应用发布只接受 `master`，需要手动选择 `DEPLOY_APP`，并在 `production-asr` 环境执行。工作流会备份 SQLite、Qdrant 快照和旧后端镜像，失败时执行后端回滚。

涉及部署、生产验证或服务归属的任务，先阅读本文件和对应 workflow；不要从仓库推断私有主机地址或凭据，也不要在未确认目标环境时直接执行生产操作。
