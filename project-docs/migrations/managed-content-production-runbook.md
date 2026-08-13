# 受管知识资料库生产运行与旧资料迁移手册

- 状态：117 份普通资料已迁移发布，待旧索引下线验收
- 适用环境：Ubuntu 生产应用节点
- 当前基线日期：2026-08-13
- 关联方案：`../plans/managed-content-library.md`
- 关联决策：`../decisions/0003-managed-content-library.md`
- 当前功能：`../features/document-indexing.md`

## 1. 当前生产基线

以下事实由生产部署 workflow `31500815860`、迁移 workflow `31669922373` 和当前源码共同证明：

- 生产提交：`61ac39f5edbc6fc9319b1d65a3b6e33fd2f79cfa`；
- 主机内容根目录：`/data/business/ragpincheng/content`；
- 容器内容根目录：`/app/content`；
- `CONTENT_MANAGEMENT_ENABLED=true`；
- `CONTENT_HEAD_ENFORCEMENT=compat`；
- `app.sqlite` Schema 为 `5`，七个一级分类已创建；
- 部署前备份：`/data/backup/databases/ragpincheng/app-only-31500815860-1`；
- 部署前后 Qdrant 均为 `38,491` points；
- 既有视频转录准入状态保持不变；
- 迁移以提交 `b0454e4e9fb82da181ef0b3af8bed2fabe4298e5` 执行；117 份普通资料已登记为 `legacy` 来源，全部为 `awaiting_review`；
- `content_item_heads=0`，没有资料被确认、发布或切换到正式受管检索；`CONTENT_HEAD_ENFORCEMENT` 仍为 `compat`；
- 旧 `/data/business/ragpincheng/source/docs` 和 `source/media` 均未删除或移动，旧媒体仍由既有链路管理。

当前部署备份包含 `app.sqlite`、`parents.sqlite`、Qdrant snapshot 信息、部署前后 Git 状态和回滚镜像信息，不包含完整 `CONTENT_ROOT` 文件副本。内容根目录开始保存正式对象后，必须另有独立文件级备份。

## 2. 事实来源与目录职责

正式事实由 `app.sqlite` 和 `CONTENT_ROOT/objects` 共同组成。人工目录、只读视图和旧 `source/docs`、`source/media` 都不能单独决定分类、版本或检索可见性。

```text
/data/business/ragpincheng/content/
├─ inbox/
│  ├─ web/<batch_id>/       # 网页上传的短期暂存目录
│  └─ server/<batch_id>/    # BIM 工程师后台投递批次
├─ objects/sha256/          # 正式内容寻址对象，禁止人工改名或移动
├─ media/                   # 受管媒体预留目录，不替代既有 source/media
├─ transcription-artifacts/
├─ manifests/
├─ published/               # 发布解析工作副本，禁止作为分类事实来源
├─ quarantine/
└─ views/current/           # 可重建的一至四级只读目录视图
```

`views/current` 中的目录名为 `<显示编号>_<显示名称>`。编号和显示名称来自数据库，不从文件原路径实时推导。该目录可删除后重建，但不得人工编辑或交给索引器扫描。

## 3. 主机权限

内容根目录应由运行维护账号 `bimtrans` 拥有，并使用 setgid 让新建子目录继承同一用户组：

```bash
sudo install -d -o bimtrans -g "$(id -gn bimtrans)" -m 2775 \
  /data/business/ragpincheng/content
```

只读核对：

```bash
stat -c '%U %G %a %n' /data/business/ragpincheng/content
find /data/business/ragpincheng/content -maxdepth 2 -printf '%M %u %g %p\n' | sort
docker compose -p ragpincheng-prod \
  -f /data/business/ragpincheng/source/docker/docker-compose.yml \
  exec -T backend sh -eu -c 'test -w /app/content && echo writable'
```

预期根目录权限为 `2775`。`views/current` 由程序生成，目录和文件分别收紧为只读模式；不要用递归 `chmod 777` 处理权限问题。

## 4. 分类设置位置

分类表位于生产 `app.sqlite` 的 `category_nodes`，人工目录别名位于 `category_import_aliases`。网页“分类设置”页通过 API 修改显示编号、显示名称、排序和启停状态；`category_key` 是稳定业务标识，创建后不随显示名称改变。

首批一级分类为：

| 编号 | 显示名称 | 稳定标识 |
|---|---|---|
| 01 | 行业规范与标准 | `industry_standards` |
| 02 | 客户标准与要求 | `client_requirements` |
| 03 | 公司内部标准 | `company_standards` |
| 04 | 项目资料 | `project_materials` |
| 05 | 培训资料 | `training_materials` |
| 06 | 项目经验与案例 | `project_experience` |
| 99 | 待确认资料 | `pending_confirmation` |

