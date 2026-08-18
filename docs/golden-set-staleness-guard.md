# 历史方案：黄金集陈旧告警（Staleness Guard）

## 状态
状态：已实施的历史方案。当前实现见 `src/eval/fingerprint.py` 和 `scripts/run_eval_retrieval.py`；本文件不构成新的审批或执行授权。

## 目标
让 `run_eval_retrieval.py` 在每次评测开始时，比对"当前索引指纹"与"黄金集被标注时的基准指纹"，不匹配时**显著告警**"黄金集可能已陈旧"，避免再次出现"全 0 命中却被静默接受"的事故。不阻断运行（评测应仍可产出指标供观察），但让"错"的数字难以被忽略。

## 背景（已核对）
- `scripts/relabel_golden.py::_index_fingerprint(parent_ids)` 已在生产验证中坐实：parent_count + sha256(sorted parent_ids) 两项即可稳定识别索引内容。
- `golden.jsonl` 当前 79 题（检索 69 + multi_turn 10 + no_answer 6 + comparison 4），不含任何"该集何时被标注/基准指纹"的元数据——再次出现"集已陈旧但没人知道"时，没有程序化检测手段。
- 当前 `run_eval_retrieval.py` 启动只 `load_jsonl` 后立刻进入评测循环，零校验。

## 方案

### 1. 抽出共享 fingerprint 函数
把 `relabel_golden.py::_index_fingerprint` 抽到 `src/eval/fingerprint.py`（新文件），两边复用。删 `relabel_golden.py` 里的本地副本。
- 函数签名保持 `parent_ids: list[str] -> {parent_count, parent_id_sha256}`。
- 同步在 `relabel_golden.py` 加个"写基准指纹"的小工具：算出来后写到 `src/eval/golden.fingerprint.json`（sidecar，单文件 `{parent_count, parent_id_sha256, frozen_at}`）。

### 2. 基准指纹的落点与刷新时机
- **存储**：`src/eval/golden.fingerprint.json`（**新增独立文件**）。**不**嵌入 `golden.jsonl`（避免污染 JSONL 行结构、`load_jsonl` 不需感知）。
- **写入时机**：
  1. **重建/重标黄金集时**（如方案 B 重标完成、`relabel_golden.py fingerprint --json` 已算出现行）→ 人工或脚本把当时 fingerprint 写入 sidecar，作为新基准。这是"**冻结**"操作。
  2. 可选：未来若 `sample.py` 加 `--freeze-fingerprint` 标志，重采样后自动冻结。当前**手动**。
- 每次重建/重标黄金集时**必须**更新 sidecar（属黄金集生命周期的一部分，记到流程约束里）。

### 3. 评测启动校验
在 `run_eval_retrieval.py` 的 `load_jsonl` 之后、评测循环之前加：
```
[eval] live index fingerprint: parent_count=N parent_id_sha256=X
[eval] baseline fingerprint:    parent_count=N parent_id_sha256=Y (frozen_at=...)
```
- **匹配**：静默通过（不刷屏）。
- **不匹配**：打印**显眼**告警（`!!!` 前缀 / 单独段落），包含：
  - live 指纹与基准指纹；
  - 差异（parent_count 差几个、sha256 是否变化）；
  - 建议："黄金集可能已陈旧，建议重跑 relabel_golden.py fingerprint 校准，或按 docs/fix-golden-staleness.md 流程重标。"（**不要**自动重标——重标本身是独立 R2 决策。）
- **非阻断**：`run_eval_retrieval.py` 仍继续跑、产出指标。理由：陈旧集下的指标可能仍对调试有用（"全 0 命中"本身就是信号），但用户必须**主动看到**告警，不会被静默结果误导。
- 增加 `--strict-staleness` CLI 标志：不匹配时**退出码非 0**（如 `SystemExit(2)`），供 CI 流水线在"集已陈旧"时直接失败。默认关闭。

### 4. 第一次上线的基准
- 当前生产索引：`parent_count=20088`，sha256=`8478af626ecd9624f5ba05ca930c64d43587e79122a2e7dc941139f1cc4f67f8`（2026-07-30 `relabel_golden.py fingerprint` 产出）。
- 把这一对写入 `src/eval/golden.fingerprint.json` 作为新基准（与 79 题黄金集同源生成），从此以后的评测以此为锚点。

## 拟修改文件
- `src/eval/fingerprint.py`（**新**）：共享 fingerprint 计算函数。
- `src/eval/golden.fingerprint.json`（**新**）：sidecar 存当前基准。
- `scripts/relabel_golden.py`：删本地 `_index_fingerprint`，改 import；`fingerprint` 子命令增加 `--freeze` 写 sidecar。
- `scripts/run_eval_retrieval.py`：启动校验 + 告警 + `--strict-staleness` 标志。
- **不改** 黄金集 `EvalItem` schema、不改现有 `grade_one/grade_comparison`、不动索引/检索。

## 实施步骤
1. 抽 `src/eval/fingerprint.py`，迁移测试。
2. 写 `golden.fingerprint.json`（当前 live 指纹为基准）。
3. 改 `relabel_golden.py` + `run_eval_retrieval.py`。
4. 本地：人工篡改 `golden.fingerprint.json` 一个字段 → 跑 `run_eval_retrieval.py --limit 1` → 验证告警显示、`--strict-staleness` 退出码非 0。
5. 生产：pull、跑一次 baseline 对比（不变） + 模拟陈旧（改 sidecar 一行）跑一次验证告警路径。

## 验证 / 风险 / 回滚
- **风险**：sidecar 漏更新会让告警持续误报——流程约束里把"重标必 freeze"写清楚。脚本侧不自动 freeze，避免隐式修改。
- **兼容**：`golden.jsonl` 不变；不动 EvalItem；旧的 79 题数据/评分结果不受影响。
- **回滚**：删除 `golden.fingerprint.json` 即可回到无校验；改动小、git 易退。
- **是否需索引重建**：否。

## 明确不做
- 不自动重标（重标本身需用户决策，不在工具链里隐式触发）。
- 不引入新依赖。
- 不改 `EvalItem` schema（`frozen_at` 等元数据放 sidecar，不污染行结构）。
- 不在 `run_eval_retrieval.py` 里修改评分逻辑、不动 multi_turn/comparison 路径。

## 仍需用户决定
- `golden.fingerprint.json` 位置：用 `src/eval/` 下还是仓库根？默认建议 `src/eval/`，靠近被标的黄金集。
- `--strict-staleness` 默认开关：建议**默认关**（仅打印告警），CI 再传开。
- 重标与 fingerprint freeze 的当前入口以 `scripts/relabel_golden.py` 和评测脚本帮助文本为准。

## R2 审批提示
本方案改评测脚本 + 新增 sidecar 协议。**尚未获实施授权**，需用户看方案后明确"批准执行"。方案范围或风险升级时需重新审批。
