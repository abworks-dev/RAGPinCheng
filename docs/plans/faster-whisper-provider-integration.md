# faster-whisper Provider 接入 R2 实施基线

> 状态：历史 R2 Provider 接入基线；代码已在后续提交中实施，Profile 准入与生产身份以当前转录功能文档和 deployment workflow 为准。
> 日期口径：2026-08-05，Asia/Shanghai。
> 风险等级：R2（跨应用 Profile、remote Provider 与独立 ASR service engine 契约）。

## 1. 目标

在不改变 Canonical Transcript、发布、索引和人工 Markdown 流程的前提下，
将固定 faster-whisper 候选接入现有多引擎端口与适配器架构：

```text
faster-whisper engine
→ EngineChunkCandidate | ProviderFailure
→ ProviderCandidate | ProviderFailure
→ pipeline.py
→ normalizer
→ Canonical Transcript
→ formatter
```

R2 只形成可测试的代码契约，不安装依赖、不下载模型、不运行 GPU 推理，也不开放
管理员创建 faster-whisper 任务。

## 2. 当前依据

- `pipeline.py` 独占 Provider 调用、严格返回边界和 Candidate → Canonical 流程；
- `RemoteAsrProvider` 已提供 create/upload/start/poll/result 的引擎中立远程协议；
- `asr_service.EngineRegistry` 已按 `service_profile_id` 解析引擎；
- `asr_service.scheduler` 当前仍把完整输入作为单个 chunk，本阶段不宣称真实长音频分块；
- 历史 `faster-whisper-phase0-precheck.md` 固定了相同包、模型和 revision；
- 历史 R3-A/retry 记录均在下载与推理前停止，不构成安装、CUDA、质量或生产资格。

## 3. 固定身份

```text
provider_key=faster-whisper
application_profile_id=faster-whisper-zh-experimental-v1
service_profile_id=faster-whisper-large-v3-turbo-v1
faster-whisper=1.2.1
ctranslate2=4.8.1
model_id=dropbox-dash/faster-whisper-large-v3-turbo
model_revision=0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
language=zh-CN
engine_language=zh
device=cuda
compute_type=float16
```

Profile 状态固定为：

```text
qualification=experimental
admission=disabled
requires_review=true
auto_publish=false
auto_index=false
```

代码实现不自动取得运行资格。只有另行完成 R3 依赖、模型、CUDA、资源和质量验证后，
才可单独审批把 `admission` 改为 `enabled`。

## 4. 固定推理参数

```text
task=transcribe
beam_size=1
temperature=0.0
vad_filter=false
condition_on_previous_text=false
word_timestamps=false
hotwords=null
local_files_only=true
```

不得静默切换模型、revision、CPU、INT8、VAD、热词或其他解码参数。

## 5. 模块边界

### 应用侧

- `profile.py`：拥有严格、不可变的 faster-whisper 可信配置；
- `profile_catalog.py`：拥有白名单 Profile、准入和服务能力映射；
- `transcription_runtime.py`：将两个远程 provider factory 注入 Registry；
- `remote_provider.py`：复用同一个远程协议，不按引擎名称分支业务流程；
- `routes_transcription.py`：只组装已登记的两个 factory。

### 独立 ASR service

- `engine_protocol.py`：只接受两个固定服务 Profile 身份；
- `model_cache.py`：使用同一严格 Manifest 规则校验各自固定模型树；
- `engines/faster_whisper.py`：唯一允许动态导入 faster-whisper/CTranslate2 的文件；
- `app.py`：注册两个引擎；faster-whisper 缓存或依赖缺失时只报告该 Profile 不可用，
  不阻止现有 SenseVoice 服务启动；
- `config.py`：新增成对、可选的 faster-whisper 本地缓存配置。

依赖方向保持：

```text
API composition
→ Profile/Provider Registry
→ remote Provider
→ ASR service contract

ASR service app
→ EngineRegistry
→ AsrEngine adapter
→ EngineChunkCandidate | ProviderFailure
```

核心流程不导入具体引擎包。

## 6. 时间戳与输出规则

