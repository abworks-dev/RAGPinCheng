# faster-whisper Phase 0 CPU-only 静态预检报告

> 状态：已完成静态预检；可作为单独 R3 实测方案的输入，但不构成安装、GPU 兼容、质量门禁或 Phase 0 通过结论。
> 风险等级：R2（涉及候选 ASR 后端、依赖与 GPU 兼容性判断，但本次只写公开证据报告）。
> 执行日期（Asia/Shanghai）：2026-08-01。
> 审批依据：用户明确批准“R2 CPU-only 预检方案”。

## 1. 目标与当前依据

本报告在不安装任何依赖、不下载模型、不运行推理、不连接生产机的前提下，对 `faster-whisper` 作为下一轮 Phase 0 候选进行静态预检，回答以下问题：

1. 是否能固定可复现的包版本、模型仓库和不可变 revision；
2. Windows x64 + Python 3.10 是否存在声明级可用 wheel；
3. 与现有 FunASR Phase 0 顶层依赖是否存在非空版本区间；
4. CTranslate2 对 CUDA 12.8、Blackwell `sm_120` 和 Windows Whisper GPU 的官方发布说明是否提供进入实测的依据；
5. 中文、时间戳、VAD、热词和资源预算有哪些静态能力与限制；
6. 哪些门禁仍必须由另批 R3 实测完成。

当前仓库依据：

- `scripts/funasr_phase0/setup_venv.ps1` 固定生产侧解释器入口为 Python 3.10，并将 PyTorch CUDA 版本固定为 `torch==2.7.0` / `torchaudio==2.7.0` 的 cu128 wheel；
- `scripts/funasr_phase0/requirements-asr.txt` 固定现有 FunASR 沙箱的顶层依赖范围；
- `docs/plans/funasr-phase0-execution-plan.md` 保留 ASR 峰值显存 `< 8 GB`、ASR 与 BGE 合计 `< 14 GB`、磁盘预算 `30 GB` 等门禁；
- 既有 Contextual Paraformer 固定 A/B 未通过预注册质量门禁，因此可调查替代后端，但不能据此跳过新候选的独立门禁。

## 2. 本次明确不做

本次仅做公开元数据和源码级核验，未执行且未授权执行：

- 安装、升级或卸载 Python 包；
- 创建或修改 venv；
- 下载 wheel、模型、Git LFS 对象或其他 artifact；
- 对 wheel 或模型文件进行本地 SHA-256 重算；
- CPU 或 GPU 推理、CUDA/DLL 冒烟、短样本或长音频测试；
- 与 BGE 的同卡共存压测；
- 读取 `.env`、Token、密钥或客户数据；
- SSH 或生产机操作；
- 修改源码、依赖文件、模型配置、热词、reference、阈值或冻结样本；
- 宣告 Phase 0 通过或进入 Phase 1。

## 3. 推荐固定候选

| 项目 | 固定候选 | 静态结论 |
|---|---|---|
| Python | 3.10 x64 | 与现有 Phase 0 入口一致；`faster-whisper 1.2.1` 声明 `python_requires >= 3.9` |
| faster-whisper | `faster-whisper==1.2.1` | 固定发布版本 |
| CTranslate2 | `ctranslate2==4.8.1` | 固定 Windows/Python 3.10 wheel 候选，并包含 CUDA 12.8 / Blackwell 之后的发布改动 |
| 模型仓库 | `dropbox-dash/faster-whisper-large-v3-turbo` | 使用 canonical 仓库，不只记录可漂移别名 |
| 模型 revision | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | immutable Git revision |
| 模型许可证 | MIT | Hugging Face 仓库声明 |
| `model.bin` 大小 | `1,617,884,929` bytes | Hugging Face Git LFS pointer 公开值 |
| `model.bin` LFS SHA-256 | `e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31` | Hugging Face 文件页公开的 LFS 对象 SHA-256；本次未下载、未独立重算 |

### 3.1 模型身份说明

`faster-whisper v1.2.1` 源码中的内建 `turbo` 别名指向：

```text
mobiuslabsgmbh/faster-whisper-large-v3-turbo
```

该旧命名空间当前会重定向到 Dropbox canonical 仓库。为了避免别名映射或默认分支继续漂移，后续如获批下载，必须同时固定：

```text
model_id=dropbox-dash/faster-whisper-large-v3-turbo
model_revision=0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
```

