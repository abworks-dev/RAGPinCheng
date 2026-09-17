# 方案：企业知识库规则与检索体系重设计（基于主流成熟方案调研）

- 状态：候选方案（待批准）
- 风险等级：R2（跨模块：检索、意图路由、prompt 规则、前端来源展示、可能新增配置）
- 日期：2026-09-17

## 1. 背景与问题

近期为提升"枚举/计数"类问答做了 4 个 PR（#817–#820：枚举 top-k、标题召回、枚举 source 真实片段、来源 keep_all），枚举类问题已能正确列出全部视频。但用户反馈两处**检索体验退化**：

1. **寒暄类**（"你好 你能做什么"）：检索到 5 份但 LLM 严格按规则 1 回答"未找到相关内容。"——错误的拒答（本应是助手自我介绍）。
2. **内容类**（"虹吸雨水管处理原则"）：用户措辞与培训原文有距离时，教学视频被规范文档挤出候选——召回质量不足。
3. 附带观察：多轮追问（"详细点列出来我看看我学什么"）回答易漂移。

这些不是单一 bug，而是**整个回答规则/检索体系缺少"意图分层"**。因此系统性调研了主流企业知识库的成熟设计，重新设计本项目规则与检索。

## 2. 调研：主流成熟方案汇总（附来源）

