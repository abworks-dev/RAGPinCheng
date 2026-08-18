# 多引擎视频自动转录 — 总体实施方案

- 状态：**历史总体方案；架构方向已批准，Phase 1～5A/5B 已在后续提交中实施，Phase 5C 与真实生产操作仍需独立审批**
- 风险等级：**R2**（涉及 ASR Provider、配置档案、`app.sqlite` Schema、后台任务、管理端 API/UI、版本发布、索引门禁和单卡 GPU 调度）
- 批准日期：2026-08-01
- 关联决策：[0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)
- Phase 1 详细计划：[多引擎视频自动转录 Phase 1](multi-engine-transcription-phase1.md)
- FunASR 候选专项计划：[FunASR 视频自动转录](funasr-auto-transcription.md)
- faster-whisper 候选材料：[faster-whisper Phase 0 静态预检](faster-whisper-phase0-precheck.md)

> 本方案是自动转录的总体历史基线。FunASR、faster-whisper 及未来模型均作为可插拔候选；候选通过技术验证不自动授权业务集成。当前能力、Profile 状态和未完成工作以 `docs/features/transcript-pipeline.md`、TODO 和受控 workflow 为准。

## 1. 目标

允许管理员上传 MP4 后选择受控的转录配置，由后台调用相应 ASR Provider Adapter 生成引擎中立 Candidate，经唯一 normalizer 生成 Canonical Transcript JSON，再输出确定性 Markdown；同一媒体可保留多个转录版本，只有经审核并进入 `publishing` 的候选版本可以构建候选发布索引，索引成功后才能原子提升为唯一正式版本。

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

- **Provider** 是受控的自动转录引擎适配器，例如 SenseVoice、Contextual Paraformer 或 faster-whisper；Adapter 只把受控执行配置转换为引擎中立 Candidate 或结构化 Failure，Canonical 只能由统一 normalizer 构造；Provider 不负责资格、审核、发布或索引策略。
- **Profile** 是管理员可选择的服务端白名单配置档案，固定 Provider、模型、revision、有效参数和热词版本，并分别关联资格、准入和派生发布策略。
- **Provider 可用性** 是运行环境事实，不属于 Profile 资格状态。
- 管理员只能提交 `profile_id`，不能提交任意模型路径、下载地址、热词正文、底层解码参数或发布策略覆盖。
- 人工 Markdown 是独立受保护的输入路径，不是 Provider，也不是 ASR Profile。

候选 Profile 示例：

```text
system-recommended
funasr-general-zh-v1
funasr-bim-hotword-v1
faster-whisper-zh-v1
```

### 3.2 Profile 资格、准入与可用性

不得用一个 Profile 状态枚举混合三个不同维度：

```text
ProfileQualification：pending_evaluation | experimental | qualification_approved
ProfileAdmission：enabled | disabled | deprecated
ProviderAvailability：available | unavailable
```

规则：

- 只有 `qualification_approved` 才能成为 `system-recommended` 的目标；
- `experimental` 强制派生 `requires_review=true`、`auto_publish=false`、`auto_index=false`，矛盾组合必须由 Schema 拒绝；
- `unavailable` 只描述当前运行环境，显示脱敏原因但不可提交，且不能改变资格；
- `disabled/deprecated` 不影响历史任务和已发布转录稿的只读访问；`deprecated` 禁止新任务和新重试、仅允许旧候选显式人工继续，`disabled` 阻止尚未完成的发布动作；
- 任务保存不可变 Profile 快照，发布时使用快照与当前策略中更严格者；后续资格提升不能取消旧任务已有的审核要求。

任务执行、人工审核、正式发布和索引还必须分别使用 `TranscriptionJobStatus`、`TranscriptionJobStage`、`ReviewStatus`、`PublicationStatus`、`PublicationIndexStatus`，完整类型和值见 Phase 1 详细计划。

### 3.3 多版本、单正式版本

- 同一媒体可以保留多个成功转录历史版本；
- 同一媒体同时最多一个 `pending/running` 转录任务；
- 转录成功只产生候选版本，不覆盖当前正式版本，也不等同于已审核、已发布或已索引；
- 同一媒体同时只能有一个正式发布版本；
- 只有审核通过且进入 `publishing` 的候选版本可以创建绑定 `candidate_version_id` 的 publication index job；
- 候选索引成功后，才原子更新当前正式版本指针并标记 `published`；
- 新版本发布或索引失败时，旧正式版本指针和旧正式索引继续可用；
- 删除真实媒体、版本或 artifacts 仍须单独审批。

### 3.4 Phase 1 与候选评测解耦

统一 Canonical JSON、formatter、Provider Protocol 和 fake fixtures 可以在没有 `qualification_approved` ASR 的情况下开发验证。没有正式引擎时：

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
        execution: TranscriptionExecutionConfig,
        checkpoint_dir: Path,
    ) -> CanonicalTranscript: ...
