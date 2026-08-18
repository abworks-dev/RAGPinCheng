# Ubuntu 应用节点 + Windows GPU 节点迁移执行手册

> 状态：历史迁移执行手册；双节点迁移及后续生产切换已有 workflow 证据。当前部署、备份和 source 解耦状态以本文件后续收口记录及最新 workflow 为准。
> 适用项目：RAGPinCheng  
> 目标拓扑：Ubuntu 承载 Web/API/Qdrant/持久化数据，Windows 保留 BGE-M3 与 reranker GPU 推理。  
> 使用者：Claude Code、Codex 和项目维护人员。  
> 重要：本文保留审批、回滚和验证边界，不构成对新的生产数据操作或生产切换的一次性授权。

## 1. 已确认背景

- Ubuntu 主机：
  - 系统：Ubuntu 24.04.4 LTS；
  - 主机名：`${PRODUCTION_HOSTNAME}`；
  - 内网地址：`${APP_NODE_IP}/24`；
  - 网关：`${PRODUCTION_GATEWAY_IP}`；
  - 无 NVIDIA GPU；
  - SSH 公网入口为 `${PRODUCTION_APP_SSH_HOST}:2222`，路由器转发到主机 TCP 22；
  - SSH 已确认 `PubkeyAuthentication yes`、`PasswordAuthentication no`、`KbdInteractiveAuthentication no`、`PermitRootLogin no`；
  - GNOME Remote Desktop 与向日葵是公司保留的内部远程管理方式；
  - UFW 当前未启用，正式调整防火墙前需另行审批和保留回滚入口。
- Windows GPU 主机：
  - 与 Ubuntu 位于同一公司局域网；
  - 能保证工作时间持续开机；
  - 当前承担项目的 GPU 推理及生产部署；
  - 固定内网地址、GPU 型号、驱动版本、CUDA 版本和非工作时间可用性仍需记录。
- 当前 GitHub Actions：
  - CI 使用 GitHub 托管 `ubuntu-latest`；
  - 生产 CD 使用带 `windows + production + gpu` 标签的自托管 Runner；
  - `scripts/deploy-production.ps1` 当前在 Windows 上构建并部署 backend 与 Qdrant。

## 2. 目标架构

```text
用户浏览器
  → HTTPS
Ubuntu ${APP_NODE_IP}
  ├─ 反向代理 / TLS
  ├─ FastAPI + React
  ├─ Qdrant
  ├─ app.sqlite
  ├─ parents.sqlite
  ├─ docs / media / data/parsed
  └─ 调用 Windows GPU 内网 API
       ├─ BAAI/bge-m3 embedding
       └─ BAAI/bge-reranker-v2-m3 rerank
```

查询链路：

```text
查询
→ Ubuntu 请求 Windows 生成 Dense + Sparse 向量
→ Ubuntu 查询本机 Qdrant
→ Ubuntu 将 query + 候选 Child 发给 Windows 重排
→ Ubuntu 从 parents.sqlite 展开 Parent
→ Ubuntu 调用 GLM
→ Ubuntu 返回答案和引用
```

索引链路：

```text
上传文档
→ Ubuntu 解析、分块、生成表格摘要
→ Ubuntu 批量请求 Windows embedding
→ Ubuntu 写 Qdrant Child
→ Ubuntu 写 parents.sqlite Parent
```

## 3. 架构不变量

实施中必须保持：

1. `ParsedDoc → Parent/Child → Qdrant/parents.sqlite → RetrievedParent → ChatSession` 契约不变。
2. Dense 向量维度保持 1024，Sparse indices/values 语义不变。
3. `BAAI/bge-m3` 的模型 revision、FlagEmbedding、Transformers、Torch 和文本预处理方式必须固定并可查询。
4. reranker 仍接收与当前一致的 `query + passage`，并保持 header 拼接行为。
5. Qdrant Child 与 `parents.sqlite` Parent 继续通过确定性 `parent_id` 关联。
6. `app.sqlite` 与 `parents.sqlite` 必须独立备份、迁移和恢复。
7. Qdrant 必须使用 snapshot 迁移，不得在运行中直接复制其底层 volume。
8. GPU 不可用时应快速返回明确的 `503`，不得静默在 Ubuntu 上加载模型并长时间 CPU 降级。
9. Windows GPU API 不得暴露公网，只允许 Ubuntu 内网地址访问。
10. 不在 Git、日志或接口响应中写入 API Key、管理员密码、Cookie 或业务文档正文。

