# 多引擎视频自动转录 Phase 5C：Windows ASR 统一部署方案

- 状态：R3A 仓库实施与 PR #8 远端 CI 验证完成；R3B/R3C 未授权
- 日期口径：2026-08-03，Asia/Shanghai
- 适用主机：Windows GPU `192.168.11.11`、Ubuntu backend `192.168.11.12`
- 固定模型：`iic/SenseVoiceSmall` revision `7bf452403abd7353a300cd760f7adae7701c92c1`

## 1. 目标

建立可审计、可回滚、默认不激活的 Windows ASR 独立部署通道。R3A 只提交仓库代码、配置模板、脚本、手动 workflow 和离线测试；不连接生产主机、不安装依赖、不下载模型、不修改防火墙、不写入生产 Token、不启动服务。

## 2. 冻结目录与配置所有权

```text
D:\Services\RAGPinCheng-ASR\
├── app\
├── venv\
└── scripts\

D:\ServiceData\RAGPinCheng-ASR\
├── config\asr.env
├── models\SenseVoiceSmall\7bf452403abd7353a300cd760f7adae7701c92c1\
├── spool\
├── logs\
└── backups\
```

- Windows ASR 服务端只读取 `D:\ServiceData\RAGPinCheng-ASR\config\asr.env`，模板是 `asr_service/.env.example`。
- Ubuntu backend 客户端继续读取生产 `prod.env`，模板是根 `.env.example` 中的 `ASR_ENABLED`、`ASR_SERVICE_URL` 等客户端键。
- 两份 env 不能拼接。Token 值不进入仓库、命令行、Scheduled Task XML 或日志。
- `BGE_PRIORITY_PROBE_TOKEN` 必须与现有 GPU service 的 `GPU_SERVICE_TOKEN` 完全一致；它是 ASR 调用 GPU `/v1/activity` 时使用的 Bearer Token，不与 Ubuntu backend 调用 ASR 的 `ASR_SERVICE_TOKEN` 混用。
- 现有 GPU service 保持原 Python 环境；本阶段只增加鉴权 `GET /v1/activity`。

## 3. 运行契约

### 3.1 模型缓存

服务只接受 `asr-model-manifest/1`。manifest 必须位于固定模型目录，字段集合严格为 `schema_version`、`model_id`、`model_revision`、`model_path`、`files`。每个文件记录严格为相对路径、非负整数大小和 64 位小写 SHA-256；除 manifest 自身外，模型目录中的所有常规文件必须被完整、唯一枚举，未列出的文件和符号链接同样 fail-closed。路径逃逸、未知字段、身份不匹配、缺失或篡改均 fail-closed。运行时固定 `local_files_only=true`、`disable_update=true`，不允许隐式联网补模型。

### 3.2 BGE 优先级

GPU service 的受鉴权端点返回唯一结构：

```json
{
  "api_version": "gpu-activity/1",
  "model_loaded": true,
  "inflight_requests": 0,
  "asr_chunk_allowed": true
}
```

ASR 仅在四个字段均合法、模型已加载、inflight 为 0 且明确允许时运行下一块。超时、非 200、非法 JSON、版本不匹配、未知字段、GPU 未加载或忙碌均暂停；不得以固定 allow 绕过探针。

### 3.3 进程与身份

- Scheduled Task 名称：`RAGPinCheng-ASR`。
- R3B 初始运行账号：`Administrator`，作为显式安全例外。
- 补偿措施：配置目录 ACL 中 Administrators/SYSTEM 拥有 Full Control，受信任的 GitHub Actions runner 执行身份 NETWORK SERVICE 仅拥有 Modify，用于受控 payload 部署和 Environment Secret 注入；Token 仅从 env 文件进入进程；防火墙未来只允许 `192.168.11.12` 访问 8200；服务与 GPU 8100 独立。
- 后续迁移到低权限服务账号属于独立 R3，不在 R3A。

## 4. R3A 仓库实施

1. 严格本地模型 manifest 校验和固定 revision 接线。
2. 鉴权 GPU `/v1/activity` 与 fail-closed HTTP probe。
3. 独立的 `start-asr-service.ps1`、`deploy-asr.ps1`、`verify-asr-service.ps1`，以及 Windows ASR 专用 `requirements-windows.txt`；依赖安装只在未来显式开关下执行。
4. 仅 `workflow_dispatch` 的 `deploy-asr-production.yml`，要求完整 40 位 SHA，GitHub Environment 为 `production-asr`，安装和激活默认均为 false。
5. Windows/Ubuntu env 模板职责分离。
6. CI 运行真实 GPU contract tests，但用测试本地 torch stub，不安装 Torch、FlagEmbedding、FunASR 或模型。
7. 单元、合约和静态边界测试。

## 5. R3B 生产准备（未授权）

需单独逐项批准后才能执行：

1. 在 Windows 建立固定目录、专用 `venv` 和配置 ACL。
2. 由管理员写入 ASR 与 GPU probe Token；两端 Token 必须匹配各自调用方。
3. 通过现有 GPU service 部署流程更新 GPU service 代码并验证受鉴权 `/v1/activity`；保持其现有 Python 环境，不在 R3B 中迁移运行时。
4. 安装明确锁定的 Python/CUDA/FunASR 依赖；创建专用 `venv` 时只从 HKLM 或 `Program Files` 解析机器级 Python 3.11，并以运行时版本探针拒绝 Python 3.10、用户级 Launcher 或其他版本。
   同一完整 SHA 的残留 staging 不删除，先移动到 `backups` 的 `stale-staging-*`；依赖安装失败的 staging 移动为 `failed-staging-*`，保证后续重试可恢复且保留审计证据。
5. 离线准备固定 revision 模型，生成并复核 manifest。
6. 配置仅允许 `192.168.11.12 -> 192.168.11.11:8200` 的防火墙规则。
7. 注册但先不对业务开放 Scheduled Task，执行 health/capabilities/activity 验证。

## 6. R3C 隔离验收与开放（未授权）

1. 使用非敏感短媒体和 experimental Profile 完成单任务端到端验证。
2. 验证 BGE 忙碌时 ASR 暂停、空闲时恢复，取消/超时/失败均可控。
3. 检查 spool、artifact、日志权限与清理；不使用生产资料。
4. Ubuntu backend 先保持 `ASR_ENABLED=false`，验收通过后再单独批准开启。
5. 任何正式 Profile、自动发布、自动索引或生产灰度均需后续审批。

## 7. 回滚

- 仓库：单独 revert R3A 提交。
- Windows 程序：停止/禁用 `RAGPinCheng-ASR`，将 `backups` 中上一版本恢复到 `app`。
- Ubuntu：保持或恢复 `ASR_ENABLED=false`；人工 Markdown 路径不受影响。
- 数据：R3A 不创建生产数据；后续不得自动删除模型、spool、artifact 或日志，清理由独立审批执行。

## 8. R3A 完成标准

- 固定目录、完整 SHA、env 分离和默认关闭均有静态测试。
- 模型 manifest、路径、大小、SHA-256 与篡改边界有离线测试。
- `/v1/activity` 鉴权、idle/busy/unloaded、异常释放计数有 contract tests。
- HTTP probe 的 allow/busy/unavailable 唯一映射有离线测试。
- CI 不安装真实 GPU/ASR 依赖且实际运行 GPU contract tests。
- 脚本通过 PowerShell 语法解析；Python 模块通过编译。
- 未连接生产主机、未安装依赖、未下载模型、未改防火墙、未写生产 Token、未启动服务。
