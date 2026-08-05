# Qwen3-ASR 独立 R2/R3 接入与资格验证计划

> 状态：R2 代码已完成，待干净环境 CI/审查；统一 R3 仍待一次性审批。本文不代表
> Qwen3-ASR 已安装、已完成真实引擎验证、已部署或可用于生产。
>
> 调查边界：只读核对仓库与官方资料；未安装依赖、未下载模型、未启动服务、
> 未运行推理、未修改生产状态。
>
> 审批边界：R2 与 R3 必须分别审批。R2 完成不自动授权 R3；R3 通过也不自动
> 启用 application Profile 或修改生产 ASR 服务。

## 1. 目标与结论摘要

目标是在保留现有业务契约的前提下，把 Qwen3-ASR 作为第三个本地 ASR 候选：

```text
Qwen3-ASR engine
→ 现有 ASR service contract
→ 现有 TranscriptionProvider
→ ProviderCandidate | ProviderFailure
→ 现有 pipeline
→ 现有 normalizer
→ 现有 Canonical Transcript
→ 现有 formatter / parser / review / publish / index
```

静态调查结论：

1. 官方开源系列包含 `Qwen3-ASR-0.6B`、`Qwen3-ASR-1.7B` 和提供时间戳的
   `Qwen3-ForcedAligner-0.6B`；代码和上述模型卡均标为 Apache-2.0。
2. 本计划的 Windows 基线选择 **Transformers backend**，不选择 vLLM。
   PyTorch 官方支持 Windows CUDA；vLLM 官方明确“不原生支持 Windows”，仅建议
   WSL 或社区 fork，不能作为当前生产 Windows 服务的受支持基线。
3. 初始候选固定为 `Qwen3-ASR-0.6B + Qwen3-ForcedAligner-0.6B`。原因是现有
   RTX 5060 Ti 与 BGE/ASR 共卡，时间戳又是当前 Canonical/Markdown 的必要输入；
   1.7B 只作为统一 R3 前置门禁失败后的**另案**，不得自动回退或同时下载。
4. 官方 `qwen-asr==0.0.6` 固定 `transformers==4.57.6`，与根项目
   `transformers>=4.46,<5` 有版本交集，但还引入 `accelerate`、`librosa`、
   `soundfile`、`sox`、`gradio`、`flask`、`qwen-omni-utils` 等依赖。官方明确建议
   使用全新隔离环境；因此不得直接加入现有生产 ASR venv 或 backend requirements。
5. 当前没有官方证据证明 `qwen-asr==0.0.6`、ForcedAligner、Windows CUDA、
   Blackwell、现有 `torch==2.7.0+cu128` 和全部转接依赖在本机组合下可用。
   这些均保持 `R3_REQUIRED`，不得把 PyTorch 支持 Windows 等同于完整栈已支持。

## 2. 当前仓库依据与不可变契约

当前主线已经具备多引擎端口：

- `src/transcription/provider_protocol.py` 独占 `TranscriptionProvider`、
  `ProviderCandidate` 和 `ProviderFailure`；
- `src/transcription/pipeline.py` 是 Provider 调用与 Candidate → Canonical 的
  唯一业务入口；
- `src/transcription/normalizer.py` 独占确定性段落规范化和 warnings；
- `src/transcription/canonical.py`、`formatter.py` 与现有 parser 构成发布前契约；
- `src/transcription/profile.py` 拥有可信配置、Profile 快照和 release policy；
- `src/transcription/profile_catalog.py` 负责 application Profile 白名单与准入；
- `asr_service/engine_registry.py` 按固定 `service_profile_id` 解析引擎；
- 实验 Profile 强制 `requires_review=true`、`auto_publish=false`、
  `auto_index=false`。

Qwen3-ASR 接入不得：

- 新建第二套业务 Provider/normalizer/Canonical 类型；
- 让引擎输出直接写 Markdown、数据库、Qdrant 或索引；
- 把自由模型路径、revision、prompt、hotwords、dtype、device、batch 或解码参数
  暴露给管理员/API；
