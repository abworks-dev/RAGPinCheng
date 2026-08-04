# 功能地图

本目录描述系统当前可复核的功能边界、跨模块调用链和验证入口，供开发者与 Agent 在设计或修改功能前快速定位上下文。

使用顺序：

1. 从下表选择目标功能；
2. 阅读目标功能及其直接依赖；
3. 打开文档列出的关键源码、Schema 和配置核对当前事实；
4. 代码或运行结果与文档冲突时，以代码和可复核运行结果为准，并在本次修改范围内同步修正文档。

不要为了局部样式、文案或单文件小修加载全部功能文档。

## 状态定义

- `已实现`：当前源码存在完整可用链路；
- `部分实现`：核心链路存在，但仍缺少明确列出的环节；
- `开发中`：已有获批实现正在进行；
- `候选设计`：只存在需求或方案，不得描述成现有能力；
- `已废弃`：不应继续作为新实现入口。

## 功能索引

| 功能 | 状态 | 主要入口 | 直接依赖 | 文档 |
|---|---|---|---|---|
| 对话运行时 | 已实现 | `ChatSession.ask_stream` | 检索、回答生成、认证 | [chat-runtime.md](chat-runtime.md) |
| 检索与重排 | 已实现 | `retrieve_for_turn` / `retrieve` | 文档索引 | [retrieval-pipeline.md](retrieval-pipeline.md) |
| 文档摄取与索引 | 已实现 | `index_single` | MinerU、Qdrant、parents.sqlite | [document-indexing.md](document-indexing.md) |
| 引用与来源面板 | 已实现 | `SourceDTO` / `citations.ts` | 对话运行时 | [citations-and-sources.md](citations-and-sources.md) |
| 视频转录链路 | 部分实现 | `chunk_transcript` | 文档索引、引用与来源 | [transcript-pipeline.md](transcript-pipeline.md) |
| 认证与授权 | 已实现 | `require_user` / `require_csrf` | app.sqlite、Cookie | [authentication.md](authentication.md) |
| 反馈处理工作流 | 已实现 | `/api/admin/feedback` | feedback.jsonl、app.sqlite、管理员权限 | [feedback-management.md](feedback-management.md) |

## 新增或更新文档

复制 [TEMPLATE.md](TEMPLATE.md)，只记录当前源码能够证明的事实。重大架构选择另行写入 [`../decisions/`](../decisions/README.md)，过程和完成记录继续写入根目录 `WORKLOG.md`，未来计划继续写入 `TODO.md`。

