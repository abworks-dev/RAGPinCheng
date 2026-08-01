# 多引擎视频自动转录 — 总体实施方案

- 状态：**架构方向已批准；阶段 A 文档调整已授权；Phase 1～Phase 6 尚未授权执行**
- 风险等级：**R2**（涉及 ASR Provider、配置档案、`app.sqlite` Schema、后台任务、管理端 API/UI、版本发布、索引门禁和单卡 GPU 调度）
- 批准日期：2026-08-01
- 关联决策：[0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)
- FunASR 候选专项计划：[FunASR 视频自动转录](funasr-auto-transcription.md)
- faster-whisper 候选材料：[faster-whisper Phase 0 静态预检](faster-whisper-phase0-precheck.md)

> 本方案是自动转录的总方案。FunASR、faster-whisper 及未来模型均作为可插拔候选，不再以“选出唯一赢家”作为统一流水线开发的前置条件。候选通过技术验证不自动授权业务集成；Phase 1～Phase 6 仍须逐阶段提交详细实施方案并单独审批。

## 1. 目标

允许管理员上传 MP4 后选择受控的转录配置，由后台调用相应 ASR Provider，统一生成 Canonical Transcript JSON 和确定性 Markdown；同一媒体可保留多个转录版本，但只有经审核并明确发布的唯一正式版本可以进入索引。

目标能力：

- 保留现有“MP4 + 人工 Markdown”流程作为永久回退路径；
- 支持多个 ASR 引擎和模型配置，但不向管理员暴露任意模型路径或任意底层参数；
- 同一媒体允许顺序运行多个引擎并比较结果，同时最多一个活跃转录任务；
- 引擎资格、运行可用性、转录成功、人工审核、正式发布和索引分别建模；
- 实验性引擎强制人工审核，禁止自动发布和自动索引；
- 单卡 GPU 下继续保证在线 BGE 优先，ASR 串行、可停止、可恢复；
- 自动转录默认关闭，生产启用和真实数据操作另行审批。

## 2. 当前事实与依据

### 2.1 已实现的媒体后半段链路

当前源码已经具备：

- `media_assets` 业务状态；
- 视频保存、鉴权 HTTP Range 播放和引用 seek；
- 教学视频 Markdown 分块、媒体 ID 贯通、索引和引用展示；
- 管理端 `POST /api/admin/media`，但当前必须同时上传 MP4 与人工 Markdown；
- 当前自动转录、转录任务、多版本审核和 Provider Registry 尚未实现。

自动转录只替换“转录稿如何产生”，不得另建一套检索、引用或播放协议。

### 2.2 FunASR 已有实测结论

- SenseVoiceSmall 冻结 8 短样本全部完成、处理失败 0、质量通过 7/8；噪声 BIM 样本的 CER 和专业术语召回未达预注册阈值。
- Contextual Paraformer 的 `bim-v1` 热词配置完成 8/8 A/B，噪声 BIM 样本明显改善，但正向收益覆盖不足且一个反例退化，总体 `overall_pass=false`。
- 两套 FunASR 路线均证明工程上可运行，但尚未取得自动转录生产资格；当前应登记为实验候选，而不是正式默认引擎。

### 2.3 faster-whisper 当前边界

- 已有静态预检和多轮 R3-A 执行材料；截至本方案批准时，尚未完成 wheel/model 下载、安装、GPU 推理和冻结样本质量评测。
- faster-whisper 是待评测 Provider，不是 FunASR 的既定替代品。
- 本方案不授权继续 retry、不复用或删除既有 retry artifacts。

## 3. 已批准的架构决策

### 3.1 Provider 与 Profile 分离

- **Provider** 是受控的引擎适配器，例如 SenseVoice、Contextual Paraformer、faster-whisper 或人工稿适配器。
- **Profile** 是管理员可选择的白名单配置档案，固定 Provider、模型、revision、有效参数、热词版本、资格状态和审核策略。
- 管理员只能提交 `profile_id`，不能提交任意模型路径、下载地址、热词正文或底层解码参数。

候选 Profile 示例：

```text
system-recommended
funasr-general-zh-v1
funasr-bim-hotword-v1
faster-whisper-zh-v1
manual-transcript
```

### 3.2 Profile 状态

统一使用：

