# 品成 BIM 知识库：内网运维速查

本文是 IT 人员的日常速查，不替代生产部署 workflow、备份方案或迁移
Runbook。生产环境为双节点：Ubuntu 应用节点运行 backend、Qdrant 和
LibreOffice；Windows GPU 节点运行独立的 Embedding/Rerank 服务。

## 访问与健康检查

在任意能访问应用节点的机器执行：

```bash
curl http://${APP_NODE_IP}/api/health
```

GPU 节点只允许从受控网络检查：

```bash
curl http://${GPU_SERVICE_IP}:8100/health
curl http://${GPU_SERVICE_IP}:8100/model-info
```

不要把响应中的计数值写入文档作为固定基线；parents、children 和模型状态
会随索引与发布变化。

## 日常操作

所有 Ubuntu 命令在生产应用节点执行，使用实际的 Compose env 文件：

```bash
cd ${PRODUCTION_APP_REPO_PATH}
sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} ps

sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} logs --tail 200 backend
```

日常重启仅在确认没有索引、发布或迁移任务运行时执行：

```bash
sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} restart backend
```

禁止使用 `docker compose down -v`，也不要直接操作 Qdrant volume。

## 资料入口

日常资料通过管理后台上传、审核、发布和索引。文件系统批量导入只用于
受控兼容目录，并必须由已批准的索引流程执行：

```bash
sudo docker compose -p ragpincheng-prod \
  -f docker/docker-compose.yml \
  -f ${PRODUCTION_APP_COMPOSE_OVERRIDE} \
  --env-file ${PRODUCTION_APP_ENV_FILE} exec backend \
  python scripts/build_index.py
```

真实资料、删除、索引 Reset、旧目录迁移和严格 head 切换不属于普通 IT
操作，必须遵守 [受管资料生产迁移 Runbook](../migrations/managed-content-production-runbook.md)。

全量重建不得在主机直接运行 `scripts/build_index.py --reset`。获批后只能从
`master` 手动运行 `Rebuild Production Index Manual`，输入合并后的完整 SHA
并选择 `REBUILD_PRODUCTION_INDEX`。该 workflow 会取得生产排他锁、阻止活动
任务、备份两个 SQLite 和 Qdrant、构建并验证影子索引，再进行短暂停机切换；
head 在构建期间发生变化时会停止而不会切换。成功证据必须包含
`REBUILD_BACKUP status=complete`、`REBUILD_SHADOW status=verified` 和
`REBUILD_CUTOVER status=success`。失败时必须核对
`REBUILD_ROLLBACK status=complete`，不得改用裸 Reset 继续处理。

## 网络与 HTTPS

防火墙、反向代理、证书和 Cookie 安全属性由基础设施负责人按公司网络规范
配置。纯 HTTP 调试时使用 `SESSION_COOKIE_SECURE=false`；启用 HTTPS 后必须
改为 `true`，并重启 backend。不要在仓库文档中写入真实 IP、Token 或密码。

## 部署与回滚

正常部署使用 [Git 同步部署与测试流程](Git同步部署与测试流程.md) 及其引用的
GitHub Actions workflow。生产部署必须选择完整 master SHA、完成备份和健康
检查，不能在生产机直接 `git checkout` 未审计提交。

回滚、Qdrant 快照、SQLite 恢复和旧资料切换边界见：

- [受管资料生产迁移 Runbook](../migrations/managed-content-production-runbook.md)；
- [Ubuntu 应用与 Windows GPU 迁移手册](../migrations/ubuntu-app-windows-gpu-runbook.md)；
- [用户验收规范](../USER_ACCEPTANCE.md)。

## 故障排查

- 应用不可用：先检查 backend health，再查看 backend 日志和 Qdrant health；
- 引用或检索异常：确认 GPU `/health`、`/model-info` 与当前 runtime identity；
- Office 失败：检查 LibreOffice 服务状态和磁盘空间，见 [Office 转换运维手册](OFFICE_CONVERSION.md)；
- ASR 异常：保持 `ASR_ENABLED=false` 的回退能力，使用受控 ASR workflow，不要手工杀进程或修改共享 venv。
