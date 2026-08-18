# 多引擎视频自动转录 Phase 1 — 引擎无关契约详细实施计划

- 状态：**历史 Phase 1 计划；契约代码已在后续提交中实施，当前事实以视频转录功能文档和源码为准**
- 风险等级：**R2**（定义跨模块类型、运行时 Schema、状态边界和未来发布门禁）
- 编写日期：2026-08-01
- 上位方案：[多引擎视频自动转录总体实施方案](multi-engine-auto-transcription.md)
- 关联决策：[0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)
- 历史候选方案：[FunASR 视频自动转录](funasr-auto-transcription.md)

> 本文件只记录 Phase 1 的纯 Python 契约、Schema、归一化、确定性 formatter 与测试边界。真实 ASR、生产部署和真实数据操作仍不由本历史计划授权；当前契约以源码和 feature 文档为准。

## 1. 目标

Phase 1 建立一个不依赖 FunASR、faster-whisper、数据库或服务运行时的转录核心契约，使未来任意受控 ASR Provider 都能：

1. 接收服务端可信 Profile 解析出的不可变执行配置；
2. 通过 Provider Adapter 返回引擎中立、严格校验的 Candidate 或结构化 ProviderFailure；
3. 由唯一 normalizer 入口把 Candidate 转换为 Canonical Transcript；
4. 由确定性 formatter 生成当前 `src/chunk.py` 可解析的 Markdown；
5. 通过 fake Provider、固定 fixture 和契约测试验证，而不加载真实模型。

Phase 1 同时冻结未来集成必须遵守的边界：Profile 资格、准入、Provider 可用性、任务执行、人工审核、发布和索引不得混用；转录成功不得被解释为已经审核、发布或索引。

## 2. Phase 1 范围

### 2.1 包含

- 纯 Python 枚举、不可变值对象和运行时 Schema；
- 冻结 `types/candidate/profile → provider_protocol → Provider Adapter → pipeline → normalizer → Canonical → formatter` 的单向模块依赖、唯一公共执行入口与结果流；
- `pipeline.execute_transcription` 唯一公共调用入口、`TranscriptionProvider` Protocol、Provider Candidate 与结构化 ProviderFailure；
- 可信 `TranscriptionProfileDefinition`、`ProfileRegistry/resolve_profile` 与不可变 `TranscriptionExecutionConfig`；
- 未来不可信 `StartTranscriptionRequest` 的最小边界 Schema；
- ProviderCapabilities、TranscriptionInputRef、TranscriptionExecutionConfig、CandidateSegment、ProfileSnapshot、Canonical Transcript、normalizer-only warning、artifact reference、严格格式接受集合和 provider-specific 可信配置 Schema；
- 引擎中立的时间戳与 segment normalizer；
- Canonical JSON → Markdown 确定性 formatter；
- Profile policy 派生、发布有效策略合并和纯状态转换 guard；
- 固定 JSON fixture、Fake Provider、第三 Provider 扩展契约测试；
- 仅针对上述纯函数和类型的单元/契约测试。

### 2.2 明确排除

Phase 1 不得新增、修改或调用：

- `api/**`、`frontend/**`；
- SQLite、Qdrant、数据库 Schema、迁移或正式版本指针持久化；
- 任务 worker、任务队列、checkpoint 持久化、API endpoint 或 UI；
- 网络、HTTP、生产服务或外部 API；
- FFmpeg、PyAV、音频提取或媒体转码；
- FunASR、faster-whisper、torch、CUDA 或真实引擎导入；
- 模型下载、模型路径探测、GPU 检查或真实推理；
- 任何真实 ASR 依赖；
- 当前人工 Markdown 上传、验证、原始字节保存和索引路径；
- LLM 校正、说话人分离、逐字字幕或多引擎投票合并。

旧 FunASR 方案中“Phase 1 包含音频提取适配器”的范围属于历史方案，已被本计划取代。音频提取和真实 Provider 适配必须在后续阶段另行提交方案与审批。

## 3. 职责边界与冻结依赖图

### 3.1 模块依赖、公共入口和结果流

Phase 1 冻结 `src/transcription/pipeline.py` 为唯一公共 Provider 调用入口和 Candidate → Canonical 流程所有者。应用代码不得直接调用 Adapter 的 `transcribe()` 后自行拼接 normalizer/formatter；formatter 只消费 pipeline 成功返回的 Canonical，不参与 Provider 调用。

唯一执行结果流为：

```text
caller
→ pipeline.execute_transcription
  → provider_protocol.TranscriptionProvider
  → Provider Adapter
  → ProviderCandidate | ProviderFailure
  ├─ ProviderFailure → 失败分支结束
  └─ ProviderCandidate → normalizer → CanonicalTranscript
CanonicalTranscript + 显式受控 formatter context
→ formatter
→ automatic candidate Markdown bytes
```

为保持现有“Provider 不接收资格/审核/发布策略”的边界，公共函数采用下列冻结签名；相对三参数草案只增加必需的 keyword-only `profile_snapshot`，不得通过全局 Registry、隐藏单例或把策略塞入 Provider-facing execution config 来补取该值：

```python
def execute_transcription(
    provider: TranscriptionProvider,
    input_ref: TranscriptionInputRef,
    execution_config: TranscriptionExecutionConfig,
    *,
    profile_snapshot: ProfileSnapshot,
) -> CanonicalTranscript | ProviderFailure: ...
```

`pipeline.py` 独占以下职责：

1. 严格运行时校验 `input_ref`、`execution_config` 和 `profile_snapshot`，并校验三者的 profile/provider/version/fingerprint 交叉一致性；
2. 在调用前生成 `input_ref` 与 `execution_config` 的 Canonical serialization bytes 和 fingerprint；
3. 仅通过 `TranscriptionProvider` Protocol 调用 Provider；
4. 只捕获计划明确允许归一化的 Provider 异常，其他契约错误失败关闭；
5. 验证返回值严格为 `ProviderCandidate` 或 `ProviderFailure`，拒绝 Canonical、Markdown、私有对象或其他联合成员；
6. 调用后重新生成 serialization/fingerprint 并做常量时间比较，证明 `input_ref` 与 `execution_config` 未改变；
7. `ProviderFailure` 原样进入失败分支，不调用 normalizer/formatter，不生成 Canonical 或 Markdown；
8. `ProviderCandidate` 只能调用 normalizer 的唯一公开入口生成 `CanonicalTranscript`；
9. 不调用 formatter；formatter 是成功结果之后的独立纯渲染步骤；
10. 禁止 Adapter、formatter 或其他核心模块绕过 pipeline 组成完整工作流。

失败分支在 `ProviderFailure` 结束，不得制造空 Canonical、空 Markdown 或伪成功状态。同一个已校验、不可变的 `TranscriptionInputRef`、`TranscriptionExecutionConfig` 与 `ProfileSnapshot` 必须进入 pipeline；Candidate 不得携带、覆盖或重新解释 `media_id`、输入哈希、输入种类、大小、输入时长或 Profile 身份。

冻结模块与允许依赖如下：

| 模块 | 定义/职责 | 允许导入 |
| --- | --- | --- |
| `types.py` | JSON primitive、基础枚举、`TranscriptionInputRef`、warning/artifact 基础类型、严格格式校验、哈希与序列化工具 | Python 标准库 |
| `candidate.py` | `CandidateSegment` 及 Candidate 时间/文本值类型；不定义 Provider 结果联合 | `types.py` |
| `profile.py` | `ProfileRegistry`、`resolve_profile`、`ProfileOperation`、Profile 定义/拒绝结果、provider-specific 可信配置、`TranscriptionExecutionConfig`、`ProfileSnapshot`、策略派生 | `types.py` |
| `provider_protocol.py` | 仅定义 `TranscriptionProvider`、`ProviderCandidate`、`ProviderFailure`、`ProviderResult`、`ProviderCapabilities`、Provider error code 与 transient/permanent 分类 | `types.py`、`candidate.py`、仅作为类型使用的 `profile.py` |
| Provider Adapter | 实现 Protocol，把私有引擎结果转换为 Candidate/Failure | `provider_protocol.py`、`candidate.py`、对应 provider-specific 可信配置类型 |
| `canonical.py` | `CanonicalTranscript`、严格 Schema 与 Canonical JSON bytes | `types.py`、仅作为值类型使用的 `profile.py` |
| `normalizer.py` | `ProviderCandidate → CanonicalTranscript` 的唯一转换入口 | `types.py`、`candidate.py`、`provider_protocol.py`、`profile.py`、`canonical.py` |
| `pipeline.py` | 唯一公共 Provider 调用、契约验证、不可变检查、Failure 分流和 Candidate → Canonical 编排 | `types.py`、`profile.py`、`provider_protocol.py`、`normalizer.py`、`canonical.py` |
| `formatter.py` | `CanonicalTranscript + FormatterContext → Markdown bytes` | `canonical.py`、`types.py` |
| `policy.py` | 正交状态和纯 guard | `types.py`、`profile.py` |