后台导入会按父级逐层识别以下目录名：显示名称、`编号_显示名称`、`编号 显示名称`，以及显式配置的导入别名。无法映射或超过四级的资料进入 `99 待确认资料` 并标记 `needs_mapping=true`，不得因此自动发布。

## 5. 网页上传流转

管理端导航职责固定为：

- “资料库”：普通资料的唯一网页上传、确认和发布入口；
- “分类设置”：维护数据库分类的编号、名称、层级、排序和启停；稳定标识由服务端生成并在业务界面隐藏；
- “索引监控”：分别查看资料库发布任务和兼容期旧目录索引任务，不提供新资料上传。

启用受管资料库时，旧 `/api/admin/upload` 会在写入前返回 `409`。批量确认、退回或发布单次最多 20 份，逐项返回成功或失败；禁止将 117 份历史迁移资料无审查地一次性发布。

```text
网页选择分类和文件
→ inbox/web/<batch_id>/.<uuid>.upload 临时写入并计算 SHA-256
→ objects/sha256/<前两位>/<sha256>
→ app.sqlite 登记批次、资料和版本（draft）
→ BIM 工程师提交（awaiting_review）
→ 资料负责人确认（approved）
→ 系统管理员发布并建立候选索引
→ 索引成功后切换 content_item_heads
→ published 工作副本 + 正式检索可见
→ rebuild_content_view 生成只读目录视图
```

上传成功后，临时文件被移动或丢弃，`inbox/web/<batch_id>` 可能只剩空批次目录。不能通过查看 `inbox/web` 判断资料是否已经保存；应以管理页面、数据库记录和 `objects/sha256` 为准。

同一 SHA-256 可以复用一个物理对象，但每次业务上传仍保留独立资料/版本关系。跨项目不做强制业务合并。

## 6. 服务器后台投递流转

BIM 工程师只在新的批次目录中整理资料，例如：

```text
/data/business/ragpincheng/content/inbox/server/20260811-bim-001/
├─ 01_行业规范与标准/
├─ 02_客户标准与要求/
├─ 03_公司内部标准/
├─ 04_项目资料/
├─ 05_培训资料/
├─ 06_项目经验与案例/
└─ 99_待确认资料/
```

先 dry-run，不写数据库和对象存储：

```bash
docker compose -p ragpincheng-prod \
  -f /data/business/ragpincheng/source/docker/docker-compose.yml \
  exec -T backend python scripts/import_content_batch.py \
  /app/content/inbox/server/20260811-bim-001
```

检查输出中的 `planned`、`category_id`、`needs_mapping`、`reason` 和 SHA-256。只有负责人确认目录映射、文件数量和异常项后，才能在独立 R3 批准中使用具有 `import_server` 权限的真实 `actor-user-id` 执行 `--apply`。apply 会复制到对象存储、登记数据库并直接进入待确认状态，不会绕过确认和发布。

```bash
docker compose -p ragpincheng-prod \
  -f /data/business/ragpincheng/source/docker/docker-compose.yml \
  exec -T backend python scripts/import_content_batch.py \
  /app/content/inbox/server/20260811-bim-001 \
  --actor-user-id <已授权用户ID> --apply
```

不得把 `/data/business/ragpincheng/source/docs` 直接作为 apply 输入，也不得在 `objects`、`published` 或 `views/current` 中人工放文件。

## 7. 旧 docs 和 media 的处理原则

不需要重新通过网页上传全部旧资料，也不应直接移动旧目录。迁移按“只读清点 → 映射确认 → 复制到新批次 → dry-run → apply → 确认发布 → 观察”执行。

旧目录只读清点：

```bash
cd /data/business/ragpincheng/source
python scripts/inventory_legacy_content.py \
  --docs-root docs \
  --media-root media \
  --output /data/backup/databases/ragpincheng/legacy-inventory-20260811.json
```

清点输出文件必须是新路径；脚本发现同名输出时会拒绝覆盖。清点会计算大小和 SHA-256，但不会写 `app.sqlite`、Qdrant 或 `CONTENT_ROOT`。

### 7.1 离线迁移规划

清点结果不能直接决定分类。负责人必须另行编写显式映射 JSON，将旧 `docs` 下的相对目录前缀映射到稳定 `category_key`，并声明该范围是普通资料还是视频转录稿。不要把 `category_hint` 当作发布授权，也不要把绝对生产路径写入映射或规划结果。

