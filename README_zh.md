# 品成 BIM 知识库

中文 | [English](README.md)

面向 BIM 咨询企业的中文内部知识系统。它将行业规范、客户要求、公司标准、项目资料和培训视频转录稿转化为带可核查引用的回答。

本仓库包含 RAG 流水线、FastAPI 应用、React 前端、受管资料工作流，以及可独立部署的 GPU、ASR 和 Office 转换服务。

## 当前能力

- 密集与稀疏混合检索、重排序、问题改写、多轮上下文、流式回答，以及章节或时间戳引用。
- Session Cookie 鉴权、CSRF 防护、用户管理、会话历史、反馈处理和系统维护工具。
- 受管资料库，覆盖分类、审核、发布、版本化产物、索引任务、来源预览和细粒度权限。
- PDF 和 Markdown 摄取；启用 Office 处理及 LibreOffice 转换服务后支持 DOCX、XLSX 和 PPTX。
- 版本化视频转录、人工校对、审核、发布，以及可选的远程 ASR Profile。

功能是否可用由配置控制。其中 `CONTENT_MANAGEMENT_ENABLED`、`ASR_ENABLED` 和 `QUERY_DECOMPOSE_ENABLED` 默认关闭，启用前请核对 [.env.example](.env.example)。

## 系统架构

```text
受管发布或 content/legacy-docs（兼容入口）
  -> MinerU / Markdown / Office 转换
  -> Parent/Child 分块
  -> BGE-M3 Dense+Sparse 写入 Qdrant，Parent 写入 SQLite
  -> RRF 与规范编号补召 -> BGE 重排序
  -> ChatSession -> GLM 回答与引用
  -> FastAPI SSE -> React
```

系统严格区分两个 SQLite 的职责：`data/parents.sqlite` 是可重建的 RAG 状态；`data/app.sqlite` 保存用户、权限、会话和受管资料状态，重建索引时不得删除。

## 本地开发

环境要求：Python 3.11+、Node.js 18+、Docker，以及约 10 GB 可用磁盘空间。

```bash
# 向量数据库
docker run -d --name pincheng-qdrant -p 6333:6333 qdrant/qdrant:v1.18.3

# Python 环境与后端
python -m venv .venv
# 使用当前 Shell 对应的命令激活 .venv，然后执行：
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn api.main:app --reload --port 8000

# 另开终端启动前端
cd frontend
npm install
npm run dev
```

在 `.env` 中至少设置 `ZHIPU_API_KEY`、`MINERU_API_KEY`、`ADMIN_EMPLOYEE_ID` 和 `ADMIN_PASSWORD`。使用纯 HTTP 本地开发时，还要加入 `SESSION_COOKIE_SECURE=false`。前端地址为 `http://localhost:5173`，后端 API 地址为 `http://localhost:8000/api`。

后端首次启动时，如果配置的员工编号尚不存在，会自动创建管理员。员工也可以通过 `/register` 注册。

## Docker 部署

生产 Compose 栈包含 Qdrant、同时提供 FastAPI/React 的后端，以及 LibreOffice 转换服务。Embedding 和重排序预期使用独立的 Windows GPU 服务。

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
curl http://localhost/api/health
```

以上命令只是快速启动入口，不是完整生产 Runbook。TLS、密钥、持久化存储、远程 GPU、备份和受管资料切换请遵守下方链接的部署及迁移文档。

## 添加资料

受管资料库是主要资料工作流。针对目标环境显式启用后，管理员通过资料管理页面完成分类、审核、发布、索引、预览和退役；只有已发布版本进入正式检索。

`content/legacy-docs/` 保留为文件系统批量导入的兼容入口：

```bash
python scripts/build_index.py
```

不要把项目文档或真实客户资料提交到仓库。仓库中的 `docs/` 只保存项目文档，绝不是业务资料摄取入口。

## 验证与调试

```bash
# 只验证检索，不请求生成回答
python scripts/test_retrieve.py "Q345 钢手工焊用什么焊条？"

# 完整 RAG 调试，需要配置 LLM
python scripts/eval_query.py "Q345 钢手工焊用什么焊条？"

# 检索黄金集与索引指纹检查
python scripts/run_eval_retrieval.py --strict-staleness
```

评测结果取决于索引语料、配置和索引指纹。历史方案或旧运行产物中的指标不代表当前检出版本或生产环境的现状。

## 文档导航

- [项目文档地图](docs/README.md)
- [当前功能地图](docs/features/README.md)
- [页面、权限、API 与测试清单](docs/design/page-inventory.md)
- [IT 部署指南](docs/operations/部署指南_IT.md)
- [Ubuntu 应用与 Windows GPU Runbook](docs/migrations/ubuntu-app-windows-gpu-runbook.md)
- [受管资料生产迁移 Runbook](docs/migrations/managed-content-production-runbook.md)
- [Office 转换运维说明](docs/operations/OFFICE_CONVERSION.md)
- [用户验收指南](docs/USER_ACCEPTANCE.md)
- [开发规则与架构不变量](AGENTS.md)
- [当前产品待办](TODO.md)

## 安全边界

不得提交 `.env`、密钥、客户资料、用户对话、SQLite 数据库、Qdrant 存储、模型缓存或转录产物。索引 Reset、破坏性迁移、生产部署和真实数据处理必须具备针对目标环境的备份与审批方案。
