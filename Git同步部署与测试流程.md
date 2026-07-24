# RAGPinCheng Git 同步、部署与测试流程

## 1. 当前约定

- 正式仓库（Fork）：`https://github.com/abworks-dev/RAGPinCheng.git`
- 原仓库：`https://github.com/Desmond766/RAGPinCheng.git`
- 正式分支：`master`
- 开发目录：`E:\Repository\Github\RAGPinCheng`
- 服务器目录：`${PRODUCTION_REPO_PATH}`
- 服务编排：`docker/docker-compose.yml`
- 服务器代理：`http://${PRIVATE_ZEROTIER_IPV4}:7897`
- Qdrant 固定版本：`v1.18.3`

代码流转：

```text
本地功能分支
  → 本地测试
  → 合并到 Fork/master
  → 服务器拉取 origin/master
  → Docker 构建并重建容器
  → 浏览器验收
  → 成功结束或回滚
```

## 1.1 GitHub Actions 快速部署

仓库提供两个工作流：

- `.github/workflows/ci.yml`：在 Pull Request 和 `master` 推送时执行 Python 语法检查、前端生产构建和 Compose 配置检查；
- `.github/workflows/deploy-production.yml`：只允许维护者从 Actions 页面手动触发并勾选生产确认框，再由生产服务器的 self-hosted runner 执行部署。CI 成功不会自动发布。

推荐将正式仓库迁移为独立 Private 仓库。GitHub Free 私有仓库不依赖 Environment 审批，本项目以 `workflow_dispatch` 手动触发作为单人发布确认；创建私有仓库、迁移 Git 历史和切换开发机/服务器 `origin` 需单独执行，本工作流不会自动修改远端。

生产 runner 需要以下标签：

```text
self-hosted, windows, production, gpu
```

服务器需要安装 runner，并让其服务账号具备访问 `${PRODUCTION_REPO_PATH}`、`${PRODUCTION_BACKUP_DIRECTORY}`、Git 和 Docker 的权限。不要把 `.env`、API Key、管理员密码或生产数据放入 GitHub Actions。

如果构建需要代理，在 GitHub 仓库的 **Settings → Secrets and variables → Actions → Variables** 中配置仓库级非敏感变量：

```text
DEPLOY_HTTP_PROXY=http://${PRIVATE_ZEROTIER_IPV4}:7897
```

发布脚本 `scripts/deploy-production.ps1` 只允许从干净的 `master` 分支快进部署。它会记录旧 Commit、检查 Compose、构建并重建服务、轮询 `/api/health`；失败时尝试重建旧 Commit。若待发布差异包含数据库或索引结构敏感文件，部署会停止，要求先备份应用数据和 Qdrant，再按本指南人工审查部署。脚本不会执行 `down -v`、索引 Reset、数据库迁移或数据目录清理。

首次使用前，在生产服务器手动确认：

```powershell
cd ${PRODUCTION_REPO_PATH}
git pull --ff-only origin master
git status
docker compose -f docker/docker-compose.yml config --quiet
Test-Path scripts\deploy-production.ps1
```

最后一条命令只确认部署脚本已经同步，不会执行生产部署。日常发布进入 GitHub **Actions → Deploy production → Run workflow**，选择 `master`、勾选生产确认框后运行。每次发布前仍须确认数据备份和可回滚版本；CI 成功本身不会触发部署。

## 2. 数据保护原则

以下内容属于生产数据，不通过 Git 同步：

```text
.env
data/app.sqlite
data/parents.sqlite
data/feedback.jsonl
data/parsed/
docs/
Docker volume: qdrant_storage
Docker volume: hf_cache
```

正常执行 `git pull`、`docker compose build` 和 `docker compose up -d` 不会删除这些数据。

禁止在生产服务器执行：

```powershell
git clean -fdx
docker compose down -v
docker builder prune
docker system prune -a
docker compose build --no-cache
```

