# 修复方案：retrieve_multi passages>100 触发 rerank HTTP 422（R2，待批准）

## 状态
调查完成，方案待批准。**尚未修改任何代码。** 本文件仅为方案。

## 问题现象
开启 `QUERY_DECOMPOSE_ENABLED=true` 后，比较型查询走 `retrieve_multi`，偶发：
```
rerank returned HTTP 422: {"detail":[{"type":"too_long","loc":["body","passages"],
"msg":"List should have at most 100 items after validation, not 104"}]}
```
2026-07-30 补比较型黄金集时，`eval-comparison-0004` 实测触发。该题因此整条评测失败（被 try/except 吞掉，但生产链路会让该轮问答降级/报错）。

## 根因（已逐项源码核对）
- **上限来源**：`gpu_service/config.py:21` `MAX_BATCH_SIZE=100`，在 `gpu_service/app.py:207` 对 `/v1/rerank` 的 `body.passages` 强制校验，超过即 422。
- **单查询路径不超限**：`src/retrieve.py::_recall_scored` 的 Qdrant `query_points(limit=RERANK_TOP_K)`（`RERANK_TOP_K=40`）保证一次召回 ≤40 条 child，送 rerank 也 ≤40。
- **多查询路径会超限**：`src/retrieve.py::retrieve_multi`（约 365–401 行）对每个子查询各调一次 `_recall_scored`（各 ≤40），再取**并集** `all_child_ids = list(child_point.keys())`，去重后最多 `DECOMPOSE_MAX_SUBQUERIES(3) × RERANK_TOP_K(40) = 120` 条；子查询间重叠少时就 >100。随后 `passages = [... for p in points]` 一次性整批送 `rerank_scores(original_query, passages)`，**rerank 前无任何截断** → 触发 422。
- **触发条件**：子查询数 ≥3 且各路召回重叠低（比较型天然如此，两侧文档不同）。子查询越正交越易踩。

## 目标
让 `retrieve_multi` 在送 rerank 前，把候选 child 数量**安全地控制在 rerank 批上限内**，且不破坏"每个子查询两侧都保留"的既有语义（quota 机制）。开关关闭路径完全不受影响。

## 方案候选与取舍

### 方案 A（推荐）— rerank 前按融合分数预截断到上限
- 做法：在 `retrieve_multi` 组装 `all_child_ids` 后、调用 `rerank_scores` 前，用**已算出的 cross-query RRF 融合分 `child_rrf_fused`** 对候选排序，截断到 `min(len, RERANK_BATCH_CAP)`（新增常量，默认 100，或更稳妥取 96 留余量）。只对**截断后的子集**做 rerank。
- 关键：截断必须发生在 quota 保底**之前的候选池**上，但要保证每个子查询的**头部若干条**不被误删——用"每子查询先保底取前 K_reserve 条进入 rerank 池，再按融合分补足到上限"的方式，避免某一侧被整体截掉（否则又回到"单侧"问题）。
- 优点：改动集中在 `retrieve_multi` 内部，不动 gpu_service、不动单查询路径、不改 rerank 契约；截断依据是已有的 RRF 融合分，无额外计算。
- 缺点：极端情况下（子查询召回高度正交）仍可能牺牲尾部候选，但那些本就排名靠后，影响小。

### 方案 B — 分批 rerank 后合并
- 做法：把 >100 的 passages 切成多批（每批 ≤100）分别 rerank，再按分数合并。
- 优点：不丢任何候选。
- 缺点：多次 GPU 往返、延迟上升；跨批分数可比性需注意（同一 query、同一模型，理论可比，但多批调用增加失败面）；实现更复杂。比较型触发频率低（<5% 轮次），不值得为它加分批复杂度。

### 方案 C — 调高 gpu_service MAX_BATCH_SIZE
- 做法：把 `MAX_BATCH_SIZE` 从 100 提到 ≥120。
- 缺点：治标不治本——上限是为保护 GPU 显存/延迟设的，提高它把风险转移到 OOM/超时；且 `DECOMPOSE_MAX_SUBQUERIES` 若未来调大，又会超。**不推荐**作为主修法。

### 结论
采用 **方案 A**：rerank 前预截断 + 每子查询保底。gpu_service 上限保持不变（它是正确的保护）。

## 拟修改文件
- `src/retrieve.py`：`retrieve_multi` 内，rerank 前加候选预截断（每子查询保底 + 融合分补足）。
- `src/config.py`：新增 `RERANK_BATCH_CAP`（默认 96，注释说明须 ≤ gpu_service MAX_BATCH_SIZE）。
- （不改 `gpu_service/*`，不改单查询 `retrieve`/`_recall_scored`。）

## 实施步骤（候选）
1. `config.py` 加 `RERANK_BATCH_CAP=96`。
2. `retrieve_multi`：构造 `all_child_ids` 后，若 `len > RERANK_BATCH_CAP`：
   - 先按 `per_sub_children` 每子查询保留前 `ceil(CAP / n_sub)` 条（保底两侧），去重收集；
   - 不足 CAP 时，按 `child_rrf_fused` 降序补足剩余名额；
   - 用截断后的 id 列表重建 `points`/`passages`，再 rerank。
3. 未截断（≤CAP）时保持原逻辑逐字节不变。

## 验证方式
- **单测**（本地）：构造 3 个子查询、各返回 40 个不重叠 child 的假场景，断言进入 rerank 的 passages ≤ CAP、且每子查询头部 K 条都在池内（两侧不被截掉）。
- **生产**：对 `eval-comparison-0004`（已知触发）实跑 `retrieve_multi`，确认不再 422、且 both_hit 仍 =True（截断没把某侧挤出）。
- **回归**：开关关闭路径 `run_eval_retrieval.py` 全量跑，79 题结果与当前基线逐项一致（证明单查询零影响）。
- **拆分整体**：`--kinds comparison` 重跑，both-sides 覆盖率不低于当前（off3/on4）。

## 风险 / 兼容 / 回滚
- **风险**：预截断策略若保底分配不当，可能把某侧头部挤出 → 用"每子查询保底 K 条"规避，并用单测覆盖。
- **兼容**：不改 rerank 契约、不改 gpu_service、不改数据结构；`retrieve()` 单查询完全不动。
- **回滚**：改动集中在 `retrieve_multi` + 一个常量，git 可退。
- **是否需索引重建**：否。

## 明确不做
- 不改 `gpu_service` 的 `MAX_BATCH_SIZE`（那是正确的保护）。
- 不引入分批 rerank（方案 B，复杂度不划算）。
- 不改单查询检索路径、不改评分脚本。
- 不默认开启 `QUERY_DECOMPOSE_ENABLED`。

## 仍需用户决定
- `RERANK_BATCH_CAP` 取值（建议 96，留 4 条余量；若要用满可 100）。
- 每子查询保底条数 K_reserve 的取值（建议 `RERANK_BATCH_CAP // n_sub`，即均分）。

## R2 审批提示
本方案改检索核心 `retrieve_multi`，属 R2。**尚未获实施授权**，需用户看方案后明确"批准执行"。仅当 `QUERY_DECOMPOSE_ENABLED` 开启时该路径才生效，但改动仍应经上述回归验证。
