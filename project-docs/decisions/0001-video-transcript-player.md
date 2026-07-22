# 0001 — 视频转录播放器与媒体资产流水线

- 状态：已批准（设计；第一阶段尚未授权执行）
- 日期：2026-07-23
- 关联功能：[视频转录链路](../features/transcript-pipeline.md)、[引用与来源面板](../features/citations-and-sources.md)、[文档摄取与索引](../features/document-indexing.md)、[认证与授权](../features/authentication.md)
- 关联 ADR：`project-docs/decisions/README.md`

## 给执行者的授权边界

本文记录用户已经选定的架构和第一阶段实施方案，但不等于已经授权修改业务代码。根据根目录 `CLAUDE.md` 与 `AGENTS.md` 的 R2 门禁，Claude Code 或 Codex 开始实施前仍须取得用户明确的“批准执行第一阶段”或同等授权。

第一阶段授权不包含自动转录、FFmpeg/HLS、全量索引 Reset、生产部署、真实媒体删除或文档级 RBAC；这些事项需要单独调查、方案和审批。

## 背景与当前依据

当前系统已实现教学视频 Markdown 转录稿的识别、时间戳分块、索引、检索、回答引用、`SourceDTO` 传递，以及前端引用角标到来源卡片的定位。当前没有视频资产登记、转录稿与视频绑定、鉴权媒体访问、HTTP Range、播放器或引用点击 seek。

已确认的用户选择：

- 视频保存到服务器本地独立目录；
- 第一阶段上传 MP4 与现成 Markdown 转录稿进行测试；
- 最终形态为上传视频后自动生成并绑定转录稿；
- 第一阶段只支持 H.264/AAC MP4；
- 使用 FastAPI 登录鉴权与 HTTP Range；
- 所有已登录用户均可播放；
- 使用单实例右侧播放器抽屉，移动端为底部弹层；
- 点击视频引用时定位来源、seek 并尝试播放；
- 第一阶段只实现时间点播放，不实现完整交互式转录；
- 其余细节采用本方案默认值。

## 决策

### 1. 建立统一媒体资产流水线

第一阶段和未来自动转录共用同一条后半段流水线：

```text
第一阶段：MP4 + 人工转录 Markdown
未来阶段：MP4 → 本地自动转录 → 规范化 Markdown
                         ↓
              media_id 绑定与落盘
                         ↓
              现有转录索引入口
                         ↓
       SourceDTO(media_id, start_time)
                         ↓
          鉴权播放、引用 seek、来源播放
```

自动转录只替换“转录稿产生方式”，不另建一套绑定、索引或播放协议。

### 2. 使用稳定 `media_id`

所有跨模块关联使用随机 UUID `media_id`，不依赖视频和转录稿文件名相同。数据库不保存完整播放 URL；API 根据 `media_id` 产生同源地址 `/api/media/{media_id}`。客户端不得收到服务器绝对路径。

### 3. 媒体登记属于 `app.sqlite`

新增 `media_assets` 表。媒体登记、文件位置和处理状态属于不可随索引 Reset 删除的业务状态，必须留在 `app.sqlite`；`parents.sqlite` 只增加可空 `media_id` 作为可重建的来源关联。

建议字段：

```text
media_id               TEXT PRIMARY KEY
title                  TEXT NOT NULL
original_filename      TEXT NOT NULL
storage_rel_path       TEXT NOT NULL
mime_type              TEXT NOT NULL
file_size              INTEGER NOT NULL
sha256                 TEXT
transcript_source_path TEXT
transcript_origin      TEXT NOT NULL  -- uploaded | generated
status                 TEXT NOT NULL
created_by             INTEGER
created_at             INTEGER NOT NULL
updated_at             INTEGER NOT NULL
error                  TEXT
```

统一状态机预留为：

```text
uploading → uploaded → transcribing → transcript_ready → indexing → ready
                                                               ↘ failed
```

第一阶段跳过 `uploaded → transcribing`，直接使用 `uploading → transcript_ready → indexing → ready`。`index_jobs` 增加可空 `media_id`，由索引 worker 传给 `index_single()`。

### 4. 媒体与转录文件位置

视频保存为：

```text
media/<media_id>/original.mp4
```

容器挂载为 `/app/media`，通过 `MEDIA_DIR` 配置；`media/` 必须进入 Git 忽略。视频大小使用独立的 `MAX_VIDEO_UPLOAD_MB`，不得复用 PDF 的默认限制。

转录稿保存为：

```text
docs/教学视频/<安全标题>__<media_id前8位>.md
```

第一阶段上传的 Markdown 和未来自动生成的 Markdown 必须使用当前 `chunk_transcript()` 可解析的“说话人 + HH:MM:SS + 正文”规范。文件先写临时位置，全部校验成功后再原子移动到最终位置。

