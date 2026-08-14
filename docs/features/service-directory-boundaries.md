# 服务目录边界

## 状态

已实现

## 目录职责

| 目录 | 职责 | 运行入口 | 允许依赖 |
|---|---|---|---|
| `api/` | 主应用 FastAPI、鉴权、管理端和任务编排 | `api.main:app` | `src/`、共享 Python 依赖 |
| `src/` | RAG、索引、配置和共享领域契约 | 由主应用、脚本和服务导入 | 不依赖服务目录 |
| `gpu_service/` | Windows GPU Embedding/Rerank HTTP 服务 | `gpu_service.app` | 自身实现和 GPU 依赖 |
| `asr_service/` | 独立 ASR HTTP 服务、调度、存储和引擎适配 | `asr_service.app:create_app` | `src.transcription` 共享契约，不依赖 `api` |
| `libreoffice/` | 独立 Office 转换容器 | `libreoffice.app` | 自身实现和 LibreOffice 运行时 |
| `docker/` | Compose、镜像构建和部署编排 | `docker-compose.yml` | 通过 build context 引用上述目录 |

## 依赖规则

- 主应用允许 `api -> src`；`src` 不得反向导入 `api` 或任何服务目录。
- `asr_service` 可导入 `src.transcription.*` 中的协议、类型和运行时端口，以保持跨进程契约一致；不得导入 `api.*`。
- `gpu_service` 不得导入 `api.*`、`asr_service.*` 或 `src.*`，通过 HTTP 契约与主应用通信。
- `libreoffice` 不得导入项目 Python 包；通过 HTTP/容器接口提供转换能力。
- 服务目录物理路径是当前部署契约，本轮不移动或重命名目录。

## 验证入口

- `tests/test_service_directory_boundaries.py`：AST 静态依赖边界；
- `docker/docker-compose.yml`、`scripts/start-gpu-service.ps1`、`scripts/start-asr-service.ps1`：运行入口和 build context；
- `docs/features/transcript-pipeline.md`、`docs/features/gpu-runtime-deployment.md`：服务功能事实。

服务目录物理重组、Python package 重命名或部署入口变更需要单独 R2 方案；生产主机上的目录迁移属于更高风险操作。