强制无环规则：

- `types.py`、`candidate.py`、`profile.py`、`canonical.py` 不得反向导入 `provider_protocol.py`、Adapter、`pipeline.py`、`normalizer.py` 或 `formatter.py`；
- `provider_protocol.py` 不执行 Provider，不调用 normalizer/formatter/pipeline，不导入具体 Adapter；
- `normalizer.py` 不导入 pipeline/formatter/具体 Adapter；`pipeline.py` 可以导入 normalizer，反向禁止；
- `canonical.py` 不接收或导入 `CandidateSegment`/`ProviderCandidate`；只有 normalizer 可以构造 Canonical；
- `formatter.py` 不得导入 Candidate、Profile Registry、Provider Protocol、pipeline 或 Adapter；
- 除 `pipeline.py` 外，`src/transcription/**` 的应用执行路径不得调用 `TranscriptionProvider.transcribe`；normalizer 的直接调用只允许其单元测试和 pipeline；
- 新 Provider 只能新增叶子 Adapter、严格可信配置类型/Registry 登记项和 fixture，不得修改 pipeline、normalizer、canonical、formatter 或 policy，也不得增加 `provider_key` 名称分支；
- 使用 `TYPE_CHECKING` 不能掩盖运行时循环；静态测试同时检查普通导入、相对导入和动态导入。

### 3.2 Provider 成功与失败契约

`provider_protocol.py` 只定义协议与结果类型，不执行 Provider、不调用 normalizer、不调用 formatter。Provider Adapter 只负责引擎适配：

- 声明稳定 `provider_key` 和 `ProviderCapabilities`；
- 接收由可信 Registry 解析、校验并深层冻结的 `TranscriptionExecutionConfig`；
- 把引擎结果转换为引擎中立 `ProviderCandidate`，或返回结构化 `ProviderFailure`；
- 可以返回受控 `ArtifactReference`，但不得把原始输出塞入 Candidate、Canonical 或 warning；
- 不负责 Profile 资格/Registry 准入、审核、发布、索引、管理员参数解析、数据库、API、UI 或生产服务。

冻结 Protocol：

```python
class TranscriptionProvider(Protocol):
    @property
    def provider_key(self) -> str: ...

    def capabilities(self) -> ProviderCapabilities: ...

    def transcribe(
        self,
        input_ref: TranscriptionInputRef,
        execution: TranscriptionExecutionConfig,
    ) -> ProviderResult: ...
```

其中 `ProviderResult = ProviderCandidate | ProviderFailure` 是封闭联合；不得注册第三个结果成员。

成功与失败规则：

1. Provider **不得直接返回或构造 `CanonicalTranscript`**；`pipeline.execute_transcription` 是唯一公共调用入口，且只由 `normalizer.normalize_candidate(input_ref, candidate, profile_snapshot, execution_config)` 生成 Canonical。
2. `ProviderCandidate` 必须通过严格 Candidate Schema 后才能进入 normalizer；非法 Candidate 由 pipeline 归一化为 `ProviderFailure(error_code="invalid_provider_output", classification="permanent")`，不得让私有异常或半合法对象穿透。
3. Candidate 的 `provider_key`/`language` 必须分别与 execution 完全一致，`duration_ms` 必须与同一 `input_ref.duration_ms` 一致；任一不一致均为永久 `invalid_provider_output`，Candidate 不得覆盖输入或 Profile 身份。
4. `ProviderFailure` 至少包含：`provider_key`、稳定有限枚举 `error_code`、`classification = transient | permanent`、可空 `timeout_ms`、零或多个 `artifact_refs`。不得包含任意异常对象、堆栈、路径、URL、密钥或自由 debug 字典。
5. `error_code` Phase 1 固定为：`invalid_input`、`provider_unavailable`、`provider_timeout`、`transient_provider_error`、`permanent_provider_error`、`invalid_provider_output`、`execution_config_mutated`、`provider_contract_violation`。
6. `provider_timeout` 必须为 `transient`，并记录与本次执行配置相同的正整数 `timeout_ms`；其他 failure 的 `timeout_ms` 必须为 null。`retryable` 不单独存储，由 `classification == transient` 派生。
7. pipeline 只捕获并归一化以下异常类别：显式 Provider timeout → `provider_timeout`；显式可重试 Provider error → `transient_provider_error`；显式永久 Provider error → `permanent_provider_error`。未知异常、返回非法联合成员或异常对象泄漏统一失败关闭为永久 `provider_contract_violation`；不得持久化异常正文/stack。
8. Phase 1 只冻结 timeout 表达和异常归一化，不实现线程、subprocess、真实 watchdog 或进程终止；Fake Provider 通过确定性 fixture 返回/触发 timeout。
9. pipeline 调用前后必须分别比较 `input_ref` 与执行配置的 Canonical serialization bytes，并重算 fingerprint；任一变化均覆盖原结果并返回永久 `execution_config_mutated`。
10. `ProviderFailure` 被 pipeline 校验后保持字段值不变进入失败分支；不得生成 Canonical/Markdown，也不得把失败改写成空 Candidate。
11. 三个 Fake Provider 的参数化矩阵必须全部调用同一个 `pipeline.execute_transcription`，共同覆盖：合法成功、transient/permanent 失败、timeout、非法输出、尝试修改 `input_ref` 或执行配置；不得读取真实媒体。
12. `provider_protocol.py` 及核心执行模块不得基于 `provider_key` 的具体字符串分支；差异只能位于 Adapter、严格配置类型和 Registry 登记数据。

### 3.3 Profile Registry 与白名单解析

Profile 是服务端维护的受控白名单定义，不是 Provider 别名，也不是管理员可自由填写的参数集合。`profile.py` 是下列内容的唯一所有者：

- `ProfileRegistry`：以严格 `profile_id` 为键保存冻结的 `TranscriptionProfileDefinition`；
- `ProfileOperation`：封闭枚举 `new_attempt`、`retry`、`continue_existing`、`publish_existing`；
- `resolve_profile(profile_id, operation)`：只做登记查找和 admission 操作门禁，返回冻结的成功结果或结构化 `ProfileResolutionFailure`；
- 未登记、disabled、deprecated-for-operation 等稳定拒绝 code；
- Profile 定义、可信 provider-specific 配置、`TranscriptionExecutionConfig`、`ProfileSnapshot` 和策略派生。

`TranscriptionProfileDefinition` 分开保存：

- `profile_id`、显示名称、说明和 `provider_key`；
- 严格类型化、服务端可信的 provider-specific 配置；
- `ProfileQualification`、`ProfileAdmission` 和派生发布策略；
- 定义版本、配置哈希、Adapter/Schema/normalizer/formatter 版本和证据引用。

Provider-specific 配置必须是登记过的 discriminated union 成员：每个成员使用 `frozen=True, slots=True` 的专用类型和严格运行时 Schema，拥有固定 `config_kind`/`config_version`；禁止 `dict[str, Any]`、`Mapping[str, object]`、`extra` 或未登记键。新增 Provider 只新增叶子配置类型和 Registry 登记，不在核心工作流增加引擎名分支。

Registry/admission 固定矩阵：

