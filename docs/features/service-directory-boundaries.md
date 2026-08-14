# 服务目录边界

## 状态

已实现

## 目录职责

| 目录 | 职责 | 运行入口 | 允许依赖 |
|---|---|---|---|
| `api/` | 主应用 FastAPI、鉴权、管理端和任务编排 | `api.main:app` | `src/`、共享 Python 依赖 |
| `src/` | RAG、索引、配置和共享领域契约 | 由主应用、脚本和服务导入 | 不依赖服务目录 |
| `services/gpu_service/` | Windows GPU Embedding/Rerank HTTP 服务 | `services.gpu_service.app` | 自身实现和 GPU 依赖 |
| `services/asr_service/` | 独立 ASR HTTP 服务、调度、存储和引擎适配 | `services.asr_service.app:create_app` | `src.transcription` 共享契约，不依赖 `api` |
| `services/libreoffice/` | 独立 Office 转换容器 | `libreoffice.app` | 自身实现和 LibreOffice 运行时 |
| `docker/` | Compose、镜像构建和部署编排 | `docker-compose.yml` | 通过 build context 引用上述目录 |

## 依赖规则

- 主应用允许 `api -> src`；`src` 不得反向导入 `api` 或任何服务目录。
- `services.asr_service` 可导入 `src.transcription.*` 中的协议、类型和运行时端口，以保持跨进程契约一致；不得导入 `api.*`。
- `services.gpu_service` 不得导入 `api.*`、`services.asr_service.*` 或 `src.*`，通过 HTTP 契约与主应用通信。
- `services.libreoffice` 不得导入项目 Python 包；通过 HTTP/容器接口提供转换能力。
- 根目录 `asr_service` 和 `gpu_service` 仅为旧模块入口兼容 shim，不包含业务实现。

## 验证入口

- `tests/test_service_directory_boundaries.py`：AST 静态依赖边界；
- `docker/docker-compose.yml`、`scripts/start-gpu-service.ps1`、`scripts/start-asr-service.ps1`：运行入口和 build context；
- `docs/features/transcript-pipeline.md`、`docs/features/gpu-runtime-deployment.md`：服务功能事实。

移除根目录兼容 shim 或迁移生产主机上的既有 release 入口需要独立审批；仓库目录迁移不自动改变已部署 release。
