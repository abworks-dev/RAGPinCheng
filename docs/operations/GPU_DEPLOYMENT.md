# GPU 部署与运行时说明

## 当前架构

生产环境不是把 backend 容器直接放到 GPU 主机上。当前拓扑为：

- Ubuntu 应用节点：FastAPI、React、Qdrant、SQLite 和 LibreOffice；
- Windows GPU 节点：`services/gpu_service/`，提供 BGE-M3 Embedding 和
  BGE-reranker HTTP 服务；
- backend 通过 `EMBED_PROVIDER=remote`、`RERANK_PROVIDER=remote` 和受控
  `GPU_SERVICE_URL` / `GPU_SERVICE_TOKEN` 访问 GPU 服务。

本文只记录当前远程 GPU 运行时的边界。旧版“backend 容器预留 NVIDIA GPU、
安装 CUDA Torch、添加 Compose `deploy.resources`”方案已经废弃，不得照此
修改当前 Compose。

## 当前验证入口

```bash
curl http://${GPU_SERVICE_IP}:8100/health
curl http://${GPU_SERVICE_IP}:8100/model-info
```

`/model-info` 返回的模型、runtime source fingerprint、lock hash 和 CUDA
设备必须与受控 deployment workflow 选择的 release 一致。应用节点只检查
服务契约，不在生产部署时临时安装依赖或下载模型。

## 运行时状态

当前锁文件为 `services/gpu_service/runtime-lock.json`。它必须处于
`validated`，并绑定 qualification run、源码 fingerprint、lock SHA 和
Torch wheel SHA；`candidate` 只能进入人工复核，不能 promotion。

详细的候选解析、qualification、release promotion、回滚和存储清理见：

- [GPU runtime 功能事实](../features/gpu-runtime-deployment.md)；
- [Ubuntu 应用与 Windows GPU 迁移手册](../migrations/ubuntu-app-windows-gpu-runbook.md)；
- `scripts/deploy-gpu.ps1` 和对应的手动 GitHub Actions workflow。

## 故障与回滚边界

- GPU health 或模型身份不匹配时，保持应用回退/停止状态，不直接重启未知进程；
- release promotion 失败时使用 workflow 保存的 activation state 和备份回滚；
- 不修改共享 wheel cache、正式模型、生产 `.env` 或其他服务的进程；
- 不使用 `Stop-Process -Name python -Force`、全局 pip 或手工替换 runtime lock。

任何真实 GPU qualification、模型准备、Windows 服务激活和生产流量变更都需要
独立 R3 方案与审批。