| Registry/Admission | `new_attempt` | `retry` | `continue_existing` | `publish_existing` |
| --- | --- | --- | --- | --- |
| 未登记 | 拒绝 `profile_not_registered` | 拒绝 `profile_not_registered` | 拒绝 `profile_not_registered` | 拒绝 `profile_not_registered` |
| `enabled` | Registry 通过；继续独立判断 qualification/policy/availability | Registry 通过；继续独立判断 qualification/policy/availability | Registry 通过；继续任务状态 guard | Registry 通过；继续 review/effective policy guard |
| `deprecated` | 拒绝 `profile_deprecated_for_operation` | 拒绝 `profile_deprecated_for_operation` | 仅已处于 running 的同一原 attempt 可通过；不得创建新 attempt | 仅管理员显式动作可继续，并强制 `auto_publish=false`、`auto_index=false`、采用快照与当前策略较严者 |
| `disabled` | 拒绝 `profile_disabled` | 拒绝 `profile_disabled` | 拒绝正常继续；只允许 Registry 外的安全终止/保存诊断路径 | 拒绝 `profile_disabled`；已发布历史版本只读不受影响 |

矩阵不得改变第 4 节已批准语义。`resolve_profile` 的成功只表示“已登记且 admission 对该 operation 未拒绝”，**不表示 qualification 已批准、Provider 当前可用、审核通过或允许发布**。Qualification guard、admission 结果和 `ProviderAvailability` 必须正交计算；Availability 是瞬时运行事实，不写回 Registry 的持久资格。

`ProfileResolutionFailure` 只包含严格 `profile_id`、`operation` 和有限 `reason_code`，不得包含路径、URL、模型名、热词、decoder 参数或自由 message/debug。管理员请求序列化仍然只能包含 `profile_id`；resolver 不读取、推断或接收任意模型路径/目录、URL/revision、热词/词表或 beam/temperature/VAD/decoder 参数。

### 3.4 可用性

`ProviderAvailability` 是运行环境的瞬时事实，例如依赖缺失或服务不可达；它不属于 Profile 资格，也不能自动升级产品资格。

```text
qualification 决定产品资格
admission 决定新建、重试与继续动作
availability 描述当前环境是否能执行
```

三个轴必须使用不同字段和枚举。

### 3.5 管理员请求边界

未来 API 的不可信请求类型与可信 Registry 类型必须分离：

```python
@dataclass(frozen=True, slots=True)
class StartTranscriptionRequest:
    profile_id: str
```

运行时 Schema 必须拒绝额外字段。管理员请求不得包含模型路径/目录、模型或仓库 URL、revision 覆盖、热词/词表正文、任意文件路径、beam/temperature/VAD/decoder 参数，或 `requires_review`、`auto_publish`、`auto_index` 等策略覆盖。`profile_id` 只能解析到服务端已登记且当前允许使用的 Profile；前端隐藏字段不能替代后端拒绝。

### 3.6 核心边界 Schema

以下字段集合在 Phase 1 冻结；所有对象均拒绝额外字段，所有集合均使用 tuple 或其他只读值，禁止可变 list/dict/set 穿入执行边界：

| 类型 | 冻结字段与约束 |
| --- | --- |
| `ProviderCapabilities` | `provider_key`；排序且无重复的 `supported_languages`；排序且无重复的 `accepted_input_kinds`；`emits_segment_timestamps`；`emits_confidence`；可空正整数 `max_duration_ms`。不得包含模型路径或运行时探测结果。类型定义归 `provider_protocol.py`。 |
| `TranscriptionInputRef` | `media_id`、`input_kind`、`content_sha256`、`size_bytes`、`duration_ms`。只表达身份与完整性，不含 Path、URL、文件句柄或 bytes。Phase 1 仅使用虚构哈希 fixture。 |
| `ProviderTrustedConfig` | `config_kind`、`config_version` 和登记过的冻结专用 payload；严格 discriminated union，不接受自由映射。 |
| `TranscriptionExecutionConfig` | `profile_id`、`provider_key`、`profile_definition_version`、`provider_adapter_version`、`language`、`timeout_ms`、`provider_config`、`normalizer_config`、`canonical_schema_version`、`normalizer_version`、`formatter_version`、`execution_fingerprint`。不含 qualification/review/publication/index policy。 |
| `CandidateSegment` | `original_position`、十进制字符串 `start_value`/`end_value`、`time_unit = milliseconds | seconds`、`text`、可空有限 `confidence`。不接受 float 时间、引擎对象或任意 metadata。 |
| `ProviderCandidate` | `provider_key`、`language`、`duration_ms`、非空 `segments` tuple、`artifact_refs` tuple。**没有 `warnings` 字段**，不得包含 Canonical ID、输入身份、原始引擎对象或任意 `extra/raw/provider_warning`。类型定义归 `provider_protocol.py`。 |
| `ProviderFailure` | `provider_key`、`error_code`、`classification`、可空 `timeout_ms`、`artifact_refs` tuple；错误详情只用稳定 code 表达。 |
| `ProfileSnapshot` | `profile_id`、`provider_key`、`profile_definition_version`、`config_hash`、`qualification`、`admission`、严格 `release_policy`（仅 `requires_review`、`auto_publish`、`auto_index`）、`provider_adapter_version`、`canonical_schema_version`、`normalizer_version`、`formatter_version`、`execution_fingerprint`。 |
| `TranscriptWarning` | `code`、可空 `primary_original_position`、`related_original_positions` tuple；无自由 message/debug 字段；只能由 normalizer 构造。 |
| `ArtifactReference` | `artifact_id`、`kind`、`content_sha256`、`size_bytes`；严格接受集合见 `3.7`，禁止绝对/相对路径、URL、原始 bytes 和访问凭据。 |
| `ProfileResolutionFailure` | `profile_id`、`operation`、`reason_code`；无自由 message/debug/参数字段。 |

深层不可变要求：

- `frozen dataclass` 仅是第一层；嵌套对象也必须是冻结类型，集合必须 tuple 化，构造时拒绝或复制后冻结调用方可变容器；
- 运行时 Schema 完成后分别生成 `input_ref`、执行配置的 Canonical serialization bytes 和 fingerprint；pipeline 调用 Provider 前后重新生成并做常量时间字节比较；
- Provider 的赋值、嵌套修改、别名容器修改三类尝试均须由测试证明不能改变执行对象；检测到差异按 `execution_config_mutated` 失败；
- `ProfileSnapshot` 单独传给 pipeline/normalizer，不传给 Provider；snapshot 与 execution config 的 profile/provider/version/fingerprint 必须严格一致。

### 3.7 严格格式接受集合

本节是运行时 Schema 与测试的唯一格式规则。实现者不得替换正则、放宽大小写或自行引入第三方验证库。所有正则使用 Python 标准库 `re.fullmatch(..., flags=re.ASCII)`；长度按 Unicode code point 计，但接受字符均为 ASCII。

1. **UUID（`media_id` 等 UUID 字段）**
   - 输入只能是 36 字符、小写、带连字符的 `8-4-4-4-12`：`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`；大写、无连字符、花括号、URN 前缀拒绝。
   - 用标准库 `uuid.UUID(value)` 解析；`parsed.int == 0` 的 nil UUID 拒绝；不另行限制 UUID version。
   - `str(parsed)` 必须与原输入逐字节相等；Canonical serialization 只输出该 `str(parsed)`。
2. **slug**
   - `profile_id`：长度 3–64；`[a-z][a-z0-9]*(?:-[a-z0-9]+)*`。只允许小写字母、数字和单连字符分段；不允许下划线或点。
   - `provider_key`：长度 2–32；`[a-z][a-z0-9]*(?:-[a-z0-9]+)*`。只允许小写字母、数字和单连字符分段；不允许下划线或点。
   - `artifact_id`：长度 1–128；`[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*`。允许小写字母、数字、连字符、下划线，并允许单点分隔的非空组件；禁止前导/尾随点和 `..`。
   - 三类 slug 均拒绝 `/`、`\`、空白、`:`、`?`、`#`、`%`、`://`、前导 `.`/`..` 或任何可解释为绝对/相对路径或 URL 的形式；不得先做 trim、大小写折叠、URL decode 或路径 normalization 后再接受。
