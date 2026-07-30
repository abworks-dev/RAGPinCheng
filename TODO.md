# 品成 BIM 知识库 — 待办事项 Roadmap

按优先级排序的功能开发计划。

---

## 🔴 高优先级 — 已有明确设计方案

### 1. 查询拆分 / 多跳检索 — Phase 2

**状态**：核心已实现（默认关闭，开关 `QUERY_DECOMPOSE_ENABLED`，待黄金集验证收益后灰度开启）。2026-07-30 实施。

**适用场景**：用户提问需要对比/合并多个实体或主题，但**没有规范编号**做为确定性触发信号。

示例问题：
- "对比 Revit 族与 Dynamo 节点的复用方式"
- "客户A和客户B对图层命名的不同要求"
- "钢结构和混凝土结构在BIM建模流程上的差异"

**已实现**：
- [x] 新增 Prompt `prompts/decompose_system.md`（+ `decompose_user.md`）
- [x] 使用便宜模型（`LLM_REWRITE_MODEL`，默认 glm-4.5-air）判断是否需要拆分
- [x] 返回 JSON 格式：`{"decompose": bool, "sub_queries": [...]}`，最多 3 个子查询；解析失败/单子查询安全回退不拆分
- [x] 启发式触发 gate：query 含 `对比|比较|相比|区别|差异|不同点|异同|分别|各自|VS|vs` 等标记时才调用 LLM，预计触发率 <5%/turn
- [x] 新增 `retrieve_multi`：多子查询召回、跨查询 RRF 融合、每子查询最低配额（`DECOMPOSE_MIN_QUOTA_PER_SUBQUERY=2`）、按原始问题全局 rerank、截断到 `DECOMPOSE_FINAL_TOP_K=8`
- [x] `ask`/`ask_stream` 经统一 `_fresh_retrieve` 对称接入；开关关闭时与旧行为逐字节等价
- [ ] 待验证：黄金集补充比较型用例；开关开/关的 Recall@1/@5/MRR/no-answer 与多主题覆盖率对比；触发率、每轮额外 LLM 调用与 P50/P95 延迟遥测（需容器/Ubuntu 节点补跑）
- [ ] 待灰度：验证收益且延迟/成本可接受后再默认开启

**✅ 预算截断已修复（2026-07-30，拆分感知上下文预算）**：
- 原缺陷：`_build_context` 顺序线性打包 + `MAX_CONTEXT_CHARS=6000` 截断，拆分场景排在前的一侧填满预算、另一侧被截，LLM 只见一侧遂拒答。
- 修复：`RetrievedParent` 加 `subquery_idx`；`retrieve_multi` 标注每个 parent 所属子查询；`_build_context` 检测到 `subquery_idx` 时按子查询 **interleave 轮流打包**（每侧先出 top，再出次优），保证预算耗尽前两侧都入选；拆分场景改用更宽的 `DECOMPOSE_MAX_CONTEXT_CHARS=8000`（`_context_budget` 依据 `subquery_idx` 自动选择）。单查询/普通路径逻辑不变。
- 本机单测验证：interleave 轮流顺序、单查询顺序不变、拆分场景两侧都存活（对照旧顺序逻辑只留一侧）、budget 选择（普通 5300 / 拆分 7300）。
- [ ] 待生产冒烟：Ubuntu 容器开启开关重跑比较题，确认两侧都进上下文、回答变为真正对比（不再拒答）。
- 现状：**仍维持默认关闭**；灰度开启前仍需比较型黄金集量化收益（见 `### 7-R`）。

**涉及文件**：
- 新增 `prompts/decompose_system.md`、`prompts/decompose_user.md`、`src/decompose.py`
- 修改 `src/retrieve.py`（`retrieve_multi` + 共享召回/去重 + `subquery_idx`）、`src/generate.py`（`_render_source`/`_interleave_by_subquery`/`_build_context`）、`src/session.py`（`_context_budget`）、`src/config.py`（`DECOMPOSE_MAX_CONTEXT_CHARS`）
- 补充黄金集对比型测试用例（待办）

### 2. 分层查询增强（查询拆分 + MQE + 受控 HyDE）

**状态**：候选设计，尚未实现。查询拆分为主路径，MQE 用于术语扩展，HyDE 仅作为特定场景的辅助 Dense 检索通道，不全量默认开启。

**目标**：在保留规范编号、客户名称、型号和专业术语精确性的前提下，提高口语化、术语不一致、故障现象描述及培训视频类问题的召回率，同时避免 HyDE 假设内容损害 no-answer 判断。

**分层路由**：
- [ ] 所有查询先复用现有多轮独立问题改写和查询守卫；
- [ ] 包含规范编号、条款号、客户名、型号或精确参数时，只走原始查询的 Dense + Sparse + code boost，不启用 HyDE；
- [ ] 比较、合并或分别查询多个实体时，优先进入“查询拆分 / 多跳检索”路径；
- [ ] 无强精确标识、但可能存在用户用词与文档术语错配时，生成 2～3 个语义等价 MQE 查询；
- [ ] 故障现象、操作经验或培训视频类自然语言描述，在低置信度场景下可额外生成 1 个 HyDE 假设文档，仅用于 Dense 检索；
- [ ] 原始查询始终保留为最高可信检索通道，扩展查询和 HyDE 不得覆盖或替代原始查询。

**候选融合策略**：
- [ ] 建立真实的多查询检索入口；当前源码不存在 TODO 上文所称的 `retrieve_multi`，实现前先明确接口与返回契约；
- [ ] 每个子查询保留最低候选配额，避免比较问题的一侧被高分结果完全挤出；
- [ ] 按 `parent_id` 去重，通过跨查询 RRF、命中覆盖奖励或等价方法融合，不直接比较不同检索通道的原始分数；
- [ ] HyDE 结果使用较低权重，禁止进入 Sparse 和规范编号 code boost 通道；
- [ ] 融合后统一使用原始用户意图或子查询做全局 rerank，再按现有上下文预算返回 Parent 证据；
- [ ] 增加功能开关、触发原因、生成查询、各通道命中和延迟/Token 遥测，以便灰度、回滚和问题定位。

**实施顺序**：
1. 先实现上文的比较意图查询拆分；
2. 补齐多查询检索、去重、覆盖约束和全局重排基础设施；
3. 在术语错配黄金集上试验 MQE；
4. 最后以默认关闭的实验开关加入 HyDE，并单独评估查询漂移和 no-answer 退化；
5. 只有固定黄金集证明收益且延迟、成本可接受后，才扩大触发范围。

**验证门槛**：
- [ ] 黄金集新增比较型、术语错配、模糊现象、培训视频、规范编号、精确数值和跨领域 no-answer 用例；
- [ ] 分类型对比 Recall@1、Recall@5、MRR、no-answer 保持率及多主题覆盖率；
- [ ] 记录 P50/P95 检索延迟、每轮 LLM 调用次数和 Token 成本；
- [ ] 规范编号、客户标准和精确参数类结果不得因 MQE/HyDE 出现回归；
- [ ] 不修改 Chunk、Embedding 或索引 Schema，候选实现原则上无需重建索引；若方案改变这些契约，必须重新评估并审批。

---

## 🟡 中优先级 — 已有基础实现，需完善

### 3. 跨父文档多轮上下文携带

**现状**：当前 `ChatSession` 只携带上一轮最优 2 个来源的片段，局限在同一文档内的连续追问。

**待实现**：
- [ ] 记录每轮所有命中父文档的元数据（doc_title、category、section_path）
- [ ] 后续轮次自动将相关历史文档的标题作为软提示注入重写器
- [ ] 极端情况下可展开历史来源的完整文本（需额外预算控制）
- [ ] 支持真正的"跨文档综合"复杂多轮对话

**参考**：`scripts/append_handwritten.py:27`

---

## 🟢 体验增强 — 无破坏性，逐步迭代

### 4. 视频播放器第二阶段（交互式转录增强）

**现状**：
- 第一阶段已完成：媒体目录配置、数据库迁移、数据契约打通、管理端上传、鉴权 Range 播放、播放器抽屉、引用点击 seek、来源卡片播放按钮
- `media_id` 已通过数据契约完整传递

**待实现**：
- [ ] 自动语音识别集成 —— 已独立拆分为下方「🎬 视频自动转录（FunASR）— 候选方案，待 R2 批准」，本条不再单列 Whisper
- [ ] 视频播放过程中同步高亮当前说话人段落
- [ ] 播放进度持久化与恢复
- [ ] 移动端底部弹层优化

### 5. 来源面板增强功能

**文件**：`frontend/src/components/SourcesPanel.tsx`

待实现：
- [ ] "复制来源"按钮（复制文档标题 + 章节路径）
- [ ] 来源卡片直接跳转到对应 PDF 页码（需 PDF.js 集成）
- [ ] 支持批量导出引用列表

---

## 📋 运维与质量

### 6. 反馈日志分析仪表盘

**现状**：`api/feedback.py` 已实现数据收集接口，用户可报告引用错误。

**待实现**：
- [ ] 管理员后台可视化反馈趋势图表
- [ ] 按文档/章节聚合报错率
- [ ] 检索失败案例的自动聚类分析
- [ ] 定时生成检索质量报告

### 7. 检索黄金集扩展

**文件**：`src/eval/`

**当前基线**（2026-05）：R@1 = 90%, R@5 = 96%, no-answer = 100%（~97 条）

**待补充**：
- [ ] 更多对比型问题（Phase 2 上线前需要）
- [ ] 多轮对话案例
- [ ] 边界 case（纯数字、纯代码、无意义输入）
- [ ] 视频转录本相关问题

> ⚠️ 上方"当前基线 R@1=90% / R@5=96%"是 2026-05 旧索引上的历史数字，**已失效**。当前生产索引上该黄金集实测全 0 命中，原因见下方"检索黄金集重建"。在完成重建前，本节的"扩展"待办无法量化验证；扩展应在重建之后进行。

### 7-R. 检索黄金集重建（Golden Set Rebuild）— R2，未获实施授权

