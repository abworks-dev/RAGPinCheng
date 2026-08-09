# RAGPinCheng 部署与测试流程

## 1. 架构概览

```text
用户浏览器
  → HTTPS
Ubuntu ${APP_NODE_IP}（应用节点）
  ├─ FastAPI + React（backend 容器）
  ├─ Qdrant（向量数据库容器）
  ├─ app.sqlite / parents.sqlite / parsed/
  └─ 调用 Windows GPU 内网 API
       ├─ Embedding（BGE-M3）
       └─ Rerank（BGE-reranker-v2-m3）
```

**代码流转：**

```text
本地开发 → git push → GitHub CI（测试）
  → CD 自动部署：
    1. deploy-gpu（Windows GPU 服务）
    2. deploy-app（Ubuntu 应用 + Qdrant）
```

## 2. 仓库与分支

- 仓库：`https://github.com/abworks-dev/RAGPinCheng.git`
- 分支：`master`
- 开发目录：`${REPOSITORY_CHECKOUT_PATH}`
- Windows 生产目录：`${PRODUCTION_REPO_PATH}`
- Ubuntu 生产目录：`${PRODUCTION_APP_REPO_PATH}`

## 3. CI/CD 自动部署

### 3.1 CI（代码检查）

推送 `master` 或提交 PR 时自动触发：

| Job | 检查内容 |
|-----|---------|
| `validate` | Python 语法、前端构建、Compose 配置 |
| `test-providers` | Provider 单元测试 |
| `test-gpu-contract` | GPU 服务契约检查 |
| `validate-migration-config` | 迁移配置完整性 |

### 3.2 CD（自动部署）

CI 全部通过后自动触发，顺序执行：

1. **deploy-gpu**（Windows 自托管 Runner）
   - 备份旧 GPU 服务
   - 拉取最新代码
   - 更新 Python 依赖
   - 重启服务
   - 健康检查 + 冒烟测试

2. **deploy-app**（Ubuntu 自托管 Runner，依赖 deploy-gpu 成功）
   - 检查 GPU 服务契约（`/model-info`）
   - 拉取最新代码
   - 备份 SQLite 数据库
   - 构建 backend 镜像
   - 滚动更新容器
   - 健康检查

### 3.3 手动触发

在 GitHub Actions 页面选择 **Deploy production** → **Run workflow**，勾选确认框。

## 4. 生产服务器信息

### 4.1 Windows GPU 节点

| 项目 | 值 |
|------|-----|
| 主机名 | ${PRODUCTION_HOSTNAME} |
| 内网 IP | ${GPU_SERVICE_IP} |
| 运行服务 | GPU 推理服务（`gpu_service/`） |
| 服务端口 | 8100 |
| 认证 | API Token（`GPU_SERVICE_TOKEN`） |
| 日志 | `${PRODUCTION_REPO_PATH}\gpu_service.log` |

### 4.2 Ubuntu 应用节点

| 项目 | 值 |
|------|-----|
| 主机名 | ${PRODUCTION_HOSTNAME} |
| 内网 IP | ${APP_NODE_IP} |
| 系统 | Ubuntu 24.04 LTS |
| 仓库路径 | `${PRODUCTION_APP_REPO_PATH}` |
| 数据路径 | `${PRODUCTION_APP_DATA_PATH}/` |
| 文档路径 | `/data/business/ragpincheng/source/docs/` |
| 媒体路径 | `/data/business/ragpincheng/source/media/` |
| Compose 基础 | `docker/docker-compose.yml` |
| Compose 覆盖 | `${PRODUCTION_APP_COMPOSE_OVERRIDE}` |
| 环境变量 | `${PRODUCTION_APP_ENV_FILE}` |
| 项目名 | `ragpincheng-prod` |

## 5. 手动部署（非紧急情况请使用 CD）

### 5.1 Ubuntu 应用更新

```bash
cd ${PRODUCTION_APP_REPO_PATH}
git pull

sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} \
  build backend && \
sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} \
  up -d backend
```

### 5.2 Windows GPU 服务更新

```powershell
cd ${PRODUCTION_REPO_PATH}
git pull
$env:GPU_SERVICE_TOKEN = "你的token"
# 停掉旧服务
Stop-Process -Name python -Force
# 启动新服务
python -m gpu_service.app
```

