# 首轮 `docs/` 目录释放与项目文档改名方案

- 状态：代码完成待验证
- 风险等级：仓库兼容代码与目录改名为 R2；旧生产文件归档为独立 R3
- 范围：释放仓库根目录 `docs/` 作为项目文档目录，将 `project-docs/` 改名为 `docs/`

## 目标

```text
docs/                  项目开发、架构、运维和验收文档
content/               受管业务资料及其对象、发布和视图
content/legacy-docs/   旧资料兼容路径
```

项目文档不得被摄取器、分类树、旧上传入口或源文件回退扫描。`DOCS_DIR` 作为部署兼容配置名保留，但本地默认值不再指向仓库项目文档目录。

## 已完成的生产前置

- 117 条旧普通资料迁移记录已经收口，116 份建立正式 head；
- T11 workflow `31741989386` 已下线对应旧普通资料索引；
- T12-B workflow `31767567546` 已完成剩余旧索引和媒体记录收口；
- app-only workflow `31769119451` 已启用 strict 和 source-decoupled 挂载；
- 旧生产文件仍保留，物理归档不属于本方案授权。

## 实施

1. 将本地 `DOCS_DIR` 默认值从 `ROOT/docs` 改为 `CONTENT_ROOT/legacy-docs`，保留显式环境变量兼容。
2. 将 Compose 默认宿主机 bind source 改为 `../content/legacy-docs`，容器内 `/app/docs` 契约保持不变。
3. 添加回归测试，证明项目文档不会进入 legacy 批量摄取。
4. 将 `project-docs/` Git 重命名为 `docs/`，更新协作规则、README、方案、源码证据引用和 TODO 链接。
5. 移除 `.gitignore` 对根 `docs/*` 的业务资料忽略规则；运行时资料继续由 `content/` 忽略规则保护。

## 验证

- `project-docs/` 活跃引用为零；
- 项目 `docs/` 与 `DOCS_DIR` 不重叠；
- Python import/语法检查和相关 pytest；
- 前端构建；
- `docker compose config` 和 source-decoupled 静态测试；
- backend 镜像构建（本机 Docker daemon 不可用时交由 CI/可用环境补验）；
- 文档链接与 Git 跟踪状态检查。

## 回滚与明确不做

- 代码和目录改名保持在独立分支及独立提交，可整体 revert；
- 不改变生产 `/app/docs` 容器路径或私有生产挂载；
- 不执行生产文件归档、删除、Qdrant Reset 或数据库写入；
- 不整理服务目录，不重组 `api/src`，不拆分 `data`。