**优先级**：🔴 高（阻塞 Phase 2 查询拆分收益量化——commit 9bf065a 默认关闭，开启前需要能量化收益的黄金集）

**状态**：调查完成，方案待批准。尚未修改业务代码/黄金集，尚未开始 R2 实施。

**关联**：本节是上方"7. 检索黄金集扩展"的前置修复。扩展（对比型/多轮/边界/转录用例）应在重建、标尺恢复后进行，二者不要合并。

#### 根因（经生产验证升级，2026-07-30）

**两层根因,主次分明:**

1. **表层——ID 过时**: `expected_parent_ids` 存旧 UUID,与当前索引集合无交集。fingerprint 已在生产坐实:`∩ live index = 0`(67 个旧 id ≈ 20088 个当前 id 零交集)。

2. **深层——语料域整体替换(主导因素)**: 生产 `candidates` 抽样验证发现,旧黄金集 91 道检索题**全部是钢结构主题**(冲孔硬化区/墙架柱/桁架节点板/柱人孔/应变速率/Q345 焊条/螺栓螺纹长度……),但当前 122 篇索引**无一篇钢结构规范**。当前设计规范全是:混凝土、给排水、防火、暖通、节能、热工、电气、防雷、消防、化粪池、检查井、砌体。公司标准是 BIM 建模/机电算量/管综/出图流程。这不是"题还在只是 ID 变了"——是**很多旧题在当前语料里根本没有答案**,方案 A 前提失效。

#### 需修正/补充的背景要点（对照核对结果）

- [ ] **no-answer 判定不是精确 `==`，而是 `startswith`**：`_grade_no_answer()` 用 `answer_text.strip().startswith("资料中未找到相关内容。")`（脚本 docstring/表头写的"== equals"与实现不一致，属脚本内部措辞 bug）。
- [ ] **更关键的口径冲突**：脚本期望前缀是"资料中未找到相关内容。"，但当前 `prompts/answer_system.md` 第 1 条指示模型答"**未找到相关内容**"（无"资料中"前缀、无句号），第 9 条明确要求**不要**在正文追加"资料来源"列表。而脚本注释（第 51–58 行）仍假设"prompt 会强制追加资料来源脚注，故用 startswith"。→ prompt 已演进、脚本判定短语已过期，no-answer 判不合规是 **prompt 输出与评分短语不匹配**，比"口径对不上"更具体：连前缀都对不上。
- [ ] **生产索引具体数字（123 篇 / GB 32 篇 / 分类计数）无法在本地只读环境复核**：`list_indexed_documents()` 机制存在且可产出该清单，但具体数字来自用户生产实跑，本次未在生产核对，记为"用户侧已观察、待实施阶段用同一函数固化"。

#### 现成能力盘点（决定重建走哪条路）

- [ ] `src/eval/sample.py` + `scripts/sample_for_eval.py` **可复用**：从 `parents.sqlite` 按 kind 加权采样 → `sampled_parents.json`（确定性 seed），仅覆盖 factual/table_formula/code_lookup/transcript；multi_turn 与 no_answer 需人工写。→ 支撑"重采样"路线（方案 B）。
- [ ] **不存在**"从当前索引反向回填 `expected_parent_ids`"的现成脚本；需新增一个小工具。→ 影响"半自动回填"路线（方案 A）成本。
- [ ] **可复用的重标资产**：`golden.jsonl` 每条含 `notes`（答案原文片段，如"首段：通过扩钻和刨边消除局部硬化区")与 `question`、`source_parent_id`（旧）。`notes`/`question` 保留了 ground-truth 语义，可用来在当前索引里重新定位正确父块，是方案 A 的关键依据。`drafts.jsonl`（68 条合成草稿）亦可参考。

#### 已定路线（用户 2026-07-30 生产验证后拍板）

- [x] **转方案 B 全重建**：生产 `candidates` 验证坐实方案 A 前提失效(旧题无对应语料),改用 `sample.py` 从当前 20088 个父块重采样 → Agent 合成面向真实语料(BIM/机电/建筑规范)的全新题 → 人工评审入 `golden.jsonl`。
- [x] **旧钢结构黄金集归档保留**：`golden.jsonl` 归档为历史参考(重命名或移入子目录),不删除、不再作为当前基线,保留人工标注痕迹。
- [x] no-answer 口径：改脚本对齐 prompt(已于 commit 96bd539 交付)。

#### 已否决方案（记录取舍,勿重提）

- ~~**方案 A — 半自动回填 ID**~~：**前提失效,已否决**。生产验证显示旧题主题(钢结构)与当前语料域(BIM/机电/建筑规范)不重叠,90% 以上旧题在当前索引无正确父块可回填。`relabel_golden.py candidates` 工具本身仍有用(用于新题的候选辅助与未来校准),但"回填旧题 ID"这一用法作废。
- ~~组合(以 A 为主)~~：随 A 否决而作废。

#### 重采样重建（方案 B）实施要点

**范围决策（用户 2026-07-30 定）：分期——第一期只建规范类,公司流程类另立项。**

- [x] **评测范围：分期**。第一期黄金集只覆盖**规范/标准/图集类**(GB 规范、中海客户标准、图集)的"查数值/查条文"题——确定性强、好评分、与旧集同型。**公司流程/BIM 操作类**(建模要点、机电算量、管综、培训视频 transcript)因题形态是"怎么做"、难客观打分,**单独立项**,后续设计专门评测方式,不混入第一期。
- [ ] `sample_for_eval.py` 从当前 `parents.sqlite` 重采样,产出新 `sampled_parents.json`。**第一期只从设计规范/客户标准类文档抽样**(需给 `sample.py` 加 category/doc 过滤,当前它不区分来源)。
- [ ] **抽样质量过滤(抽样预览发现,必做)**：现有 `MIN_PARENT_CHARS=200` 不足,预览抽到约一到两成噪声块——图集签署栏(人名如"审核/校对")、`![](images/...)` 图片链接残块、纯目录/图集号、无数据表头。需加过滤:剔除图片链接占比高、纯签署/目录、全表头无数据行的父块。
- [ ] kind 配额需按当前语料重新定。抽样预览结论:**当前语料强项是 GB 规范**(code_lookup 料极多、质量好),建议提高 code_lookup/table_formula 配额;factual 保留但只取规范类;transcript **第一期排除**(归入公司流程另立项)。
- [ ] Agent 合成器读 `sampled_parents.json` 产 `drafts.jsonl` → 人工评审 → 新 `golden.jsonl`。Agent 可做初筛(答案是否出自父块、可检索性、难度、去重),终审与抽查(15-20题)由用户/领域同事把关;出题与审题尽量分离视角,降低同源偏差。
- [ ] no_answer 题需重设:旧的 6 条(幕墙/Revit窗族/暖通冷负荷/装配式/工作集/钢筋混凝土抗震)有些现在**语料里已有**(暖通、混凝土规范都在),不再是"域外",需换成真正当前语料覆盖不到的主题。
- [ ] `candidates` 子命令用途转正:不再用于"回填旧题",改为**新题合成后的可检索性反向验证**(跑 retrieve 确认新题目标父块进 top-5)。

#### 公司流程/BIM 操作类评测（第二期,另立项占位）

- [ ] 待第一期规范类基线恢复后设计。覆盖:BIM 建模流程、机电算量、管综要点、培训视频 transcript。题形态是"操作要点/怎么做",非"查数值",评分方式需专门设计(可能需 LLM 裁判或要点覆盖率,不同于确定性 parent_id 命中)。属独立项目,不与第一期混。

#### no-answer 判定与当前 prompt 对齐（重建同批修复）

- [ ] 统一"标准拒答短语"的**单一事实源**：或修 `prompts/answer_system.md` 使拒答固定输出脚本期望短语，或修 `run_eval_retrieval.py`（含 docstring/表头/`_grade_no_answer`）使其匹配 prompt 实际短语；二选一，避免两处再次漂移。（属 prompt/评分契约改动，本身即 R2，需与重建一并审批。）
- [ ] 判定语义确认：保留 `startswith` 以容忍尾部脚注，还是收紧为规范化后精确匹配——取决于对齐后 prompt 是否仍可能追加内容。

#### 防止未来再次陈旧

- [ ] 评测运行时记录**索引指纹**（如 parents 行数 + parent_id 集合的哈希/采样），写入 run 日志；启动时与黄金集标注时的指纹比对，不匹配则显著告警"黄金集可能已陈旧"，避免再次静默全 0。
- [ ] 建立"索引重建后重跑标注校准"的流程/脚本入口（方案 A 的回填脚本可复用为校准工具），并在 `README`/`WORKLOG` 记录该约定。

#### 是否区分"检索黄金集"与"生成质量评测"

- [ ] 明确二分：本节只负责**检索**黄金集（parent_id 集合命中，确定性、无 LLM 裁判）；答案文案/引用正确性属"生成质量评测"，另立项，不混入本次重建，以免把不确定的生成判定引入确定性检索标尺。

#### 数据契约

- [ ] `EvalItem`（`src/eval/types.py`）Schema 不变即可满足方案 A（仅 `expected_parent_ids` 取值更新，可扩多值）；如需记录"本条依据的当前索引指纹/重标日期"，评估新增可选字段（不改现有必填字段，保持旧行可读）。

#### 预计修改文件（候选，实施前重新确认）

- [ ] `src/eval/golden.jsonl`（重标 `expected_parent_ids`；本次禁止改，仅规划）
- [ ] 新增 `scripts/relabel_golden.py`（只读检索 + 人工确认回填/校准工具）
- [ ] `scripts/run_eval_retrieval.py` 与/或 `prompts/answer_system.md`（no-answer 口径对齐、索引指纹记录）
- [ ] 可能：`src/eval/sample.py` / `sampled_parents.json`（走方案 B 增量补充时）

#### 分阶段步骤（候选）