- 将引擎概率或 token score 映射为 Canonical confidence；
- 让第三方包在 import、测试收集或服务启动时自动下载模型；
- 修改 Candidate、Canonical、normalizer、formatter、parser、数据库 Schema、
  发布事务或索引契约。

## 3. 官方模型、许可与支持矩阵

### 3.1 固定候选

| 项目 | R2 固定值 | 静态结论 |
|---|---|---|
| application provider | `qwen3-asr` | 新 Provider key |
| application Profile | `qwen3-asr-zh-experimental-v1` | `experimental + disabled` |
| service Profile | `qwen3-asr-06b-aligner-v1` | 仅服务内部白名单 |
| ASR 模型 | `Qwen/Qwen3-ASR-0.6B` | Apache-2.0；revision 待 R3 前冻结 |
| 时间戳模型 | `Qwen/Qwen3-ForcedAligner-0.6B` | Apache-2.0；revision 待 R3 前冻结 |
| Python 包 | `qwen-asr==0.0.6` | Apache-2.0；Python `>=3.9` |
| backend | `transformers` | Windows 候选 |
| dtype | `bfloat16` 首检 | 不可用则 FAIL，不自动降级 |
| language | `Chinese` | 不启用自动语言检测作为首个 Profile |
| timestamps | `return_time_stamps=true` | 必须走 ForcedAligner |
| prompt/hotwords | 空 | 不在首轮引入可变 prompt |
| batch | `1` | 先满足共卡资源门禁 |

R2 实施前通过只读 `ls-remote` 冻结了两个官方仓库的完整 commit SHA；未克隆仓库
或下载模型。R2 代码、模型 Manifest 和后续 R3 workflow 必须使用上述同一 SHA。
若 R3 审批前决定升级 revision，需更新计划并重新审批。

### 3.2 官方资料

- 官方仓库与能力说明：<https://github.com/QwenLM/Qwen3-ASR>
- 官方包元数据：<https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/pyproject.toml>
- 官方代码许可证：<https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/LICENSE>
- 官方 0.6B 模型卡：<https://huggingface.co/Qwen/Qwen3-ASR-0.6B>
- 官方 1.7B 模型卡：<https://huggingface.co/Qwen/Qwen3-ASR-1.7B>
- 官方 ForcedAligner 模型卡：
  <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B>
- PyPI 固定包元数据：<https://pypi.org/pypi/qwen-asr/0.0.6/json>
- PyTorch Windows/CUDA 支持：
  <https://pytorch.org/get-started/locally/>
- vLLM 平台限制：
  <https://docs.vllm.ai/en/latest/getting_started/installation/gpu/>

### 3.3 许可证门禁

Apache-2.0 仅覆盖已核对的 Qwen 官方代码/模型声明，不覆盖全部传递依赖。R3 必须：

1. 对实际 wheelhouse 和三个模型仓库快照生成许可证矩阵；
2. 保存 LICENSE、model card、包元数据和 immutable revision；
3. 拒绝 GPL/AGPL/SSPL、未知许可证、缺少许可证文件或声明冲突；
4. 记录 wheel URL、文件名、大小、SHA-256 与安装后版本；
5. 不把“PyPI 页面显示 Apache-2.0”扩展为所有依赖均已通过。

## 4. Windows/CUDA 与依赖冲突判断

### 4.1 Windows/CUDA

| 组合 | 结论 |
|---|---|
| PyTorch + Windows CUDA | 官方支持，但仍需实机验证目标 wheel/驱动/GPU |
| Qwen Transformers + Windows | 官方示例未给出 Windows 资格声明；`R3_REQUIRED` |
| Qwen ForcedAligner + Windows | `R3_REQUIRED` |
| FlashAttention 2 + Windows | 不进入首轮；不能要求本机编译 |
| vLLM + 原生 Windows | 官方不支持；排除 |
| vLLM + WSL/Linux | 独立架构另案；不属于本计划 |
| CPU fallback / INT8 fallback | 禁止作为资格通过条件 |

R3 必须证明目标 RTX 5060 Ti、驱动、`torch==2.7.0+cu128`、BF16、音频解码与
ForcedAligner 全部可用。任一缺失均 FAIL，不自动更改 torch/CUDA、dtype、模型或
backend。

### 4.2 已知依赖风险

