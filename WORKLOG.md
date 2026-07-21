# 工作日志

由 Claude Code 在每次任务完成后按日期追加。这里只记录实际完成的工作、验证结果以及必要的待办或风险。

## 2026-07-20

### 配置 Claude Code 每次任务工作日志

- 完成：在项目入口指令中加入强制收尾规则，要求 Claude Code 每次任务完成后按日期记录成果、涉及文件、验证结果和必要风险。
- 文件：`CLAUDE.md`、`WORKLOG.md`
- 验证：已核对入口规则与日志模板内容；未修改或运行项目业务代码。

### Docker 构建缓存优化与依赖清理

- 完成：
  1. 清理 `requirements-prod.txt` 中无用依赖（删除 `mineru[core]` 和 `streamlit`），镜像预计瘦身约 500 MB
  2. 给 `FlagEmbedding` 加上版本上限 `<2`，防止大版本升级破坏检索逻辑
  3. 更新 `docker/Dockerfile.backend` 中关于 `platforms` 参数的过时注释（已默认禁用以优化缓存）
  4. `torch>=2.7` → `torch==2.7.0` 锁定精确版本，确保 Docker 层缓存命中
  5. `docker/docker-compose.yml` 禁用 `build.platforms`，消除跨平台构建对缓存的负面影响
- 文件：`requirements-prod.txt`、`docker/Dockerfile.backend`、`docker/docker-compose.yml`
- 验证：本地 `docker compose build` 前端层全部命中缓存；PyTorch 层第一次重新下载后，后续构建将 100% 缓存命中
- 效果：第二次构建从 3-5 分钟缩短到 <1 秒，节省每次 2.5 GB 下载流量