- [x] **阶段 0 生产验证(2026-07-30 完成)**：在生产实跑 `fingerprint` 坐实 ∩=0、`candidates --limit 3` 验证候选,发现语料域替换(钢结构→BIM/机电/建筑规范),方案 A 否决,转向 B。
- [x] no-answer 口径修复(commit 96bd539 交付)。
- [x] **阶段 1 代码(2026-07-30 交付)**：`sample.py` 加 category 白名单(设计规范+客户标准)、质量过滤(_is_noise_parent: 剔图片链接块/签注/纯表头)、新配额(factual40/code25/table20/transcript0);`sample_for_eval.py` CLI 暴露新参数;旧 `golden.jsonl`/`drafts.jsonl` 归档到 `src/eval/archive/`。本地 compile + 过滤单测通过。**待生产执行**:`sample_for_eval.py` 产出新 `sampled_parents.json`(需 docker cp 新代码进容器)。
- [ ] 阶段 2：Agent 合成器读 `sampled_parents.json` 产 `drafts.jsonl` → 人工评审 → 新 `golden.jsonl`。
- [ ] 阶段 3：索引指纹记录集成到 `run_eval_retrieval.py` 启动流程(需评估优先级)。
- [ ] 阶段 4：在新 `golden.jsonl` 上重跑 `run_eval_retrieval.py`,确立**新基线**。
- [ ] 阶段 5：补充对比型/多轮/边界/转录用例（接续"7. 检索黄金集扩展"）。

#### 自动化验证 / 真实索引验证

- [ ] 回填脚本单测：给定构造 parents 与题目，校验候选检索与 ID 回填逻辑。
- [ ] 真实索引：重跑检索评测，R@1/R@5/MRR@5 恢复到非零合理区间；no-answer 合规率随口径修复回升；结果与新指纹一并存档。

#### 风险 / 兼容性 / 回滚

- [ ] 风险：重标引入人工偏差（把主题相关但非出处的父块误标为正确）——用 top-K 人工确认 + `notes` 对照缓解。
- [ ] 兼容性：`EvalItem` Schema 尽量不破坏；如加字段用可选默认，保证旧 run 日志与 `io.load_jsonl` 仍可读。
- [ ] 回滚：新黄金集先写副本、经 diff 审阅再替换；保留旧 `golden.jsonl`（git 历史 + 备份）可随时回退。

#### 是否需要索引重建

- [ ] **否**。本方案针对黄金集与评测脚本，不触碰索引；反而依赖"当前索引保持不变"来重标。若实施期间语料再次重建，需重跑校准。

#### 用户决定（2026-07-30 已定，含生产验证后升级）

- [x] **重建路线：方案 B 全重建**——旧钢结构黄金集与当前语料域(BIM/机电/建筑规范)不重叠,方案 A 前提失效。改用 `sample.py` 从当前 20088 个父块重采样,合成面向真实语料的全新题。
- [x] **旧钢结构黄金集归档保留**——重命名/移入子目录,不删除,不再作为当前基线。
- [x] **no-answer 口径：改脚本对齐 prompt**——以 `prompts/answer_system.md` 为准(线上生效契约),修 `run_eval_retrieval.py`(docstring/表头/`_grade_no_answer`)匹配 prompt 实际短语；不改动线上回答行为。(已于 commit 96bd539 交付)
- [x] **重建策略 A → 否决**：因语料域不重叠,无法回填旧题。

#### 仍需用户决定的事项（可延后至实施前）

- [ ] 是否新增 `EvalItem` 可选字段记录指纹/重标日期。
- [ ] 目标题量与 kind 配额是否沿用现状（97）还是调整。

#### R2 审批提示

- [ ] 本节为 R2，涉及评测契约与 prompt 输出口径；**尚未获实施授权**。实施前需用户看方案后明确"批准执行"；方案范围或风险升级需重新审批。

### 8. Ubuntu 应用节点 + Windows GPU 节点迁移 ✅

**状态**：已完成，迁移实施中

**参考**：`project-docs/migrations/ubuntu-app-windows-gpu-runbook.md`

**已完成**：
- [x] 阶段 0 — 基线采集与决策
- [x] 阶段 1 — GPU 推理接口（21/21 测试通过，GPU 冒烟通过）
- [x] 阶段 2 — Provider 抽象（22/22 测试通过）
- [x] 阶段 3 — 容器拆分（镜像构建成功）
- [x] 阶段 4 — CI 扩展
- [x] 阶段 5 — CD 拆分
- [x] Ubuntu 基础设施搭建（Docker、Runner、目录结构、Compose 覆盖）
- [x] Qdrant snapshot 恢复（38488 points）
- [x] Backend 启动并验证（浏览器登录成功）
- [x] CD 全流程跑通（deploy-gpu → deploy-app）
- [x] GitHub Actions Secrets 配置
- [x] docs/ 和 media/ 迁移完成
- [x] 稳定观察期（2026-07-30 用户确认结束）

---

## ✅ 最近已完成（已从待办移除）

按时间倒序：

1. ✅ **Office 文档支持 — 阶段 1：契约与样本基线** — 数据契约定义、Schema 迁移、前端类型同步（2026-07-27）
2. ✅ **Office 文档支持 — 阶段 2：上传与安全校验** — 文件签名校验、zip bomb 检测、宏检测、解析分发骨架（2026-07-27）
3. ✅ **Office 文档支持 — 阶段 3：DOCX 索引解析** — Docling Slim 接入，DOCX → Markdown（2026-07-27）
4. ✅ **Office 文档支持 — 阶段 4：DOCX 前端预览** — docx-preview 渲染，右侧面板（2026-07-27）
5. ✅ **Office 文档支持 — 阶段 5：XLSX 索引解析** — openpyxl 转换器，多 Sheet 分块（2026-07-27）
6. ✅ **Office 文档支持 — 阶段 8：LibreOffice 转换服务** — 独立容器，XLSX 公式重算 + Office → PDF 转换（2026-07-27）
7. ✅ **来源面板分类分组显示** — 按分类分组、可折叠、组标题带图标和数量（2026-07-25）
2. ✅ **来源面板关键词高亮** — 匹配检索词加黄色背景标记（2026-07-25）
3. ✅ **来源预览文本移除 HTML 标签** — `stripMarkdown()` 增加 `<[^>]*>` 清理，覆盖面包屑和 Tooltip（2026-07-25）
4. ✅ **视频播放器第一阶段（基础设施）** — 媒体目录配置、数据库迁移、数据契约打通、鉴权 Range 播放、管理端上传、视频播放器抽屉、引用点击 seek（2026-07-23）
2. ✅ **SSE 心跳机制防止流超时断开** — `api/routes_chat.py`（2026-07-22）
3. ✅ **纯数字输入无论第几轮对话都拦截** — `src/query_guard.py`（2026-07-22）
4. ✅ **引用角标 tooltip 闪烁、遮挡、溢出修复** — `Message.tsx`（2026-07-22）
5. ✅ **WPS 风格引用角标样式** — `Message.tsx`（2026-07-22）
6. ✅ **移除 LLM 输出中重复的"资料来源"小节** — `prompts/answer_system.md`（2026-07-22）
7. ✅ **查询验证守卫（无意义输入拦截）** — `src/query_guard.py`（2026-07-20）
8. ✅ **Docker 构建缓存优化与依赖清理** — `requirements-prod.txt`、`docker/*`（2026-07-20）
9. ✅ **Code-boost 规范编号检索增强** — `src/retrieve.py`（2026-05）
10. ✅ **表格摘要索引增强** — `src/table_summary.py`（2026-05）
11. ✅ **每轮遥测 + 每用户聊天限流** — `api/rate_limit.py`（2026-05）

---


## 🎬 视频自动转录（FunASR）— 候选方案，待 R2 批准

> **状态**：候选设计，尚未批准实施，尚未修改任何业务代码。本节所有 FunASR 性能、显存、准确率数字均为“需实测”，不得当作当前事实。
> **依据**：本节由一次只读源码复核形成，替代原「视频播放器第二阶段」中“自动语音识别（Whisper）集成”一条；ADR `0001` 第 9 节原倾向 `faster-whisper`，本节在核对现状后改为倾向 FunASR，最终引擎选择仍需 R2 审批。
> **前置**：视频转录链路第一阶段（MP4 + 人工 Markdown → 索引 → 鉴权 Range 播放）已实现（见 `project-docs/features/transcript-pipeline.md`）。本方案只替换“转录稿的产生方式”，复用其后全部绑定、索引、检索、引用、播放链路。

### 目标与用户价值

- [ ] 管理员只上传 MP4，即可后台自动生成符合现有 `chunk_transcript()` 规范的转录 Markdown，自动进入现有索引链路，最终可检索、可引用、可跳播；
- [ ] 完整保留现有「MP4 + 人工 Markdown 直接索引」旧流程，不得退化；
- [ ] 消除人工逐句转写教学视频的成本，同时把转录质量、时间戳、专业术语的可控性留在企业内网，不外发资料。

### 当前代码事实（已逐项源码核对，2026-07-30）