`qwen-asr==0.0.6` 直接固定 `transformers==4.57.6`，并依赖：

```text
nagisa==0.2.11
soynlp==0.0.493
accelerate==1.12.0
qwen-omni-utils
librosa
soundfile
sox
gradio
flask
pytz
```

风险包括：

- 当前 ASR Windows freeze 与 `accelerate`、`numpy`、`numba`、`librosa`、
  `soundfile`、`transformers` 的解析冲突；
- `sox` Python 包不等同于系统 SoX 可执行文件，Windows 音频路径需实测；
- Gradio/Flask 是当前服务不需要的上层依赖，却会扩大依赖和许可证面；
- `qwen-omni-utils` 未固定版本，必须在 R3 解析后冻结完整 wheel Manifest；
- `transformers==4.57.6` 不能替换根项目约束或影响 BGE backend；
- FlashAttention 可能要求本机编译或缺少 Windows wheel，因此首轮明确禁用；
- 同一 venv 共存即使 `pip check` 通过，也不能证明 native CUDA/DLL 无冲突。

因此采用两个隔离层：

1. application/backend 继续通过现有 Remote Provider 调用 ASR service；
2. Qwen 资格与后续运行使用独立 Qwen engine venv/子进程，ASR service 只接收严格
   本机 loopback 结果；不得把 Qwen 依赖加入现有生产 ASR venv。

若 R2 设计评审认定“ASR service 内跨 venv 子进程”超出现有部署边界，则停止并提交
新的 R2 架构方案，不得退回同 venv 安装。

## 5. Profile 策略

R2 新增唯一 application Profile：

```text
profile_id=qwen3-asr-zh-experimental-v1
provider_key=qwen3-asr
service_profile_id=qwen3-asr-06b-aligner-v1
qualification=experimental
admission=disabled
requires_review=true
auto_publish=false
auto_index=false
language=zh-CN
```

策略：

- R2 合并后仍 disabled，管理员不可创建任务；
- R3 PASS 后仍 disabled；
- 只允许固定模型、固定 revision、固定依赖 Manifest 和固定推理参数；
- 不提供 1.7B、无 aligner、auto-language、streaming、hotword、prompt 或 CPU
  Profile；
- 1.7B 若需评估，必须提交新计划和新资源门禁；
- 后续 admission 开放是独立 R2/R3 生产变更，继续强制人工审核。

## 6. R2：离线代码接入计划

### 6.1 目标

在不安装真实依赖、不下载模型、不运行服务的条件下，增加严格 Qwen 配置、引擎适配
边界、模型 Manifest 校验、注册和纯 Fake 测试。

### 6.2 拟修改文件

| 文件 | 计划修改 |
|---|---|
| `src/transcription/profile.py` | 新增不可变 `Qwen3AsrRemoteConfig`，固定 service/model/aligner identity |
| `src/transcription/profile_catalog.py` | 新增 disabled experimental Profile |
| `api/transcription_runtime.py` | 注入现有 `RemoteAsrProvider` factory |
| `api/routes_transcription.py` | 只登记固定 provider key，不增加自由参数 |
| `asr_service/engine_protocol.py` | 新增固定 service config |
| `asr_service/engines/qwen3_asr.py` | 唯一 Qwen 动态导入/子进程适配边界 |
| `asr_service/model_cache.py` | 分别校验 ASR 与 aligner 的固定 Manifest |
| `asr_service/config.py` | 新增成对、可选的本地路径与 Manifest 配置 |
| `asr_service/app.py` | 依赖/缓存缺失时仅隐藏 Qwen Profile，不阻止现有引擎 |
| `asr_service/requirements-qwen3-asr.txt` | 只记录顶层固定候选，不接入生产安装入口 |
| `asr_service/*-manifest.example.json` | 严格无真实文件示例 |
| `tests/test_transcription_profile*.py` | Profile、快照、准入和交叉 identity 负向 |
| `tests/test_transcription_remote_provider.py` | 复用 Remote Provider/pipeline/normalizer |
| `tests/test_transcription_static_boundaries.py` | 禁止核心层导入 Qwen/torch/transformers |
| `asr_service/tests/test_qwen3_asr.py` | Fake 模块、输出转换、异常和时间戳边界 |
| `asr_service/tests/test_model_cache.py` | 双模型 Manifest、路径逃逸、hash/revision |
| `asr_service/tests/test_api_contract.py` | capabilities 排序、缺依赖 fail-closed |
| `project-docs/features/transcript-pipeline.md` | 仅记录“代码已接入、Profile disabled” |
| `TODO.md`、`WORKLOG.md` | 按项目规则记录审批与实际完成事实 |

