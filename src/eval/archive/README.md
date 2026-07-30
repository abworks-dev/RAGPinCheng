# 归档：钢结构时代黄金集（已废弃）

本目录保存 2026-07-30 之前的检索评测集,**仅作历史参考,不再是当前基线**。

- `golden_steel_legacy.jsonl` — 旧 `src/eval/golden.jsonl`(97 条,全钢结构主题)。
- `drafts_steel_legacy.jsonl` — 对应的旧合成草稿。

## 为何归档

生产验证(见 `TODO.md` 的 `7-R` 章节)确认:

1. 旧集 `expected_parent_ids` 与当前索引 parent_id 集合 `∩ = 0`(内容重新解析/分块后哈希全变);
2. 更根本——旧集 91 道检索题**全是钢结构主题**,而当前索引 122 篇**无一篇钢结构规范**(语料域已整体替换为 BIM/机电/建筑规范)。旧题在当前语料无答案。

因此无法"重标 ID"复用,只能针对当前语料重建(方案 B)。新黄金集由方案 B 第一期产出后写入 `src/eval/golden.jsonl`。

在新集产出前,`run_eval_retrieval.py` 直接运行会因找不到 `golden.jsonl` 而报错——这是预期行为,避免误用废弃数据当基线。