- [ ] 上传接口 `POST /api/admin/media`（`api/routes_admin.py:716`）**当前强制同时上传 `video`(.mp4) + `transcript`(.md)**，二者缺一即 400；自动转录必须把 `transcript` 改为可选。
- [ ] `media_assets` 表（`api/db.py:88`）**已存在**，且 `status` 注释已预留 `uploading|uploaded|transcribing|transcript_ready|indexing|ready|failed`，`transcript_origin` 已预留 `uploaded|generated`；但当前上传逻辑直接写入 `status='transcript_ready'`、`transcript_origin='uploaded'`（`api/routes_admin.py` 媒体分支），从未使用 `transcribing`/`generated`。
- [ ] `index_jobs.media_id`（`api/db.py:74`，nullable）**已存在**并由 worker 透传给 `index_single(..., media_id=...)`（`api/indexing.py:136`）。`index_jobs.status` 目前只有 `pending|parsing|chunking|embedding|summarizing|done|failed`，**不含任何转录状态**，转录任务不适合直接塞进 `index_jobs`。
- [ ] 索引 worker 是**单队列、单并发、FIFO**（`api/indexing.py`），CPU 密集流程在 `run_in_executor` 线程跑；进程重启时非 `done/failed` 任务被标记 `failed` 待人工重试（`resume_pending_on_boot`）。
- [ ] 部署为**双节点**：Ubuntu 应用节点（无 GPU，跑 FastAPI+Qdrant+SQLite+`docs/`+`media/`）+ Windows RTX 5060 Ti 节点（跑 `gpu_service/`，仅 BGE-M3 embedding + reranker）。见 `project-docs/migrations/ubuntu-app-windows-gpu-runbook.md`。视频文件落盘在 Ubuntu 侧 `media/<media_id>/original.mp4`（`src/config.py:MEDIA_DIR`）。
- [ ] `gpu_service`（`gpu_service/app.py`、`models.py`）：Bearer Token 鉴权、`/health`、`/model-info`、`/v1/embeddings`、`/v1/rerank`；`ModelManager` 为单例，`_gpu_semaphore = Semaphore(1)` **串行化 embedding 与 rerank**，两模型自启动起常驻显存；`MAX_REQUEST_BYTES` 默认 1MB（`gpu_service/config.py:23`），不足以传音频。
- [ ] 后端通过 `EMBED_PROVIDER`/`RERANK_PROVIDER=remote` 经 `src/providers.py` 的 httpx 客户端调用 GPU 节点，启动时用 `/model-info` 校验 API version/dim 契约。
- [ ] 依赖现状：torch 需 cu128 且 `>=2.7`（Blackwell sm_120 首个支持版本，`GPU_DEPLOYMENT.md`）、`FlagEmbedding>=1.3,<2`、`transformers>=4.46,<5`（`requirements*.txt`、`gpu_service/requirements.txt`）；**无 funasr / modelscope / av(PyAV) / onnxruntime / ffmpeg 依赖**。
- [ ] 转录 Markdown 精确格式：分块靠正则 `^#*\s*说话[人⼈]\s+\d+\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$`（`src/chunk.py:322`），即每段以「说话人 <序号> <MM:SS 或 HH:MM:SS>」独占一行、其后为正文；上传校验（`api/routes_admin.py:693`）只要求 UTF-8 且至少一条该标记行。首个标记之前的内容（标题/元信息表）会被丢弃。
- [ ] Parent/Child 时间戳传递：`chunk_transcript()`（`src/chunk.py:346`）中 Parent 继承**组内第一条** turn 的 `first_ts`；每个 Child 携带**自身** turn 的 `ts`。写入时 **Child 的 `start_time` 已进入 Qdrant payload**（`src/index.py:272`），Parent 的 `start_time` 进入 parents.sqlite（`src/index.py:152`）。
- [ ] **引用时间戳确为 Parent 首句时间，而非实际命中 Child 时间**：`retrieve.py` 按 `parent_id` 去重后只 `fetch_parents()` 取 Parent 行，`RetrievedParent.start_time = p.get("start_time")`（Parent 值，`src/retrieve.py:284`），命中 Child 的 payload 时间在去重时被丢弃；`generate.py:63` 与 `session.py:279` 均用 Parent 的 `start_time`。**此为已确认缺陷，非本方案引入。**
- [ ] 任务重试 `POST /api/admin/index/jobs/{job_id}/retry`（`api/routes_admin.py:607`）对 `failed|done` 任务重跑 `index_single`，**输入是磁盘上的 `.md` source_path，不触发任何 ASR**——这正好是“转录成功但索引失败时不重复 ASR”的现成基础。
- [ ] 近期 Office 改造（阶段 1–9）扩展了 `index_single` 的 `doc_type` 分发与 `data/parsed/` 缓存、`SourceDTO` 字段，但**未改动媒体上传接口、`index_jobs` 结构或 media 播放链路**，与本方案无冲突。

### 推荐架构（核对现状后的结论，可在 R2 阶段调整）

- [ ] **ASR 独立进程，部署在 Windows GPU 节点，但与 `gpu_service` 分离为独立服务/端口**（例如 `asr_service`），不塞进现有 `gpu_service` 进程：
  - 理由 1：`gpu_service` 的 `_gpu_semaphore(1)` 串行化设计是为在线低延迟 embed/rerank 服务的；ASR 长任务（分钟级）若共用同一信号量会**阻塞在线检索**。
  - 理由 2：ASR 依赖（funasr/modelscope/PyAV）体量大且与推理依赖解耦，独立进程便于按需加载、崩溃隔离、单独重启。
  - 理由 3：ASR 与 embed/rerank 需**跨进程共享同一张 16GB 卡**，用独立进程 + 一个**跨进程 GPU 互斥（文件锁 / 独立信号量服务 / 显存预算约定）**比进程内信号量更清晰。
- [ ] **音频提取（PyAV）在 Ubuntu 应用节点完成**，只把 16kHz 单声道音频（分块）经内网发到 Windows ASR 服务：
  - 理由：视频原文件在 Ubuntu 侧；Ubuntu 无 GPU，PyAV/FFmpeg 解码是 CPU 工作，放应用节点避免大文件二次搬运，也让 GPU 节点只做纯 ASR。
  - `gpu_service` 现有 `MAX_REQUEST_BYTES=1MB` 不够传音频；ASR 服务需独立、更大的请求体上限或改用分块流式上传。
- [ ] **新增独立 `transcription_jobs` 表（app.sqlite），不复用 `index_jobs`**：转录是媒体生命周期的一环，语义、状态机、重试粒度都与文档索引不同；混用会污染 `index_jobs` 的 `doc_type/source_path` 契约。转录完成后**再**照旧创建 `index_jobs`（doc_type=transcript, media_id 关联），两阶段解耦。
- [ ] **幂等分层**：媒体状态机负责“是否已生成转录稿”，索引任务负责“是否已索引”。转录产物（权威 JSON + 生成的 .md）落盘后，索引失败只重跑 `index_jobs`（不重跑 ASR）；只有转录产物缺失才重跑转录。

### 备选方案与取舍

- [ ] **ASR 塞进 `gpu_service` 同进程**：省一个服务，但长任务阻塞在线检索、依赖耦合、难以独立扩缩——**不推荐**。
- [ ] **ASR 跑在 Ubuntu 应用节点（CPU）**：无需跨节点传音频，但 Ubuntu 无 GPU，Paraformer CPU 转录慢且抢占应用 CPU——**不推荐**。
- [ ] **引擎改用 `faster-whisper`（ADR 0001 原倾向）**：多语种更强、社区大，但中文标点/热词生态不如 FunASR，且 Whisper 系已被本任务明确排除在第一阶段外——**本方案倾向 FunASR，faster-whisper 仅作 Phase 0 对照项记录，不实现**。
- [ ] **转录任务复用 `index_jobs`**：省一张表，但状态机与契约冲突、重试粒度错配——**不推荐**。
- [ ] **音频提取放 Windows GPU 节点**：GPU 节点承担解码 CPU 负载并需接收整段视频，放大跨节点传输——**不推荐**，除非实测 Ubuntu 解码成为瓶颈。

### 第一阶段范围（Phase 1，最小可用自动转录）

- [ ] 管理端上传单个 MP4（不带 Markdown）→ 音频提取 → FunASR 转录（含 VAD + 自动标点 + BIM 热词）→ 生成权威 JSON → 生成 `chunk_transcript()` 兼容 Markdown → 自动创建 `index_jobs` → 复用现有索引/检索/引用/播放；
- [ ] 保留旧「MP4 + 人工 Markdown」上传路径；
- [ ] 第一阶段说话人统一标记为「说话人 1」（不做说话人分离）；
- [ ] 管理端显示媒体状态、转录进度、失败原因与重试；
- [ ] 自动转录功能开关（关闭时上传接口回退为“必须提供 Markdown”的旧行为）。

### 明确不做（第一阶段排除，除非调查证明是必需基础）

- [ ] 生产部署、真实客户视频测试、真实姓名/声纹识别、说话人分离；
- [ ] 在线转录稿编辑器、逐字字幕高亮、HLS、视频转码、缩略图；
- [ ] Whisper / WhisperX / Qwen3-ASR / 云端 ASR；
- [ ] NAS/共享盘迁移、多实例分布式转录队列、自动删除媒体、全量索引 Reset。

### 数据契约

- [ ] **权威中间产物：结构化 JSON**（转录的唯一权威结果，Markdown 由它派生）：
  ```json
  {
    "schema_version": 1,
    "engine": "funasr",
    "model": "paraformer-zh",
    "media_id": "<uuid>",
    "audio_sha256": "<hex>",
    "hotwords_version": "<可选>",
    "segments": [
      { "start_ms": 12000, "end_ms": 16800, "speaker": "speaker_1", "text": "首先建立项目基点" }
    ]
  }
  ```
- [ ] JSON 落盘位置候选：`media/<media_id>/transcript.json`（与视频同目录，属可重建派生产物，随媒体删除一并清理）；`partial` 写 `transcript.partial.json`，全部完成后**原子 rename** 为 `transcript.json`。
- [ ] 生成的 Markdown 落盘沿用现有约定：`docs/教学视频/<安全标题>__<media_id前8位>.md`，且 `transcript_origin='generated'`。
- [ ] JSON→Markdown formatter 必须产出严格匹配 `TRANSCRIPT_TURN_RE` 的「说话人 1 HH:MM:SS」行；毫秒时间戳需转为 `HH:MM:SS`（校验分块可解析）。
- [ ] 校验规则：空段过滤、`start_ms<=end_ms`、段按 `start_ms` 单调不减、分块合并处去重与边界重叠处理、至少一条有效段否则整体判失败。

### 状态机（候选，需 R2 确认归属）