## 4. 明确不做

- 不在 Ubuntu 安装 NVIDIA 驱动或伪造 GPU 能力。
- 不把 Qdrant、SQLite 或文档数据迁到 Windows GPU 服务。
- 不在第一阶段改变 Chunk、ID、Embedding 文本或 Qdrant collection schema。
- 不趁迁移同时实现 MQE、HyDE、查询拆分或其他检索行为改造。
- 不在未验证备份恢复前停止旧生产环境。
- 不以本文替代生产部署、数据停写、DNS 切换、防火墙或删除旧环境的专项批准。

## 5. 风险与审批门禁

| 阶段 | 风险 | 门禁 |
|---|---:|---|
| 现状调查、基线采集 | R0 | 说明范围后可执行 |
| 文档和测试脚手架 | R1 | 用户明确要求修改时可执行 |
| GPU API、provider、Compose、CI/CD 改造 | R2 | 提交本阶段方案并等待“批准执行” |
| 测试数据恢复到隔离环境 | R2 | 明确数据范围和隔离方式后批准 |
| 生产备份、停写、迁移、域名/入口切换 | R3 | 逐项说明目标、备份和回滚，逐项批准 |
| 删除旧容器、旧数据或旧生产环境 | R3 | 稳定观察期结束后再次单独批准 |

Claude Code 每次只能执行已批准阶段。范围、接口或数据策略发生实质变化时，必须重新评级和审批。

## 6. 阶段 0：基线与前置决策

### 6.1 只读采集

记录但不得把敏感值提交到仓库：

- 当前生产 Commit SHA；
- Windows 内网 IP，并在路由器设置 DHCP 地址保留或静态地址；
- Windows GPU、驱动、CUDA、Python、Torch、FlagEmbedding、Transformers 版本；
- 实际 Hugging Face 模型 revision 和缓存位置；
- 当前 Docker、Compose、Qdrant 版本；
- Qdrant collection 信息、向量维度、point 数量；
- `app.sqlite`、`parents.sqlite` schema/version、文件大小和更新时间；
- `docs/`、`media/`、`data/parsed/` 的容量；
- 当前 `/api/health`、`/api/config` 输出；
- 固定检索黄金集结果和至少 10 个代表查询的结果、延迟；
- 索引一个小型测试文档的耗时与结果；
- Windows 工作时间、计划维护窗口和现场恢复联系人。

### 6.2 必须由用户决定

- Windows GPU 服务的固定内网 IP 和端口；
- GPU API 在非工作时间不可用时，是显示维护提示还是要求 Windows 继续开机；
- 内网 API 采用 API Token + Windows 防火墙，还是进一步配置 TLS/mTLS；
- Ubuntu 正式 HTTPS 域名和证书终止方式；
- 生产切换允许的停机窗口；
- 旧 Windows Web/API 保留多久作为回滚环境；
- GitHub Ubuntu 自托管 Runner 部署在目标主机还是独立部署节点。

### 6.3 完成标准

- 基线和决策记录完整；
- 未读取或提交真实密钥；
- 固定查询和数据计数可用于迁移前后对比；
- 已确定备份位置，且备份不只存在于源服务器。

## 7. 阶段 1：定义 GPU 推理接口

建议新增候选目录：

```text
services/gpu_service/
  app.py
  schemas.py
  models.py
  config.py
  requirements.txt
  tests/
```

最终目录名可在 R2 方案中调整，但不能把服务实现混入 `api/` 后复制现有聊天编排。

### 7.1 接口

```text
GET  /health
GET  /model-info
POST /v1/embeddings
POST /v1/rerank
```

`/model-info` 至少返回：

