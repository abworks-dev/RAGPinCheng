# 生产部署拓扑

本文档记录当前仓库能够确认的生产部署边界。主机地址、生产 `.env` 的内容和令牌不写入仓库，统一由 GitHub Environment `production-asr` 的 Variables/Secrets 提供。经确认且不含凭据的运维路径记录在下文，供排障时识别正确节点和 Compose 栈。

## 节点职责

| 节点 | GitHub Actions Runner 标签 | 运行服务 |
| --- | --- | --- |
| Ubuntu 应用节点 | `self-hosted, Linux, X64, ubuntu, production, app` | `backend`（FastAPI + 构建后的 React 前端）、`qdrant`、`libreoffice` |
| Windows GPU/ASR 节点 | `self-hosted, Windows, X64, asr-production` | ASR HTTP 服务、GPU Embedding、GPU Rerank、模型缓存与运行时 |

Ubuntu 节点的服务定义位于 `docker/docker-compose.yml`。前端不单独部署 Node/Nginx 容器，由 `backend` 镜像同时提供 `/api/*` 和前端页面。

Windows 上的 Docker Desktop 不承载生产 `backend`、`qdrant` 或 `libreoffice`。在 Windows 项目目录执行 `docker compose` 只能检查本机 Docker，不能判断 Ubuntu 生产容器状态。PPTX 转 PDF 由 Ubuntu 节点的 `libreoffice` 容器完成，与 Windows GPU/ASR 服务无关。

## 生产 Compose 快速参考

生产应用栈使用以下固定信息：

| 项目 | 值 |
| --- | --- |
| Compose project | `ragpincheng-prod` |
| 仓库基础 Compose | `${PRODUCTION_APP_REPO_PATH}/docker/docker-compose.yml` |
| 私有生产 override | `/data/services/docker/compose/ragpincheng/prod/compose.prod.yaml` |
| Compose env 文件 | `/data/secrets/ragpincheng/prod.env` |
| LibreOffice 文件上限（本次批准目标） | `LIBREOFFICE_MAX_FILE_MB=8192`（8 GiB） |
| Source 解耦 overlay | `${PRODUCTION_APP_REPO_PATH}/docker/compose.source-decoupled.yml` |

`PRODUCTION_APP_REPO_PATH`、`PRODUCTION_APP_COMPOSE_OVERRIDE` 和 `PRODUCTION_APP_ENV_FILE` 由 GitHub Environment `production-asr` 提供。不要输出或提交 `prod.env` 的内容。

部署 workflow 先合并仓库基础 Compose 与私有 override。`SOURCE_DECOUPLING_COMPLETE=true` 时，还会清理私有 override 中已经废弃的 source mount，并把 `compose.source-decoupled.yml` 作为最终 overlay；不要把私有 `compose.prod.yaml` 单独当作完整应用栈。

在 Ubuntu 生产仓库根目录进行只读状态检查：

```bash
docker compose \
  -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f /data/services/docker/compose/ragpincheng/prod/compose.prod.yaml \
  --env-file /data/secrets/ragpincheng/prod.env \
  ps
```

不确定生产仓库路径时，可以先按 Compose 标签检查实际运行容器，不需要读取 env 文件：

```bash
docker ps \
  --filter label=com.docker.compose.project=ragpincheng-prod
```

检查 PPTX 转换服务：

```bash
docker compose \
  -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f /data/services/docker/compose/ragpincheng/prod/compose.prod.yaml \
  --env-file /data/secrets/ragpincheng/prod.env \
  ps libreoffice

docker compose \
  -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f /data/services/docker/compose/ragpincheng/prod/compose.prod.yaml \
  --env-file /data/secrets/ragpincheng/prod.env \
  logs --tail 200 libreoffice

docker compose \
  -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f /data/services/docker/compose/ragpincheng/prod/compose.prod.yaml \
  --env-file /data/secrets/ragpincheng/prod.env \
  exec libreoffice curl -fsS http://localhost:8101/health
```

这些命令只用于状态、日志和健康检查，不构成重建或重启生产容器的授权。

## 发布入口

- 应用节点：`.github/workflows/deploy-production-app-manual.yml`
- Windows GPU/ASR 节点：`.github/workflows/deploy-asr-production.yml`
- Windows ASR 激活与 Ubuntu 联调：`.github/workflows/activate-asr-production.yml`

应用发布只接受 `master`，需要手动选择 `DEPLOY_APP`，并在 `production-asr` 环境执行。工作流会备份 SQLite、Qdrant 快照和旧后端镜像，失败时执行后端回滚。

需要重建或更新 Ubuntu 生产容器时，使用 `Deploy Production App + Content/ASR Manual` workflow（`.github/workflows/deploy-production-app-manual.yml`），并绑定获批的 `master` commit。常规应用重建选择：

- `confirm_production=DEPLOY_APP`
- `transcription_admission=PRESERVE_CURRENT`
- `content_root_policy=PRESERVE_EXISTING`
- `schema_migration=APPLY_PENDING`，除非变更方案明确要求阻止待执行迁移

该 workflow 负责 Compose 配置组装、备份、构建、健康检查和失败回滚。不要在生产服务器上用手工 `git pull && docker compose up -d --build` 替代它，也不要为了恢复单个服务执行 `docker compose down -v`。

涉及部署、生产验证或服务归属的任务，先阅读本文件和对应 workflow；不要从仓库推断私有主机地址或凭据，也不要在未确认目标环境时直接执行生产操作。