- [ ] **媒体状态（media_assets.status）**：`uploading → uploaded →（自动模式）extracting_audio → transcribing → transcript_ready → indexing → ready`，失败置 `failed`；人工模式仍 `uploading → transcript_ready → indexing → ready`。
- [ ] **转录任务状态（transcription_jobs.status，候选）**：`pending → extracting → transcribing → merging → writing → done|failed`。
- [ ] **索引任务状态**：沿用现有 `index_jobs`，不新增转录语义。
- [ ] 避免多 worker 覆盖状态：转录 worker 只写 `transcription_jobs` 与 media 的转录相关字段；索引 worker 只写 `index_jobs` 与 media 的 `indexing/ready`；用**单并发队列 + 明确字段归属**防竞争。
- [ ] 进程重启恢复：仿 `resume_pending_on_boot`，非终态转录任务标记 `failed`（附“重启中止，请重试”）供人工重试，避免僵尸中间态。

### 是否新增 `transcription_jobs`（候选字段，**不实际建表**）

- [ ] 候选字段：`id PK`、`media_id`（唯一约束，一个媒体一条活动转录任务）、`status`、`engine`、`model`、`audio_sha256`、`error`、`progress`（0–100 或已处理秒数）、`created_at`、`started_at`、`finished_at`、`transcript_json_path`。
- [ ] 幂等：`(media_id, audio_sha256)` 已有 `done` 记录且 JSON 存在时，跳过 ASR 直接进入索引。
- [ ] 兼容迁移：与现有做法一致，`init_db()` 内 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 增列，**向前兼容、不破坏旧库、不需要索引 Reset**。

### BIM 热词

- [ ] 热词接口需 Phase 0 实测确认 Paraformer 的实际热词传入方式（FunASR `hotword` 参数 / 独立热词文件）及数量/长度上限；
- [ ] 仓库内维护**通用 BIM 术语表**（可入库），部署环境敏感词（客户名/项目名）放**部署侧配置**（`.env` 或挂载文件），二者分离；
- [ ] 需实测热词是否损害普通词准确率（对照有/无热词的 CER）。

### GPU 调度（结论需真实测量，不得只按参数量推断显存）

- [ ] 单张 RTX 5060 Ti 16GB 需同时承载 BGE-M3 + reranker（常驻）+ Paraformer/VAD/CT-Punc；
- [ ] 候选策略：ASR 模型**延迟加载 + 空闲卸载**（转录任务间隙释放显存），在线 embed/rerank 优先级更高；
- [ ] 跨进程 GPU 协调：ASR 服务与 `gpu_service` 用**跨进程互斥**（文件锁/信号量），ASR 大批处理时让路在线检索；
- [ ] OOM 隔离：ASR 服务 OOM 只影响转录任务并快速返回明确错误，不得拖垮在线检索（对齐 runbook「GPU 不可用快速 503」不变量）；
- [ ] 健康接口需能区分 BGE 与 ASR 两类模型的加载状态；
- [ ] **验证门槛**：`- [ ] 在非生产环境实测 ASR 单独、ASR+BGE 并发时的峰值显存、是否 OOM、在线检索 P50/P95 延迟退化`，未实测前不得写“16GB 足够”。

### 精确时间戳修复（✅ 已选定方案 A，已实施，2026-07-30）

> **决策**：采用**方案 A（覆盖 `start_time` 单字段）**，不采用方案 B（新增 `best_child_start_time` 独立字段）。
> 理由：当前全链路只有“显示 / LLM 引用注入 / 跳播”三个消费者，均应使用命中段时间，无任何代码需要 Parent 首句时间；方案 A 改动仅 1 层（`src/retrieve.py`），数据契约、`SourceDTO`、前端类型均不变，且天然保证“LLM 引用时间 = 显示 = 跳播”三者一致。方案 B 会跨 6 层改契约、命中“SourceDTO 变化需同步”不变量，且若不同步改 `generate` 注入时间则引用匹配会错位——收益仅为未来“同时展示段落起点+命中点”的预留，可推迟到播放器第二阶段。

- [x] 已确认现状：命中 Child 时间被丢弃，播放跳到 Parent 首句（见“当前代码事实”）。
- [x] 关键发现已复核：**Child 的 `start_time` 已在 Qdrant payload 中**（`src/index.py:272`），且在 `query_points(with_payload=True)` 结果可直接读到。
- [x] 方案 A 实施：在 `retrieve.py` 去重循环记录每个 parent 首次命中（即最佳命中）Child 的 `payload.start_time`，构造 `RetrievedParent` 时以其覆盖 `start_time`，该 Child 无时间时回退 Parent `start_time`。下游 `generate`/`session`/`SourceDTO`/前端零改动自动继承。
- [x] **结论确认：无需修改 Qdrant payload、无需重建索引**（payload 已含所需字段）。
- [x] 旧会话：已持久化 `sources_json` 保留旧值，恢复时按旧值显示，不报错、不迁移。
- [ ] 后续（非阻塞）：如播放器第二阶段需同时展示“段落起点 + 命中点”，届时再评估是否引入方案 B 的独立字段。

### 预计修改文件（候选，实施前重新确认）

- [ ] 新增：`src/asr/`（音频提取 PyAV、分块、JSON schema、JSON→Markdown formatter）、`asr_service/`（Windows GPU 独立服务）或在 `gpu_service` 外并列、`api/transcription.py`（转录任务队列/worker）。
- [ ] 修改：`api/routes_admin.py`（媒体上传改 transcript 可选 + 自动模式分支）、`api/db.py`（`transcription_jobs` 迁移）、`api/indexing.py`（转录完成后建索引 job）、`src/config.py`（ASR 服务 URL/Token、热词路径、功能开关、音频分块参数）、`requirements*.txt` 与 `asr_service` 依赖清单。
- [ ] 精确时间戳修复涉及：`src/retrieve.py`、`src/generate.py`（可选）、`src/session.py`、`api/schemas.py`、`frontend/src/types.ts`、`frontend/src/components/Message.tsx`、`SourcesPanel.tsx`。
- [ ] 前端：`frontend/src/pages/AdminDashboard.tsx`（上传模式选择、转录进度、失败重试、开关降级）。

### 分阶段实施步骤

- [ ] **Phase 0 技术验证**：许可证核查；funasr/modelscope/PyAV 与 torch2.7-cu128 / transformers<5 / Blackwell sm_120 兼容性；模型下载与缓存位置；短音频转录正确性；毫秒时间戳有效性；热词接口与收益；ASR 单独 + ASR/BGE 并发峰值显存实测；对在线检索延迟影响实测。
- [ ] **Phase 1 结构化转录基础**：PyAV 音频提取（16kHz 单声道）；分块与合并去重；权威 JSON schema；JSON→Markdown formatter（严格匹配 `TRANSCRIPT_TURN_RE`）；原子写入；单元测试（无需 GPU 的 formatter/合并/校验测试）。
- [ ] **Phase 2 任务与恢复**：`transcription_jobs` 表；媒体/转录/索引三段状态机；`(media_id, audio_sha256)` 幂等；进度上报；失败重试；进程重启恢复；转录成功后自动建 `index_jobs`。
- [ ] **Phase 3 GPU 服务集成**：Windows 独立 ASR 服务（内部 Bearer 鉴权、请求体上限、超时、并发=1、跨进程 GPU 互斥、OOM 隔离、健康检查）；后端 provider/客户端；契约（mock）测试。
- [ ] **Phase 4 管理端**：自动/人工模式切换、进度、错误、重试、功能关闭降级。
- [ ] **Phase 5 检索与引用**：自动转录产物定向索引；最佳命中 Child 时间修复；旧会话兼容；视频跳播端到端验证。
- [ ] **Phase 6 验收与灰度**：见下方验证与验收。

### 自动化验证

- [ ] formatter/合并/校验/时间转换的纯单元测试（不依赖 GPU/模型）；
- [ ] 转录任务状态机与幂等的单元测试（mock ASR）；
- [ ] ASR 服务契约 mock 测试（鉴权、超时、413、503、OOM 路径）；
- [ ] 前端 `npm run build`；
- [ ] 精确时间戳修复的检索冒烟（`scripts/test_retrieve.py`）与既有转录黄金集不退化。

### 真实 GPU 验证（非生产环境）

- [ ] 在非生产环境实测 Paraformer-zh + FSMN-VAD + CT-Punc 端到端转录与时间戳；
- [ ] 在非生产环境实测 ASR 单独与 ASR+BGE 并发的峰值显存、是否 OOM；
- [ ] 在非生产环境实测每小时视频的转录耗时与在线检索 P50/P95 延迟退化；
- [ ] 在非生产环境实测 BIM 热词对术语与普通词 CER 的影响。

### RAG 回归验证

- [ ] 自动转录产物走完整索引后，检索能命中且引用/跳播正确；
- [ ] 精确时间戳修复后，对既有转录黄金集比较 Recall@1、Recall@5、MRR、no-answer 保持率，确认无退化；
- [ ] 确认 `media_id`/`best_child_start_time` 不进入 Embedding 文本、不改变排序。

### 用户验收（代码完成后按 `docs/USER_ACCEPTANCE.md` 提供）

- [ ] 前置条件、非敏感测试视频、上传（自动/人工）操作步骤、预期转录/索引/检索/跳播结果、失败与重试检查、清理方式；
- [ ] 明确“Reset/删除/生产部署/真实客户视频”不作为普通验收步骤，需单独审批。

### 风险

- [ ] FunASR 与 torch2.7-cu128 / transformers<5 / Blackwell sm_120 的兼容性未经证实（Phase 0 必须先验证）；
- [ ] 16GB 单卡 ASR 与在线 BGE 争用可能导致 OOM 或检索延迟退化（需实测）；
- [ ] 长视频转录耗时长，需异步任务与进度反馈，避免请求超时；
- [ ] 生成 Markdown 若不严格匹配分块正则会导致检索为空；
- [ ] 跨节点音频传输带来磁盘/网络压力；
- [ ] 热词过多可能损害普通词准确率。

### 兼容性

- [ ] 人工转录旧流程完全保留；
- [ ] 新增 `transcription_jobs` 与可空列均为向前兼容迁移，旧库原地可用；
- [ ] 精确时间戳修复候选结论为**不改 Qdrant payload、不重建索引**（实现前需冒烟复核 payload 确含 child `start_time`）。