以下示例仅使用合成目录名：

```json
{
  "schema_version": 1,
  "mappings": [
    {
      "kind": "docs",
      "legacy_prefix": "公司标准",
      "category_key": "company_standards",
      "handling": "document"
    },
    {
      "kind": "docs",
      "legacy_prefix": "教学视频",
      "category_key": "training_materials",
      "handling": "transcript"
    }
  ]
}
```

规划器只读取 inventory 和 mapping JSON，不读取清点根目录，也不连接 SQLite、Qdrant、对象存储或外部服务：

```bash
python scripts/plan_legacy_content_migration.py \
  --inventory /tmp/legacy-migration/inventory.json \
  --mapping /tmp/legacy-migration/mapping.json \
  --output-json /tmp/legacy-migration/plan.json \
  --output-csv /tmp/legacy-migration/plan.csv
```

默认大小上限为 200 MiB，可用 `--max-bytes` 显式调整以匹配未来导入窗口。输出存在时命令整体拒绝，不会只改写 JSON 或 CSV 中的一个；只有明确使用 `--overwrite` 才会替换输出，并且任何输出都不得与输入文件同路径。规划输出不包含文件正文或清点根绝对路径。

负责人逐项检查以下 disposition：

| disposition | 含义和后续动作 |
|---|---|
| `import_document` | 显式映射且满足普通资料导入约束；未来 R3 批准后才可复制到新 `inbox/server` 批次 |
| `preserve_legacy_media` | 保持在既有视频链路；不得交给普通资料导入器 |
| `review_transcript_link` | 教学/培训视频 Markdown 转录稿；人工核对 media、版本和 transcript head，禁止自动重复登记 |
| `pending_mapping` | 未命中显式映射；负责人补充或修正 mapping 后重新规划 |
| `unsupported` | 类型不支持、超过大小限制、目录超过四级或 transcript 范围出现非 Markdown；先人工处理 |
| `symlink_rejected` | 符号链接；不得跟随或迁移 |

同一映射前缀范围内相同 SHA-256 会进入 `duplicate_group`；不同映射范围或 docs/media 间相同 SHA-256 只列入 `related_sha256_paths`。两种标记都只供审查，不会删除文件或合并业务资料。

### 7.2 合成演练

开发和演练必须只使用临时目录与合成内容。下面的 PowerShell 示例不会读取真实 `source/docs` 或 `source/media`：

```powershell
$migrationLab = Join-Path ([System.IO.Path]::GetTempPath()) "ragpincheng-legacy-migration-lab"
New-Item -ItemType Directory -Force "$migrationLab/docs/公司标准", "$migrationLab/media/demo-media" | Out-Null
Set-Content -Encoding utf8 "$migrationLab/docs/公司标准/demo.md" "# synthetic"
Set-Content -Encoding utf8 "$migrationLab/media/demo-media/original.mp4" "synthetic-media-placeholder"
python scripts/inventory_legacy_content.py --docs-root "$migrationLab/docs" --media-root "$migrationLab/media" --output "$migrationLab/inventory.json"
python scripts/plan_legacy_content_migration.py --inventory "$migrationLab/inventory.json" --mapping "$migrationLab/mapping.json" --output-json "$migrationLab/plan.json" --output-csv "$migrationLab/plan.csv"
```

先在该临时目录中准备只含合成目录名的 `mapping.json`。演练完成后仅删除自行创建的临时实验目录；不要把真实目录、正式 inventory、迁移报告或业务资料提交到 Git。

### 7.3 T10 只读预检与真实批次边界

T9 恢复点生成的规划只能作为 T10 候选输入。生产只读预检使用
`scripts/preflight_legacy_content_t10.py` 重新核对当前旧文件的大小和 SHA-256，并以只读模式查询
`app.sqlite` 的活动分类。预检不得创建 `upload_batches`、写对象存储、复制到生产 `inbox`、确认、发布或索引。

受控 workflow `.github/workflows/preflight-managed-content-t10.yml` 固定到已验证的 T9 恢复点和预期候选数量，
只上传聚合分类、类型、数量、总字节和数据库基线；artifact 不包含真实文件名、相对路径、业务文件 SHA-256
或正文。预检通过只证明候选源文件仍与 T9 规划一致，不代表资料负责人已确认，也不授权真实 apply。

