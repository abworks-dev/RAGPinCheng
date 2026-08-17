# Office 转换服务运维手册

## 适用范围

本文覆盖独立 `libreoffice` 容器提供的 Office 转 PDF 和 XLSX 公式重算能力。DOCX/PPTX 的 Markdown 解析仍由 backend 中的 Docling 完成，XLSX Markdown 解析由 openpyxl 完成。原始 Office 文件是事实来源；PDF、重算 XLSX 和 Markdown 均为可重新生成的派生产物。

本手册不改变 Office 上传安全策略，不处理外部链接或嵌入对象，也不授权删除生产数据或 Docker volume。

## 配置与启动

backend 使用 `LIBREOFFICE_URL` 访问转换服务，Compose 默认值为：

```text
LIBREOFFICE_URL=http://libreoffice:8101
```

请求超时由 `LIBREOFFICE_TIMEOUT` 控制，默认 120 秒。新环境从仓库根目录构建并启动：

```powershell
docker compose -f docker/docker-compose.yml build libreoffice
docker compose -f docker/docker-compose.yml up -d libreoffice
docker compose -f docker/docker-compose.yml ps libreoffice
```

不要把 `LIBREOFFICE_URL` 配置为公网地址。backend 与转换服务应位于受控的 Compose 网络中，转换端口无需对宿主机或公网暴露。

## 健康检查

容器健康检查每 30 秒请求一次 `/health`，启动宽限期为 30 秒。检查状态和最近日志：

```powershell
docker compose -f docker/docker-compose.yml ps libreoffice
docker compose -f docker/docker-compose.yml logs --tail 200 libreoffice
docker compose -f docker/docker-compose.yml exec libreoffice curl -fsS http://localhost:8101/health
```

健康响应应包含 `status: ok` 和 LibreOffice 版本。健康检查只证明进程可调用；发布验收仍需用合成 PPTX 执行一次 `/v1/convert?target_format=pdf`，并确认结果以 `%PDF-` 开头。

## 故障判断

| 现象 | 可能原因 | 检查与处置 |
|---|---|---|
| `/health` 返回 503 | LibreOffice 未安装、启动失败或 HOME 不可写 | 检查容器日志、镜像构建结果和 `/tmp/libreoffice-home` 权限 |
| backend 超时 | 文档复杂、CPU/内存不足或进程阻塞 | 核对 `LIBREOFFICE_TIMEOUT`、`docker stats` 和同时间转换数量；不要仅通过无限增大超时掩盖问题 |
| HTTP 500 且无输出 | LibreOffice 未生成目标文件 | 检查输入格式、LibreOffice stderr 和 `/data` 可写性 |
| HTTP 500 且提示 invalid PDF | 输出为空、损坏或格式不符 | 保留原始文件，丢弃派生预览，检查字体、磁盘和 LibreOffice 日志后重试 |
| HTTP 413 | 上传超过转换服务 100 MB 上限 | 不在服务端绕过限制；缩小合成测试样本或按变更流程评估上限 |
| 磁盘空间不足 | `/data` 临时文件、容器日志或 Docker 存储增长 | 先停止新转换并确认正在运行的请求；检查磁盘和 volume 使用，清理前按生产删除流程单独审批 |

服务会限制为最多两个并发 LibreOffice 进程。超时、HTTP 失败、缺失输出和损坏 PDF 均应受控失败，不得把无效内容发布为预览文件。

## 临时产物与资源观察

转换输入写入 `/data/input/<uuid>`，输出写入 `/data/output/<uuid>`。成功响应发送完成后由后台任务清理；转换异常会立即清理对应输入和输出目录。backend 生成的 PPTX 预览使用源文件同目录的 `.preview.pdf` 后缀。

观察资源和镜像体积：

```powershell
docker stats --no-stream
docker image inspect pincheng-libreoffice:latest --format '{{.Size}}'
docker system df -v
docker compose -f docker/docker-compose.yml exec libreoffice sh -lc "du -sh /data/input /data/output /tmp/libreoffice-home"
```

重点记录转换期间 CPU 峰值、内存峰值、单文件耗时和 `/data` 增量。日志已配置为单文件 10 MB、最多 5 个文件。不得使用 `docker compose down -v` 清理临时文件，因为该命令会删除整个 volume。

## 停用与回滚

当前没有独立的 Office 功能开关。停止 `libreoffice` 容器只会停用 PPTX 转 PDF和 XLSX 重算；DOCX/PPTX Docling 解析与 XLSX openpyxl 解析仍在 backend 中运行。因此，完整停用 Office 新解析需要回滚到启用 Office 之前的已验证应用镜像，不能用停止单个容器冒充完整停用。

受控回滚顺序：

1. 停止接收新的 Office 上传或发布任务，等待在途索引任务结束。
2. 记录当前 backend 和 `libreoffice` 镜像摘要、Compose 配置及失败文件标识，不记录客户文档内容。
3. 将应用和转换服务恢复到上一组已验证镜像；不要修改 PDF、Markdown 或转录服务配置。
4. 保留原始 Office 文件。派生 PDF/Markdown 可以在恢复后重新生成，不应作为唯一恢复点。
5. 验证 PDF、Markdown、转录检索及预览未受影响，再用非敏感 Office 样本决定是否恢复 Office 流量。

删除 `.preview.pdf`、重算缓存或 volume 内容属于数据操作。生产清理前必须确认目标范围、备份和恢复方式，并取得单独批准。

## 发布验证

代码侧专项验证：

```powershell
python -m pytest tests/test_office_conversion_resilience.py tests/test_office_upload_security.py tests/test_xlsx_converter.py -q
docker compose -f docker/docker-compose.yml config
```

测试只使用运行时生成的合成 DOCX、PPTX 和 XLSX，不得把真实客户资料加入 fixture。发布后用非敏感样本验证正常转换、超时/服务不可用提示、损坏输出拒绝和原始文件仍可下载。