### 回滚

- [ ] 功能开关关闭：上传回退为“必须提供人工 Markdown”，不触发 ASR；
- [ ] 停用 ASR 服务不影响在线检索与既有播放；
- [ ] 保留已生成 JSON/Markdown/视频文件供恢复，删除媒体须单独明确确认；
- [ ] 精确时间戳修复可独立回退（前端回退用 Parent start_time）。

### 是否需要索引重建

- [ ] 自动转录新产物：仅对新媒体**定向索引**，不需要全量 Reset；
- [ ] 精确时间戳修复：候选结论**不需要重建**（payload 已含 child 时间），但**必须在实现前用现有索引冒烟复核后再最终确认**，不得未经核对就断言。

### 仍需用户决定的事项

- [ ] 引擎最终确认：FunASR（推荐）vs ADR 0001 原倾向 faster-whisper；
- [ ] ASR 服务形态：Windows 独立 `asr_service`（推荐）vs 扩展 `gpu_service`；
- [ ] 是否新增 `transcription_jobs`（推荐）vs 扩展 `index_jobs`；
- [ ] 精确时间戳修复是否与自动转录合并为同一 R2，还是拆成独立小改先行；
- [ ] BIM 热词来源与敏感词管理边界；
- [ ] Phase 0 实测在哪台非生产环境进行。

### R2 审批提示

> 本节仅为候选设计。后续任一实施步骤均属 R2（涉及依赖、数据库结构、GPU 服务、上传契约、RAG 行为），必须由用户阅读本方案后明确回复“批准执行”。方案范围扩大或风险升级需重新评级、重新审批。**尚未修改业务代码，尚未开始 R2 实施。**

---

## 🔷 Office 文档支持（待审批方案）

> 状态：已完成调查，本阶段仅限方案设计，未实施任何代码。
> 方案依据：`project-docs/migrations/office-document-support-plan.md`

### 调查结论：当前调用链

```
上传（POST /api/admin/upload）
  → _classify_doc_type() 拒绝非 .pdf/.md 文件
  → 写入 docs/<category>/<filename>
  → create_job() + enqueue()
  → run_index_job() → index_single()
    → _build_pdf_doc() / _build_markdown_doc() / _build_transcript_doc()
    → _purge_existing() 清理旧数据
    → chunk_document() 分块
    → store_parents() + index_children()
    → 写 Qdrant + parents.sqlite

预览（GET /api/pdf/{parent_id}）
  → 查 parents.sqlite → source_path → 返回 PDF 文件
  → 前端 react-pdf 渲染

数据契约：
  ParsedDoc: source_path, markdown_path, doc_type("pdf"|"transcript")
  Parent: parent_id, text, doc_title, section_path, source_path, doc_type
  Child: child_id, parent_id, text, embed_text, content_type, source_path, doc_type
  Qdrant payload: source_path, doc_title, category, section_path, doc_type, content_type, ...
  parents.sqlite: parent_id, doc_title, category, section_path, source_path, text, doc_type, ...
  SourceDTO: parent_id, doc_title, section_path, category, score, text, doc_type, start_time, media_id
  IndexJobDTO: id, filename, category, doc_type, source_path, file_size, status, error, ...
```

### 已确认决策

| # | 问题 | 决策 |
|---|------|------|
| 1 | DOCX 结构锚点 | 文本哈希（段落前 50 字符的哈希值） |
| 2 | XLSX 虚拟表格组件 | `@tanstack/react-virtual` + 自行渲染（MIT 许可证） |
| 3 | SheetJS 授权 | ✅ CE 版（Apache 2.0）满足生产使用要求 |
| 4 | LibreOffice 部署方式 | 独立容器（新增 `libreoffice` 服务，不放入 backend 镜像） |
| 5 | 旧版 .doc/.xls/.ppt | 第一版不支持，延后到第二版 |
| 6 | 部分成功状态 | 允许，索引成功 + 预览失败显示"预览暂不可用"，可单独重试预览 |
| 7 | Schema 迁移 | 需要，新增 `doc_type` 值：`docx`/`xlsx`/`pptx`，不再复用 `doc_type="pdf"` |
| 8 | 复杂 DOCX 自动降级 | 允许，但需显式标记 `parsed_via="mineru_fallback"`，`doc_type` 仍标记为 `docx` |

### 阶段 1：契约与样本基线

**目标**：定义 Office 文档的数据契约，收集测试样本，建立验收标准。

**当前代码依据**：
- `src/ingest.py` — `ParsedDoc` 只有 `doc_type="pdf"|"transcript"`，需要新增类型
- `src/chunk.py` — `Parent` 和 `Child` 的 `doc_type` 字段，需要扩展
- `api/schemas.py` — `SourceDTO` 和 `IndexJobDTO` 的 `doc_type` 字段
- `src/index.py` — `parents.sqlite` schema 和 `fetch_parents` 返回的字段
- `frontend/src/types.ts` — `Source` 类型

**拟修改文件**：
- `src/ingest.py`：`ParsedDoc` 新增 `doc_type` 枚举值
- `src/chunk.py`：`Parent`、`Child` 新增 Office 相关字段
- `src/index.py`：`parents.sqlite` schema 迁移
- `api/schemas.py`：`SourceDTO` 新增 Office 预览字段
- `frontend/src/types.ts`：同步更新 `Source` 类型
- `tests/`：新增 Office 测试样本

**前置依赖**：无

**实施要点**：
- 新增 `doc_type` 值：`docx`、`xlsx`、`pptx`，不再复用 `doc_type="pdf"`
- 评估 `ParsedDoc` 是否需要新增 `preview_path`、`parsed_path` 等字段
- 评估 `Parent` 是否需要新增 `sheet_name`、`cell_range`、`slide_number`、`paragraph_anchor` 等字段
- 明确这些字段是否需要写入 Qdrant payload
- 说明兼容旧索引的方式

**验证方法**：无代码变更，仅文档

**验收标准**：数据契约文档评审通过

**风险**：新增字段可能影响现有 `sqlite3.Row` 访问模式

**回滚方式**：不涉及代码修改，无需回滚

**是否改变索引契约**：是，新增 `doc_type` 值和字段

**是否需要重建索引**：否，仅对新上传的 Office 文档生效



### 阶段 2：Office 上传和公共解析接口

**目标**：扩展上传接口接受 `.docx`/`.xlsx`/`.pptx`，实现文件类型校验、安全检查和解析任务分发。

**当前代码依据**：
- `api/routes_admin.py` — `_classify_doc_type()` 当前只接受 `.pdf` 和 `.md`
- `api/routes_admin.py` — `upload_documents()` 写入 `docs/` 目录
- `api/indexing.py` — `run_index_job()` 分发到 `index_single()`
- `src/indexing_pipeline.py` — `index_single()` 当前只接受 `doc_type="pdf"|"transcript"`
- `src/config.py` — `MAX_UPLOAD_BYTES` 当前对应 200MB

**前置依赖**：阶段 1（契约定义）

**实施的 CheckList**：
- [ ] 扩展 `_classify_doc_type()` 支持 `.docx`/`.xlsx`/`.pptx`（延后 `.doc`/`.xls`/`.ppt`）
- [ ] 评估：是否第一版支持 `.doc`/`.xls`/`.ppt`
- [ ] 扩展名、MIME 类型和文件签名（magic bytes）校验
- [ ] 压缩炸弹防护（检测 zip bomb）
- [ ] 超大工作簿/超多 Sheet/Slide 检测
- [ ] 恶意外部链接和宏文件检测（先拒绝带宏的 Office 文件）
- [ ] 上传大小限制（复用现有 `MAX_UPLOAD_BYTES`）
- [ ] 解析超时和并发限制
- [ ] 临时目录隔离与清理
- [ ] 原始文件（`docs/`）、解析缓存（`data/parsed/`）和预览产物命名冲突防护
- [ ] 删除文档时同步清理原始文件、解析缓存、预览产物、Qdrant 点和 parents.sqlite 行
- [ ] 扩展 `index_single()` 的 `doc_type` 校验，分发到对应的解析器
- [ ] 扩展 `IndexJobDTO` 和 `IndexedDocumentDTO` 的 `doc_type` 类型

**拟修改文件**：
- `api/routes_admin.py`、`api/indexing.py`、`src/indexing_pipeline.py`、`src/config.py`、`api/schemas.py`

**明确不修改**：
- 不分发到 MinerU（Office 不使用 MinerU 解析）
- 不修改前端上传 UI（本阶段只完成后端）

**验证方法**：
- 上传 `.docx`/`.xlsx`/`.pptx` 文件，确认被接受并创建索引任务
- 上传 `.doc`/`.xls`/`.ppt` 文件，确认被拒绝（或接受，取决于待决策）
- 上传带宏的文件，确认被拒绝
- 上传超大文件，确认被拒绝

**验收标准**：Office 文件上传后进入索引队列，状态正确

**风险**：zip bomb 可能消耗大量内存，需要流式检测

**回滚方式**：回退 `_classify_doc_type()` 即可阻止 Office 上传

**是否改变索引契约**：否（本阶段仅上传，不分发解析）

**是否需要重建索引**：否



### 阶段 3：DOCX 索引解析

**目标**：接入 Docling Slim 将 DOCX 解析为结构化 Markdown，供现有分块管道使用。

**当前代码依据**：
- `src/indexing_pipeline.py` — `index_single()` 分发文档到各解析器
- `src/indexing_pipeline.py` — `_build_pdf_doc()` 模式，DOCX 需要类似的新函数
- `src/chunk.py` — `chunk_document()` 接收 `ParsedDoc` 的 Markdown 进行分块

**前置依赖**：阶段 2（上传接口）