3. **language**
   - 只接受精确 `und`，或 `[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?`。
   - canonical form 固定为：primary language 2–3 个小写 ASCII 字母；可选 script 为首字母大写加 3 个小写字母；可选 region 为 2 个大写字母或 3 位数字。示例：`en`、`zh-CN`、`sr-Latn`、`es-419`。
   - `und` 只允许裸值；不允许 `und-CN`。不接受 extension、variant、grandfathered、private-use（含 `-u-`、`-t-`、`-x-`）或超过该 grammar 的标签。
   - 未知但符合 grammar 的标签按格式合法接受，不查询 IANA Registry、不联网；输入必须已是 canonical case，Schema 不自动改写大小写。
4. **`ArtifactReference.kind`**
   - Phase 1 完整有限枚举仅为：`provider_diagnostic`、`provider_timing`、`provider_vad`、`provider_tokens`、`provider_confidence`。
   - 未知值严格拒绝；kind 只描述受控 artifact 类别，不得使用 Provider 名、模型名、Python 类名、MIME/路径/URL 或任意私有类型逃逸。新增 kind 必须走后续 Schema 版本评审。
5. **Canonical Schema version**
   - 当前唯一接受和输出值为固定 ASCII 字符串 `canonical-transcript/1`。
   - `canonical-transcript/1.0`、`canonical-transcript/1.1`、`canonical-transcript/2`、大小写变体、前后空白、缺少前缀/斜杠或其他格式全部拒绝；不存在“兼容未知 minor”的隐式规则。
   - Canonical JSON 中 `schema_version` 与 `ProfileSnapshot.canonical_schema_version` 必须都逐字节等于 `canonical-transcript/1`；serialization 不做版本别名或规范化。
6. **SHA-256 与数值格式**
   - 所有 SHA-256 字段严格为 `[0-9a-f]{64}`；大写、`0x` 前缀、base64 和错误长度拒绝。
   - confidence 只接受 JSON number 语义的有限值 `0 <= value <= 1`，bool 拒绝；时间和大小字段规则按 `7.3`/`8.1`。

## 4. 正交状态与发布语义

Phase 1 只定义领域类型和纯 guard，不实现数据库或后台任务。

| 类型 | 冻结值 | 唯一职责 |
| --- | --- | --- |
| `ProfileQualification` | `pending_evaluation`、`experimental`、`qualification_approved` | 产品资格与评测结论 |
| `ProfileAdmission` | `enabled`、`disabled`、`deprecated` | 是否允许创建、重试或继续动作 |
| `ProviderAvailability` | `available`、`unavailable` | 当前运行环境可执行性 |
| `TranscriptionJobStatus` | `pending`、`running`、`succeeded`、`failed`、`cancelled` | 转录执行生命周期 |
| `TranscriptionJobStage` | `validating_input`、`transcribing`、`normalizing`、`formatting` | running 内部阶段 |
| `ReviewStatus` | `not_required`、`awaiting_review`、`review_approved`、`review_rejected` | 人工审核生命周期 |
| `PublicationStatus` | `not_published`、`publishing`、`published`、`publication_failed` | 正式发布生命周期 |
| `PublicationIndexStatus` | `pending`、`parsing`、`chunking`、`embedding`、`done`、`failed` | **仅用于 candidate transcript 的 publication-only 索引过程** |

`PublicationIndexStatus` 是 Phase 1 领域 guard 的候选发布索引状态，**不声称替代、复用或迁移现有通用 `index_jobs.status`**。Phase 2 必须单独设计两者的持久化映射和兼容性，不能因同名值直接共用数据库列。

强制语义：

- 执行 stage 不包含审核、发布或索引阶段；Provider 返回值不包含 Review/Publication/PublicationIndex 状态；
- `TranscriptionJobStatus.succeeded` 只表示严格 Canonical 和草稿 Markdown 已成功生成；
- `review_gate_satisfied` 的唯一定义为：

```text
review_status == review_approved
OR (review_status == not_required AND effective_policy.requires_review == false)
```

- 当 `effective_policy.requires_review == true` 时，`not_required` 不满足门禁且应视为非法组合；`awaiting_review`/`review_rejected` 永远不满足；
- `transcription succeeded ≠ review approved ≠ published ≠ publication indexed`；任一成功状态不得隐式推进其他轴。

`ProfileAdmission` 语义冻结为：

| admission | 新任务 | 已运行任务 | 失败后的重试 | 已有成功候选的审核/发布 |
| --- | --- | --- | --- | --- |
| `enabled` | 允许（仍需资格/可用性 guard） | 允许继续 | 可按策略创建新 attempt | 可按有效策略继续 |
| `deprecated` | 禁止 | 已经 running 的原 attempt 可完成并保存候选 | 禁止创建新 attempt | 只允许管理员显式审核/发布；强制 `auto_publish=false`、`auto_index=false`，并继续采用快照与当前策略较严者 |
| `disabled` | 禁止 | 允许安全终止或保存诊断，但不得进入后续发布 | 禁止 | 阻止尚未完成的发布/promote；已 published 的历史版本保持可读，不在 Phase 1 删除 |

“重试”始终是新 execution attempt，不能借用旧任务 ID 绕过 deprecated/disabled。Phase 1 通过纯 guard 验证这些语义，不实现取消、持久化或历史版本读取。

## 5. Experimental Profile 强制策略

### 5.1 构造不变量

Profile Schema 必须强制：

```text
qualification == experimental
⇒ requires_review == true
⇒ auto_publish == false
⇒ auto_index == false
```

这些值必须从资格和受控策略派生；若快照为了序列化保留派生字段，加载时必须重新计算并拒绝矛盾组合。`deprecated` 还会在有效策略层强制关闭 auto publish/index，但不改变任务创建时的 qualification 事实。

### 5.2 不可变任务快照

任务创建时保存 `ProfileSnapshot`，包含 `3.6` 冻结字段。Provider 只接收 `TranscriptionInputRef` 与 `TranscriptionExecutionConfig`，不接收资格、审核、发布或索引策略；normalizer 通过独立参数接收同一 input ref 与快照并写入 Canonical。

### 5.3 发布时采用更严格策略

未来发布 guard 必须组合任务快照与当前 Profile：

```text
effective.requires_review = snapshot.requires_review OR current.requires_review
effective.auto_publish    = snapshot.auto_publish AND current.auto_publish
effective.auto_index      = snapshot.auto_index AND current.auto_index
```

当前 `disabled` 阻止未完成发布；当前 `deprecated` 采用第 4 节的显式人工继续语义；后续资格提升不能追溯性取消旧任务审核要求；任何冲突失败关闭。

## 6. 发布与候选索引契约

Phase 1 不实现版本表、数据库事务或真实索引任务，只冻结 publication-only 纯 guard：

```text
review_gate_satisfied
→ PublicationStatus.publishing
→ candidate PublicationIndexStatus.done
→ promote_allowed == true
→ （Phase 2：原子切换 current_published_version_id）
→ PublicationStatus.published
```

规则：

1. 只有 `review_gate_satisfied` 且处于 `publishing` 的候选可进入 publication-only candidate index 流程。
2. 纯 guard 的输入必须包含明确 `candidate_version_id`、Canonical/Markdown SHA-256 和目标索引身份；不得隐式读取当前正式版本。
3. Phase 1 只验证：当 `PublicationIndexStatus != done` 时 `promote_allowed == false`；`pending/parsing/chunking/embedding/failed` 全部不能 promote。
4. Phase 1 不创建/修改索引，不更新正式指针，不模拟事务，也不能证明索引失败时旧正式索引确实保持不变。
5. 数据库事务、正式指针原子切换、候选与旧索引的隔离、失败回滚和旧索引保护全部移交 Phase 2，并须独立 R2 审批和持久化测试。
6. `auto_index` 不是“转录成功后立即索引”；它只影响已满足审核和发布门禁的未来候选发布编排。

## 7. Canonical Transcript Schema

### 7.1 顶层结构

Canonical Transcript 是版本化、引擎中立的 JSON 文档，只能由 normalizer 构造。逻辑字段冻结为：

