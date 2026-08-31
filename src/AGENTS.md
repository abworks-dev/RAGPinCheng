# RAG / Python Backend Rules (领域规则)

本文件在 DSH 进入 `src/`、`scripts/`、`prompts/`、`api/` 等后端相关目录时自动加载。涉及后端改动时与根 `AGENTS.md` 同时适用。

- 先追踪 `ParsedDoc → Parent/Child → Qdrant/SQLite → RetrievedParent → ChatSession` 数据契约。
- 普通Markdown在非`教学视频`分类下按普通文档处理；该分类下`.md`才是Transcript。
- Parent用于回答，Child用于检索；Child必须保存可回取的`parent_id`。
- 表格/公式保护、标题上下文和确定性UUIDv5是检索不变量。
- 表格摘要只增强Child检索文本，不修改Parent原始证据。
- 修改Chunk、ID、Embedding、Payload或Collection前，说明旧索引兼容性和是否需要Reset。
- 检索变化先运行单问题冒烟，再比较固定黄金集；分别报告Recall@1、Recall@5、MRR和no-answer。
- 不为提高指标修改黄金集答案或屏蔽失败类型。
- 新Prompt放`prompts/*.md`并通过`src/prompts.py`加载。
- 新调用入口使用`ChatSession`，不要复制编排逻辑。

## Python 后端依赖边界

- `api/**` 是 FastAPI 传输层，是所有 RAG 逻辑的薄 HTTP 包装；retrieval/generation/state 行为在 `src/`，尤其是 `src.session.ChatSession`。不要在 API 层复制 ChatSession 编排逻辑。
- 新 Python 运行依赖需同步 `requirements.txt` 与 `requirements-prod.txt`，除非明确只属于本地或生产。
- 跨模块契约或 Schema 变化时，遵守根 `AGENTS.md` 的「核心不变量」与「风险评级」门禁。