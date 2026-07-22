# 视频转录链路

- 状态：部分实现
- 最后核对：2026-07-22

## 用户可观察能力

教学视频转录稿可以被索引和检索，回答能够显示带时间戳的视频引用，点击引用可以定位到对应来源卡片。

## 当前边界

### 已实现

- `教学视频/` 分类下 `.md` 被识别为 `doc_type="transcript"`；
- 按“说话人 + HH:MM:SS”解析发言段落；
- Parent 和 Child 携带 `start_time`；
- `doc_type`、`start_time` 经检索、生成、`SourceDTO` 和前端完整传递；
- 前端显示 `🎬 @HH:MM:SS` 并能定位来源卡片。

### 未实现

- 视频媒体上传或资产登记；
- 转录稿与视频文件、媒体 ID 或可访问 URL 的关联；
- 经过认证的媒体访问和 Range 请求；
- 内嵌播放器、播放按钮和引用点击 seek；
- 视频字幕轨道生成与同步。

## 入口与调用链

```text
教学视频/*.md
→ _build_transcript_doc
→ chunk_transcript
→ Parent/Child(doc_type, start_time)
→ parents.sqlite + Qdrant
→ RetrievedParent
→ <source time=... type="transcript">
→ SourceDTO
→ 视频引用角标与来源卡片
```

## 关键文件

- `src/ingest.py`
- `src/indexing_pipeline.py`
- `src/chunk.py`
- `src/index.py`
- `src/retrieve.py`
- `src/generate.py`
- `api/schemas.py`
- `frontend/src/components/citations.ts`
- `frontend/src/components/SourcesPanel.tsx`

## 数据契约

- 当前已有：`doc_type="transcript"`、`start_time`、`doc_title`、`source_path`；
- `source_path` 当前指向转录 Markdown，不是视频播放地址；
- `SourceDTO` 当前没有媒体 URL、媒体 ID 或播放权限字段；
- 媒体相关字段均为候选设计，不是现有 Schema。

## 依赖与下游消费者

- 依赖文档索引、检索、回答生成、引用与来源面板；
- 若实现播放器，还将依赖认证、媒体存储、HTTP Range/对象存储和部署挂载。

## 不变量与安全边界

- 媒体播放链路完成前，只能描述为候选实现；
- 不得向普通用户暴露服务器绝对路径；
- 视频访问必须考虑认证、授权、Content-Type、Range 和路径穿越防护；
- 新媒体字段涉及跨模块契约，必须走 R2 方案审批并说明索引兼容性。

## 验证

- 当前链路：转录解析、时间戳索引、检索命中、回答引用、前端匹配；
- 候选播放器：匿名/登录权限、合法与非法媒体 ID、Range 请求、seek、同名视频和缺失媒体降级；
- 前端变化运行 `npm run build`，RAG 字段变化运行检索冒烟和相关黄金集。

## 已知限制

- 当前只有“时间戳引用”，没有“媒体播放能力”。

## 相关决策

- [0001 — 视频转录播放器与媒体资产流水线](../decisions/0001-video-transcript-player.md) 已批准设计；当前源码仍未实现，第一阶段尚待明确执行授权。