```json
{
  "schema_version": "canonical-transcript/1",
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "input_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "profile_snapshot": {
    "profile_id": "fake-bim-v1",
    "provider_key": "fake-alpha",
    "profile_definition_version": "1",
    "config_hash": "1111111111111111111111111111111111111111111111111111111111111111",
    "qualification": "experimental",
    "admission": "enabled",
    "release_policy": {"requires_review": true, "auto_publish": false, "auto_index": false},
    "provider_adapter_version": "1",
    "canonical_schema_version": "canonical-transcript/1",
    "normalizer_version": "1",
    "formatter_version": "1",
    "execution_fingerprint": "2222222222222222222222222222222222222222222222222222222222222222"
  },
  "language": "zh-CN",
  "duration_ms": 120000,
  "segments": [
    {"id": 0, "start_ms": 0, "end_ms": 4200, "text": "今天介绍钢结构施工的基本要求。", "confidence": null}
  ],
  "warnings": [],
  "artifact_refs": []
}
```

根对象、`profile_snapshot`、segment、warning 和 artifact reference 均使用严格字段集合，等价 JSON Schema 设置 `additionalProperties: false`。

### 7.2 允许和禁止的值

Canonical 只允许 JSON object/array/string/integer/boolean/null、有限枚举和格式化字符串。禁止 FunASR result/chunk、faster-whisper segment/word/generator、Path、Enum/dataclass 实例、tuple/set/bytes、numpy/tensor、token/logit/beam/VAD/debug，以及未声明 `metadata`/`extra`/`raw`/`provider_data`。

运行时对象必须经显式 `to_json_dict()` 生成 JSON-native 数据，再做严格边界校验；测试不得因为 Python 对象“可 json.dumps”就视为合法。

### 7.3 格式与数值不变量

- UUID、`profile_id`、`provider_key`、`artifact_id`、SHA-256、language、`ArtifactReference.kind` 和 Schema version 必须逐项符合 `3.7` 的唯一接受集合；实现不得另选 regex 或 canonicalization；
- `schema_version` 与 snapshot 版本必须精确为 `canonical-transcript/1`；
- `duration_ms`、`start_ms`、`end_ms` 是非负整数，bool 不得冒充整数；
- 每段 `end_ms > start_ms` 且 `end_ms <= duration_ms`；ID 从 0 连续唯一；
- text 去除首尾 Unicode 空白后非空；confidence 为 null 或有限 [0,1] 数值；
- segments 非空；空 Candidate 或全空文本必须结构化失败。

### 7.4 Warning 与 artifact

`ProviderCandidate` **没有 warnings 字段**，Provider 不得产生 `TranscriptWarning`，也不得提前提交 `segment_overlap`、`duplicate_segment_dropped` 或其他 normalizer 专属 warning。Canonical warning 只能由 normalizer 根据 `8` 的固定算法构造；code 固定为 `empty_segment_dropped`、`duplicate_segment_dropped`、`segment_overlap`、`short_segment_merged`、`long_segment_split`，只记录稳定 code 和原始位置，不存自由文本。

Provider 观察到的调试、VAD、token、置信度或私有诊断只能引用为 `3.7` 有限 kind 的独立 `ArtifactReference`；若无法成功产生合法 Candidate，则进入 `ProviderFailure`。不得增加 `extra`、`raw`、`provider_warning`、自由 metadata 或 Provider 私有 kind 作为逃逸字段。

原始引擎输出与 Canonical 分离。formatter、policy、发布 guard 和索引不得读取 artifact 内容。Phase 1 不写 artifact 文件。

## 8. Normalizer 与确定性算法

Normalizer 的唯一公开转换入口为 `normalize_candidate(input_ref, candidate, profile_snapshot, execution_config) -> CanonicalTranscript`。应用执行路径只能由 `pipeline.execute_transcription` 调用它；直接调用仅允许 normalizer 单元测试。Normalizer 从 input ref 写入 `media_id`/`input_sha256`，校验 Candidate 与 execution/profile 身份一致性，并独占全部 Canonical warning 生成。算法冻结如下：

### 8.1 时间换算与文本预处理

1. `start_value`/`end_value` 是不带指数、最多 6 位小数的非负十进制字符串；用 `Decimal` 精确解析。
2. `milliseconds` 直接取值；`seconds` 精确乘 1000；结果统一使用 `ROUND_HALF_UP` 舍入到整数毫秒（恰好半毫秒向远离 0 的方向，负值已提前拒绝）。
3. 不支持的单位、NaN/Infinity、负值、`end <= start`、越过 `duration_ms` 均拒绝，不静默裁剪。
4. 文本把 CRLF/CR 统一为 LF，只去除首尾 Unicode 空白，内部空格和换行保持；空文本丢弃并产生 warning，最终为空则失败。

### 8.2 排序、去重与重叠

1. 按 `(start_ms, end_ms, original_position)` 稳定排序。
2. `start_ms/end_ms/normalized_text` 完全一致才是精确重复，只保留排序后的首个并告警；相同 ID 或局部相似文本不参与模糊去重。
3. 非精确重复的重叠不裁剪、不重排、不自动合并，只保留并产生 `segment_overlap`。
4. 所有 merge/split 完成后按最终顺序从 0 重建连续 Canonical ID。

### 8.3 合并与拆分

`NormalizerConfig` 字段固定为 `min_segment_chars`、`max_segment_chars`、`max_merge_gap_ms`，均为非负/正整数并计入 config hash。

- 只在当前段 Unicode code point 长度小于 `min_segment_chars`、与下一段不重叠、gap 不大于 `max_merge_gap_ms`、合并后长度不超过 `max_segment_chars` 时向后合并；每轮从左到右且每段最多参与一次合并；文本用单个 LF 连接，时间取首段 start 和末段 end；
- 超过 `max_segment_chars` 的正文按 Unicode code point 拆分。每片选择上限内最靠后的边界，边界优先级固定为 LF、`。！？!?；;`、`，,`；同优先级选择最靠后位置；无边界时按精确 code point 数切分；
- 拆分后的时间按正文 code point 累计比例分配：中间端点为 `start + floor(duration * cumulative_chars / total_chars)`，最后一片强制等于原 end；若无法保证每片至少 1ms，整体失败，不制造零长度段；
- merge 在 split 之前执行，二者都只执行一次，不对新片段递归重跑规则。

### 8.4 Warning、Canonical bytes 与哈希

Warning 只能由 normalizer 依据本节算法产生。固定 code 顺序表为：`empty_segment_dropped` → `duplicate_segment_dropped` → `segment_overlap` → `short_segment_merged` → `long_segment_split`；同 code 按 `(-1 if primary_original_position is None else primary_original_position, related_original_positions)` 排序，完全相同 warning 去重。ProviderCandidate 不提供 warning 输入，normalizer 不合并外部 warning。

Canonical JSON bytes 唯一算法：`json.dumps(to_json_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`；无 BOM、无尾随换行。所有 fixture 的预期 bytes/hex digest 必须固定，不能只比较 Python dict。

哈希输入精确冻结为：

| 字段 | SHA-256 对应的精确 bytes |
| --- | --- |
| `input_sha256` | 原始输入内容 bytes；Phase 1 只使用预先固定的虚构 fixture digest，不读取真实媒体 |
| `artifact.content_sha256` | artifact 原始 bytes；Phase 1 只验证引用和预计算 digest，不写入/读取真实 artifact |
| `config_hash` | 可信 Profile 执行 payload 的 Canonical JSON bytes，排除 `config_hash` 自身和资格/审核/发布/索引状态 |
| `execution_fingerprint` | 下述 fingerprint object 的 Canonical JSON bytes |
| Canonical 内容 SHA-256 | 完整 Canonical JSON bytes |
| Markdown SHA-256 | formatter 返回的完整 Markdown bytes |

fingerprint object **只**包含：`profile_id`、`provider_key`、`profile_definition_version`、`provider_adapter_version`、`input_sha256`、`input_kind`、`input_size_bytes`、`input_duration_ms`、`language`、`timeout_ms`、`provider_config_kind`、`provider_config_version`、provider config JSON、normalizer config JSON、`canonical_schema_version`、`normalizer_version`、`formatter_version`。字段不得增删；资格、准入、availability、review/publication/index 状态和当前时间不参与 execution fingerprint。