```json
{
  "api_version": "1",
  "embedding_model": "BAAI/bge-m3",
  "embedding_revision": "<fixed revision>",
  "embedding_dimension": 1024,
  "reranker_model": "BAAI/bge-reranker-v2-m3",
  "reranker_revision": "<fixed revision>",
  "flag_embedding_version": "<version>",
  "transformers_version": "<version>",
  "torch_version": "<version>",
  "device": "cuda"
}
```

Embedding 请求支持字符串数组，响应顺序必须与输入一致，每项返回：

```json
{
  "dense": [0.0],
  "sparse_indices": [1],
  "sparse_values": [0.0]
}
```

Rerank 请求包含一个 query 和 passages 数组，响应返回等长 scores 数组。

### 7.2 服务约束

- 模型进程内单例加载；
- 冷启动只允许一个加载者；
- embedding 和 rerank 使用显式并发控制，避免 GPU OOM；
- 限制文本数量、单条长度和总请求体；
- Token 使用常量时间比较，日志不得记录 Token；
- 设置请求 ID、耗时、批量大小、状态码和错误类型日志；
- 默认只监听 Windows 内网 IP，不监听公网接口；
- Windows 防火墙仅允许 `${APP_NODE_IP}` 访问；
- 服务使用 NSSM、Windows Service 或任务计划程序实现开机自启；
- 健康检查区分“进程存活”和“模型已加载可推理”。

### 7.3 契约测试

- 空数组、单条和批量输入；
- 中文、英文、规范编号和超长文本；
- Dense 维度 1024；
- Sparse indices 为整数且与 values 等长；
- 返回顺序稳定；
- rerank 单候选返回数组而不是标量；
- Token 缺失/错误返回 401/403；
- 超限返回 413/422；
- GPU 不可用返回 503；
- 同一模型与输入对比旧实现，误差在审批时定义的容差内。

## 8. 阶段 2：抽象本地/远程推理 Provider

### 8.1 拟修改文件

- `src/embed.py`
- `src/rerank.py`
- `src/config.py`
- `api/main.py`
- `api/rate_limit.py`
- `api/indexing.py`
- `src/retrieve.py`
- `src/session.py`
- `.env.example`
- 新增远程客户端模块与测试

### 8.2 配置候选

```text
EMBED_PROVIDER=local|remote
EMBED_SERVICE_URL=
RERANK_PROVIDER=local|remote
RERANK_SERVICE_URL=
GPU_SERVICE_TOKEN=
GPU_CONNECT_TIMEOUT_SECONDS=
GPU_REQUEST_TIMEOUT_SECONDS=
GPU_MAX_RETRIES=
GPU_EXPECTED_API_VERSION=1
GPU_EXPECTED_EMBED_DIM=1024
```

真实 Token 只写目标主机 `.env` 或 GitHub Environment Secret。

### 8.3 实现要求

- 保留当前 local provider，作为开发和回滚路径；
- 上层继续调用 `encode`、`encode_one`、`rerank_scores`，避免在业务链路散落 HTTP 调用；
- remote provider 在启动时验证 `/model-info`；
- 模型/API/维度不兼容时拒绝启动；
- 连接失败、超时和服务端错误映射为明确的领域异常；
- 只对可安全重试的请求执行有限重试和退避；
- 索引任务失败应标记为可诊断的 failed，不得报告 done；
- 在线查询失败快速返回 503，不写入伪造结果；
- 不在异常和日志中输出文档全文；
- 保持 `RERANK_USE_HEADER` 的现有行为。

### 8.4 验证

- local provider 回归；
- mock remote provider；
- 真实 Windows GPU 冒烟；
- 并发冷启动与并发查询；
- GPU 服务断开、超时、错误 Token、维度不匹配；
- `python scripts/test_retrieve.py "<固定问题>"`；
- 固定黄金集对比 Recall@1、Recall@5、MRR 和 no-answer；
- 迁移前后相同查询的 rerank 排序对比。

## 9. 阶段 3：拆分容器与运行依赖

### 9.1 Ubuntu 应用镜像

调整：