实际实现前应再次用 `git diff` 确认主线变化；若现有所有权或文件名改变，更新计划并
重新审批。

### 6.3 明确不修改

- Candidate、Canonical、normalizer、pipeline、formatter、parser；
- 数据库 Schema、迁移、Store、任务状态、发布事务；
- 管理端 UI、上传格式、媒体路径、索引 Schema；
- 根 `requirements.txt`、`requirements-prod.txt`；
- 现有 `asr_service/requirements-windows.txt` 和生产部署脚本；
- SenseVoice、faster-whisper Profile/模型/服务配置；
- 生产 env、Scheduled Task、防火墙、端口、SQLite、Qdrant、模型缓存。

### 6.4 引擎输出适配

Qwen 引擎只可输出现有 `EngineCandidate`，再由既有 Remote Provider 转成
`ProviderCandidate`：

- `provider_key=qwen3-asr`；
- language 必须确定性映射为 `zh-CN`；
- duration 来自已验证音频元数据，不信任模型自由文本；
- ForcedAligner 的有序时间戳映射为 `CandidateSegment`；
- 拒绝空文本、空时间戳、NaN/Inf、负数、逆序、重叠越界和末段超过 duration；
- 不产生 Canonical warning/confidence；
- 模型输出、prompt 或第三方异常文本不得直接进入 API 错误或日志。

### 6.5 R2 验证

不安装真实 Qwen 依赖，只使用 Fake modules/subprocess：

```text
python -m pytest asr_service/tests/test_qwen3_asr.py
  asr_service/tests/test_model_cache.py
  asr_service/tests/test_api_contract.py -v
python -m pytest tests/test_transcription_profile.py
  tests/test_transcription_profile_catalog.py
  tests/test_transcription_remote_provider.py
  tests/test_transcription_static_boundaries.py -v
python -m pytest asr_service/tests tests/test_transcription*.py
  tests/test_transcript_manual_regression.py -v
python -m compileall -q api src asr_service tests
git diff --check
```

CI 必须证明测试收集不要求 qwen-asr、torch、模型、CUDA、SoX、服务或外网。

### 6.6 R2 完成标准

- Qwen Profile 与双模型 identity 严格冻结；
- Profile 为 `experimental + disabled`；
- 缺依赖/缓存/config 时 fail-closed 且不影响现有两个引擎；
- 所有输出只走 ProviderCandidate → normalizer → Canonical；
- 现有 contract/manual regression 不退化；
- 没有生产依赖、模型、服务或部署变化。

## 7. R3：一次性隔离资格验证计划

统一 R3 是一个审批、一个独立 PR/CI 交付、一个手动 workflow 和一个最终
PASS/FAIL 报告。原 R3A/R3B 仅保留为同一 workflow 内部的顺序门禁，不再要求中途
再次审批或人工续跑：

```text
仓库工具与测试
→ preflight
→ 依赖/wheel/许可证/CUDA 静态门禁
→ 双模型下载与 Manifest
→ 隔离服务与真实 CUDA
→ 8 样本业务全链路
→ 资源/生产末态/清理
→ 单一 PASS/FAIL
```

前置门禁失败时 workflow 自动停止在该点。例如依赖解析失败时不下载模型，模型
Manifest 失败时不启动服务，BGE 忙时不运行推理。一次性授权允许前置门禁全部通过后
自动继续，但不允许失败后更换模型、依赖、参数、样本、路径或阈值。

### 7.1 固定执行环境与目录

目标环境：既有 `production-asr` Windows GPU runner；不登录或修改 Ubuntu
backend，不接触真实业务媒体。

