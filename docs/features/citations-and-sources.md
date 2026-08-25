# 引用与来源面板

- 状态：已实现（含视频引用播放）
- 最后核对：2026-08-25

## 用户可观察能力

回答正文中的文档或视频时间引用会显示为角标；点击角标可以展开来源面板、滚动到对应来源并高亮。悬停或聚焦角标会显示可交互的来源摘要：具备 `media_id` 的视频来源提供独立播放按钮并定位引用时间点，PDF 和 Office 文档提供独立预览按钮。来源详情可展示证据原文并提交引用报错。

## 当前边界

### 已实现

- PDF `[文档 §章节]` 与转录 `[标题 @HH:MM:SS]` 引用格式；
- `SourceDTO` 从会话状态传递到 SSE、历史消息和前端；
- 引用解析、来源匹配、双向悬停高亮和来源卡片定位；
- 支持鼠标和键盘的引用摘要浮层，以及 PDF、DOCX、XLSX、PPTX 文档预览；
- 来源工作区支持关键词高亮、单条复制、批量复制和 Markdown 下载；
- 文档开头的内部章节标记 `(intro)` 对用户统一显示为“文档开头”，空章节显示为“未提供定位信息”；
- Excel 工作表/单元格、PowerPoint 页码和 Word 段落锚点优先作为定位信息，缺失时回退到章节路径；
- PDF 页码、跨页范围、Word 段落、Excel 单元格范围和 Office 文本证据会贯通索引、SSE、历史会话与预览；预览加载后自动滚动并高亮可匹配位置；
- 公司内部标准可在分类旁展示具体公司名称；
- 视频引用独立播放按钮触发的播放器打开、metadata seek 和自动播放失败降级；
- 来源卡片的“从时间点播放”入口；未关联视频的历史来源继续只展示时间引用。

### 未实现

- 按分类折叠或分组来源。

## 入口与调用链

```text
RetrievedParent
→ ChatSession._sources_for_ui
→ SourceDTO / sources_json / SSE
→ frontend Source
→ linkifyCitations + resolveCitation
→ CitationMarker
├─ 角标点击 → CITATION_EVENT → SourceWorkspace
├─ 视频播放按钮 → VideoPlayerProvider → VideoPlayerDrawer
└─ 文档预览按钮 → PdfPreviewProvider → PdfPreview / Office renderer
```

## 关键文件

- `src/session.py`
- `src/generate.py`
- `api/schemas.py`
- `api/routes_chat.py`
- `frontend/src/types.ts`
- `frontend/src/components/citations.ts`
- `frontend/src/components/Message.tsx`
- `frontend/src/components/SourceWorkspace.tsx`
- `frontend/src/lib/source-export.ts`

## 数据契约

- `SourceDTO` 与前端 `Source` 字段必须同步；
- 当前核心字段包括 `parent_id`、标题、章节、分类、可空公司名称、分数、证据文本、`doc_type`、`start_time`、可空 `media_id`，以及定位字段 `page_number`、`page_end`、`sheet_name`、`cell_range`、`slide_number`、`paragraph_anchor`、`topic_id`、`heading_anchor`、`location_quote`、`location_confidence`；
- 旧会话缺少后加字段时按空值恢复；旧 `(intro)` 索引不需要重建，由前端共享格式化层转换显示；
- 回答文本引用只是展示标记，权威来源详情来自结构化 `sources`。

## 依赖与下游消费者

- 依赖检索元数据、回答 Prompt、会话持久化；
- 下游为 Message、SourceWorkspace、视频播放器、文档预览、反馈接口和管理员反馈页面。

## 不变量与安全边界

- 保持 Markdown、GFM、Math/KaTeX 与引用解析兼容；
- 来源字段变化必须同步后端 Schema、SSE、历史恢复、前端类型和组件；
- 不把服务器 `source_path` 暴露给普通用户来源 DTO。

## 验证

- 验证 PDF 精确/叶节点匹配、视频时间匹配、同名文档回退和找不到来源；
- 验证 PDF 第 N 页跳转、跨页证据、MinerU 结构化结果缺失时的安全降级；
- 验证角标点击、悬停、键盘聚焦、Escape 关闭、面板展开和滚动；
- 验证视频按钮只在具备 `media_id` 时出现并传递正确时间，文档按钮只对受支持类型出现并传递定位信息；
- 前端变化运行相关单测、浏览器视觉检查和 `npm run build`。

## 已知限制

- LLM 生成的引用文字可能与结构化来源存在轻微差异，前端包含叶节点和标题回退匹配；定位元数据缺失或无法匹配时保持默认预览，不阻断来源核验。

## 相关决策

- [0001 — 视频转录播放器与媒体资产流水线](../decisions/0001-video-transcript-player.md) 是历史架构背景；当前实现状态以本功能文档和源码为准。