```text
pending_evaluation  待评测
approved            正式可用
experimental        实验性可用
unavailable         当前运行环境不可用
disabled            禁止新任务
deprecated          停止新增任务，仅保留历史
```

规则：

- `approved` 才能成为 `system-recommended` 的目标；
- `experimental` 仅管理员可选，强制人工审核，禁止自动发布和自动索引；
- `unavailable` 显示原因但不可提交；
- `disabled/deprecated` 不影响历史任务和已发布转录稿的只读访问；
- 资格状态是经过审批的产品状态，运行时健康状态不能自行把候选升级为 `approved`。

### 3.3 多版本、单正式版本

- 同一媒体可以保留多个成功转录历史版本；
- 同一媒体同时最多一个 `pending/running` 转录任务；
- 转录成功只产生候选版本，不覆盖当前正式版本；
- 同一媒体同时只能有一个正式发布版本；
- 只有发布成功的版本可以创建索引任务；
- 新版本发布或索引失败时，旧正式版本继续可用；
- 删除真实媒体、版本或 artifacts 仍须单独审批。

### 3.4 Phase 1 与候选评测解耦

统一 Canonical JSON、formatter、Provider Protocol 和 fake fixtures 可以在没有正式 `approved` ASR 的情况下开发验证。没有正式引擎时：

- 人工 Markdown 路径继续可用；
- 自动转录部署开关保持关闭；
- 候选评测可独立继续或停止；
- 某个候选失败只改变该 Profile 状态，不阻塞统一流水线。

## 4. 统一服务契约

### 4.1 Provider Protocol

建议的逻辑接口：

```python
class TranscriptionProvider:
    def capabilities(self) -> ProviderCapabilities: ...
    def health(self) -> ProviderHealth: ...
    def transcribe(
        self,
        audio_path: Path,
        profile: TranscriptionProfile,
        checkpoint_dir: Path,
    ) -> CanonicalTranscript: ...
```

首批候选适配器：

- `ManualTranscriptProvider`；
- `FunASRSenseVoiceProvider`；
- `FunASRContextualParaformerProvider`；
- `FasterWhisperProvider`。

未通过资格或没有继续价值的适配器可以保留代码和历史证据，但默认不注册或标记为 `disabled`。

### 4.2 Profile Registry

Profile 应由服务端版本化白名单提供，并至少包含：

- `profile_id`、展示名称和说明；
- Provider、模型 ID、固定 revision；
- 影响结果的固定配置和 `config_hash`；
- 热词/词表版本身份，但不向前端返回敏感正文；
- 资格状态、强制审核和自动发布策略；
- 语言、时间戳、热词等能力声明；
- 当前运行可用性和脱敏不可用原因；
- 最近一次资格评测摘要及证据引用。

运行任务必须保存 Profile 快照，避免注册表后续变化改写历史含义。

## 5. Canonical Transcript JSON

所有 Provider 必须先转换成统一 Schema，再进入 formatter：

```json
{
  "schema": "canonical-transcript/1",
  "media_id": "uuid",
  "audio_sha256": "sha256",
  "profile": {
    "profile_id": "funasr-bim-hotword-v1",
    "engine": "funasr",
    "model": "contextual-paraformer",
    "model_revision": "immutable-revision",
    "config_hash": "sha256"
  },
  "language": "zh",
  "duration_ms": 120000,
  "segments": [
    {
      "id": 0,
      "start_ms": 0,
      "end_ms": 4200,
      "text": "今天介绍钢结构施工的基本要求。",
      "confidence": null
    }
  ],
  "warnings": [],
  "metrics": {
    "processing_ms": 5400,
    "peak_gpu_memory_mb": 1186
  }
}
```

约束：

- 时间统一为非负整数毫秒；
- 不强制所有引擎提供置信度，也不跨模型直接比较置信度；
- segment 顺序、重叠、空文本和越界必须由 Schema/normalizer 明确处理；
- 原始引擎输出可作为受控审计 artifact，但不能直接进入索引；
- JSON→Markdown formatter 必须确定性运行，不调用生成式 LLM；
- 同一 Canonical JSON 重复格式化必须产生字节一致结果；
- formatter 继续输出现有 `chunk_transcript()` 可解析的“说话人 + 时间戳 + 正文”格式。