```text
资格根：
${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\qwen3-asr

每次 run：
${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\qwen3-asr\runs\<github.run_id>\

固定非敏感样本：
${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\qualification\qwen3-asr\inputs\

ASR 模型最终目录：
${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\models\Qwen3-ASR-0.6B\
5eb144179a02acc5e5ba31e748d22b0cf3e303b0\

Aligner 最终目录：
${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\models\Qwen3-ForcedAligner-0.6B\
c7cbfc2048c462b0d63a45797104fc9db3ad62b7\

隔离 ASR 地址：
http://127.0.0.1:18300
```

run-local venv、wheelhouse、spool、配置、日志、报告和 staging 均位于本次 run
目录。不得写入全局/用户 site-packages、用户 Hugging Face cache、现有生产 ASR
venv、GPU service venv 或仓库工作区。

### 7.2 拟新增和修改文件

| 文件 | 职责 |
|---|---|
| `.github/workflows/qualify-qwen3-asr-production.yml` | 默认关闭的单一手动 workflow；绑定完整 master SHA、`production-asr` Environment、Windows runner、并发锁和总超时 |
| `scripts/qualify-qwen3-asr-production.ps1` | preflight、wheelhouse、隔离 venv、模型、临时服务、样本、监控、清理和 verdict 总编排 |
| `scripts/prepare_qwen3_asr_models.py` | 固定双 repo/revision 下载、staging、全文件 Manifest、无 symlink 与幂等提升 |
| `scripts/run_qwen3_asr_qualification.py` | 严格样本 Manifest、完整业务链、质量/时间戳/RTF 指标和脱敏报告 |
| `scripts/prepare-qwen3-asr-qualification-samples.ps1` | 生成固定 8 个非敏感 Windows TTS/确定性噪声样本；默认不覆盖已有有效样本 |
| `asr_service/qwen3-asr-qualification-manifest.example.json` | 严格、不含真实音频的样本 Manifest 示例 |
| `asr_service/tests/test_qwen3_asr_qualification.py` | 无真实依赖的工具、门禁、失败关闭和报告测试 |
| `tests/test_asr_deployment_static.py` | workflow 默认关闭、固定目录/端口、无生产激活或防火墙改写 |
| `project-docs/features/transcript-pipeline.md` | 只按真实结果记录资格状态 |
| `TODO.md`、`WORKLOG.md` | 按实际实施、CI 和 workflow 结果收口 |

不得修改 R2 Profile/Provider/Canonical/normalizer/pipeline、数据库、前端、生产部署
脚本或现有 ASR/GPU 服务定义。若实际需要修改上述任一项，视为方案实质变化并停止。

### 7.3 Workflow 固定输入、权限与 Secret

workflow 只接受：

```text
commit_sha=<完整 40 位、等于 dispatch revision 且已合并 master>
execute_qualification=false|true（默认 false）
prepare_synthetic_samples=false|true（默认 false）
```

不得接受自由模型 ID/revision、依赖版本、device、dtype、batch、prompt、hotwords、
目录、端口、voice、样本文本或阈值。只使用既有 `production-asr` Environment 中已
批准的依赖代理、模型代理和 BGE probe 凭据；不得新增 Secret、写出 Secret 值或把
Token 放入命令行。临时 ASR service 使用进程内随机 Token，finally 后失效。

### 7.4 统一执行步骤

#### A. Preflight

1. 校验完整 master SHA、runner 身份、Windows/Python 3.11 x64 和 35 GiB 空间；
2. 校验 8100/8200 健康，现有 Scheduled Task 与防火墙快照正常；
3. 要求 BGE `model_loaded=true`、`inflight_requests=0`、
   `asr_chunk_allowed=true`；
4. 校验 18300 未监听，无本方案拥有的活动资格进程；
5. 校验固定 8 样本 Manifest；如显式启用样本准备，仅生成仓库冻结的非敏感文本；
6. 记录脱敏 GPU baseline、生产 capabilities 和配置身份。

任一失败不创建 venv、不下载依赖或模型。

#### B. 依赖、许可证与 CUDA 静态门禁