## 9. Formatter 与现有 Markdown 兼容边界

Formatter 签名冻结为 `format_transcript(canonical, *, title) -> bytes`。`title` 必须由外层受控 context 显式传入，或来自调用方已验证的文档标题；Provider 不返回标题，formatter 不根据模型、路径、媒体名或正文猜测标题。

### 9.1 输出字节规则

- title 是去除首尾空白后非空的单行文本，包含 CR/LF 或 speaker marker 形态时拒绝；
- `start_ms` 必须小于 100 小时，即 `0 <= start_ms < 360_000_000`；超出返回 `formatter_timestamp_out_of_range`，不得输出三位小时数；
- 时间戳使用 `start_ms // 1000` 向下取整到整秒，并格式化为两位 `HH:MM:SS`；
- 固定输出：`# {title}`，随后一个空行；每个 turn 为 `说话人 {1-based-index} HH:MM:SS`、LF、正文；turn 之间恰好一个空行；文件末尾恰好一个 LF；
- 编码严格 UTF-8、无 BOM，只含 LF；不得出现 CRLF、额外尾随空行或平台相关换行；
- 不插入当前时间、随机数、机器路径、引擎信息，不调用 LLM、网络、数据库、Provider 或 artifact。

### 9.2 Speaker marker 冲突

正文中任一独占行，在允许的 Markdown heading 前缀和空白归一化后如果匹配现有 speaker marker 语法，唯一策略是**严格拒绝**并返回 `formatter_speaker_marker_collision`。不得转义、改写、缩进或替换正文，因为这些处理会不可逆改变人工可审阅内容。

### 9.3 真实 parser 回归

Golden Markdown 不能复制 `TRANSCRIPT_TURN_RE` 或另写等价正则进行自证。`tests/test_transcript_manual_regression.py` 必须直接导入并调用当前 `src.chunk._parse_transcript_turns` 和 `src.chunk.chunk_transcript`：

- 对 formatter golden `*.md` 验证真实 turns 与 `*-golden.json` 一致；
- 用临时 `ParsedDoc` 和临时 Markdown 文件调用真实 `chunk_transcript`，比较 parent/child 数量、start_time、文本和稳定 ID；
- 对仓库内受控人工 Markdown fixture 做同样回归，证明 Phase 1 没改变人工解析行为；
- 不修改 `src/chunk.py`，不复制 parser regex，不把测试 helper 变成第二套 parser。

## 10. 人工 Markdown 受保护路径

两条路径永久分离：

```text
自动：Provider Adapter → Candidate → normalizer → Canonical → formatter → automatic candidate
人工：uploaded Markdown → 现有验证/原始字节保存/索引路径 → manual version
```

强制边界：不注册 `ManualTranscriptProvider`；人工路径不依赖 Provider Registry/Profile/`ASR_ENABLED`；Phase 1 不修改 `api/routes_admin.py` 或 `src/chunk.py`；人工 Markdown 不经 Canonical formatter round-trip；未来如需导入 Canonical，另立 `ManualTranscriptImporter` 方案。

## 11. 拟实施文件与修改边界

代码实施另获批准后，拟新增/修改范围冻结为：

```text
src/transcription/__init__.py
src/transcription/types.py
src/transcription/candidate.py
src/transcription/profile.py
src/transcription/provider_protocol.py
src/transcription/canonical.py
src/transcription/pipeline.py
src/transcription/normalizer.py
src/transcription/formatter.py
src/transcription/policy.py

tests/transcription_fixture_helpers.py
tests/test_transcription_types.py
tests/test_transcription_profile.py
tests/test_transcription_canonical.py
tests/test_transcription_normalizer.py
tests/test_transcription_formatter.py
tests/test_transcription_provider_contract.py
tests/test_transcription_policy.py
tests/test_transcript_manual_regression.py
tests/test_transcription_static_boundaries.py
tests/fixtures/transcription/*.json
tests/fixtures/transcription/*.md
tests/fixtures/transcription/*-golden.json

.github/workflows/ci.yml
```

`.github/workflows/ci.yml` 仅列入**未来 Phase 1 代码实施**范围，用于把上述测试持续纳入 CI；本轮不修改 CI。Phase 1 优先使用标准库，不新增第三方运行时依赖。实施时可合并不影响依赖方向的小文件，但不得取消 `candidate.py`/`provider_protocol.py`/`pipeline.py`/`normalizer.py` 的逻辑边界，也不得带入数据库、API、UI 或真实引擎。

当前 CI 事实：`validate` job 不安装 Python 测试依赖，`test-providers` 只安装 `httpx pytest python-dotenv`；而真实导入 `src.chunk` 还需要仓库已声明的 `requests`、`pypdf`、`langchain-text-splitters` 等现有依赖。未来 CI 必须使用**当前已声明依赖**提供真实 parser 导入环境，或在实施前证明一个不复制 parser 的等价受控导入方式。如果当前 CI requirements 无法提供该环境，则这是 Phase 1 代码实施 blocker：停止实施并提交依赖/CI 范围复审，不能复制正则绕过。

## 12. 测试计划与静态边界

### 12.1 类型、Profile Registry 与 Policy

`tests/test_transcription_types.py` 覆盖 `3.7` 每条精确格式规则、深层不可变和 serialization/fingerprint；`test_transcription_profile.py` 覆盖 `ProfileRegistry`、四个 `ProfileOperation`、未登记/disabled/deprecated 结构化拒绝、enabled 后 qualification/policy 正交判断、请求仅 `profile_id` 和 resolver 不接收自由参数；`test_transcription_policy.py` 覆盖 experimental、review gate、deprecated/disabled、发布和 publication-only promote guard。

Profile 固定矩阵必须同时有正负断言：registered+enabled 只通过 admission、不能暗示 qualification/availability；unregistered 对四个 operation 全拒绝；disabled 拒绝 new/retry/正常 continue/publish；deprecated 拒绝 new/retry、仅允许已 running 原 attempt continue、仅允许显式管理员 publish 并应用更严格策略。

### 12.2 Pipeline、Provider 与 Canonical

`test_transcription_provider_contract.py` 参数化运行三个 Fake Provider，且每次都从同一个 `pipeline.execute_transcription` 入口进入，覆盖成功、transient/permanent failure、timeout、非法联合成员/非法 Candidate、输入或配置修改尝试和允许异常归一化；测试必须证明 Failure 不调用 normalizer/formatter、Candidate 只能经 normalizer 产生 Canonical、ProviderCandidate 无 `warnings` 字段、核心无 `provider_key` 名称分支。

`test_transcription_canonical.py` 覆盖严格字段、JSON-native、私有类型隔离、normalizer-only warning、有限 artifact kind 和精确 Canonical bytes/hash。对 Provider 的调试/VAD/token/confidence 私有诊断，正向只接受受控 artifact reference，负向拒绝 `warning/provider_warning/extra/raw` 或 Provider 私有 kind。

### 12.3 Normalizer、Formatter 与真实回归

`test_transcription_normalizer.py` 覆盖时间、排序、去重、重叠、merge/split、normalizer 独占 warning 顺序/去重和 hash；`test_transcription_formatter.py` 覆盖 100 小时边界、speaker marker 拒绝、显式 title/context、UTF-8/LF/BOM/末尾换行和 golden bytes；`test_transcript_manual_regression.py` 必须调用真实 parser/chunker。

### 12.4 Fixture 约定

- `*.json`：严格 Schema 的 Candidate/Canonical/Profile/Fake Provider 输入；Candidate fixture 不得出现 `warnings`；
- `*.md`：formatter 和人工 Markdown 的字节级 golden；
- `*-golden.json`：真实 parser/chunker 的结构化预期，不包含复制的 regex 结果；
- fixture helper 只能负责加载 bytes、构造临时 `ParsedDoc` 和比较结构，不得解析 speaker marker、模拟 normalizer 或绕过 pipeline 调用 Provider。