未来获独立 R3 批准后，先用 `scripts/stage_legacy_content_t10.py` 将同一计划复制到唯一批次并生成完整
manifest，再使用 `scripts/apply_legacy_content_t10.py` 登记。apply 必须显式匹配 plan SHA-256、预期文件数、
授权用户 ID 和 `APPLY_T10` 确认词；导入结果停在 `awaiting_review`。同一 plan 标识只能创建一个 legacy
批次；中途失败的批次标记为 `failed`，不得不经恢复核对直接重跑。

生产 apply 只通过 `.github/workflows/apply-managed-content-t10.yml` 执行。workflow 在 staging 前创建独立
SQLite、Qdrant 和完整 `CONTENT_ROOT` 恢复点，固定 T9 run、plan SHA-256、候选数和 `compat` 模式，并从
受保护 Environment 变量 `T10_IMPORT_ACTOR_USER_ID` 读取执行身份。该 workflow 的存在不构成真实执行授权。

迁移边界：

- 普通 PDF、Markdown 和 Office 资料：按新分类复制到新的 `inbox/server/<batch_id>`，再走 dry-run/apply/确认/发布；
- `.preview.pdf` 和 `.preview.xlsx` 是 Office 文件生成的预览产物，不是独立业务资料；规划结果必须标为 `derived_artifact/generated_preview`，T10 apply 对旧计划中的同类文件也会失败关闭；
- 旧 `docs/教学视频`、`docs/培训视频` 中的转录稿：先核对是否已有 `media_assets`、`transcript_versions` 和正式 transcript head，避免把同一转录稿误当普通资料重复发布；
- 旧 `source/media`：现阶段继续作为既有视频链路的事实目录，不由 `import_content_batch.py` 导入，不移动到 `CONTENT_ROOT/media`；
- 未能可靠映射或无法确认业务归属的资料：复制到 `99_待确认资料`，不得自动发布；
- 离线规划器只生成审查报告，不复制文件，也不调用 `import_content_batch.py`；
- T10 只读预检只重新核验候选普通资料，不读取正文到日志，不写生产数据库或内容根目录；
- 观察期内旧 `docs`、`media` 和旧索引保持原状，至少 1 至 2 周后再提交独立清理方案。

### 7.4 已误收预览资料的受控归档

`scripts/archive_legacy_generated_previews.py` 只处理 `source_origin=legacy` 且文件名以 `.preview.pdf` 或
`.preview.xlsx` 结尾的当前未归档资料。默认运行是只读 dry-run，只输出数量、生命周期聚合和阻塞数量，
不输出文件名、路径或正文：

```bash
docker compose -p ragpincheng-prod \
  -f /data/business/ragpincheng/source/docker/docker-compose.yml \
  exec -T backend python scripts/archive_legacy_generated_previews.py \
  --database /app/data/app.sqlite
```

真实归档属于生产数据修改，必须纳入独立 R3 审批并使用活动管理员 ID：

```bash
docker compose -p ragpincheng-prod \
  -f /data/business/ragpincheng/source/docker/docker-compose.yml \
  exec -T backend python scripts/archive_legacy_generated_previews.py \
  --database /app/data/app.sqlite \
  --actor-user-id <approved-user-id> \
  --apply --confirm ARCHIVE_GENERATED_PREVIEWS
```

脚本发现任一候选存在正式 head、活动/已发布 publication 或活动索引任务时整批拒绝。apply 只设置
`content_items.archived_at` 并写 `content.generated_preview_archived` 审计事件；不删除对象字节、旧源文件、
parents.sqlite 或 Qdrant point。生产执行前后都要记录候选数、阻塞数、正式 head 和审计事件增量。

## 8. 只读视图

发布完成后，由管理员在受控窗口重建：

```bash
docker compose -p ragpincheng-prod \
  -f /data/business/ragpincheng/source/docker/docker-compose.yml \
  exec -T backend python scripts/rebuild_content_view.py
```

只读核对：

```bash
find /data/business/ragpincheng/content/views/current \
  -maxdepth 4 -printf '%M\t%p\n' | sort
```

重建使用新目录生成后原子替换 `views/current`。它会复制正式 head 对应文件并收紧权限，因此需要额外临时磁盘空间。

## 9. 后续部署策略

生产 app-only workflow 的 `content_root_policy` 含义：

- `REQUIRE_EMPTY`：仅用于首次启用或明确要求空根目录的部署；根目录已有任何内容时立即失败；
- `PRESERVE_EXISTING`：用于内容根目录已经投入使用后的应用部署，保留已有对象和批次。

当前生产根目录已经由程序初始化，后续正常应用部署应使用 `PRESERVE_EXISTING`。该参数只防止部署误覆盖，不等于内容备份。