涉及数据库结构、索引结构、Qdrant 版本或重要文档处理逻辑时，部署前必须备份 `.env`、`data/`、`docs/`，并为 Qdrant 创建快照。

## 3. 本地开发流程

### 3.1 创建开发分支

```powershell
cd E:\Repository\Github\RAGPinCheng

git switch master
git pull --ff-only origin master
git status
git switch -c feature/功能名称
```

缺陷修复使用：

```powershell
git switch -c fix/问题名称
```

不要直接在 `master` 上进行日常开发。

### 3.2 本地检查和测试

查看修改：

```powershell
git status
git diff
```

后端测试：

```powershell
.venv\Scripts\python.exe -m pytest
```

运行单项测试：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_query_guard.py
```

前端测试：

```powershell
cd frontend
npm ci
npm run build
cd ..
```

Compose 配置检查：

```powershell
docker compose -f docker/docker-compose.yml config --quiet
```

### 3.3 提交并推送功能分支

明确添加文件，不要未经检查直接执行 `git add .`：

```powershell
git add path/to/file
git diff --cached --stat
git diff --cached
git commit -m "feat: 修改说明"
git push -u origin feature/功能名称
```

### 3.4 不提 PR，合并到 Fork/master

```powershell
git switch master
git pull --ff-only origin master
git merge --no-ff feature/功能名称
.venv\Scripts\python.exe -m pytest
git push origin master
git log -1 --oneline
```

生产验证成功后再删除功能分支：

```powershell
git branch -d feature/功能名称
git push origin --delete feature/功能名称
```

## 4. 服务器部署前检查

### 4.1 检查仓库状态

```powershell
cd ${PRODUCTION_REPO_PATH}

git branch --show-current
git status
git remote -v
```

必须满足：

- 当前分支是 `master`；
- 工作区显示 `working tree clean`；
- `origin` 指向 `abworks-dev/RAGPinCheng`。

服务器如有本地代码修改，应停止部署并先建立备份分支，不得直接覆盖。

### 4.2 记录当前生产版本

```powershell
$oldVersion = git rev-parse HEAD
$oldVersion | Set-Content ${PRODUCTION_BACKUP_DIRECTORY}\last-production-commit.txt
```

### 4.3 获取并审查新版本

如果 GitHub 需要代理：

```powershell
git config --local http.proxy http://${PRIVATE_ZEROTIER_IPV4}:7897
```

获取远程信息：

```powershell
git fetch origin --prune
git --no-pager log --oneline HEAD..origin/master
git --no-pager diff --name-status HEAD..origin/master
```

重点检查是否修改了：

- `.env`、`data/`、`docs/`；
- 数据库或索引结构；
- `requirements-prod.txt`；
- Dockerfile 或 Compose；
- Qdrant 版本；
- 文档上传、删除和索引流程。

## 5. 服务器同步和构建

### 5.1 拉取正式版本

```powershell
git switch master
git pull --ff-only origin master
git log -1 --oneline
git status
```

### 5.2 按修改类型部署

仅修改 `.env`：

```powershell
docker compose -f docker/docker-compose.yml up -d --force-recreate backend
```

仅修改 Qdrant 镜像版本：

```powershell
docker compose -f docker/docker-compose.yml pull qdrant
docker compose -f docker/docker-compose.yml up -d qdrant
```

修改前端、后端、依赖或 Dockerfile时，构建后端镜像：

```powershell
$proxy = "http://${PRIVATE_ZEROTIER_IPV4}:7897"
$noProxy = "localhost,127.0.0.1,qdrant"

docker compose -f docker/docker-compose.yml build `
  --build-arg HTTP_PROXY=$proxy `
  --build-arg HTTPS_PROXY=$proxy `
  --build-arg NO_PROXY=$noProxy `
  --build-arg http_proxy=$proxy `
  --build-arg https_proxy=$proxy `
  --build-arg no_proxy=$noProxy `
  --progress=plain `
  backend
