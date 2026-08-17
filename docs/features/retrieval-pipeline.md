# 检索与重排

- 状态：已实现
- 最后核对：2026-08-16

## 用户可观察能力

系统根据问题从企业资料中召回相关子块，聚合回父段落，经过融合、规范编号增强和重排后交给回答生成。

## 当前边界

### 已实现

- BGE-M3 Dense + Sparse 检索；
- RRF 融合、分类过滤和规范编号 code boost；
- Child 命中聚合回 SQLite Parent；
- BGE reranker 重排；
- 多轮上下文携带和预算裁剪；上下文字符基线由全局回答策略控制，比较题保留
  2,000 字符的拆分 headroom，并受策略最大值约束。
- 检索结果记录 Top-1/Top-2 rerank、margin 与 RRF 校准信号；相关性门禁默认关闭，
  仅单轮非分解路径具备判定资格，阈值可由系统管理员按策略版本调整。

### 未实现

- TODO 中基于比较意图的查询拆分 Phase 2 生产灰度；
- 完整跨父文档历史提示与扩展预算。

## 入口与调用链

```text
ChatSession
→ retrieve_for_turn
→ retrieve
→ Qdrant Dense/Sparse + filters/code boost
→ fetch_parents(parents.sqlite)
→ RetrievedParent
→ rerank / merge / budget
→ generate
```

## 关键文件

- `src/session.py`
- `src/retrieve.py`
- `src/rerank.py`
- `src/index.py`
- `src/config.py`

## 数据契约

- Qdrant Child payload 必须含 `parent_id`、文档元数据和原始子块文本；
- `RetrievedParent` 是检索到生成阶段的父证据契约；
- Parent 正文从 `parents.sqlite` 回取，不以 Child 文本替代。

## 依赖与下游消费者

- 依赖文档分块、Embedding、Qdrant 和 parents.sqlite；
- 下游为 `ChatSession`、回答生成、评测和来源 UI。

## 不变量与安全边界

- Parent 用于回答，Child 用于检索；
- 修改 Chunk、ID、Embedding、Payload 或 Collection 必须说明旧索引兼容性和是否需要 Reset；
- 不得为了获得通过结果修改黄金集答案或屏蔽失败类型。

## 验证

- 先执行单问题检索冒烟；
- 按影响运行固定黄金集，分别报告 Recall@1、Recall@5、MRR 和 no-answer；
- 对规范编号、表格公式、转录本和多轮案例做定向检查；
- 比较题（4 条同池 GB50189/GB55015 规范）走 Phase A 2×2 离线协议：
  `scripts/run_eval_retrieval.py --kinds comparison` 跑出
  off_k5/off_k8/on_k5/on_k8 四单元格真实调用 + ITT/applied-only 两种分析集
  + 5 个 contrast + gain/loss，结论写进
  `src/eval/runs/run_<ISO>.summary.json` 侧车。**Phase A 仅评估
  retrieval 机制，不等价于生产开关 A/B；decision_eligible=false**。
  生产真实开关 A/B、上下文打包、回答质量、延迟与成本归 Phase B。

## 已知限制

- 当前检索质量依赖本地索引与模型状态，README 的历史指标不能代替本次验证；
- 相关性阈值必须绑定 reranker provider/model 与 policy version；环境默认策略为
  `uncalibrated-v1`，在可答/不可答分布校准完成前不得在生产开启。管理员保存的策略
  版本和每个回答版本的策略快照用于回溯，不改变默认关闭状态；
- Phase A 4 条比较题样本过薄（且全部是同一文档对、均带强规范编号），任何 delta>0
  须警惕"虚假高收益"，仅供机制层面参考，灰度决策仍需 Phase B。

## 相关决策

- 暂无独立 ADR；查询拆分的 Phase A 评测方案见 plan 文件
  `${LOCAL_USER_HOME}/.claude/plans/lexical-wobbling-fairy.md`（本仓库外）。