1. 只读记录当前生产 ASR freeze/`pip check`，不修改该 venv；
2. 在 run 目录创建 Python 3.11 venv 和 wheelhouse；
3. 解析 `torch==2.7.0+cu128`、`qwen-asr==0.0.6` 及实际传递依赖；
4. 只接受 Windows x64 binary wheel，拒绝 sdist、VCS、editable 和浮动 branch；
5. 记录 URL、文件名、大小、SHA-256，随后从同一 wheelhouse 离线安装；
6. 执行 `pip check`、freeze、模块来源和许可证审计；
7. 验证 `torch.cuda.is_available()` 与 BF16 支持，但尚不加载模型；
8. 拒绝生产/用户/global site 泄漏，以及 GPL/AGPL/SSPL/未知许可证。

任一失败不下载模型、不启动服务。

#### C. 双模型准备

1. 只请求计划固定的两个官方 repo/revision；
2. 下载至本次 run staging，禁止运行时联网和用户 cache；
3. 拒绝 symlink、非常规文件、路径逃逸、空目录和不完整快照；
4. 全文件记录路径、大小和 SHA-256，生成两个严格 Manifest；
5. 用 R2 `validate_qwen3_asr_cache()` / `validate_qwen3_aligner_cache()` 交叉验证；
6. 最终目录已存在且有效则只读复用；存在但无效则停止，不覆盖、不删除；
7. staging 验证成功后才原子提升，失败 staging 保留审计但不发布。

#### D. 隔离服务与真实 CUDA

1. 加载固定 ASR 与 aligner，固定 Transformers、CUDA BF16、batch 1、Chinese、
   timestamps enabled、无 FlashAttention/CPU/INT8 fallback；
2. 仅以精确 PID 启动 loopback 18300 临时 ASR service；
3. 临时 capabilities 必须包含现有两个 Profile 与
   `qwen3-asr-06b-aligner-v1`，生产 8200 capabilities 必须保持不变；
4. `/health`、认证、错误脱敏和双模型 identity 必须通过；
5. 运行期间持续检查 BGE/8100/8200；任何异常立即停止资格 PID。

#### E. 8 样本全链路与末态

使用 5 个正向、3 个负向的固定自制/公开授权样本：

- 清晰普通中文；
- BIM 术语；
- 规范编号；
- 带噪声 BIM 中文；
- 中英混合；
- 三个不含目标术语/编号的负向控制。

每条必须走既有多引擎业务链，不得直接调用模型后计算指标：

```text
WAV
→ 隔离 Qwen engine / loopback ASR service
→ RemoteAsrProvider / TranscriptionProvider
→ ProviderCandidate
→ pipeline / normalizer
→ Canonical
→ Markdown formatter
→ transcript parser
```

最后必须终止并等待精确资格 PID，确认 18300 释放、无资格子进程残留、8100/8200
健康、Scheduled Task/防火墙快照未变，且 Qwen application Profile 仍 disabled。

### 7.5 质量与资源门禁

质量阈值沿用现有 faster-whisper 资格基线，执行前冻结：

| 门禁 | 阈值 |
|---|---|
| 处理失败率 | `0%` |
| 清晰样本 CER | `<= 0.10` |
| BIM/噪声样本 CER | `<= 0.15` |
| BIM 术语召回 | `>= 0.70` |
| 规范编号召回 | `>= 0.95` |
| 起始时间戳 P95 漂移 | `<= 1500 ms` |
| 每条样本 RTF | `<= 0.60` |
| 负向目标词/编号新增误报 | `0` |
| Canonical/Markdown/parser | 全部成功且复跑确定性一致 |

资源门禁：

```text
整卡峰值显存 < 14 GiB
相对空闲 baseline 的峰值显存增量 < 8 GiB
BGE 全程 model_loaded=true
BGE 全程 inflight_requests=0
BGE 全程 asr_chunk_allowed=true
无 CUDA OOM
无 CPU/INT8 fallback
8100/8200 全程健康且 capabilities 不变
```

0.6B ASR 与 0.6B aligner 即使参数量较小，也不得据此假定低于门禁；必须记录模型
加载、单条推理和清理后的实测峰值。

### 7.6 自动停止条件

任一条件即 FAIL：