## 6. 任务、版本和发布契约

### 6.1 transcription_jobs

至少记录：

- `id`、`media_id`、创建人；
- `profile_id`、Provider、模型、固定 revision、`config_hash`；
- Profile 资格状态快照和 `requires_review`；
- `audio_sha256`；
- `status`、`stage`、错误类型和脱敏错误摘要；
- `processed_ms`、`total_ms`；
- checkpoint、Canonical JSON 和草稿 Markdown 相对路径；
- 创建、开始、完成和更新时间。

有效幂等身份至少包含：

```text
(media_id, audio_sha256, profile_id, model_revision, config_hash)
```

### 6.2 transcript_versions

至少记录：

- `id`、`media_id`、关联转录任务；
- 来源 `automatic | manual`；
- Profile 和模型身份快照；
- Canonical JSON、Markdown 相对路径和内容 SHA-256；
- 审核状态、审核人和审核时间；
- 发布时间和被替代版本；
- 是否是当前正式版本由媒体上的唯一正式版本指针决定。

### 6.3 状态边界

```text
转录任务：pending → running → succeeded | failed | cancelled
候选版本：draft → awaiting_review → approved | rejected
正式版本：approved → publishing → published
索引任务：pending → parsing → chunking → embedding → done | failed
```

必须保持：

```text
ASR succeeded ≠ reviewed ≠ published ≠ indexed
```

## 7. 管理端行为

上传 MP4 后，管理员选择：

- 系统推荐；
- 普通中文快速转录；
- BIM 专业词增强（实验性，必须人工审核）；
- faster-whisper 候选（仅在注册且可用时显示为可选）；
- 稍后上传人工转录稿。

UI 必须显示：

- Profile 的资格状态和实验警告；
- 当前是否可用及脱敏原因；
- 是否强制人工审核；
- 任务阶段、按时长进度、失败原因和恢复动作；
- 同一媒体的历史版本、来源、审核和发布状态。

管理员可以：

- 预览/下载 Canonical JSON 与 Markdown；
- 上传修订后的 Markdown；
- 使用另一个 Profile 创建新的顺序任务；
- 比较历史版本；
- 审核候选版本；
- 明确发布一个版本并触发定向索引。

前端隐藏不是权限控制；所有 Profile 白名单、管理员权限、CSRF、状态迁移和发布门禁必须由后端强制。

## 8. GPU 调度和服务边界

- ASR 保持为独立进程/服务，不加载进现有 `gpu_service`；
- 单 GPU 同时最多一个 ASR 推理任务，不并行加载多个候选模型；
- 在线 BGE 始终优先，不为 ASR 主动卸载 BGE；
- Provider 需要统一 health、超时、取消、checkpoint、OOM 和不可用响应；
- 长音频采用块级执行和可恢复边界，不依赖单个长 HTTP 请求；
- 一个 Provider 失败不得改变其他 Profile 或当前正式转录版本；
- 生产开关和真实数据灰度仍须独立审批。

## 9. 分阶段实施

每个阶段必须单独提交详细方案并审批，前一阶段完成不自动授权下一阶段。

### 阶段 A — 架构文档调整

- 新增本总方案和 ADR；
- 将旧 FunASR 总方案标记为 FunASR 候选专项历史方案；
- 更新 TODO；
- 不修改代码、数据库、生产环境或 retry artifacts。

### Phase 1 — 引擎无关转录契约

- Canonical JSON Schema；
- Provider Protocol 和 Profile Schema；
- 时间戳 normalizer；
- 确定性 JSON→Markdown formatter；
- 仅使用固定夹具和 fake providers；
- 不安装真实 ASR，不连接生产服务和数据库。

### Phase 2 — 任务、版本与恢复

- `transcription_jobs`、`transcript_versions` 和正式版本指针；
- 活跃任务唯一约束、幂等、checkpoint、审核和发布状态；
- `app.sqlite` 兼容迁移、备份和恢复方案；
- 保留当前人工上传路径。

### Phase 3 — 独立 ASR 服务与 Provider Registry

- 独立 `asr_service`、鉴权、能力和健康接口；
- Profile Registry；
- 经批准的 Provider 适配器；
- 单卡调度、BGE 优先、超时、取消、OOM 和恢复；
- 候选资格与业务集成分别审批。