- 移除 Compose NVIDIA GPU reservation；
- 不在 Ubuntu 镜像安装 cu128 Torch；
- 不下载或预热 BGE 模型；
- backend 启动时检查 Windows GPU API；
- Qdrant 继续仅在 Compose 内网暴露；
- 保留 `data`、`docs`、`media` bind mount；
- 保留 Qdrant 与日志 volume；
- 增加 GPU API URL 与 Token 环境变量；
- 健康检查区分核心 API、Qdrant 和 GPU 依赖状态。

优先考虑拆分 requirements：

```text
requirements-prod.txt
requirements-gpu.txt
```

不得让 GPU 依赖继续隐式进入 Ubuntu 生产镜像。

### 9.2 Windows GPU 运行方式

Windows 可采用原生 Python venv 服务；若继续使用容器，必须单独验证：

- NVIDIA 驱动与容器工具链；
- GPU 透传；
- 模型缓存持久化；
- 服务重启和健康检查；
- 防火墙作用于实际监听端口。

不要为了形式统一而强制使用容器；以可复现、可运维和 GPU 兼容为准。

### 9.3 Compose 验证

- `docker compose -f docker/docker-compose.yml config --quiet`；
- Ubuntu backend 镜像构建；
- 无 GPU 的 Ubuntu 启动不加载 Torch/BGE；
- Qdrant 不映射主机公网端口；
- backend 只能通过配置的内网地址访问 GPU API；
- GPU 不可用时 backend 行为符合既定健康策略。

## 10. 阶段 4：调整 CI

保留现有：

- Python compile；
- 前端 `npm ci && npm run build`；
- Compose 配置验证。

新增：

- provider 单元测试；
- GPU API schema/contract 测试；
- Ubuntu backend 镜像构建；
- GPU requirements/import 测试；
- remote provider 的错误与超时测试；
- 迁移配置示例完整性检查。

CI 不依赖真实生产密钥，不向真实 Windows GPU 发送业务内容。真实 GPU 冒烟放在受控自托管 Runner，并使用非敏感固定测试文本。

## 11. 阶段 5：拆分 CD

### 11.1 Windows GPU 发布作业

保留 Windows 自托管 Runner，职责改为：

1. 拉取已通过 CI 的目标 Commit；
2. 创建 GPU 服务代码/配置备份；
3. 更新依赖或服务；
4. 重启 GPU 服务；
5. 检查 `/health` 与 `/model-info`；
6. 执行固定 embedding/rerank 冒烟；
7. 失败时恢复旧服务版本。

不再由 Windows 作业部署 Qdrant、SQLite 或 Web backend。

### 11.2 Ubuntu 应用发布作业

新增标签候选：

```yaml
runs-on:
  - self-hosted
  - linux
  - ubuntu
  - production
  - app
```

新增 Linux 部署脚本，替代 Windows 路径假设。职责：

1. 检查目标分支、Commit 和工作树；
2. 在变更命中数据/索引敏感路径时阻止自动部署；
3. 检查 Windows `/model-info` 契约；
4. 验证 Compose；
5. 构建并滚动更新 Ubuntu backend；
6. 验证 Qdrant、GPU、API 和前端；
7. 记录部署 Commit；
8. 失败时恢复旧镜像/Commit。

### 11.3 发布顺序

```text
CI 成功
→ Windows GPU 服务发布并通过契约测试
→ Ubuntu 部署前检查 GPU 服务兼容
→ Ubuntu backend 发布
→ 端到端检索冒烟
```

GPU API 必须向后兼容，不能要求两台主机在同一秒原子更新。

### 11.4 GitHub 安全

- 使用 GitHub Environments 区分测试与生产；
- 生产部署建议保留人工审批；
- Secret 不通过 workflow 输出；
- Runner 账号使用最小权限；
- Windows GPU 与 Ubuntu 各自只能访问所需 Secret；
- 不允许来自未受信任 PR 的代码直接在生产自托管 Runner 执行。

## 12. 阶段 6：Ubuntu 基础设施准备

此阶段涉及服务器状态，执行前单独审批。

准备：