应用部署必须在备份、Git 更新和容器重启之前，以只读方式检查 `content_index_jobs`、兼容期 `index_jobs`、`transcript_publication_index_jobs` 和 `transcription_jobs`。任一队列存在活动任务时部署直接失败，并且只输出各队列聚合数量；等待任务结束后重新执行同一部署，不要在部署窗口强制中断或清理任务。

发布失败时先在“资料库”查看最新失败原因、可重试性和建议操作；“索引监控”默认每个版本只显示最新尝试，通过“查看历史尝试”核对完整审计记录。需要密码的 PDF 应先解除密码保护后重新上传；解析服务或索引服务类可重试错误应在依赖恢复后再发布，避免连续重复提交。

## 10. 备份、验证与回滚边界

每个真实资料迁移批次开始前至少保留：

1. `app.sqlite` 一致性备份；
2. `parents.sqlite` 一致性备份；
3. Qdrant collection snapshot 及迁移前 points 计数；
4. `CONTENT_ROOT` 独立文件级备份或存储快照；
5. 旧 `source/docs` 和 `source/media` 的只读备份/快照；
6. 批次清单、目录映射、文件数、总字节数和 SHA-256 清单。

每批验证至少包括：

- 数据库 `PRAGMA integrity_check`；
- 导入条目数与异常项；
- 确认、发布和失败重试状态；
- Qdrant points 变化与正式 head 数量；
- 非敏感样本的检索、引用和预览；
- 只读视图路径、文件数和权限；
- 旧视频转录检索和播放链路不退化。

应用部署失败时，现有 workflow 会恢复部署前 backend 镜像并核对健康状态。它不会自动回滚真实资料批次，也不会恢复 `CONTENT_ROOT`、SQLite 或 Qdrant。出现数据错配时应立即停止新上传、确认和发布，保留现场副本，并按同一迁移批次的备份单元提交 R3 恢复方案。未验证备份可恢复前，不得删除旧目录或旧索引。

## 11. 明确禁止

- 不直接移动、重命名或删除 `objects/sha256` 中的文件；
- 不把 `views/current` 或 `inbox` 加入旧索引扫描目录；
- 不用递归 `chmod 777`、不把容器改成特权模式；
- 不在未 dry-run、未确认映射或无独立备份时执行 `--apply`；
- 不把旧媒体批量塞入普通资料导入脚本；
- 不在观察期内删除旧 `source/docs`、`source/media` 或旧索引；
- 不把 `compat` 改为 `strict`，直到旧资料迁移、正式 head 覆盖和检索回归均通过。

## 12. T11 旧普通资料索引受控下线

117 份普通资料全部发布后，旧目录索引不会自动覆盖或删除。生产仍保持 `compat`，因此同一资料的旧未版本化索引和新受管版本可能同时参与召回。T11 只清理与当前 117 个正式 `legacy` head 的 `source_rel_path` 精确对应的未版本化 Parent/Point，以及已归档的生成预览；不按 `content_version_id IS NULL` 全库删除，不处理教学/培训视频转录，不修改文件、内容对象、发布记录或正式 head。

受控 workflow 为 `.github/workflows/retire-managed-content-legacy-index.yml`，固定确认词 `RETIRE_LEGACY_INDEX_T11`、完整生产 commit、117 条迁移记录（116 个正式 head + 1 个已归档生成预览）和 `compat` 模式。执行前必须满足四类活动任务均为零，并生成冻结的 Parent ID/Point ID 清单。清单只保存在独立备份目录，公开 artifact 仅包含聚合计数。

执行顺序为：

1. 只读扫描 `app.sqlite`、`parents.sqlite` 和 Qdrant，核对每个正式版本均同时存在 Parent 与 Point；
2. 创建 `app.sqlite`、`parents.sqlite` 一致性备份并下载独立 Qdrant collection snapshot；
3. 在短暂数据库写锁内重算同一计划，任何 ID、计数或任务状态漂移都停止执行；
4. 只按冻结 Point ID 分批删除 Qdrant，再按冻结 Parent ID 删除 `parents.sqlite`；
5. 核对正式版本集合、删除数量、数据库完整性、Qdrant 绿色状态和后端健康；
6. 删除后验证失败时，使用同一恢复点自动恢复 Qdrant 和 `parents.sqlite`。

T11 成功后仍保留 `source/docs`、`source/media` 和受管对象至少 1 至 2 周。不得删除整个 `/data/business/ragpincheng/source`；旧视频链路继续使用 `source/media`，生产仓库和部署配置也必须按现行部署路径保留。观察期结束后的文件归档属于独立 T12 R3 操作。