```

旧容器在构建过程中可以继续运行。构建日志显示 `CACHED` 或 `Using cached` 表示缓存命中。

### 5.3 启动新版本

```powershell
docker compose -f docker/docker-compose.yml up -d --remove-orphans
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs --tail 200 backend
```

健康检查：

```powershell
curl.exe --fail http://localhost/api/health
```

正常返回：

```json
{"status":"ok"}
```

## 6. 浏览器验收流程

在服务器浏览器访问：

```text
http://localhost
```

也可以从内网电脑访问：

```text
http://服务器内网IP
```

首先按 `Ctrl+F5` 强制刷新，避免浏览器使用旧前端资源。

### 6.1 基础冒烟测试

1. 首页正常加载，无白屏；
2. 登录页面正常；
3. 原管理员和用户可以登录；
4. 历史会话仍然存在；
5. 管理后台能看到原有文档；
6. 文档分类和数量正常；
7. 页面操作没有持续加载或明显报错。

### 6.2 RAG 测试

正常问题：

```text
GB 50017 对钢结构焊缝有什么要求？
```

检查：

- 能生成答案；
- 能显示引用来源；
- 来源面板可以展开；
- 来源标题、页码和分类正常；
- 响应时间无明显异常。

查询保护测试：

```text
11
```

系统应提示信息不足，而不是生成不可靠答案。

追问测试：

```text
那焊缝等级呢？
```

系统应能够结合上一轮上下文回答。

### 6.3 数据完整性测试

- 原账号和历史会话存在；
- `feedback.jsonl` 未被清空；
- 原文档能够检索；
- 管理后台文档数量正常；
- 不随意删除正式文档；
- 上传测试应使用专用测试分类和小型测试文件。

### 6.4 GPU 检查

```powershell
docker compose -f docker/docker-compose.yml exec backend `
  python -c "import torch; print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

预期：

```text
CUDA: True
NVIDIA GeForce RTX 5060 Ti
```

### 6.5 最终日志检查

```powershell
docker compose -f docker/docker-compose.yml logs --tail 200 backend |
  Select-String "ERROR|Exception|Traceback"
```

记录正式运行版本：

```powershell
git rev-parse HEAD
docker compose -f docker/docker-compose.yml ps
```

## 7. 回滚流程

新版本异常时先保留现场日志：

```powershell
docker compose -f docker/docker-compose.yml logs --tail 300 backend
```

读取上一个版本：

```powershell
$oldVersion = Get-Content ${PRODUCTION_BACKUP_DIRECTORY}\last-production-commit.txt
git switch --detach $oldVersion
```

重新构建并启动旧版本：

```powershell
docker compose -f docker/docker-compose.yml build backend
docker compose -f docker/docker-compose.yml up -d
curl.exe --fail http://localhost/api/health
```

恢复成功后不要在 detached HEAD 状态开发。下一次正式部署前执行：

```powershell
git switch master
git pull --ff-only origin master
```

如果新版本执行过数据库迁移，回滚代码可能不够，还需要恢复部署前的数据备份或执行对应的数据库回滚方案。

## 8. 日常发布速查

本地：

```powershell
git switch master
git pull --ff-only origin master
git switch -c feature/功能名称

# 修改并测试

git add 指定文件
git commit -m "feat: 修改说明"
git push -u origin feature/功能名称

git switch master
git merge --no-ff feature/功能名称
git push origin master
```

服务器：

```powershell
cd ${PRODUCTION_REPO_PATH}
git status
git fetch origin --prune
git --no-pager diff --name-status HEAD..origin/master
git pull --ff-only origin master

docker compose -f docker/docker-compose.yml build backend
docker compose -f docker/docker-compose.yml up -d
curl.exe --fail http://localhost/api/health
```

最后在浏览器按 `Ctrl+F5`，依次完成登录、旧数据、正常问答、引用来源、查询保护和管理后台文档检查。