- 固定 Ubuntu 地址 `${APP_NODE_IP}`；
- 安装并固定 Docker/Compose 版本；
- 准备仓库、数据、备份和日志目录；
- 配置磁盘容量告警；
- 配置 HTTPS 反向代理；
- 仅暴露 HTTPS 与获批 SSH；
- RDP 3389/3390 仅允许 `${PRODUCTION_SUBNET}`；
- 保留向日葵并验证防火墙启用后的连接；
- Qdrant 6333 不对公网开放；
- 配置时间同步；
- 设置自动安全更新策略和维护窗口；
- 设置服务开机自启及日志轮转。

任何 UFW 启用动作都必须：

1. 保持现有 SSH 会话；
2. 保留向日葵或现场终端；
3. 先添加允许规则；
4. 启用后新开连接验证；
5. 失败时通过保留连接执行 `sudo ufw disable`。

## 13. 阶段 7：备份与测试迁移

### 13.1 备份清单

- Git Commit SHA；
- `app.sqlite`；
- `parents.sqlite`；
- Qdrant snapshot；
- `docs/`；
- `media/`；
- `data/parsed/`；
- 必需的反馈文件；
- 配置变量名清单；
- Windows 模型版本和 GPU 服务版本。

`.env` 只通过安全渠道重新创建，不进入仓库或普通压缩包。

### 13.2 一致性要求

生产最终同步时：

1. 暂停上传、索引和会话写入；
2. 确认后台索引任务停止；
3. 分别备份两个 SQLite；
4. 创建 Qdrant snapshot；
5. 记录三者时间和计数；
6. 迁移并恢复；
7. 验证 Child、Parent、用户和会话数量；
8. 验证后才解除停写。

测试迁移应使用经授权的数据副本。不得在未知隔离条件下复制真实客户资料。

### 13.3 恢复演练

在测试 Ubuntu 环境实际完成：

- 恢复 `app.sqlite`；
- 恢复 `parents.sqlite`；
- 通过 Qdrant API 恢复 snapshot；
- 恢复文档和媒体；
- 启动应用；
- 验证匿名、普通用户、管理员和 CSRF；
- 验证固定检索与回答；
- 验证上传、索引、删除的非破坏性测试路径；
- 验证视频与来源访问；
- 验证备份能够再次生成。

## 14. 阶段 8：迁移兼容性判定

满足全部条件时，可以迁移现有索引而不全量重建：

- embedding 模型及 revision 一致；
- FlagEmbedding/Transformers/Torch 与计算配置一致；
- Dense 维度 1024；
- Sparse 生成逻辑一致；
- `embed_text` 未变化；
- collection、named vectors 和 payload schema 未变化；
- 固定样本向量与检索结果通过容差比较。

任一条件不满足时：

- 停止生产切换；
- 提交全量重建索引的 R3 方案；
- 明确 Qdrant 与 `parents.sqlite` 的重建/恢复方式；
- 使用固定黄金集验证；
- 不复用不兼容的旧向量。

## 15. 阶段 9：生产切换

生产切换属于 R3，必须逐项批准。

### 15.1 前置条件

- 测试迁移与恢复演练通过；
- Windows GPU 服务稳定；
- Ubuntu HTTPS、备份、监控和回滚可用；
- CI/CD 双节点作业通过；
- 用户已完成验收；
- 已通知停机窗口；
- 旧生产环境可恢复；
- 现场或向日葵应急入口可用。

### 15.2 切换步骤

1. 宣布维护开始；
2. 停止写入与索引队列；
3. 记录旧环境 Commit、容器、数据计数和健康状态；
4. 创建最终 SQLite 备份与 Qdrant snapshot；
5. 同步 docs/media/parsed；
6. 在 Ubuntu 恢复数据；
7. 启动 Windows GPU 服务并确认模型契约；
8. 启动 Ubuntu Qdrant/backend；
9. 运行健康检查、认证、检索和回答冒烟；
10. 切换反向代理、域名或入口；
11. 由用户执行验收；
12. 解除维护；
13. 进入稳定观察期。

### 15.3 回滚触发条件

- GPU API 不稳定或模型契约不一致；
- Qdrant/Parent 计数不一致；
- 登录、会话、上传、检索或回答主链路失败；
- 延迟明显超过批准阈值；
- 数据写入异常；
- HTTPS、Cookie 或 CSRF 异常；
- 无法在维护窗口内修复。