### 12.5 静态边界扫描

`tests/test_transcription_static_boundaries.py` 必须扫描：

- `src/transcription/**/*.py`；
- `tests/test_transcription*.py` 和 `tests/test_transcript_manual_regression.py`；
- `tests/transcription_fixture_helpers.py` 及其递归导入 helper；
- `tests/fixtures/transcription/**/*`，确保没有真实音频/视频/模型文件。

扫描同时覆盖 AST `Import`/`ImportFrom`、相对导入、`__import__`、`importlib.import_module`、`spec_from_file_location`、`SourceFileLoader` 等常见静态和动态方式，并执行以下唯一边界断言：

- 文档冻结模块图可被解析为 DAG，且 `provider_protocol.py` 不依赖 pipeline/normalizer/formatter，`normalizer.py` 不依赖 pipeline，`formatter.py` 不依赖 pipeline/provider protocol；
- `src/transcription/**` 中调用 Provider `transcribe` 的执行位置只能是 `pipeline.py`；调用 `normalize_candidate` 的应用源码位置只能是 `pipeline.py`；测试可直接调用 normalizer 做纯单元测试，但不得直接调用 Provider 组成工作流；
- `provider_protocol.py` 只包含 Protocol/ProviderCandidate/ProviderFailure/ProviderResult/ProviderCapabilities/error 分类定义，不执行 Provider；`ProviderCandidate` 的声明和 fixture 均不得出现 `warnings`；
- `profile.py` 是 `ProfileRegistry/resolve_profile/ProfileOperation` 所有者；请求和 resolver 源码不得接受模型路径、URL、热词或 decoder 底层参数；
- `pipeline.py`、`normalizer.py`、`canonical.py`、`formatter.py`、`policy.py` 不得以具体 `provider_key` 字符串作 `if/match/dict-dispatch` 分支；Registry 数据键和叶子 Adapter 自身声明不属于核心分支；
- 拒绝导入、字符串调用或执行路径中的 FunASR、faster-whisper、torch、FFmpeg/PyAV、socket/urllib/requests/httpx/aiohttp、subprocess、sqlite3/SQLAlchemy、Qdrant、模型加载/API、真实媒体扩展；
- 允许测试文件直接导入 `src.chunk` 仅用于 `test_transcript_manual_regression.py`；该例外不能传播到 `src/transcription/**`。

## 13. Phase 1 完成标准与逐项验收映射

以下 20 项全部满足才可标记代码完成。每项必须形成判定唯一的自动测试或静态检查，并同时具备表中正向、负向或静态证据；单个 happy path 不构成完成。

1. 模块依赖符合 `3.1` 且无环；`pipeline.execute_transcription` 是唯一公共 Provider 调用和 Candidate → Canonical 工作流入口，normalizer 是唯一 Candidate → Canonical 转换入口。
2. Qualification、Admission、Availability、Job、Review、Publication、PublicationIndex 状态分型；`PublicationIndexStatus` 不冒充现有通用 index job 状态。
3. `ProfileRegistry/resolve_profile` 的四 operation 矩阵、qualification/admission 正交性、experimental、`review_gate_satisfied`、deprecated/disabled 构造与 guard 语义全部固定且失败关闭。
4. Canonical 根和所有嵌套对象严格字段，等价 `additionalProperties: false`。
5. Canonical 只接受 JSON-native 引擎中立值，不泄漏 FunASR/faster-whisper/Python/numpy/tensor/generator。
6. duration/start/end 为非负整数且 bool 非整数；Candidate 十进制时间严格解析并按固定规则换算。
7. 排序、去重、重叠、merge/split 完全确定；Canonical warning 只能由 normalizer 生成，并按固定 code/排序/去重规则覆盖非法边界。
8. UUID、三个 slug、SHA-256、language、`ArtifactReference.kind`、Canonical Schema version 和 confidence 逐字节符合 `3.7` 的唯一接受集合。
9. 可信 Profile/Provider 配置与不可信 `StartTranscriptionRequest` 分型，不共享自由参数字典。
10. 管理员请求只允许 `profile_id`；Registry resolver 只按 `profile_id + ProfileOperation` 解析登记项，路径、URL、热词、底层参数、策略覆盖和额外字段全部拒绝。
11. Provider 只返回 Candidate/Failure，只接收深层不可变 input ref/execution；三个 Fake 全部经 pipeline；Candidate 不得覆盖输入身份，调用前后 serialization/fingerprint 不变。
12. `ProviderFailure` 的 error code、transient/permanent、timeout 和 artifact 语义固定；`ProviderCandidate` 不拥有 warnings；Provider 私有诊断只可进入有限 kind artifact，或在无法成功时进入 Failure。
13. Formatter 只接受 Canonical 和显式受控 `FormatterContext`，严格执行 100 小时、speaker marker、UTF-8/LF/BOM/空行/末尾换行，并由真实 parser/chunker 验证。
14. config、execution、Canonical、Markdown 和 artifact 的 SHA-256 精确绑定 `8.4` 指定 bytes；版本字段可追溯。
15. 第三个 Fake Provider 只通过 Protocol/Registry 登记扩展，不修改 pipeline/normalizer/canonical/formatter/policy、不增加核心 `provider_key` 分支，并与另外两个共同覆盖成功、失败、timeout、非法输出和修改尝试。
16. 发布边界拆分为：
    - **Phase 1**：纯 guard 证明 candidate `PublicationIndexStatus.done` 之前 `promote_allowed == false`；
    - **Phase 2**：数据库事务、正式指针原子切换、候选/旧索引隔离和失败时旧索引保护，Phase 1 不声称已验证。
17. transcription succeeded 不自动产生 review approved、published 或 publication index done。
18. 人工 Markdown 不注册 Provider、不依赖 `ASR_ENABLED`，真实 `_parse_transcript_turns`/`chunk_transcript` 回归通过且现有源码未修改。
19. Phase 1 测试不访问数据库、Qdrant、网络、GPU、真实媒体、真实模型、生产服务或 subprocess。
20. 静态边界覆盖源码、测试、fixture helper、fixture 和常见动态导入；Phase 1 测试进入 CI，且不新增第三方运行时依赖。