### 5. 独立管理端媒体上传入口

新增 `POST /api/admin/media`，使用管理员身份和 CSRF。第一阶段 multipart 字段为：

```text
title       必填
video       必填，仅 .mp4
transcript  必填，仅 UTF-8 .md
```

未来自动转录上线时只把 `transcript` 改为可选：有文件时记为 `uploaded`，无文件时进入 `transcribing`，API 和后续索引协议不更换。

上传必须校验 MP4 扩展名、MIME、基本文件签名、大小、空文件，以及转录稿编码、时间戳和至少一个非空发言段。任一文件失败时不得创建可用资产或索引任务。

### 6. Parent 与来源契约

`Parent`、`parents` 表、`RetrievedParent`、会话来源快照、`SourceDTO` 和前端 `Source` 增加可空 `media_id`；API 可同时返回由它构造的可空 `media_url`。

`media_id` 不进入 Embedding 文本，也不参与检索排序。Child 命中后仍从 `parents.sqlite` 回取 Parent，因此无需为了该字段重建整个 Qdrant。现有转录只有在需要绑定视频时才定向重新索引。

旧会话或未关联转录缺少 `media_id` 时继续展示时间戳和来源，但不提供可用播放按钮，不得导致历史恢复失败。

### 7. 鉴权 Range 播放

新增 `GET /api/media/{media_id}`，必须经过 `require_user`。接口根据 `app.sqlite` 登记解析 `MEDIA_DIR` 下的文件，验证最终路径没有越出媒体根目录，然后返回 `video/mp4`。

接口必须支持无 Range、普通 Range、开放式 Range 和后缀 Range，正确返回 `206`、`Accept-Ranges`、`Content-Range`、分段 `Content-Length`；非法或越界 Range 返回 `416`。未登录返回 `401`，未知或缺失媒体返回 `404`。不得把 `media/` 直接挂成公开静态目录。

### 8. 单实例播放器与引用行为

前端新增全局/对话页级播放器控制器，核心请求结构为：

```ts
type PlayerRequest = {
  mediaId: string;
  mediaUrl: string;
  title: string;
  startSeconds: number;
};
```

页面只创建一个 HTML5 `<video>`。桌面端使用右侧抽屉，移动端使用底部弹层。必须提供统一的 `MM:SS` / `HH:MM:SS` 转秒函数。

视频引用点击行为：

1. 保留现有来源面板展开、定位和高亮；
2. 打开播放器抽屉；
3. 等待 `loadedmetadata`；
4. 设置 `currentTime`；
5. 尝试 `play()`；
6. 浏览器阻止自动播放时停在目标时间并显示手动播放入口。

来源卡片增加“从 HH:MM:SS 播放”按钮。PDF 引用保持当前行为。切换不同视频时必须暂停旧视频、等待新媒体 metadata 后再执行 pending seek。

### 9. 未来自动转录方向

自动转录是独立的后续 R2 阶段。目标技术方向为本地 `faster-whisper`，默认候选模型 `large-v3-turbo`，保留 `medium` 低资源配置；内部资料默认不发送外部转录服务。

该阶段必须另行设计 GPU 与 BGE 的互斥、长视频进度、失败重试、断点恢复、人工修订、是否说话人分离、模型缓存和 Docker 体积。建议复用当前单任务队列或共享 GPU 信号量，避免 Whisper 与 Embedding 同时占用 GPU。

## 第一阶段实施范围

### 包含

- 本地媒体目录、配置、挂载和 Git 忽略；
- `media_assets` 与 `index_jobs.media_id` 兼容迁移；
- `parents.media_id` 兼容迁移及完整来源传递；
- MP4 + Markdown 成对上传、校验、原子落盘和索引入队；
- 登录鉴权 HTTP Range 播放；
- 单实例右侧播放器抽屉/移动端底部弹层；
- 引用点击 seek、来源卡片播放按钮；
- 旧会话与未关联转录降级；
- 管理端状态和错误展示；
- 功能文档、TODO 和工作日志同步。

### 不包含

- 自动语音识别、说话人分离；
- FFmpeg 转码、HLS、缩略图；
- 完整交互式转录和播放中段落高亮；
- 播放进度持久化或统计；
- NAS、对象存储、签名 URL；
- 文档/分类级 RBAC；
- 自动删除媒体；
- 全量索引 Reset、生产部署或真实数据迁移。

## 预计修改面

后端与数据：

- `src/config.py`
- `src/ingest.py`
- `src/chunk.py`
- `src/index.py`
- `src/retrieve.py`
- `src/session.py`
- `src/indexing_pipeline.py`
- `api/db.py`
- `api/indexing.py`
- `api/schemas.py`
- `api/routes_admin.py`
- 新增 `api/routes_media.py`
- `api/main.py`

前端：