### 15.4 回滚

1. 重新进入停写；
2. 将入口切回旧 Windows Web/API；
3. 恢复旧环境原 Commit/容器；
4. 验证旧环境数据和健康状态；
5. 对 Ubuntu 切换期间产生的新写入单独保全，不直接覆盖旧数据；
6. 记录失败原因；
7. 不删除 Ubuntu 或 Windows 数据；
8. 修订方案后重新审批。

## 16. 阶段 10：观察与旧环境退役

建议观察至少一个完整业务周期，并检查：

- GPU API 可用率和 P95/P99 延迟；
- embedding/rerank 超时率；
- Qdrant、SQLite、磁盘容量；
- 登录与权限；
- 索引任务成功率；
- 固定检索指标；
- GLM/MinerU 外部调用；
- 日志中是否出现敏感数据；
- 备份与恢复演练。

删除旧 Web/API、旧 Qdrant 或旧数据是独立 R3 操作。观察结束不代表自动授权删除。

## 17. 验收矩阵

| 范围 | 验收内容 |
|---|---|
| GPU | CUDA 可用、模型版本正确、Dense 1024、Sparse 有效、rerank 等长 |
| 网络 | GPU 端口仅允许 `${APP_NODE_IP}`，公网不可达 |
| 后端 | GPU 不可用快速 503，恢复后无需重启即可重新工作或行为符合设计 |
| Qdrant | collection、point、payload、named vector 一致 |
| SQLite | 用户/会话与 Parent 数据分别完整 |
| 认证 | 匿名、普通用户、管理员、Cookie Secure、CSRF |
| RAG | Recall@1、Recall@5、MRR、no-answer 不低于批准阈值 |
| 索引 | 上传、解析、embedding、写入、失败重试 |
| 前端 | 登录、聊天、来源、管理员、上传和媒体 |
| CI/CD | 两节点独立发布、兼容检查、失败回滚 |
| 运维 | HTTPS、日志、监控、备份、恢复、维护提示 |

详细用户验收格式遵循 `docs/USER_ACCEPTANCE.md`。

## 18. Claude Code 分阶段执行协议

Claude Code 接手后，每个阶段都必须：

1. 阅读根目录 `CLAUDE.md`、相关 `.claude/rules/` 和本文；
2. 执行 `git status --short --branch`，保护现有修改；
3. 核对本文事实与当前代码，代码不符时先报告；
4. 明确本阶段风险等级、目标、文件、明确不做内容、验证与回滚；
5. R2/R3 阶段提交方案后结束回复，等待明确批准；
6. 只实现获批范围，不顺带处理邻近问题；
7. 先补测试或基线，再改变实现；
8. 报告实际执行的验证，不能声称未运行的测试通过；
9. 有用户可观察变化时提供 `docs/USER_ACCEPTANCE.md` 格式的验收步骤；
10. 按 `CLAUDE.md` 的交付证据规则汇总实际修改、验证结果、未验证项和 workflow 审计入口；
11. 不把计划、候选设计或未完成事项记录成成果；
12. 生产数据、停写、恢复、DNS/入口切换和删除必须逐项确认。

## 19. 建议的实施批次

为降低评审和回滚成本，建议拆成以下 PR：

1. GPU API 契约、schema 和纯单元测试；
2. Windows GPU 服务实现与本机冒烟；
3. embedding/rerank provider 抽象与 local 回归；
4. remote provider、超时、错误和契约验证；
5. Ubuntu CPU/API 镜像与 Compose 拆分；
6. CI 扩展；
7. Windows GPU CD；
8. Ubuntu应用 CD；
9. 运维、备份、恢复和验收文档；
10. 经批准的测试迁移；
11. 经逐项批准的生产切换。

每个 PR 应保持可独立审查。涉及模型/向量契约的 PR 不得与无关 UI 或业务功能混合。

## 20. 已决定事项（2026-07-26 确认）

