# 引用与来源面板

- 状态：已实现（含视频引用播放）
- 最后核对：2026-08-16

## 用户可观察能力

回答正文中的文档或视频时间引用会显示为角标；点击角标可以展开来源面板、滚动到对应来源并高亮。具备 `media_id` 的视频来源还会打开鉴权播放器并定位时间点，来源卡片可展示证据预览并提交引用报错。

## 当前边界

### 已实现

- PDF `[文档 §章节]` 与转录 `[标题 @HH:MM:SS]` 引用格式；
- `SourceDTO` 从会话状态传递到 SSE、历史消息和前端；
- 引用解析、来源匹配、双向悬停高亮和来源卡片定位；
- 来源预览和引用错误反馈；
- 视频引用点击后的播放器打开、metadata seek 和自动播放失败降级；
- 来源卡片的“从时间点播放”入口；未关联视频的历史来源继续只展示时间引用。

### 未实现

- PDF 页码直接跳转；
- 复制、分类分组、关键词高亮和批量导出。

## 入口与调用链

```text
RetrievedParent
→ ChatSession._sources_for_ui
→ SourceDTO / sources_json / SSE
→ frontend Source
→ linkifyCitations + resolveCitation
→ CitationMarker
→ CITATION_EVENT
→ SourcesPanel
```

## 关键文件

- `src/session.py`
- `src/generate.py`
- `api/schemas.py`
- `api/routes_chat.py`
- `frontend/src/types.ts`
- `frontend/src/components/citations.ts`
- `frontend/src/components/Message.tsx`
- `frontend/src/components/SourcesPanel.tsx`

## 数据契约

- `SourceDTO` 与前端 `Source` 字段必须同步；
- 当前核心字段包括 `parent_id`、标题、章节、分类、分数、证据文本、`doc_type`、`start_time` 和可空 `media_id`；
- 回答文本引用只是展示标记，权威来源详情来自结构化 `sources`。

## 依赖与下游消费者

- 依赖检索元数据、回答 Prompt、会话持久化；
- 下游为 Message、SourcesPanel、反馈接口和管理员反馈页面。

## 不变量与安全边界

- 保持 Markdown、GFM、Math/KaTeX 与引用解析兼容；
- 来源字段变化必须同步后端 Schema、SSE、历史恢复、前端类型和组件；
- 不把服务器 `source_path` 暴露给普通用户来源 DTO。

## 验证

- 验证 PDF 精确/叶节点匹配、视频时间匹配、同名文档回退和找不到来源；
- 验证角标点击、悬停、面板展开、滚动、视频 seek 和反馈；
- 前端变化运行 `npm run build`。

## 已知限制

- LLM 生成的引用文字可能与结构化来源存在轻微差异，前端包含叶节点和标题回退匹配。

## 相关决策

- [0001 — 视频转录播放器与媒体资产流水线](../decisions/0001-video-transcript-player.md) 是历史架构背景；当前实现状态以本功能文档和源码为准。