- commit、runner、目录、生产快照或样本身份不符；
- resolver、wheel、许可证、`pip check` 或模块来源失败；
- 需要修改现有生产 venv、torch、CUDA、驱动或系统 SoX；
- 模型 revision/hash/Manifest 不符；
- BF16、CUDA、音频解码或 ForcedAligner 不可用；
- 需要 FlashAttention、本机编译、vLLM、WSL、CPU/INT8 fallback；
- 时间戳/质量/RTF/显存门禁失败；
- BGE 变忙、生产服务不健康、OOM、超时或清理失败；
- 需要换 1.7B、调 prompt/hotwords、改 normalizer/阈值；
- 无法证明只终止本次资格 PID；
- 用户要求停止。

技术性网络瞬断允许同参数、有界重试；门禁或质量失败不得重试、换模型、调参、开放
Profile 或清理审计目录。workflow 无论在哪个阶段失败都必须执行末态检查并生成单一
FAIL 报告。

### 7.7 R3 验证与证据

仓库工具/测试阶段不需要真实依赖：

- workflow 默认关闭、完整 SHA、Environment、concurrency、timeout；
- run-local 目录、精确 PID、端口和 finally 清理；
- wheelhouse/许可证/Manifest 严格 Schema 与负向测试；
- 双模型交叉 identity、路径逃逸、symlink、hash/revision；
- Secret/文本/音频不进入报告；
- BGE busy、OOM、timeout、orphan、cleanup failure；
- 现有 SenseVoice/faster-whisper/Remote Provider/Canonical 回归。

真实 R3 证据至少包含：

```text
preflight.json
wheel-manifest.json
license-matrix.json
asr-model-manifest.json
aligner-model-manifest.json
gpu-samples.jsonl
sample-results.json
qualification-summary.json
cleanup-summary.json
```

GitHub 只上传脱敏摘要；音频、reference、hypothesis、Token、环境变量和客户路径不
上传。完整证据保存在 Administrator ACL 保护的本机 run 目录。

### 7.8 一次性完成标准

统一 R3 只有以下全部成立才为 PASS：

1. R3 工具 PR 经 scoped review、干净 CI 并合并；
2. workflow 绑定合并后的完整 master SHA；
3. preflight、依赖、许可证、CUDA 静态门禁全部通过；
4. 双模型 immutable identity 与全文件 Manifest 通过；
5. 真实 CUDA BF16 加载、隔离 18300 与服务契约通过；
6. 8/8 样本和全部质量、时间戳、RTF、显存门禁通过；
7. Candidate → Canonical → Markdown → parser 全链路通过；
8. BGE、生产 ASR/GPU、端口、任务、防火墙、数据库和索引未变；
9. 18300 关闭、资格 PID/子进程无残留；
10. Profile 仍 `experimental + disabled`；
11. 形成脱敏、可复核的唯一 PASS 报告。

## 8. 风险、兼容性与回滚

### 8.1 风险

- `qwen-asr` 依赖面明显大于现有引擎，存在 resolver、native wheel 和许可证风险；
- 官方建议独立 Python 3.12 环境，而生产 ASR 当前按 Python 3.11/torch cu128 固定；
- Transformers 在 Windows 可候选，但 Qwen 完整栈没有已核实的 Windows 资格声明；
- 时间戳要求额外 ForcedAligner，增加模型磁盘、显存、延迟和失败面；
- RTX 5060 Ti/Blackwell 与 torch 2.7 cu128/BF16 的组合必须实机证明；
- 共卡资格运行可能影响 BGE 或现有 ASR；
- 官方 benchmark 不能替代 BIM、噪声、编号和时间戳的本项目评测；
- 浮动传递依赖和模型 revision 会造成不可复现，必须先冻结再执行。

### 8.2 回滚

- R2：revert 单一接入提交；Profile 始终 disabled，无数据或索引回滚；
- R3 进程：finally 只终止精确资格 PID，释放隔离端口；
- R3 venv/wheel/report：全部 run-local，未被生产引用，失败后保留审计；
- 模型：staging 失败不提升；无效既有目录不覆盖、不删除；
- 生产：不修改现有 ASR/GPU Scheduled Task、env、防火墙、Ubuntu backend；
- 数据：不创建业务任务，不接触 SQLite/Qdrant，不发布、不索引；
- 清理：不自动递归删除；后续按精确目录另行 R3 确认。