| # | 问题 | 决策 |
|---|------|------|
| 1 | GPU 认证方式 | API Token + Windows 防火墙（仅允许 `${APP_NODE_IP}` 访问） |
| 2 | 非工作时间 GPU 不可用行为 | 返回 503，提示"推理服务维护中"，不静默 CPU 降级 |
| 3 | 生产切换停机窗口 | 无固定窗口，边测边切，随时可停 |
| 4 | 旧环境保留方式 | 单独备份作为回滚，不长期保留双环境 |
| 5 | Ubuntu Runner 部署 | 直接在 Ubuntu 主机上安装自托管 Runner |
| 6 | 内网 TLS/mTLS | 当前阶段不需要，API Token + 防火墙足够 |

## 21. 尚待解决的问题

- GPU API 端口（待定，默认建议 `8100`）；
- Windows GPU 服务进程管理方式（NSSM / 任务计划 / 容器）；
- HTTPS 终止方案和正式域名；
- 备份目标、保存周期和恢复责任人；
- 性能和检索质量的验收阈值（迁移前后对比用）；
- 旧环境稳定观察期和退役日期。

## 22. 迁移待办清单

### 阶段 0 ✅ 已完成 — 基线与决策

- [x] 记录当前生产 Commit SHA — `5b91f242b7932bf75801c51d07505a0a6ee92a11`
- [x] 确认 Windows 内网 IP — `${GPU_SERVICE_IP}`
- [x] 确认 GPU 信息 — RTX 5060 Ti, Driver 591.74, CUDA 13.1, 16311 MiB
- [x] 确认 Python 版本 — 3.10.11（Windows 主机）
- [x] 确认 Docker 版本 — 29.6.1, Compose v5.2.0
- [x] 确认 Qdrant 版本 — v1.18.3
- [x] 确认当前架构 — 单机 Windows 部署，Docker Compose 运行 backend+Qdrant
- [x] 确认 CI — GitHub ubuntu-latest: compileall + npm build + compose config
- [x] 确认 CD — Windows 自托管 Runner，`scripts/deploy-production.ps1`
- [x] 确认运维模式 — 24h 在线，无固定维护窗口，测试/生产合一
- [x] 记录容器内版本 — Torch 2.7.0+cu128, FlagEmbedding 1.4.0, Transformers 4.57.6
- [x] 记录 Qdrant collection — 38488 points, 76310 indexed vectors, dim=1024, Cosine
- [x] 记录 SQLite — app.sqlite 1.94 MB, parents.sqlite 116.9 MB
- [x] 记录 docs/ — 298 文件, 44.5 GB; media/ — 2 文件, 1.44 GB; parsed/ — 112 文件, 16.2 MB
- [x] 记录健康检查 — 38488 children, 20024 parents, green; LLM=glm-4.6
- [ ] **待补充**：固定检索黄金集结果（至少 10 个代表查询的延迟、结果）
- [ ] **待补充**：索引一个小型测试文档的耗时

### 阶段 1 ✅ 代码完成 — GPU 推理接口

- [x] 创建 `services/gpu_service/` 目录骨架（app.py, schemas.py, models.py, config.py, .env.example, requirements.txt, tests/）
- [x] 定义 Pydantic schema（EmbeddingRequest/Response, RerankRequest/Response, ModelInfo, Health）
- [x] 实现 GET /health、GET /model-info
- [x] 实现 POST /v1/embeddings（BGE-M3, dense + sparse）
- [x] 实现 POST /v1/rerank（BGE-reranker-v2-m3）
- [x] 实现模型单例加载、冷启动互斥、GPU OOM 保护
- [x] 实现 API Token 认证（常量时间比较）
- [x] 实现请求日志（request_id, latency, batch_size, status_code, error_type）
- [x] 实现超长文本/超量输入的截断与拒绝（413/422）
- [x] 实现 GPU 不可用 503 检测
- [x] 编写契约测试（空数组、单条、批量、中英文、超长文本、错误 Token、维度校验）
- [x] 契约测试通过（21/21，Windows 生产机验证 ✅）
- [x] 本机 Windows GPU 冒烟测试通过 ✅ — health/model-info/embedding/rerank 全部正常

### 阶段 2 ✅ 代码完成 — Provider 抽象