## 6. 日常运维

### 6.1 查看日志

```bash
# Ubuntu backend 日志
sudo docker compose -p ragpincheng-prod \
  -f ${PRODUCTION_APP_REPO_PATH}/docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} \
  logs -f backend

# Qdrant 状态
sudo docker compose -p ragpincheng-prod \
  -f ${PRODUCTION_APP_REPO_PATH}/docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} \
  ps
```

### 6.2 健康检查

```bash
curl -s http://localhost/api/health
# 返回: {"status":"ok","children":38488,"parents":20024}

curl -s http://localhost/api/config
# 返回: {"embed_model":"BAAI/bge-m3","reranker_model":"...","rerank_enabled":true}
```

### 6.3 GPU 服务检查

```bash
curl -s http://${GPU_SERVICE_IP}:8100/health
# 返回: {"status":"ok","model_loaded":true}

curl -s http://${GPU_SERVICE_IP}:8100/model-info
# 返回: {"api_version":"1","embedding_model":"BAAI/bge-m3",...}
```

### 6.4 备份

```bash
# SQLite 备份
cp ${PRODUCTION_APP_DATA_PATH}/app.sqlite ${PRODUCTION_BACKUP_DIRECTORY}/app-$(date +%Y%m%d).sqlite
cp ${PRODUCTION_APP_DATA_PATH}/parents.sqlite ${PRODUCTION_BACKUP_DIRECTORY}/parents-$(date +%Y%m%d).sqlite

# Qdrant snapshot（需要临时 curl 容器）
sudo docker run --rm --network ragpincheng-prod_default \
  curlimages/curl curl -s -X POST \
  "http://ragpincheng-prod-qdrant-1:6333/collections/pincheng_docs/snapshots"
```

## 7. 浏览器验收

访问 `http://${APP_NODE_IP}`（Ubuntu 内网地址）：

1. 首页正常加载，无白屏
2. 管理员和用户可以登录
3. 历史会话仍然存在
4. 正常问题能生成答案并显示引用来源
5. 查询保护（纯数字输入）正常工作
6. 追问测试正常工作

## 8. 回滚

### 8.1 回滚 Ubuntu 应用

```bash
cd ${PRODUCTION_APP_REPO_PATH}
# 恢复到上一个 Commit
git checkout <上一个commit>
sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} \
  build backend && \
sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} \
  up -d backend
# 恢复后切回 master
git switch master
```

### 8.2 回滚 Windows GPU 服务

```powershell
cd ${PRODUCTION_REPO_PATH}
git checkout <上一个commit>
# 重启服务
$env:GPU_SERVICE_TOKEN = "你的token"
python -m gpu_service.app
# 恢复后切回 master
git switch master
```

### 8.3 回滚到旧单机架构（紧急）

如果 Ubuntu 应用节点完全不可用，Windows 上保留旧 Docker 环境：

```powershell
cd ${PRODUCTION_REPO_PATH}
# 旧架构的 Docker 环境仍在，可直接启动
docker compose -f docker/docker-compose.yml up -d
```

## 9. 数据保护原则

以下内容属于生产数据，不同步到 Git：

```text
data/app.sqlite
data/parents.sqlite
data/feedback.jsonl
data/parsed/
docs/
media/
${PRODUCTION_APP_ENV_FILE}（Ubuntu）
Docker volume: qdrant_storage（Ubuntu）
```

禁止执行：

```bash
sudo docker compose -p ragpincheng-prod down -v
sudo rm -rf ${PRODUCTION_SERVICES_ROOT}/docker/data/ragpincheng/prod/qdrant
```

## 10. 本地开发流程

```powershell
cd ${REPOSITORY_CHECKOUT_PATH}
git switch master
git pull --ff-only origin master
git switch -c feature/功能名称

# 修改、测试
.venv\Scripts\python.exe -m pytest

# 提交
git add 指定文件
git commit -m "feat: 修改说明"
git push -u origin feature/功能名称

# 合并到 master
git switch master
git merge --no-ff feature/功能名称
git push origin master
# → CI 自动触发 → CD 自动部署
```