- `frontend/src/types.ts`
- `frontend/src/api/client.ts`
- `frontend/src/pages/AdminDashboard.tsx`
- `frontend/src/components/citations.ts`
- `frontend/src/components/Message.tsx`
- `frontend/src/components/SourcesPanel.tsx`
- 新增播放器控制器/Hook、`VideoPlayerDrawer.tsx` 和时间转换工具

部署与文档：

- `.gitignore`
- `docker/docker-compose.yml`
- `.env.example`（存在时）
- 相关 `project-docs/features/*.md`
- `TODO.md`
- `WORKLOG.md`

## 实施顺序

1. 再次检查 Git 状态，保护用户与其他 Agent 的未提交修改。
2. 增加媒体配置、目录忽略和 Docker 挂载。
3. 建立 `media_assets`、`index_jobs.media_id`、`parents.media_id` 的向前兼容迁移。
4. 打通 `media_id` 从索引输入到 Parent、检索、会话、SSE、历史恢复和前端类型的传递。
5. 实现媒体上传、校验、原子落盘、失败清理和索引入队。
6. 实现鉴权 Range 播放及安全边界。
7. 实现单实例播放器和响应式抽屉。
8. 接通引用角标和来源卡片播放。
9. 增加管理端媒体状态、错误和播放测试入口。
10. 执行数据、权限、Range、前端、历史兼容和转录检索验证。
11. 同步功能地图、TODO 与工作日志。

方案若需要扩大到第一阶段“不包含”的内容，必须重新评级和审批。

## 验证

### 上传与数据

- 合法 MP4 + 转录稿能够创建媒体、落盘并完成索引；
- 非 MP4、空文件、超限文件、非 UTF-8 或无有效发言段转录被拒绝；
- 同名上传不会覆盖；中途失败不会产生 `ready` 半成品；
- 索引失败保留媒体并记录可重试错误；
- 旧数据库可原地加 nullable 字段，`app.sqlite` 不随索引 Reset 删除。

### 播放与安全

- 匿名 401、普通用户和管理员可播放；
- 无 Range、普通/开放式/后缀 Range 及非法 Range 响应正确；
- 未知媒体 404，路径穿越与异常相对路径无法越出 `MEDIA_DIR`；
- 响应 MIME、长度和 Range 头正确，浏览器可以拖动进度条。

### 前端与兼容

- 视频引用打开正确视频并 seek；metadata 未加载时不会丢失目标时间；
- 自动播放受限时正常降级；不同视频切换正确；
- 来源按钮、未关联视频和旧会话正常；PDF 引用行为不退化；
- 运行 `frontend` 的 `npm run build`。

### RAG

- 对视频转录执行单问题检索冒烟；
- 按影响比较视频黄金集的 Recall@1、Recall@5、MRR 和 no-answer；
- 确认 `media_id` 不改变 Embedding 文本和排序；
- 不执行全量 Reset，只对需要绑定的测试转录做定向索引。

## 风险与影响

- 大文件上传和播放会增加磁盘、网络及备份压力；
- Range 边界错误会导致 seek 或拖动失败；
- 媒体落盘、转录落盘、数据库登记与索引之间必须有可恢复的状态，避免半成品；
- 浏览器可能阻止带声音的自动播放，UI 必须降级；
- `SourceDTO` 变化必须同步所有后端/前端消费者；
- 当前工作树已有未提交修改，实施时不得覆盖或整理无关改动。

## 回滚或替代

- 隐藏管理端媒体入口、播放按钮和播放器抽屉；
- 停用媒体路由，保留原有时间戳引用和来源定位；
- 保留新增 nullable 列，不做破坏性降级；
- 保留 `media/` 文件供恢复，移除挂载前先备份；
- 不需要恢复或重建 Qdrant；
- 删除转录索引默认保留视频，真实媒体删除必须单独明确确认。

未来并发或存储规模增长时，可由新 ADR 将 FastAPI 文件传输替换为 Nginx `X-Accel-Redirect`、NAS 或对象存储签名 URL，同时保持 `media_id` 与前端播放器协议不变。

## 备选方案及未采用原因

- 同名视频/转录自动关联：容易因重名、改名和版本更新误绑定，不作为权威关联方式。
- 在数据库保存完整 URL：部署域名和访问方式变化会使其失效，改用稳定 `media_id` 动态生成。
- 媒体放入 `parents.sqlite`：会被索引 Reset 误删，违反可重建索引与业务状态分离原则。
- 公开静态目录：会绕过登录鉴权和路径保护。
- 第一阶段引入 FFmpeg/HLS：显著增加依赖、任务状态、磁盘和验证成本，当前规模没有必要。
- 每张来源卡片内嵌播放器：会创建多个播放器实例并导致状态和布局复杂化。
- 第一阶段同步实现完整交互式转录：修改面接近翻倍，应在基础播放链路稳定后独立实施。