### Phase 4 — 上传 API 与管理端 UI

- transcript 可选上传和 `profile_id` 白名单；
- 能力列表、任务状态、历史版本、审核、修订和发布；
- 使用其他 Profile 重跑；
- 管理员、普通用户、匿名、CSRF 和失败恢复验证。

### Phase 5 — 隔离端到端验证

使用独立测试 collection、SQLite 和目录，验证：

```text
MP4 → 选择 Profile → 音频提取 → 转录 → Canonical JSON
→ Markdown → 审核 → 发布 → 索引 → 检索 → 引用 → 跳播
```

同时验证人工稿不退化、多版本、失败隔离、实验引擎审核门禁和旧正式版本保护。

### Phase 6 — 生产灰度

- 只允许经过审批的 `approved` Profile；
- 自动转录部署开关默认关闭；
- 明确允许数据分类、容量、监控、停止和回滚；
- 生产操作和真实数据按 R3 逐项审批。

## 10. 验证要求

### Phase 1

- Schema 合法/非法输入；
- 空音轨、空 segment、重叠和越界时间戳；
- 跨块 offset、毫秒取整、中文 UTF-8；
- 过短合并、超长拆分和重复执行字节一致性；
- FunASR、faster-whisper 和人工稿 fake fixtures；
- formatter 不调用 LLM。

### Phase 2～4

- SQLite 迁移、备份、恢复和旧数据兼容；
- 同媒体唯一活跃任务、多个历史版本和唯一正式版本；
- 实验 Profile 无法绕过审核和发布门禁；
- 管理员、普通用户、匿名、Cookie 和 CSRF；
- API 幂等、任务重启恢复和错误脱敏；
- 前端 TypeScript/Vite 构建。

### Phase 5～6

- 独立索引端到端；
- Recall@1、Recall@5、MRR、no-answer、引用正确性和跳播时间；
- BGE 健康、GPU 资源、长音频恢复和自动停止；
- 人工转录、普通文档、旧会话和播放器回归。

## 11. 风险与兼容性

- **运维复杂度**：多引擎增加依赖、模型缓存、测试和故障矩阵；用 Profile 白名单限制组合数量。
- **单卡争用**：进程隔离不等于硬件隔离；保持单 ASR 任务和 BGE 优先。
- **输出差异**：不同引擎的时间戳、语言和置信度含义不同；Canonical Schema 只保留可定义的公共契约，原始输出单独审计。
- **热词副作用**：热词必须可关闭、版本化并进入 `config_hash`；实验配置强制审核。
- **版本竞态**：转录成功、审核、发布和索引必须分离，旧正式版本在新流程失败时保持可用。
- **存储增长**：多版本和原始 artifacts 需要明确保留策略；真实数据删除另批。
- **沉没成本误判**：已实现不等于应开放；没有独特价值或不稳定的候选可以保持禁用。

## 12. 回滚

- 设置 `ASR_ENABLED=false`，停止接收自动转录任务；
- 单独禁用一个 Profile，不影响其他引擎和历史版本；
- 隐藏自动转录入口，继续使用现有 MP4 + Markdown 人工流程；
- 已发布媒体、历史任务和转录版本保持只读；
- 添加式数据库迁移回滚时保留新增表/列，避免破坏真实数据；
- 不自动删除模型、媒体、Canonical JSON、Markdown 或历史 artifacts。

## 13. 明确不做

本方案和阶段 A 不授权：

- 连接或修改生产机；
- 继续 faster-whisper retry 或创建新 retry identity；
- 安装、下载或运行任何 ASR；
- 修改业务代码、数据库、API、前端或部署；
- 多引擎并行推理、投票合并或 LLM 自动校正；
- 说话人分离、逐字字幕或在线字幕编辑器；
- 自动发布实验结果；
- 删除 retry3/retry4/retry5、FunASR 或生产 artifacts。

## 14. 后续审批顺序

1. 阶段 A：本地架构文档调整；
2. 单独编写并审批 Phase 1 详细实施计划；
3. Phase 1 完成并验证后，再分别审批 Phase 2～Phase 6；
4. faster-whisper 是否继续资格评测作为独立事项决定，不阻塞 Phase 1；
5. 任一生产执行、真实数据、删除或部署继续按 R3 精确审批。