## 9. 明确不做

- 本轮计划编制不执行任何 R3 操作；
- 获批 R3 只在 run-local venv 安装固定依赖、下载两个固定模型并启动 loopback
  临时服务，不修改现有生产 venv、模型目录内容或生产服务；
- 不启用 Qwen Profile；
- 不替换 SenseVoice 或 faster-whisper；
- 不采用 DashScope/外部 API，不外发业务音频；
- 不采用 vLLM、WSL、社区 Windows fork 或官方 `latest` Docker 镜像；
- 不做 streaming、说话人分离、热词、微调或自动语言路由；
- 不修改 normalizer/Canonical 以迁就 Qwen 输出；
- 不修改质量阈值或样本来获得 PASS；
- 不自动进入生产部署、admission 开放或真实业务验收。

## 10. 审批事项

### 10.1 R2 审批

需确认：

1. 固定 `0.6B + ForcedAligner 0.6B + Transformers` 为唯一首轮候选；
2. 接受新增严格 Profile/config/engine adapter，但保持 disabled；
3. 接受独立 Qwen engine venv/子进程边界，不并入现有 ASR venv；
4. 接受 R2 只做 Fake 测试，不安装或运行真实依赖。

建议审批语句：

```text
批准 Qwen3-ASR R2 离线接入计划；固定 0.6B ASR、0.6B ForcedAligner 和
Transformers backend，使用独立 Qwen engine 运行边界，保持 application Profile
experimental + disabled。只实施计划列出的契约、适配器、Manifest 和 Fake 测试，
不安装依赖、不下载模型、不启动服务、不修改生产状态。
```

### 10.2 统一 R3 一次性审批

一次批准需逐项确认：

1. 两个固定官方模型及 immutable revision；
2. 固定 8 个非敏感样本与本文阈值；
3. 允许创建 run-local venv/wheelhouse 并安装固定依赖；
4. 允许前置门禁通过后自动下载双模型、运行真实 CUDA BF16 与 loopback 18300；
5. 允许与 BGE 共卡但只在空闲门禁下运行；
6. 允许同参数网络瞬断有界重试，门禁失败不调参或换候选；
7. 失败保留审计 artifact，不自动删除；
8. PASS 后仍不自动开放 Profile、修改生产服务或创建业务任务。

建议审批语句：

```text
批准 Qwen3-ASR 统一 R3 一次性资格验证方案。允许在 production-asr Windows
runner 上完成仓库工具、测试、PR/CI/合并及一次绑定完整 master SHA 的手动
workflow；使用 run-local venv/wheelhouse、固定 qwen-asr==0.0.6、Qwen3-ASR-0.6B
和 ForcedAligner-0.6B immutable revision、固定 8 个非敏感样本、loopback 18300
与真实 CUDA BF16。前置门禁通过后可自动继续后续阶段，任一门禁失败立即停止并完成
清理/脱敏报告；不修改生产 ASR/GPU 服务、现有 venv、Ubuntu、防火墙、数据库、
Qdrant 或 Profile admission，不使用真实业务媒体，失败 artifact 保留审计。
```

## 11. 最终完成条件

Qwen3-ASR 只有在以下全部完成后才可称为“资格通过”：

1. R2 独立 PR、CI、审查和合并完成，Profile disabled；
2. 统一 R3 的固定 wheel、许可证、CUDA 和依赖门禁 PASS；
3. 同一 workflow 的双模型 Manifest、真实 CUDA、8/8 样本、质量/时间戳/RTF/
   资源全部 PASS；
4. Candidate → normalizer → Canonical → Markdown → parser 全链路通过；
5. BGE、现有 ASR、端口、Scheduled Task、防火墙、数据库和索引状态未变；
6. 临时端口释放、资格进程无残留；
7. 形成脱敏、可复核的单一 PASS/FAIL 报告；
8. application Profile 仍为 `experimental + disabled`。

任何部分通过、单样本成功、只导入成功、只看官方 benchmark、资源未测或清理未确认
均不得写成资格通过。
