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

## 生产 LibreOffice 受控恢复

生产 `/health` 返回 503 且确认是 LibreOffice 进程资源异常时，使用 GitHub Actions 中的
`Repair Production PPTX Previews` workflow，选择 `recover`、确认值 `RECOVER_LIBREOFFICE`，并填写
当前已部署 App 的完整 Commit SHA。恢复操作会先确认内容索引、分类调整、旧索引、转录发布索引和
转录任务均不在活动状态，然后只重启 Compose 项目 `ragpincheng-prod` 中唯一的现有 `libreoffice`
容器。它不会重建或拉取镜像，也不会重启 backend。

workflow 会记录不含命令参数的有限容器、镜像和 PID 诊断，校验重启前后镜像 ID 一致，并在 120 秒内
等待容器恢复健康。随后必须通过 `/health` 和合成 PPTX 转 PDF 验证，才会生成与 `preview` 相同的
`manifest.json`、`manifest.sha256` 和 `context.json`。这些产物只包含版本 ID、状态和稳定错误码，
不包含客户文档内容。

若 dry-run 显示缺失预览，后续 `apply` 必须引用该 recovery run ID 和准确的 manifest SHA-256，并使用
`REPAIR_PPTX` 确认。若容器未恢复健康、镜像 ID 变化、生产 Commit 变化、活动任务出现或候选集合变化，
workflow 会停止，不执行预览补生成。恢复本身的回滚方式是停止后续 apply；原容器和原镜像均被保留，
如需镜像重建、配置修改或 backend 重启，必须另行审批。

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

部署级开关 `OFFICE_PROCESSING_ENABLED` 默认值为 `true`。设置为 `false` 并重启 backend 后，系统会阻止新的 DOCX/XLSX/PPTX 上传、server import、发布、重试和 Worker 实际处理；PDF、Markdown、转录稿以及既有 Office 资料的检索、原文件和预览读取不受影响。管理后台系统概览仅向系统管理员显示当前状态。

Office 解析还会拒绝包含外部链接、OLE/嵌入对象、宏或异常压缩包的 OOXML 文件。`OFFICE_PARSE_TIMEOUT_SECONDS`（默认 120 秒）限制 Docling/openpyxl 解析时长，`OFFICE_MIN_FREE_DISK_MB`（默认 1024 MB）作为新解析任务的最低剩余磁盘阈值；触发时任务会记录可重试的失败原因，后台概览显示磁盘状态。

关闭开关不会删除、隐藏或重建既有文件、索引和派生产物。入口返回稳定错误码 `office_processing_disabled`，已排队但尚未执行的 Office 任务由 Worker 标记为失败。恢复时将开关设回 `true`、重启 backend，再由管理员重新上传、发布或重试失败任务。

受控回滚顺序：

1. 将 `OFFICE_PROCESSING_ENABLED=false` 写入目标环境的私有配置并重启 backend；确认管理后台显示“Office 新资料处理：已停用”。
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
