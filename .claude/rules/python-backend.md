---
paths:
  - "src/**/*.py"
  - "scripts/**/*.py"
  - "prompts/**/*.md"
---

# RAG Pipeline Rules

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