| # | 主要测试文件 | 正向用例 | 负向/静态用例 |
| --- | --- | --- | --- |
| 1 | `test_transcription_static_boundaries.py`、`test_transcription_provider_contract.py` | 三个 Fake 均由 pipeline 调用，Candidate 经唯一 normalizer 产生 Canonical | AST 依赖图有环、pipeline 外调用 Provider/应用源码直接调用 normalizer、protocol/formatter 反向导入或直接构造 Canonical 均失败 |
| 2 | `test_transcription_types.py`、`test_transcription_policy.py` | 每个领域状态合法构造 | 跨枚举赋值、把 PublicationIndex 当通用 index 状态或把状态嵌入 ProviderResult 失败 |
| 3 | `test_transcription_profile.py`、`test_transcription_policy.py` | registered+enabled 后独立判断 qualification；deprecated 原 attempt/显式发布；not_required 无需审核时通过 | unregistered 四 operation、disabled、deprecated new/retry、experimental 宽松组合、需审核时 not_required 和自动发布失败 |
| 4 | `test_transcription_canonical.py` | 最小/完整 golden 通过 | 根、snapshot、segment、warning、artifact 任意额外字段拒绝 |
| 5 | `test_transcription_canonical.py`、`test_transcription_provider_contract.py` | JSON-native Candidate 正常归一化 | 私有对象、Path、bytes、tuple、generator 泄漏和 raw/debug/extra 字段拒绝 |
| 6 | `test_transcription_types.py`、`test_transcription_normalizer.py` | ms/s 和半毫秒 ROUND_HALF_UP golden | bool、float 时间、指数、负值、NaN/Infinity、end<=start、越界失败 |
| 7 | `test_transcription_normalizer.py`、`test_transcription_canonical.py`、`*-golden.json` | 乱序/重复/重叠/merge/split 产生固定 warning 与 Canonical bytes | Provider warning 输入、模糊重复误删、零长度拆分、递归 merge/split、warning 未按固定 code/顺序/去重均失败 |
| 8 | `test_transcription_types.py`、`test_transcription_profile.py`、`test_transcription_canonical.py` | `3.7` 每个 UUID/slug/language/artifact kind/schema 边界与 canonical 回比通过 | 大写/无连字符/nil UUID、非法 slug/路径/URL、language 扩展/private-use/大小写、未知 artifact kind、未知 major/minor/版本格式失败 |
| 9 | `test_transcription_profile.py`、`test_transcription_static_boundaries.py` | 登记的冻结 provider config 加载 | 自由 dict/Any、未知 config kind/version、Profile 与 request 混型失败 |
| 10 | `test_transcription_profile.py`、`test_transcription_static_boundaries.py` | 请求只含 `profile_id`，resolver 只读取 `profile_id+operation` | 路径、URL、hotwords、decoder、policy override、额外字段或 resolver 自由参数签名/读取失败 |
| 11 | `test_transcription_provider_contract.py`、`test_transcription_profile.py`、`test_transcription_static_boundaries.py` | 三个 Fake 接收同一冻结 input/execution 并从 pipeline 成功，normalizer 使用 input ref/snapshot 写入身份 | 直接返回 Canonical、Candidate 覆盖身份、pipeline 外 Provider 调用、修改属性/嵌套/别名容器、前后 bytes/fingerprint 变化失败 |
| 12 | `test_transcription_provider_contract.py`、`test_transcription_canonical.py`、`test_transcription_normalizer.py` | 每类 Failure 和五个有限 artifact kind 合法，normalizer 生成全部 warning code | timeout 分类/timeout_ms 矛盾、Candidate warnings/provider_warning、异常对象/stack/path/url/raw artifact、未知 kind 拒绝；Failure 不得产生 Canonical/Markdown |
| 13 | `test_transcription_formatter.py`、`test_transcript_manual_regression.py`、`*.md` | 字节 golden 被真实 parser/chunker 接受 | 100h、speaker marker、猜测/多行标题、BOM/CRLF/尾随空行和复制 regex 自证失败 |
| 14 | `test_transcription_types.py`、`test_transcription_canonical.py`、`test_transcription_formatter.py` | 固定 bytes 对应固定 digest | 任一纳入字段/字节变化改变 hash；排除字段变化不改变 execution fingerprint |
| 15 | `test_transcription_provider_contract.py`、`test_transcription_static_boundaries.py` | 第三 Fake 只增加叶子实现/登记且全部经 pipeline | 修改核心模块、核心 provider_key if/match/dict branch、真实引擎导入、任一 Fake 未经 pipeline 或缺少失败模式失败 |
| 16 | `test_transcription_policy.py`、`test_transcription_static_boundaries.py` | review gate + publishing + index done 允许纯 promote | pending/parsing/chunking/embedding/failed 禁止；Phase 1 出现 DB/指针/索引实现失败 |
| 17 | `test_transcription_policy.py` | 显式独立转换合法 | succeeded 自动改 review/publication/index 状态失败 |
| 18 | `test_transcript_manual_regression.py`、`test_transcription_static_boundaries.py` | 真实人工/自动 Markdown fixture 的 turns/chunks golden 通过 | 注册 Manual Provider、依赖 ASR flag、修改 `src/chunk.py`/人工上传路径或 helper 复制 parser 失败 |
| 19 | `test_transcription_static_boundaries.py` | 全套 fixture 离线执行 | DB/Qdrant/network/GPU/model/subprocess/真实媒体导入或调用失败 |
| 20 | `test_transcription_static_boundaries.py`、`.github/workflows/ci.yml` | CI 明确运行全部 Phase 1 测试 | 漏扫 helper/dynamic import、引入新 runtime dependency、CI 未接入或真实 parser 环境缺失失败 |

## 14. 实施顺序

代码实施另获批准后：

1. 建立静态边界测试骨架，编码冻结模块 DAG、pipeline-only Provider 调用和禁止核心 provider-name branch；
2. 实现基础类型、`3.7` 精确格式 Schema、可信配置和深层不可变；
3. 实现 `ProfileRegistry/resolve_profile/ProfileOperation` 与四 operation 矩阵；
4. 实现 `CandidateSegment` 及 `provider_protocol.py` 中的 Candidate/Failure/Result/Capabilities/Protocol；
5. 实现 `pipeline.execute_transcription` 的唯一公共调用、异常归一化、结果联合校验和调用前后不可变检查；
6. 实现 Canonical bytes/hash 和 normalizer 唯一转换入口及 normalizer-only warning；
7. 实现确定性 formatter、Markdown fixture 和真实 parser/chunker 回归；
8. 实现三个全部经 pipeline 的 Fake Provider 与完整失败矩阵；
9. 实现状态/policy/publication-only 纯 guard；
10. 补齐 20 项映射的正负测试；
11. 修改 CI 持续运行 Phase 1 测试；若真实 parser 依赖环境无法由现有 requirements 提供则停止并报告 blocker；
12. 运行定向测试、静态边界扫描和 diff 检查，等待 Phase 2 单独审批。

## 15. 验证命令与静态检查

预期至少运行：

```text
python -m pytest tests/test_transcription_types.py
python -m pytest tests/test_transcription_profile.py
python -m pytest tests/test_transcription_canonical.py
python -m pytest tests/test_transcription_normalizer.py
python -m pytest tests/test_transcription_formatter.py
python -m pytest tests/test_transcription_provider_contract.py
python -m pytest tests/test_transcription_policy.py
python -m pytest tests/test_transcript_manual_regression.py
python -m pytest tests/test_transcription_static_boundaries.py
python -m pytest tests/test_transcription*.py tests/test_transcript_manual_regression.py
```

静态检查必须确认：

- 单向依赖图和无环性；`pipeline.py` 是源码唯一 Provider 调用点和应用态 normalizer 调用点；core 无 provider-name branch；
- `profile.py` 独占 Registry/resolver，管理员请求无自由配置；`3.7` 的 regex/枚举/version 接受集合无替代实现；
- `ProviderCandidate` 无 warnings；Canonical warning 由 normalizer 独占；Canonical/Candidate/Failure/artifact 无逃逸字段；
- 20 项完成标准和映射表均恰好覆盖 #1–#20，无缺失或重复；
- 扫描普通/相对/动态导入以及禁止模块/调用/真实媒体；
- `api/routes_admin.py`、`src/chunk.py`、数据库、Qdrant、生产和真实 ASR 不在修改集；
- `.github/workflows/ci.yml` 只在未来代码实施中加入测试，本轮不修改；
- Markdown relative links 和 `git diff --check` 通过。

## 16. 风险、兼容性、Blocker 与回滚

- **ADR 表述歧义**：本计划把“Provider 输出 Canonical”细化为“Adapter 输出 Candidate，Provider 流水线经唯一 normalizer 输出 Canonical”，不改变 ADR 的引擎中立目标；若后续要求 Adapter 直接构造 Canonical，属于实质冲突，必须停止并复审 ADR。
- **真实 parser CI 环境**：当前轻量 CI job 未安装 `src.chunk` 的完整现有依赖。未来实施必须用已声明 requirements 提供真实导入环境；不能提供即为 blocker，不得复制 regex。
- **确定性算法兼容**：任何时间舍入、merge/split、warning、JSON/Markdown bytes 或 fingerprint 字段变化都需要 Schema/版本升级和 golden 评审。
- **发布边界误读**：Phase 1 只证明纯 guard；数据库原子性和旧索引保护属于 Phase 2。
- **人工稿保护**：自动 formatter 只生成 automatic candidate，不读取、不改写、不 round-trip 人工文件。
- **新增 Provider 扩展**：通过版本化严格 provider config 和 Profile Registry 登记扩展，不开放任意 extra 参数；不得修改 pipeline/normalizer/canonical/formatter/policy。

回滚仅删除未来 Phase 1 新增纯 Python 包、测试和 CI 测试入口并恢复文档；不需要数据库降级、索引恢复或真实数据清理。任何扩大到依赖、数据库、API、UI、网络、真实引擎或生产操作的变化必须重新评级并重新审批。