不得仅传入 `turbo` 并依赖运行时默认解析。

### 3.2 哈希证据边界

Hugging Face 文件页显示 `model.bin` 最近一次文件提交为 `0c9461c6c9ac56795054ce26432a53e127623d9c`；选定仓库 revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` 的树包含该文件。文件页公开的 Git LFS pointer 为：

```text
oid sha256:e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31
size 1617884929
```

这只是托管方公开 provenance，不是本机哈希实算。任何 R3 下载都必须在下载完成后重新计算 SHA-256，并与本节值逐字节比较；不一致应立即停止，不得运行模型。

## 4. 官方证据与 provenance

| 证据 | 用途 | 官方来源 |
|---|---|---|
| faster-whisper `v1.2.1` requirements | 直接依赖范围 | <https://raw.githubusercontent.com/SYSTRAN/faster-whisper/v1.2.1/requirements.txt> |
| faster-whisper `v1.2.1` setup | `python_requires >= 3.9`、包元数据 | <https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/setup.py> |
| faster-whisper `v1.2.1` model aliases | `turbo` 别名映射 | <https://raw.githubusercontent.com/SYSTRAN/faster-whisper/v1.2.1/faster_whisper/utils.py> |
| faster-whisper `v1.2.1` transcribe source | 时间戳、VAD、hotwords、prefix 行为 | <https://raw.githubusercontent.com/SYSTRAN/faster-whisper/v1.2.1/faster_whisper/transcribe.py> |
| CTranslate2 `v4.6.2` release | Blackwell `sm_120` 编译修复、`sm_120` INT8 限制 | <https://github.com/OpenNMT/CTranslate2/releases/tag/v4.6.2> |
| CTranslate2 `v4.6.3` release | CUDA 12.8 支持、纯 CUDA Conv1D/median filter | <https://github.com/OpenNMT/CTranslate2/releases/tag/v4.6.3> |
| CTranslate2 `v4.8.1` release/changelog | Windows GPU / cuDNN / library loading 相关更新 | <https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.1>；<https://github.com/OpenNMT/CTranslate2/blob/v4.8.1/CHANGELOG.md> |
| CTranslate2 安装文档 | 官方 CUDA/cuDNN 安装说明及文档差异 | <https://opennmt.net/CTranslate2/installation.html> |
| 模型 immutable tree | 固定仓库 revision | <https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/tree/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf> |
| 模型文件页 | Git LFS SHA-256 和大小 | <https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/blob/0c9461c6c9ac56795054ce26432a53e127623d9c/model.bin> |
| PyPI Simple Index | wheel 文件名存在性 | <https://pypi.org/simple/faster-whisper/>、<https://pypi.org/simple/ctranslate2/>、<https://pypi.org/simple/av/>、<https://pypi.org/simple/onnxruntime/>、<https://pypi.org/simple/tokenizers/>、<https://pypi.org/simple/huggingface-hub/> |

核验限制：PowerShell `Invoke-RestMethod` / `curl.exe` 在本环境访问部分官方 JSON API 时出现 Schannel TLS handshake 失败，因此本次未把 JSON API 作为第二条独立证据链，也没有下载或缓存 artifact。报告采用官方源码、发布页、文件页和 Simple Index 的可见元数据。

## 5. Windows x64 / Python 3.10 wheel 静态矩阵

`faster-whisper 1.2.1` 的直接依赖为：

```text
ctranslate2>=4.0,<5
huggingface_hub>=0.21
tokenizers>=0.13,<1
onnxruntime>=1.14,<2
av>=11
tqdm
```

PyPI Simple Index 中可见以下满足声明范围的 Windows/Python 3.10 候选文件：

| 包 | 静态证据文件 | 解释 |
|---|---|---|
| faster-whisper | `faster_whisper-1.2.1-py3-none-any.whl` | 通用 Python wheel |
| CTranslate2 | `ctranslate2-4.8.1-cp310-cp310-win_amd64.whl` | Python 3.10 / Windows x64 原生 wheel |
| PyAV | `av-16.0.1-cp310-cp310-win_amd64.whl` | Python 3.10 / Windows x64 原生 wheel |
| ONNX Runtime | `onnxruntime-1.23.2-cp310-cp310-win_amd64.whl` | Python 3.10 / Windows x64 原生 wheel |
| tokenizers | `tokenizers-0.22.1-cp39-abi3-win_amd64.whl` | `abi3` wheel，覆盖 Python 3.10；满足双方范围 |
| huggingface-hub | `huggingface_hub-0.36.0-py3-none-any.whl` | 通用 Python wheel；满足双方范围 |

结论：**wheel 文件存在性为 `STATIC_PASS`**。

但这些文件名只是静态候选，不是完整 lockfile。由于未运行 pip resolver、未下载 wheel、未核对 wheel hash、未执行 `pip check`，不能把本节解释为“环境可安装”或“依赖已兼容”。`tqdm` 及所有传递依赖也必须在 R3 resolver 输出中固定。

## 6. 与现有 FunASR venv 的声明级兼容矩阵

现有 `scripts/funasr_phase0/requirements-asr.txt` 与 `faster-whisper 1.2.1` 直接依赖的交集如下：

| 包 | 现有 FunASR 范围 | faster-whisper 范围 | 非空交集 | 静态状态 |
|---|---|---|---|---|
| `av` | `>=12,<19` | `>=11` | `>=12,<19` | `STATIC_PASS` |
| `onnxruntime` | `>=1.17,<2` | `>=1.14,<2` | `>=1.17,<2` | `STATIC_PASS` |
| `huggingface-hub` | `>=0.34,<1` | `>=0.21` | `>=0.34,<1` | `STATIC_PASS` |
| `tokenizers` | `>=0.20,<0.24` | `>=0.13,<1` | `>=0.20,<0.24` | `STATIC_PASS` |
| `tqdm` | `>=4.66` | 无下限 | `>=4.66` | `STATIC_PASS` |
| `ctranslate2` | 未声明 | `>=4,<5` | 新增依赖 | `R3_REQUIRED` |

`faster-whisper` 和 CTranslate2 的顶层包元数据不要求替换 PyTorch，因此静态上不需要改动现有 `torch==2.7.0+cu128`。这只说明“没有直接声明冲突”，不代表 native runtime 一定能在同一 venv 中共存。

必须留给 R3 的实际兼容检查包括：

1. 在隔离副本/新 venv 中运行完整 resolver；
2. 固定全部传递依赖和 wheel hash；
3. 安装后执行 `pip check`；
4. 分别导入 FunASR、PyTorch、CTranslate2、PyAV、ONNX Runtime；
5. 检查 Windows `PATH`、CUDA、cuDNN 与 DLL 加载顺序；
6. 验证 FunASR 与 faster-whisper 在同一 venv 中的启动/退出和残留进程；
7. 如共存失败，优先使用独立 venv，不得为迁就新后端破坏现有 FunASR 环境。

结论：

```text
顶层版本区间交集：STATIC_PASS
完整 resolver / pip check：R3_REQUIRED
同 venv native runtime 共存：R3_REQUIRED
```

## 7. CUDA 12.8 / RTX 5060 Ti / Blackwell `sm_120` 静态矩阵

| 维度 | 官方静态证据 | 结论 |
|---|---|---|
| CUDA 12.8 | CTranslate2 `4.6.3` 发布说明增加 CUDA 12.8 支持；候选 `4.8.1` 晚于该版本 | `STATIC_PASS`（发布说明级） |
| Blackwell `sm_120` | `4.6.2` 修复 `sm_120` 编译，并明确处理 `sm_120` 的 INT8 限制 | `STATIC_PASS`（代码/发布说明级） |
| Windows Whisper GPU | `4.8.1` 发布信息包含 Windows GPU library loading/fix，并说明 Windows wheel 为 Whisper GPU 使用启用 cuDNN 构建 | `STATIC_PASS`（wheel 构建说明级） |
| PyTorch cu128 共存 | faster-whisper/CTranslate2 不以 PyTorch 为推理后端；但二者会各自加载 native CUDA 运行时 | `R3_REQUIRED` |
| RTX 5060 Ti 实机 | 未连接生产机、未加载 DLL、未运行 `ctranslate2.get_cuda_device_count()` 或模型 | `R3_REQUIRED` |
| INT8 on `sm_120` | `4.6.2` 曾明确禁用 `sm_120` INT8；本次没有足够静态证据证明首轮可安全依赖 INT8 | 首轮不得选用 |

官方文档仍有不一致：

- faster-whisper README 的通用说明是当前 CTranslate2 需要 CUDA 12 + cuDNN 9；
- CTranslate2 installation 页的 Windows speech-model 段仍写 CUDA 12.1 + cuDNN 8；
- CTranslate2 后续 release notes 又明确加入 CUDA 12.8、Blackwell 和 Windows Whisper GPU 相关改动。

因此，发布说明足以支持“进入受控 R3 冒烟”，但不足以静态宣告 RTX 5060 Ti 可运行。首轮 R3 建议：

```text
device=cuda
compute_type=float16
```

不得在首轮使用 INT8 绕过显存门禁，也不得把 CPU 成功导入等价为 GPU 兼容。

## 8. 许可证静态矩阵

| 对象 | 声明许可证 | 静态状态 | 后续要求 |
|---|---|---|---|
| `SYSTRAN/faster-whisper` | MIT | `STATIC_PASS` | R3 固定 sdist/wheel provenance |
| `OpenNMT/CTranslate2` | MIT | `STATIC_PASS` | R3 固定 wheel 及许可证文件 |
| `dropbox-dash/faster-whisper-large-v3-turbo` | MIT | `STATIC_PASS` | R3 下载后保存 model card/LICENSE 快照与哈希 |
| 传递依赖 | 各自许可证 | `R3_REQUIRED` | 以 resolver 最终清单运行现有 license audit，不得只审顶层包 |

本节只核验公开声明，不构成法律意见，也不替代下载 artifact 后的许可证文件核验。

## 9. 磁盘与显存规划

### 9.1 磁盘

- Hugging Face 仓库页面显示仓库约 `1.62 GB`；
- `model.bin` 的 Git LFS pointer 大小为 `1,617,884,929` bytes（约 `1.51 GiB`）；
- R3 隔离预检工作区建议至少预留 `5 GB`，用于模型、wheel cache、日志和临时文件；
- 父 Phase 0 已批准的 `30 GB` 磁盘预算保持不变，不能因本次静态估算而降低。

### 9.2 显存

faster-whisper 官方 README 的 large-v2 benchmark 给出约：

- GPU FP16：`4525 MB`；
- GPU INT8：`2926 MB`。

这些数字不是 `large-v3-turbo + CTranslate2 4.8.1 + RTX 5060 Ti + Windows` 的实测，不能直接作为本项目通过证据。静态规划可暂用 `3–5 GiB` 作为观察区间，但硬门禁仍沿用父计划：

```text
单 ASR 峰值显存 < 8 GiB
ASR + BGE 合计显存 < 14 GiB
```

由于 `sm_120` 的 INT8 支持存在明确历史限制，首轮 R3 必须用 FP16 测量，不得用 INT8 估算替代 FP16 门禁。

结论：`ESTIMATE_ONLY / R3_REQUIRED`。

## 10. 中文、时间戳、VAD 与热词能力边界

| 能力 | faster-whisper 静态能力 | 本项目结论 |
|---|---|---|
| 中文识别 | large-v3-turbo 为 multilingual Whisper 模型，可指定中文 | 有候选能力；中文/BIM 术语质量必须实测 |
| segment timestamps | 普通转录默认生成 segment start/end | API 能力存在；实际边界精度必须实测 |
| word timestamps | 可设 `word_timestamps=True` | API 能力存在；中文词粒度和稳定性必须实测 |
| VAD | `vad_filter` 可启用；batched API 默认启用，普通 `WhisperModel.transcribe` 默认不启用 | 必须在 R3 固定调用方式，不能依赖隐式默认值 |
| hotwords | 支持 `hotwords` 解码提示 | 仅是提示机制，不等价于 Contextual Paraformer 模型级 contextual biasing |
| hotwords + prefix | 源码只在 `prefix is None` 时把 hotwords 加入 prompt | 同时使用时 hotwords 不生效；R3 配置必须二选一并预注册 |
| initial prompt | 支持初始提示 | 需测试跨段传播、幻觉和负例退化，不得直接当术语注入成功 |

必须避免的过度结论：

- “支持 hotwords”不等于 BIM 术语召回必然提高；
- “支持中文”不等于满足冻结样本 CER/术语召回门禁；
- “有时间戳字段”不等于长音频时间戳连续、准确或可恢复；
- CPU 静态导入或 CPU 推理成功不等于 CUDA/Blackwell 成功；
- batched 与非 batched 默认 VAD 行为不同，不能混用结果。

## 11. 静态预检门禁结果

| 门禁 | 状态 | 说明 |
|---|---|---|
| 固定包版本 | `STATIC_PASS` | `faster-whisper==1.2.1`、`ctranslate2==4.8.1` |
| 固定模型身份/revision | `STATIC_PASS` | canonical repo + immutable revision 已记录 |
| 模型公开 size/LFS hash | `STATIC_PASS` | 仅托管方公开 pointer；未下载、未实算 |
| Python 3.10 Windows x64 wheel 存在性 | `STATIC_PASS` | Simple Index 可见候选文件 |
| 与现有顶层依赖范围交集 | `STATIC_PASS` | 主要共享依赖均有非空交集 |
| 完整 resolver / wheel hash / `pip check` | `R3_REQUIRED` | 本次未安装、未下载 |
| CUDA 12.8 / `sm_120` 发布说明支持 | `STATIC_PASS` | 只到发布说明/代码级 |
| RTX 5060 Ti DLL 与真实推理 | `R3_REQUIRED` | 未连接生产机、未加载 native runtime |
| 中文/BIM 术语质量 | `R3_REQUIRED` | 未运行冻结样本 |
| 时间戳实际质量 | `R3_REQUIRED` | 未运行音频 |
| hotwords 收益与反例退化 | `R3_REQUIRED` | 未做 A/B |
| 显存与 BGE 共存 | `R3_REQUIRED` | 未做 GPU 测量 |
| Phase 0 质量通过 | **未通过 / 未执行** | 静态预检不能替代 Phase 0 |
| Phase 1 | **未授权** | 不得进入实现 |

## 12. 总判断

推荐固定候选具备进入**单独 R3 安装与冒烟方案**的静态前置条件：

```text
faster-whisper==1.2.1
ctranslate2==4.8.1
model_id=dropbox-dash/faster-whisper-large-v3-turbo
model_revision=0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
compute_type(first R3 smoke)=float16
```

但本报告的结论严格限定为：

> Windows/Python 3.10 wheel、顶层依赖区间、模型固定身份以及 CTranslate2 对 CUDA 12.8 / `sm_120` 的官方发布说明，均提供了进入受控 R3 的静态依据。该依据不构成安装成功、同 venv 共存、RTX 5060 Ti GPU 兼容、中文质量、热词收益、显存门禁、Phase 0 通过或 Phase 1 授权。

## 13. 后续 R3 建议与停止条件

如用户另行批准 R3，建议拆为最小阶段，不自动连跑：

1. **artifact 固定阶段**：在隔离目录下载固定 wheel 和模型 revision，记录 URL、大小、SHA-256、许可证；任一 hash/provenance 不一致立即停；
2. **resolver 阶段**：新建独立 venv 或现有沙箱副本，固定完整依赖树，运行 `pip check` 与 license audit；不得覆盖现有可工作的 FunASR venv；
3. **CPU/import 阶段**：只验证导入、模型元数据和 API 参数，不把 CPU 结果当质量/GPU 结论；
4. **CUDA 冒烟阶段**：RTX 5060 Ti 上以 FP16 运行最小短样本，记录 DLL、driver、CTranslate2、CUDA、cuDNN、峰值显存和退出清理；
5. **冻结 8 样本 A/B**：固定 baseline、hotwords/prompt 策略和负例，沿用预注册指标，不得看结果后调参再宣告原方案通过；
6. 只有短样本与质量门禁通过后，才可另批长音频和 BGE 共存；任何阶段失败立即停止，不自动降级到 INT8、CPU 或其他模型后继续宣告成功。

以下情况必须停止并报告：

- wheel/model provenance 或 hash 不一致；
- resolver 冲突、`pip check` 失败或许可证 blocker；
- CTranslate2/CUDA/cuDNN/DLL 加载失败；
- 需要修改生产 PyTorch、BGE、PATH、系统 CUDA 或现有 FunASR 环境；
- 峰值显存触及父计划门禁；
- BGE 健康或延迟退化；
- 正向样本未达门禁或负例发生不可接受退化；
- 需要扩大到长音频、生产服务或 Phase 1。

## 14. 风险与回滚

- 本次仓库变更仅为新增本报告；实际交付证据以 Git/PR/workflow 为准；
- 未修改依赖、源码、配置、数据库、模型或生产状态；
- 若需回滚，只需恢复本报告对应的文档提交；
- 任何后续安装、模型下载、哈希实算、CUDA 冒烟、冻结样本 A/B、长音频或 BGE 共存均属于新的 R3 执行面，必须另行提交方案并获得明确批准。