**实施的 CheckList**：
- [ ] 安装 Docling（仅 Office 所需的最小 extras）
- [ ] 实现 `_build_docx_doc()` 或类似函数
- [ ] 标题、段落、列表、表格、图片说明和公式的 Markdown 输出质量验证
- [ ] 页眉页脚、目录、批注、修订和隐藏内容的处理策略（是否保留/丢弃）
- [ ] 图片是否提取/丢弃
- [ ] 复杂 DOCX 失败时是否允许转 PDF 后走 MinerU（待决策 8）
- [ ] 输出确定性（相同输入是否产生相同输出）和缓存失效策略
- [ ] 解析缓存路径和命名规则
- [ ] 状态机：`pending → parsing → chunking → summarizing → embedding → done`

**待决策**：DOCX 结构锚点如何生成并长期稳定（段落 ID / 文本哈希 / 序号偏移），用于前端引用定位

**拟修改文件**：
- 新增 `src/office_convert.py` 或 `src/docling_convert.py`
- 修改 `src/indexing_pipeline.py`，新增 `_build_docx_doc()`
- 修改 `src/indexing_pipeline.py`，`index_single()` 的 `doc_type` 分发

**明确不修改**：
- 不修改现有 PDF/MD/Transcript 解析路径
- 不修改 `src/chunk.py`（分块逻辑复用）

**验证方法**：
- 上传 DOCX 后确认 Markdown 缓存文件生成
- 确认标题层级、表格、列表结构正确
- 对比 Docling 输出与原始 DOCX 的内容一致性

**验收标准**：DOCX 解析后可在 Qdrant 中检索到对应内容

**风险**：Docling 对中文支持可能不完善，需用中文样本验证

**回滚方式**：回退 `index_single()` 的分发逻辑，DOCX 上传后仍停留在 `pending`

**是否改变索引契约**：否（复用现有 Markdown→分块管道）

**是否需要重建索引**：否



### 阶段 4：DOCX 前端预览

**目标**：在右侧预览面板中支持 DOCX 原生渲染，提供加载、缩放、滚动和段落定位功能。

**当前代码依据**：
- `frontend/src/components/PdfPreview.tsx` — 现有 PDF 预览组件
- `frontend/src/hooks/usePdfPreview.tsx` — 预览状态管理
- `frontend/src/components/SourcesPanel.tsx` — 来源卡片上的 PDF 预览按钮
- `frontend/src/components/ChatLayout.tsx` — 布局中集成预览面板
- `api/routes.py` — `GET /api/pdf/{parent_id}` PDF 文件接口

**前置依赖**：阶段 3（DOCX 索引解析）

**实施的 CheckList**：
- [ ] 安装 `docx-preview` 前端依赖
- [ ] 新增或扩展 `GET /api/source/{parent_id}/original` 受鉴权接口，返回原始 Office 文件
- [ ] 评估是否将 `PdfPreview` 重构为统一 `DocumentPreview`（待决策，见架构说明）
- [ ] DOCX 渲染器组件：加载、失败、超时和不支持状态
- [ ] 容器尺寸、缩放、搜索和滚动
- [ ] 外部链接和嵌入对象安全处理
- [ ] 根据 `paragraph_anchor` 或其他锚点滚动并高亮特定段落
- [ ] 原生渲染失真时显示"使用兼容 PDF 预览"入口
- [ ] 原始文件下载按钮
- [ ] 引用定位：点击来源面板中的引用 → 打开 DOCX 预览 → 定位到对应段落

**拟修改文件**：
- `frontend/src/components/`：新增 `DocxPreview.tsx`，修改 `PdfPreview.tsx` → `DocumentPreview.tsx`
- `frontend/src/hooks/usePdfPreview.tsx`：扩展为通用预览状态
- `api/routes.py`：新增原始文件接口
- `frontend/src/components/SourcesPanel.tsx`：预览按钮根据 `doc_type` 分发

**明确不修改**：
- 不修改现有 PDF 预览逻辑
- 不修改引用的数据契约

**验证方法**：
- 点击 DOCX 来源卡片 → 右侧打开 DOCX 预览
- 确认缩放、滚动正常
- 确认段落引用可定位

**验收标准**：DOCX 预览可用，引用定位正常

**风险**：`docx-preview` 对复杂格式（公式、SmartArt）支持有限

**回滚方式**：回退前端组件，预览按钮恢复为仅 PDF 显示

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 5：XLSX 索引解析

**目标**：基于 openpyxl 实现 XLSX 专用转换器，按工作表输出结构化 Markdown，每个 Chunk 保留 `sheet_name` + `cell_range`。

**当前代码依据**：
- `src/indexing_pipeline.py` — `index_single()` 分发文档
- `src/chunk.py` — `Parent` 和 `Child` 的现有字段
- `src/chunk.py` — `chunk_document()` 接收 `ParsedDoc` 的 Markdown

**前置依赖**：阶段 2（上传接口）

**实施的 CheckList**：
- [ ] 安装 openpyxl（纯 Python，无需系统依赖）
- [ ] 实现基于 openpyxl 的专用转换器
- [ ] 按工作表输出 Markdown 表格
- [ ] 有效数据区域识别（跳过空行/空列）
- [ ] 空白和纯装饰区域过滤
- [ ] 合并单元格处理
- [ ] 隐藏 Sheet、行和列的处理（是否跳过）
- [ ] 公式表达式与缓存值（优先取缓存值）
- [ ] 日期、百分数、金额和其他格式化值的 Markdown 输出格式
- [ ] 超宽、超长表格分块策略（避免单个 Parent/Child 过大）
- [ ] 多个独立表格区域识别
- [ ] 图表、图片、批注、命名区域和数据透视表的处理策略（丢弃/保留）
- [ ] 每个 Chunk 保存准确的 `sheet_name` + `cell_range`
- [ ] 避免一个大表整体进入单个 Parent/Child
- [ ] 状态机集成

**前置依赖**：阶段 1 确定 `Parent`/`Child` 新增字段

**拟修改文件**：
- `src/office_convert.py` 或 `src/xlsx_converter.py`（新增）
- `src/indexing_pipeline.py` — 新增 `_build_xlsx_doc()`，`index_single()` 分发

**明确不修改**：
- 不将 XLSX 转为 PDF 作为默认预览或索引方式
- 不修改前端预览（本阶段仅后端解析）

**验证方法**：
- 上传多 Sheet XLSX → 确认每个 Sheet 独立分块
- 宽表/万行级 XLSX → 确认分块策略正确
- 合并单元格、隐藏行列、公式 → 确认值正确

**验收标准**：XLSX 内容可检索，每个 Chunk 有正确的 `sheet_name` + `cell_range`

**风险**：超大 XLSX（百万行）可能消耗大量内存，需限制

**回滚方式**：回退 `index_single()` 分发逻辑

**是否改变索引契约**：是，新增 `sheet_name`、`cell_range` 字段

**是否需要重建索引**：否



### 阶段 6：XLSX 前端预览

**目标**：使用 SheetJS + 虚拟表格组件在浏览器中渲染 XLSX，支持工作表切换、冻结表头、虚拟滚动和引用定位。

**当前代码依据**：
- `frontend/src/components/PdfPreview.tsx` — 可作为统一预览面板的入口
- `api/routes.py` — 新增原始文件接口（阶段 4 已实现）

**前置依赖**：阶段 4（原始文件接口）、阶段 5（XLSX 索引解析）

**待决策**：
- 选用哪个 React 虚拟表格组件（`@tanstack/react-virtual` + 自行渲染 / `react-data-grid` / AG Grid Community）
- 核验 SheetJS Community Edition 许可证是否满足生产使用要求

**实施的 CheckList**：
- [ ] 安装 SheetJS 和选定的虚拟表格组件
- [ ] 工作表标签切换 UI
- [ ] 虚拟行列渲染（避免一次性渲染全部行/列）
- [ ] 冻结表头区域
- [ ] 合并单元格展示
- [ ] 基础格式展示（日期、百分数、金额等）
- [ ] 超大文件性能限制（显示行数上限 + 提示"仅显示前 N 行"）
- [ ] 点击引用后切换工作表、滚动到目标区域并高亮指定单元格范围
- [ ] 公式、图表、图片和数据透视表不完整时的用户提示
- [ ] 下载原文件按钮
- [ ] 加载状态、失败状态、不支持状态

**拟修改文件**：
- `frontend/src/components/`：新增 `SpreadsheetPreview.tsx`
- `frontend/src/components/`：修改 `DocumentPreview.tsx` 集成 XLSX 渲染器
- `frontend/package.json`：新增 SheetJS 和表格组件依赖

**明确不修改**：
- 不将 XLSX 转为 PDF 作为默认预览

**验证方法**：
- 多 Sheet XLSX → 工作表切换正常
- 万行级 XLSX → 虚拟滚动正常
- 引用定位 → 切换到指定 Sheet 并高亮单元格

**验收标准**：XLSX 预览可用，引用定位正常

**风险**：SheetJS Community Edition 可能限制某些功能（如条件格式、数据透视表渲染）

**回滚方式**：回退前端组件

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 7：PPTX 索引与预览

**目标**：Docling Slim 提取 PPTX 内容为 Markdown，LibreOffice 转为 PDF 供预览，确保幻灯片编号与 PDF 页码一一对应。

**当前代码依据**：
- `src/indexing_pipeline.py` — `index_single()` 分发文档
- `frontend/src/components/PdfPreview.tsx` — 复用现有 PDF 渲染
- `api/routes.py` — `GET /api/pdf/{parent_id}` PDF 文件接口

**前置依赖**：阶段 2（上传接口），阶段 8（LibreOffice 转换服务）

**实施的 CheckList**：
- [ ] Docling Slim 提取：幻灯片标题、正文、备注、表格和图片说明
- [ ] Markdown 中明确保留幻灯片边界（`---` 或 `<!-- slide N -->`）
- [ ] 每个 Chunk 保存 `slide_number`
- [ ] LibreOffice 将 PPTX 转换为 PDF（使用阶段 8 的转换服务）
- [ ] 验证幻灯片编号与 PDF 页码的映射关系
- [ ] 字体替换、公式、SmartArt、动画和媒体对象的降级方式
- [ ] 点击引用后使用现有 react-pdf 跳转到对应页
- [ ] 转换失败时允许下载原文件并显示明确错误
- [ ] 状态机集成