- [x] 定义 `EmbedProvider` / `RerankProvider` 抽象基类
- [x] 保留现有 `LocalEmbedProvider` / `LocalRerankProvider`（回归路径）
- [x] 实现 `RemoteEmbedProvider` / `RemoteRerankProvider`（HTTP 客户端）
- [x] 实现启动时 `/model-info` 契约验证
- [x] 实现连接超时、请求超时、重试与退避
- [x] 实现领域异常映射（503、401/403、413/422）
- [x] 更新 `src/config.py` 添加 provider 配置项
- [x] 实现 provider 切换，上层接口不变（encode/encode_one/rerank_scores）
- [x] 编写单元测试（mock remote provider）— 22/22 通过 ✅
- [ ] **待执行**：真实 GPU 冒烟测试（公司内网执行）

### 阶段 3 ✅ 代码完成 — 容器拆分

- [x] 拆分 requirements（requirements-prod.txt CPU, requirements-gpu.txt）
- [x] 修改 Dockerfile.backend（移除 cu128 Torch、BGE 模型下载）
- [x] 修改 docker-compose.yml（移除 GPU deploy.resources，移除 hf_cache volume）
- [x] 添加 GPU 服务环境变量（GPU_SERVICE_URL, GPU_SERVICE_TOKEN, EMBED/RERANK_PROVIDER）
- [ ] **待执行**：Ubuntu 镜像构建验证（docker compose build）

### 阶段 4 ✅ 代码完成 — CI 扩展

- [x] 添加 provider 单元测试（test-providers job）
- [x] 添加 GPU API schema/contract 测试（test-gpu-contract job）
- [x] 添加 GPU requirements/import 测试
- [x] 添加迁移配置示例完整性检查（validate-migration-config job）
- [ ] **待验证**：GitHub Actions CI 运行通过

### 阶段 5 ✅ 代码完成 — CD 拆分

- [x] 创建 Windows GPU 发布作业（scripts/deploy-gpu.ps1）
- [x] 创建 Ubuntu 应用发布作业（scripts/deploy-app.sh）
- [x] 创建部署前兼容检查（GPU /model-info 契约）
- [x] 实现发布顺序控制（deploy-gpu → deploy-app）
- [x] 配置 GitHub Environments 与 Secret（GPU_SERVICE_TOKEN, GPU_SERVICE_URL）
- [x] CD 全流程验证通过（deploy-gpu ✅ → deploy-app ✅）

### 阶段 6 ⬜ 待单独审批 — Ubuntu 基础设施

- [ ] 安装 Docker / Compose 并固定版本
- [ ] 准备仓库、数据、备份和日志目录
- [ ] 配置 HTTPS 反向代理
- [ ] 配置防火墙（仅暴露 HTTPS + SSH）
- [ ] 配置时间同步、自动安全更新、日志轮转
- [ ] 配置服务开机自启
- [ ] 配置磁盘容量告警

### 阶段 7 ⬜ 待单独审批 — 备份与测试迁移

- [ ] 备份 SQLite、Qdrant snapshot、docs、media、parsed
- [ ] 在 Ubuntu 恢复测试环境
- [ ] 验证匿名/普通用户/管理员/CSRF
- [ ] 验证固定检索与回答
- [ ] 验证上传、索引、删除路径
- [ ] 验证备份能够再次恢复

### 阶段 8 ⬜ 待条件触发 — 迁移兼容性判定

- [ ] 确认 embedding 模型 revision 一致
- [ ] 确认 dense/sparse 生成逻辑一致
- [ ] 确认 collection schema 一致
- [ ] 确认固定样本向量通过容差比较
- [ ] 通过 → 迁移现有索引 / 不通过 → 提交全量重建 R3 方案

### 阶段 9 ⛔ 待逐项批准 — 生产切换

- [ ] 逐项确认前置条件
- [ ] 执行切换步骤
- [ ] 用户验收
- [x] 进入稳定观察期（2026-07-30 用户确认观察期结束，双节点运行稳定）

### 阶段 10 ⛔ 待观察结束 — 旧环境退役

- [ ] 观察至少一个完整业务周期
- [ ] 确认所有指标稳定
- [ ] 单独审批删除旧环境