```

Provider 只接收不可变 `TranscriptionInputRef` 与服务端可信 Registry 解析出的不可变执行配置，不接收 Profile 资格、审核、发布或索引策略。首批自动转录候选适配器：

- `FunASRSenseVoiceProvider`；
- `FunASRContextualParaformerProvider`；
- `FasterWhisperProvider`。

人工 Markdown 继续走现有上传、验证、原始字节保存和索引路径；不注册 `ManualTranscriptProvider`。未来如需导入 Canonical，应使用独立 `ManualTranscriptImporter` 概念。

未通过资格或没有继续价值的适配器可以保留代码和历史证据，但默认不注册或标记为 `disabled`。

### 4.2 Profile Registry

Profile 应由服务端版本化白名单提供，并至少包含：

- `profile_id`、展示名称和说明；
- Provider、模型 ID、固定 revision；
- 影响结果的固定配置和 `config_hash`；
- 热词/词表版本身份，但不向前端返回敏感正文；
- 分开的资格、准入和派生发布策略；experimental 的强制审核、禁止自动发布/索引由 Schema 保证；
- 语言、时间戳、热词等能力声明；
- 独立 Provider 可用性和脱敏不可用原因；
- 最近一次资格评测摘要及证据引用。

运行任务必须保存不可变 Profile 快照；发布时采用任务快照与当前 Profile 策略中更严格者，避免注册表后续变化改写历史含义或放宽审核门禁。

## 5. Canonical Transcript JSON

Provider Adapter 只返回引擎中立 Candidate 或结构化 Failure；只有唯一 normalizer 可以结合同一不可变 input ref、执行配置和 Profile 快照构造 Canonical Transcript。精确字段、严格 Schema、Canonical JSON bytes 和哈希规则只在 [Phase 1 详细实施计划](multi-engine-transcription-phase1.md) 第 7～8 节定义，本总体方案不复制第二套 Schema。

约束：

- Canonical 根对象及所有嵌套对象拒绝额外字段，只包含 JSON-native、引擎中立值；
- Profile 快照只保留受控身份、资格/准入/发布策略快照、版本和哈希，不泄漏模型路径、URL、原始引擎对象或运行时 metrics；
- 时间统一为非负整数毫秒；不强制所有引擎提供置信度，也不跨模型直接比较置信度；
- segment 顺序、重叠、空文本和越界由唯一 normalizer 按冻结算法处理；
- 原始引擎输出可作为受控审计 artifact reference，但不能直接进入 Canonical、formatter 或索引；
- JSON→Markdown formatter 确定性运行，不调用生成式 LLM；同一 Canonical 和显式 title 必须产生字节一致结果；
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

状态必须按领域类型分开：

```text
TranscriptionJobStatus：pending → running → succeeded | failed | cancelled
TranscriptionJobStage：validating_input | transcribing | normalizing | formatting
ReviewStatus：not_required | awaiting_review → review_approved | review_rejected
PublicationStatus：not_published → publishing → published | publication_failed
PublicationIndexStatus：pending → parsing → chunking → embedding → done | failed（仅 candidate transcript 的 publication-only 状态，不替代现有通用 `index_jobs.status`）
```

执行 stage 不得包含审核、发布或索引阶段。发布顺序固定为：

```text
review_gate_satisfied → publishing → candidate publication index done
→ atomically promote current_published_version_id → published
```

必须保持：

```text
ASR succeeded ≠ review_approved ≠ published ≠ index done
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
- 对满足 `review_gate_satisfied` 的候选发起发布，创建候选定向索引；索引成功后再原子切换正式版本。

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

详细范围、类型不变量和完成标准见 [Phase 1 详细实施计划](multi-engine-transcription-phase1.md)。本阶段仅包含：

- Candidate/ProviderFailure、Canonical JSON 严格 Schema，以及 Candidate → Canonical 的唯一 normalizer 入口；
- Provider Protocol、可信 Profile Definition、深层不可变执行配置和最小请求 Schema；
- 正交状态类型、experimental policy 和纯发布 guard；
- 时间戳/segment normalizer；
- 确定性 JSON→Markdown formatter；
- 固定夹具、三个 fake providers 和契约测试；
- 不修改人工 Markdown 路径；
- 不安装真实 ASR，不提取音频，不连接网络、生产服务、数据库或 UI。

### Phase 2 — 任务、版本与恢复

- `transcription_jobs`、`transcript_versions` 和正式版本指针；
- 数据库发布事务、候选索引完成后的正式指针原子切换、候选/旧索引隔离和失败时旧索引保护；
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
→ Markdown → 审核 → publishing → 候选索引 → 原子发布 → 检索 → 引用 → 跳播
```

同时验证人工稿不退化、多版本、失败隔离、实验引擎审核门禁和旧正式版本保护。

### Phase 6 — 生产灰度

- 只允许经过审批的 `qualification_approved` Profile；
- 自动转录部署开关默认关闭；
- 明确允许数据分类、容量、监控、停止和回滚；
- 生产操作和真实数据按 R3 逐项审批。

## 10. 验证要求

### Phase 1

- Schema 合法/非法输入；
- 空候选 segment 集合、空文本、重叠和越界时间戳；
- 跨块 offset、毫秒取整、中文 UTF-8；
- 过短合并、超长拆分和重复执行字节一致性；
- 三个自动转录 fake Provider fixtures；人工 Markdown 只验证现有路径未进入修改集，不伪装为 Provider；
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
2. 审批并按 [Phase 1 详细实施计划](multi-engine-transcription-phase1.md) 实施纯 Python 契约；
3. Phase 1 完成并验证后，再分别审批 Phase 2～Phase 6；
4. faster-whisper 是否继续资格评测作为独立事项决定，不阻塞 Phase 1；
5. 任一生产执行、真实数据、删除或部署继续按 R3 精确审批。