- adapter 仅接受有限、非负的秒值；
- 使用 `Decimal(str(seconds)) * 1000` 和 `ROUND_HALF_UP` 转为整数毫秒；
- 拒绝 bool、NaN、Infinity、负数、`end <= start` 和超过 chunk 时长的 segment；
- 保留原始 segment 顺序并分配连续 `original_position`；
- 文本 `strip()` 后必须非空；
- 不把 token、word、log probability、no-speech probability、引擎 info 或私有对象
  放入 Candidate；
- 不生成 warnings；Canonical warnings 仍只由 normalizer 生成；
- 不映射引擎概率为 Canonical confidence。

## 7. 失败映射

| 情况 | Provider 错误 | 分类 |
|---|---|---|
| CUDA OOM | `provider_oom` | transient |
| 模型缓存、依赖、CUDA/DLL 不可用 | `provider_unavailable` | transient |
| Profile 身份不匹配 | `service_contract_mismatch` | permanent |
| segment、文本或返回结构非法 | `invalid_provider_output` | permanent |

所有生成器在 adapter 的异常边界内完整消费，延迟抛出的异常不得逃逸到 scheduler。

## 8. 模型与依赖隔离

- `asr_service/requirements-faster-whisper.txt` 只记录固定顶层候选；
- 不修改根 `requirements.txt`、`requirements-prod.txt`；
- 不修改 `asr_service/requirements-windows.txt`；
- ASR 模块导入和测试收集不得要求安装真实 faster-whisper/CTranslate2；
- 模型仅接受严格 `asr-model-manifest/1`、固定 identity、全文件大小与 SHA-256；
- R2 不创建真实 Manifest，不下载模型。

## 9. 测试映射

- Profile：两个配置严格反序列化、未知字段拒绝、固定 revision、准入关闭；
- Registry：两个 Profile/factory/engine 排序唯一，无引擎名称业务分支；
- adapter：惰性导入、仅本地 CUDA FP16、固定解码参数、时间戳取整；
- 负面：无 CUDA、缓存缺失、OOM、空文本、私有对象、非法时间、生成器尾部异常；
- remote：两个 provider 通过同一 create/upload/start/poll/result 与 pipeline；
- model cache：固定 faster-whisper Manifest 正向和交叉 identity 负向；
- static：真实动态导入只允许位于两个 adapter；主依赖和 Windows 当前依赖不含新包；
- API：应用运行时登记两个 provider，但 faster-whisper Profile 仍由 admission guard 阻断。

## 10. 明确不做

- 不修改数据库 Schema、迁移、Store 或 job/version 状态模型；
- 不修改上传 API 请求 Schema、前端 UI、worker、Qdrant、BGE、发布或索引；
- 不修改 Candidate、Canonical、normalizer、formatter 或 `pipeline.py`；
- 不实现 scheduler 长音频 chunking、segment 级恢复或 retry；
- 不安装依赖、下载模型、运行 ffmpeg/GPU/真实 ASR；
- 不修改 Windows 服务、Scheduled Task、防火墙、Token 或 Ubuntu `ASR_ENABLED`；
- 不部署生产，不上传媒体，不创建转录任务。

## 11. R3 后续门禁

若继续，必须另行审批并验证：

1. Windows Python 3.11 wheel resolver、完整 lock/hash、`pip check` 和许可证；
2. 固定 revision 模型下载、全文件 Manifest 与本机 SHA-256；
3. CTranslate2 4.8.1、CUDA 12、cuDNN/DLL、RTX 5060 Ti FP16；
4. 峰值显存、BGE 优先与同卡共存；
5. 非敏感短样本的中文、时间戳和失败恢复；
6. 通过后才单独修改 Profile admission 和生产依赖/部署流程。

任一门禁失败都保持 faster-whisper Profile disabled，不影响现有 SenseVoice 与人工
Markdown 路径。

## 12. 回滚

R2 未产生数据和部署状态。回滚只需撤销本次代码/测试/文档提交；现有数据库、
SenseVoice 模型、服务配置、人工转录稿和正式索引不需要恢复或重建。