**拟修改文件**：
- `src/indexing_pipeline.py` — 新增 `_build_pptx_doc()`
- `src/office_convert.py` — 集成 LibreOffice 转换
- `frontend/src/components/` — 复用 `PdfPreview.tsx`（无需修改）

**明确不修改**：
- 不引入新的前端预览组件（复用 PDF 预览）

**验证方法**：
- 上传 PPTX → 确认幻灯片内容可检索
- 确认 PDF 预览页码与幻灯片编号一致
- 引用定位到正确幻灯片

**验收标准**：PPTX 内容可检索，预览可翻页，引用定位到正确幻灯片

**风险**：LibreOffice 转换 PDF 时字体替换可能导致排版偏差

**回滚方式**：回退 `index_single()` 分发逻辑

**是否改变索引契约**：是，新增 `slide_number` 字段

**是否需要重建索引**：否



### 阶段 8：LibreOffice 转换服务

**目标**：实现 LibreOffice 转换服务，为 PPTX 和复杂 DOCX 提供 PDF 预览兼容兜底。

**当前代码依据**：
- `docker/Dockerfile.backend` — 当前镜像不含 LibreOffice
- `docker/docker-compose.yml` — 仅定义 backend 和 qdrant 服务
- `src/indexing_pipeline.py` — 索引管道

**前置依赖**：阶段 7（PPTX 索引与预览）

**待决策**：LibreOffice 放入 backend 镜像还是独立服务

**实施的 CheckList**：
- [ ] 评估并决定 LibreOffice 部署方式（待决策 4）
- [ ] 安装 LibreOffice（含中文字体）
- [ ] 独立用户 home 目录（profile）隔离
- [ ] 输入/输出目录隔离
- [ ] 超时和进程终止
- [ ] 并发限制（限制同时运行的 LibreOffice 进程数）
- [ ] 缓存键设计（基于原始文件哈希）
- [ ] 健康检查（LibreOffice 是否可用）
- [ ] PDF 有效性检查（产物是否有效 PDF）
- [ ] 失败重试策略
- [ ] 临时文件清理策略
- [ ] 不允许 LibreOffice 宏执行
- [ ] 生产环境资源上限（CPU、内存）
- [ ] 状态机集成

**拟修改文件/配置**：
- `docker/Dockerfile.backend` 或新增 `docker/Dockerfile.libreoffice`
- `docker/docker-compose.yml` 或新增服务
- 新增 `src/libreoffice.py` 或 `src/office_convert.py`
- `src/config.py` — 新增 LibreOffice 相关配置

**明确不修改**：
- 不修改现有 PDF/MD/Transcript 解析路径
- 预览 PDF 不作为索引输入

**验证方法**：
- PPTX → PDF 转换成功率
- 中文字体渲染正确
- 并发转换不冲突
- 超时进程被正确终止

**验收标准**：LibreOffice 转换稳定可靠，PDF 产物可用

**风险**：LibreOffice 子进程可能僵尸化，需要进程管理

**回滚方式**：移除 LibreOffice 安装或停止独立服务

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 9：引用定位

**目标**：Office 文档的引用（citation）点击后可定位到对应段落/单元格/幻灯片，前端同步切换预览状态。

**当前代码依据**：
- `frontend/src/components/citations.ts` — `resolveCitation()` 当前按 `doc_title` + `section_path` 匹配
- `frontend/src/components/Message.tsx` — `CitationMarker` 处理引用点击
- `frontend/src/components/SourcesPanel.tsx` — 来源卡片
- `frontend/src/hooks/usePdfPreview.tsx` — 预览状态管理

**前置依赖**：阶段 4、6、7（各类文档的预览组件）

**实施的 CheckList**：
- [ ] 扩展 `resolveCitation()` 支持 Office 文档的定位参数
- [ ] DOCX：`paragraph_anchor` → 打开预览 → 滚动到对应段落并高亮
- [ ] XLSX：`sheet_name` + `cell_range` → 切换工作表 → 滚动到目标区域并高亮
- [ ] PPTX：`slide_number` → 打开 PDF 预览 → 跳转到对应页码
- [ ] 统一预览状态管理：`useDocumentPreview`（或扩展 `usePdfPreview`）支持 Office 文档类型

**拟修改文件**：
- `frontend/src/components/citations.ts` — 扩展 `resolveCitation()`
- `frontend/src/hooks/usePdfPreview.tsx` — 扩展为通用预览状态
- `frontend/src/components/Message.tsx` — 处理新引用类型

**明确不修改**：
- 不修改引用数据流（前端事件分发机制不变）

**验证方法**：
- 点击 DOCX 引用 → 定位到对应段落
- 点击 XLSX 引用 → 定位到对应单元格区域
- 点击 PPTX 引用 → 跳转到对应幻灯片

**验收标准**：三种 Office 文档的引用定位均正常

**风险**：DOCX 段落锚点稳定性和 XLSX 行列号在文件修改后可能失效

**回滚方式**：回退前端引用定位逻辑，引用退化为仅打开预览面板

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 10：安全、性能和故障恢复

**目标**：确保 Office 文档处理的安全性和性能，以及故障恢复能力。

**当前代码依据**：
- `api/indexing.py` — `run_index_job()` 的异常处理模式
- `src/indexing_pipeline.py` — `_purge_existing()` 清理逻辑

**前置依赖**：阶段 2-8

**实施的 CheckList**：
- [ ] 压缩炸弹检测（zip bomb）
- [ ] 宏文件检测（拒绝带 VBA 的 Office 文件）
- [ ] 恶意外部链接检测
- [ ] 超大文件检测（复用 `MAX_UPLOAD_BYTES`）
- [ ] 解析超时限制
- [ ] 临时目录隔离
- [ ] 并发解析限制
- [ ] 删除文档时同步清理所有派生产物（原始文件、解析缓存、预览产物、Qdrant 点、parents.sqlite 行）
- [ ] 失败重试策略
- [ ] 磁盘空间告警

**拟修改文件**：
- `api/routes_admin.py` — 上传校验
- `api/indexing.py` — 索引任务状态管理
- `src/indexing_pipeline.py` — 清理逻辑

**明确不修改**：无

**验证方法**：
- 上传带宏文件 → 被拒绝
- 上传超大文件 → 被拒绝
- 删除文档 → 所有派生产物被清理

**验收标准**：安全防护到位，删除操作完整

**风险**：zip bomb 检测增加上传延迟

**回滚方式**：回退安全校验逻辑

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 11：Docker 与生产发布

**目标**：更新 Docker 构建、Compose 配置和部署文档，确保 Office 功能在生产环境可运行。

**当前代码依据**：
- `docker/Dockerfile.backend` — 单阶段构建
- `docker/docker-compose.yml` — 仅 backend + qdrant
- `scripts/deploy-app.sh` — 部署脚本
- `requirements-prod.txt` — 生产依赖

**前置依赖**：阶段 2-8、阶段 10

**待决策**：LibreOffice 放入 backend 还是独立服务

**实施的 CheckList**：
- [ ] 安装新 Python 依赖（Docling、openpyxl、SheetJS 等）
- [ ] 安装 LibreOffice（如果选择放入 backend 镜像）
- [ ] 更新 `requirements-prod.txt`
- [ ] 更新 `frontend/package.json`
- [ ] 更新 `docker/Dockerfile.backend` 或新增 Dockerfile
- [ ] 更新 `docker/docker-compose.yml` 或新增服务
- [ ] 更新 `scripts/deploy-app.sh`
- [ ] 更新 `.env.example`
- [ ] 评估镜像体积影响
- [ ] 评估是否需要数据库迁移
- [ ] 评估是否影响现有 PDF、Markdown 和视频转写稿
- [ ] 评估是否需要重建现有索引
- [ ] Office 功能开关设计
- [ ] 分阶段灰度策略
- [ ] 回滚时如何保留原始 Office 文件并停止新解析
- [ ] 如何清理可重建的解析与预览缓存

**拟修改文件**：
- `docker/`、`requirements-prod.txt`、`frontend/package.json`、`.env.example`、`scripts/deploy-app.sh`

**明确不修改**：
- 不修改现有 PDF/MD/Transcript 的容器配置

**验证方法**：
- Docker 构建成功
- 镜像体积符合预期
- 部署后 Office 功能正常

**验收标准**：生产环境可部署，Office 功能可用

**风险**：LibreOffice 安装可能增加镜像体积 300MB+

**回滚方式**：回退 Dockerfile 和 Compose，使用旧镜像

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 12：自动化测试和用户验收

**目标**：编写 Office 文档处理的自动化测试和手工验收矩阵。

**当前代码依据**：
- `tests/test_providers.py` — 现有测试模式
- `tests/` — 测试目录

**前置依赖**：阶段 2-11

**测试样本**：
- 中文普通 DOCX
- 带标题层级和表格的 DOCX
- 带公式、SmartArt 或复杂浮动对象的 DOCX
- 多 Sheet XLSX
- 宽表和万行级 XLSX
- 合并单元格、隐藏行列、公式和格式化值的 XLSX
- 中文 PPTX
- 带表格、备注、图表和特殊字体的 PPTX
- 损坏文件
- 伪造扩展名
- 超大文件
- LibreOffice 转换超时
- 同名文件重传
- 删除文件及缓存

**验收矩阵**：
- 点击引用后的 DOCX 段落定位
- 点击引用后的 XLSX 单元格区域定位
- 点击引用后的 PPTX 页码定位
- 匿名用户、普通用户和管理员的文件访问权限
- 原生渲染失败时显示兼容预览入口
- 下载原文件按钮可用

**拟修改文件**：
- `tests/test_office_convert.py`（新增）
- `tests/test_office_upload.py`（新增）
- `frontend/` — 前端测试（如有）

**验证方法**：`pytest tests/` 通过，前端构建通过

**验收标准**：所有测试用例通过

**风险**：测试样本可能占用大量存储空间

**回滚方式**：不涉及

**是否改变索引契约**：否

**是否需要重建索引**：否