### 2.1 拒答（Abstention）应是受控特性，不是单调失败
[Abstention Recipe（AI-Agents-public）](https://github.com/vasilyu1983/AI-Agents-public/blob/c95ab14ef8cf13e778412c4510960b9da9ef7700/frameworks/shared-skills/skills/ai-rag/references/abstention-recipe.md)
- 在"评分→生成"之间放一个**置信度门**（Abstention Gate），条件为 any-of：置信度下界失效、空检索、矛盾、**超出语料范围（OOS）**、时效失效。
- **双阈值**：`T_drop`（丢弃 chunk 不进 context，如 0.55）与 `T_abstain`（触发整答拒答，如 0.45）。
- 拒答话术是产品：**说明搜了什么、没找到什么、下一步**（transparency/specificity/utility）；绝不静默降级到通用知识。
- 按 surface 声明 answering stance：`strict-grounded` / `grounded-preferred` / `best-effort`——严禁逐轮磋商。
- **拒答日志**是最有价值的语料改进信号，聚合形成语料缺口待办。
- K_pass=1 时"只答能支撑的那一条并明示是单一来源"；2+ 才正常 grounded 回答。

### 2.2 意图路由：检索前先判"决策/枚举/综合"，按意图选检索与回答形态
[The Curator 意图路由（decision/enumerate/synthesis）](https://raw.githubusercontent.com/talirezun/the-curator/refs/heads/main/docs/ingestion-pipeline.md)
- 明确的三类路由：**decision**(推荐)、**enumerate**(列表/计数)、**synthesis**(综合)。
- 路由规则按锚点优先级：**list/count 命令锚点最先**（"list all…"、"How many…"）；决策 cue 其次；弱列表短语最后。解决了"计数被误读为推荐"的回归。
- **enumerate 回答形态**：首行"一行摘要+总数"；**去重**；**上限 ~40 条**（"…还有 N 条"）；每条带引用；禁止复述内部目录（防"目录回声"，有 `stripCatalogueEcho` 兜底）。
- decision 形态：首句直接给推荐，每条给理由，只引 3–7 个最相关，**不列清单**。
- 独立对抗审计：决策词会误判列表、锚点边界（前导标点/换行）、所有格误判等均加回归测试。

### 2.3 寒暄：检索前简短意图路由，不打全 RAG
[Latenode 社区：RAG 中问候语处理（保存成本 + 即时响应）](https://community.latenode.com/t/creating-greeting-responses-in-rag-systems-without-using-language-models/36675)
- 两级过滤器：①精确/正则匹配常见问候（hi/hello/早上好…）；②模糊匹配（错拼变体）。命中→预置回复，**不进入 RAG 与 LLM 生成**。
- **混合输入要拆分**：问候+真实问题 → 问候走预置，问题走 RAG。
- 按会话状态区分新客/回客话术；跟踪漏网问候以扩展规则。
- 收益：高峰时段省 40–50% LLM 调用。

### 2.4 混合检索 + 重排（本项目已具备，验证与强化）
[企业级 RAG 检索引擎优化（七牛）](https://news.qiniu.com/archives/post-1782179985849-0)
- 单一 Dense 检索对型号/编号/精确词易"语义相似但事实不符"；需 **Dense + BM25/稀疏 并行召回** → **Cross-Encoder Reranker 精排** → 严格 prompt 边界 + 置信度兜底。
- 本项目已实现 dense+sparse RRF + BGE reranker，方向正确；缺口在于**置信度门尚未启用**（relevance gate 默认 disabled）。

### 2.5 枚举/计数类：图谱或结构化索引优于纯片段 RAG
[When RAG Hallucinates Numbers: Graph-RAG](https://neo4j.com/nodes/agenda/when-rag-hallucinates-numbers-graph-rag-for-precise-answers/)
- 纯 RAG 对"数字/总数/关系"（如"共有几个培训视频"）易出错；Graph-RAG / 结构化索引把"实体-关系-计数"固化，准确率大幅提升。
- 本项目视频/转录已具备结构化元数据（media_assets、transcript_versions、media_transcript_heads），可做**媒体级/标题清单式检索**（已实现 title recall），本质对齐该思路。

### 2.6 Agentic Routing / Query Rewriting / Semantic Cache（Manning 第 7 章）
[Build an Advanced RAG Application — ch.7](https://livebook.manning.com/book/build-an-advanced-rag-application-from-scratch/chapter-7/v-4)
- 企业级 RAG 不应是 naive"检索→生成"，而应有：agentic routing（多路径检索策略）、语义缓存（降本/降延迟）、query rewriting（歧义/指代补全，本项目已有 rewrite）。

## 3. 重设计方案（针对本项目）

### 3.1 回答规则分层：按"意图"选 stance（解决寒暄被误拒、枚举无定式）

**新增意图分类 `src/intent.py` → `Intent`：`greeting | enumerate | comparison | fact`**

- **greeting（寒暄）**：检索前命中（正则+词表，复用现有 `_VAGUE_FOLLOW_UP_RE` 思路扩展），**不进 RAG**。给预置自我介绍（"我是公司内部知识库助手，可查询 Revit/CAD 建模规范、公司标准、项目经验与培训视频…请描述您要找的内容"），并建议具体问法。混合输入（"你好，xxx是什么"）→ 只把真实问题进 RAG。
  - 落地：`session._resolve_search_query` 前插入 greeting 检测；命中直接返回 `TurnResult`（不检索、不生成、无 sources），`guard_reason="greeting"`。
- **enumerate**：现有 `is_enumeration_intent` 强化为**三分类路由**（对齐 The Curator）：
  - 命令锚点优先（"列出/有哪些/几个/分别"等）；决策 cue（"该不该/怎么选/哪个好"）→ comparison；其余 → fact。
  - enumerate 回答形态明确化：首行"共 N 条"，逐条“标题[K]”，**去重**、**上限 ~40**、禁止目录回声（用现有 keep_all + snippet 已满足）。
- **comparison**：现有 decompose 路径（retrieve_multi）继续，回答形态"对比维度+结论"。
- **fact**：普通内容问答，走常规检索 + grounded 回答。

**answer_system 改造**：按 intent 动态选择 system 指令块（类似 The Curator 按 intent 选 prompt shape）。摘要：fact/strict-grounded（现有规则 1–9）；enumerate 增加第 10 条强化（已加）；greeting 不走此 prompt。

### 3.2 可信拒答门（解决"未找到相关内容"滥用）——对齐 Abstention Recipe

- 保留 `relevance_gate`（现有），但补三种触发：
  1. **OOS/寒暄**：greeting/OOS 检测命中 → 不生成，走 pre-set 话术（明确"您问的不是知识库内容/寒暄"而非冷冰冰"未找到定义"）。
  2. **低置信拒答**：当检索置信度低于 `T_abstain`（默认 0.45）且 query 不是枚举时，不把 5 份低质片段硬塞给 LLM；而是**结构化拒答**：说明搜了什么、最接近的是哪份（标题）、建议怎么改问。实现为 `finalize` 前置判断：`relevance_gate` 触发 → 返回拒答模板而非"未找到相关内容"。
  3. **部分答案**：仅 1 份高置信 → 回答并明示"基于单一来源"。
- 拒答话术模板（中文）写入 `prompts/refusal.md`，纳入提示词管理面板可微调：
  ```
  抱歉，我在 [知识库范围] 中没有找到关于「用户问句」的可靠信息。
  最接近的资料是《标题》（类别），但不足以支撑完整回答。
  您可以：换个问法（我实际检索了：X）；或询问管理员补充该资料。
  ```
- **拒答日志**：现有 `chat_turn` 日志已记录 `outcome`；补充 `refusal_reason`（oos|low_confidence|empty|contradiction|stale）与 `top_conf`，聚合后可定位语料缺口（"虹吸雨水管"案例即缺培训视频高命中）。

### 3.3 内容召回增强：视频培训类在"术语型"查询下不被规范压制

现状：`虹吸雨水管处理原则`（用户措辞）→ 规范文档得分高于培训转录。对策（可选其一或组合）：

- **A. 分类级加权（推荐，改动小）**：在 `_recall_scored` 的 rerank 前，对 `category="教学视频"` 且 query 含培训类信号（"培训/讲解/怎么/操作/建模"）的候选做轻微上浮（如 ×1.05–1.15），或在 `_dedup_to_parents` 选 parent 时保底每类别至少 1 条（对齐已有"min_quota_per_subquery"思路）。
- **B. 转录标题/术语注入 embedding**：索引时把视频标题关键词（ex. "虹吸雨水管 管径 无压"）已随 doc_title 进 embed_text；可在 `_render_source`/检索 query 端做 query 术语扩展（同义词"虹吸雨水管→满管压力流/雨水斗"），提升语义召回。改动中等。
- C. Graph-RAG 结构化计数：**暂不引入**（本项目的 title recall 已覆盖枚举/计数需求，成本高收益小）。

建议先做 **A（min-quota 保底每类别 + 教学视频轻加权）**，用"虹吸雨水管"案例做冒烟网关。

### 3.4 检索与生成编排总览（目标架构）

```text
用户输入
  │
  ▼
[1] 前置意图路由（greeting / OOS 检测）        ← 新增，省 LLM
  │  greeting → 预置自我介绍；OOS → 结构化说明
  ▼
[2] 意图分类：enumerate / comparison / fact     ← 强化现有 intent
  │  enumerate → top-k↑ + title recall + keep_all + enumerate prompt shape
  │  comparison → decompose / retrieve_multi
  │  fact      → 常规 retrieve + 分类保底
  ▼
[3] 检索：dense+sparse RRF + reranker           ← 既有，加分类 min-quota
  ▼
[4] 置信度门（Abstention Gate）                 ← 新增：T_drop / T_abstain
  │  低置信/矛盾/OOS → 结构化拒答 + 拒答日志
  ▼
[5] 生成：按 intent 选 system prompt shape      ← 新增动态 prompt
  ▼
[6] 前端来源展示（keep_all 已支持全量 list）
```

### 3.5 改动清单（建议分 2 个 PR）

**PR-R1（规则与拒答，解决寒暄/未找到滥用 + 计数形态）**
- `src/intent.py`：扩展为 `classify_intent(query) -> IntentEnum`（greeting/enumerate/comparison/fact），greeting 词表 + OOS 规则。
- `src/session.py`：greeting 前置短路（不检索不生成）；拒答门（relevance_gate 触发时用 `prompts/refusal.md` 话术 + refusal_reason 日志）；`_resolve_search_query` 停用问候的 rewrite。
- `prompts/refusal.md`（新，纳入提示词管理）；`answer_system.md` 按 intent 选择指令块（enumerate/fact/comparison）。
- 前端：无需改动（保留 keep_all 全量来源）；如需要可展示拒答原因徽标（可选）。
- 测试：intent 分类矩阵、greeting 短路、拒答门、enumerate shape 断言。

**PR-R2（召回增强，解决教学视频被规范压制）**
- `src/retrieve.py`：`_dedup_to_parents` 增加"每类别保底 1 parent"（min_quota）；`_recall_scored` 对教学视频类候选按 query 培训信号轻加权（env 开关 `TRANSCRIPT_CATEGORY_BOOST`）。
- 冒烟：`虹吸雨水管处理原则` 应在 top5 内出现"机电管综培训（七）"；`中心模型离线流程` 仍命中培训。
- 测试：min-quota、类别加权、既有回归。

## 4. 验证方式

- 单测：intent 分类矩阵（greeting/enumerate/comparison/fact + 边界：问候+问题混合、计数被误读为推荐等对抗样例）；拒答门阈值；min-quota；类别加权。
- 回归：现有 test_answer_sources / test_answer_policy / test_relevance_gate / test_enumeration_retrieval 全绿。
- 生产冒烟（每 PR 部署后）：
  - "你好 你能做什么"→ 预置自我介绍（不再"未找到相关内容"）。
  - "机电管综培训总共有几个培训视频"→ 首行共 N 条 + 全量来源（保持现状）。
  - "虹吸雨水管处理原则"→ 教学视频（七）出现在来源中。
  - "某问题找不到"→ 结构化拒答（说明搜索对象 + 最接近标题 + 建议），而非冷冰冰"未找到相关内容。"。
- CI：全部既有 checks 绿。

## 5. 风险、兼容性与回滚

- 意图分类误判风险：用对抗样例（"详细点列出来"、"哪些是最新的"）回归；greeting 词表保守（只精确+常见变体），避免吞真实问题。
- 拒答门收紧可能提高"over-refusal"：设 `T_abstain` 保守（低于现有 relevance 门），并在日志记录拒答，用户可据此调参；可经提示词面板调整话术。
- 类别加权/保底改动检索排序：以 env 开关控制，可全局关闭回滚；min-quota 仅影响 top 去重槽位，不改变非分类查询。
- 全程独立 PR，逐个可 revert。

## 6. 明确不做

- 不做 Graph-RAG（title recall 已覆盖计数）。
- 不改索引结构/不做全量重建（A 方案无需）。
- 不引入新 LLM 提供商（继续 glm）。
- 不改变已批准枚举修复（keep_all/snippet/title recall 保留）。

## 7. 参考来源

- [Abstention Recipe — AI-Agents-public](https://github.com/vasilyu1983/AI-Agents-public/blob/c95ab14ef8cf13e778412c4510960b9da9ef7700/frameworks/shared-skills/skills/ai-rag/references/abstention-recipe.md)
- [The Curator — 意图路由 (decision/enumerate/synthesis)](https://raw.githubusercontent.com/talirezun/the-curator/refs/heads/main/docs/ingestion-pipeline.md)
- [Latenode — RAG 寒暄处理与意图预处理](https://community.latenode.com/t/creating-greeting-responses-in-rag-systems-without-using-language-models/36675)
- [企业级 RAG 检索引擎优化（七牛）](https://news.qiniu.com/archives/post-1782179985849-0)
- [When RAG Hallucinates Numbers: Graph-RAG（Neo4j）](https://neo4j.com/nodes/agenda/when-rag-hallucinates-numbers-graph-rag-for-precise-answers/)
- [Build an Advanced RAG Application — ch.7 Agentic Routing（Manning）](https://livebook.manning.com/book/build-an-advanced-rag-application-from-scratch/chapter-7/v-4)