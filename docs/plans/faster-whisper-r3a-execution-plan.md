# faster-whisper Phase 0 R3-A 详细执行计划（artifact、隔离安装与 CUDA 最小冒烟）

> 状态：**待用户审批；未执行 R3-A**  
> 风险等级：**R3（生产 Windows GPU 主机、外部下载、隔离依赖安装、模型权重、GPU/BGE 与临时鉴权材料）**  
> 编制日期：**2026-07-31**  
> 审批边界：本文件是候选执行方案，不构成 Codex 自行批准；只有用户在看到本计划、补齐 §18 的决定并明确回复“批准执行”或同等授权后，才能进入执行。计划发生实质变化、范围扩大或风险升高时重新审批。

## 1. 目标与当前依据

### 1.1 R3-A 目标

在不改业务代码、不改现有 FunASR 环境、不停止 BGE、不使用客户音频的前提下，回答以下最小问题：

1. 固定的 faster-whisper/CTranslate2 wheel 能否在生产 Windows x64 + Python 3.10 上被完整解析、下载、哈希固定并离线安装到全新隔离 venv；
2. 固定模型 revision 能否下载、逐文件计算 SHA-256，并与公开的 `model.bin` Git LFS identity 核对；
3. 最终依赖树和模型许可门禁能否达到 blocker=0，或获得精确、限时、配置绑定的人工批准；
4. CTranslate2、PyAV、ONNX Runtime、faster-whisper 及其 CUDA/cuDNN/DLL 链能否在 RTX 5060 Ti 上真实加载；
5. 一个自制、非内部、非客户的短样本能否以 `device=cuda`、`compute_type=float16` 完成最小中文推理并正常退出；
6. 峰值显存、磁盘、残留进程和 BGE 前后健康是否满足父 Phase 0 安全门禁。

R3-A 只验证 artifact、隔离安装和最小 CUDA 兼容性，**不评价冻结 8 样本质量是否通过**，也不决定是否替换 FunASR。

### 1.2 已有静态依据

依据 `project-docs/plans/faster-whisper-phase0-precheck.md`：

- Python 3.10 / Windows x64 wheel 存在性：`STATIC_PASS`；
- 与现有顶层依赖范围存在交集：`STATIC_PASS`；
- CUDA 12.8 / Blackwell `sm_120` 有 CTranslate2 官方发布说明级依据：`STATIC_PASS`；
- 完整 resolver、wheel/model 实算哈希、`pip check`、传递依赖许可、DLL、RTX 5060 Ti 推理、质量、显存与 BGE 共存：仍为 `R3_REQUIRED`；
- Phase 0 尚未通过，Phase 1 尚未授权。

日期一致性说明：本计划当前日期为 `2026-07-31`，但现有静态预检报告正文标注执行日期为 `2026-08-01`。后者是晚于本计划编制日的日期。R3-A 执行前必须由用户明确说明该日期是否源自时区口径，并在不修改报告时确认仅按其内容哈希作为技术草案输入；若需修改报告日期或其他内容，则其 SHA-256 会变化，本计划必须同步更新并重新审批。日期差异未解决时，R3-A 状态为 `BLOCKED`。
静态预检报告当前 SHA-256：

```text
2edb7c53fc9aec9818eec6be70fd1fa3873d3ce4b0900d7c53e819a9fee9717e
```

执行开始时必须重新计算该报告 SHA-256；不一致则停止并请求重新审批。

## 2. 固定候选与不可变身份

R3-A 只允许以下候选，不自动升级、不回退、不换模型：

```text
faster-whisper==1.2.1
ctranslate2==4.8.1
model_id=dropbox-dash/faster-whisper-large-v3-turbo
model_revision=0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
first_smoke_device=cuda
first_smoke_compute_type=float16
```

固定的公开 Git LFS identity：

```text
model.bin size=1,617,884,929 bytes
model.bin sha256=e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31
```

该 SHA-256 只是托管方公开 pointer。R3-A 必须对下载后的本地 `model.bin` 独立重算；大小或 SHA-256 任一不一致立即停止。不得使用任何历史错误 SHA 值。

## 3. 生产目标与身份门禁

执行目标固定为：

```text
主机名=${PRODUCTION_HOSTNAME}
ZeroTier IP=${GPU_NODE_ZEROTIER_IP}
SSH user=Administrator
生产仓库=${PRODUCTION_REPO_PATH}
预期 HEAD=e2374e37e1357be3d8df93d6d3429bb0947fb9ba
GPU=RTX 5060 Ti 16 GiB
预期 SSH ED25519 fingerprint=${PRODUCTION_HOST_KEY_FINGERPRINT}
```

如选择 Codex SSH 通道，只允许 Bitwarden SSH Agent，不导出私钥，并固定：

```text
-o KexAlgorithms=curve25519-sha256
-o StrictHostKeyChecking=yes
-o UserKnownHostsFile="$env:TEMP\pincheng-gpu-known-hosts"
```

执行前必须用现有、已批准的带外证据重新核对主机指纹。禁止 TOFU、`ssh-keyscan` 代替验证、关闭 host-key checking 或接受新指纹。任一主机标识不匹配立即停止。

生产仓库必须满足：

- `HEAD` 精确等于预期 SHA；
- 工作区干净；
- 不在 R3-A 中 `git pull`、切分支、修改、stage、commit 或 reset；
- 任一不满足即停止，不自动同步或修复。

## 4. 修改面与明确不做

### 4.1 本轮计划编写阶段的仓库修改

只允许：

- 新增 `project-docs/plans/faster-whisper-r3a-execution-plan.md`；
- 在 `WORKLOG.md` 追加“计划已提交、待用户审批、R3-A 未执行”的记录。

不修改 `TODO.md`、源码、依赖文件、现有 Phase 0 脚本或其他未提交文件。

### 4.2 获批后 R3-A 在生产机允许创建的内容

只允许在单个新 run 根目录创建隔离 artifact：

```text
${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\
  phase0-fw-r3a-YYYYMMDD-HHMMSS\
    config\
    helpers\
    venv\
    wheels\
    hf-cache\
    model\
    evidence\
    logs\
    reports\
    state\
    testdata\
```

所有 pip、Hugging Face、模型和临时缓存必须重定向到该 run 目录。不得写入用户级/global Hugging Face cache、现有 FunASR venv/model/cache 或生产仓库。

### 4.3 R3-A 明确不做

- 不修改、升级、修复或删除现有 FunASR venv、模型、报告和 run；
- 不修改 `torch==2.7.0+cu128`、系统 CUDA、cuDNN、驱动、系统/用户 `PATH` 或 Python 全局 site-packages；
- 不停止、重启、卸载、重配或预热 BGE，不修改 BGE Token、端口、防火墙、服务参数和权重；
- 不改业务源码、API、Schema、数据库、Prompt、索引、部署配置或生产开关；
- 不使用 INT8，不静默 CPU 回退，不换模型/revision，不自动升级依赖；
- 不使用客户、内部会议、项目或未完成脱敏声明的音频；
- 不运行冻结 8 样本 A/B、hotwords/prompt 对照、长音频、时间戳质量评测或 BGE 并发压测；
- 不进入 Phase 1，不宣告 faster-whisper 已取代 FunASR；
- 不自动删除既有 artifact，也不在未获具体批准时递归删除本次失败 run。

## 5. 工具策略与审计边界

现有 `scripts/funasr_phase0/00_run_guarded.py` 只映射 FunASR 专用 worker，`02_compat_smoke.py` 也是 FunASR/PyTorch 路径；现有许可模型映射以 ModelScope/FunASR 为中心。因此 R3-A **不得原样调用这些脚本并声称完成 faster-whisper 门禁**。

本计划采用以下最小策略：

1. 不修改仓库脚本；
2. 在本次 run 的 `helpers\` 内生成一次性、可审计 helper；
3. helper 只负责 manifest、下载、逐文件哈希、许可清单、import/CUDA 冒烟、GPU/BGE 监控和报告；
4. 所有 helper 自身先计算 SHA-256，并与 `config\r3a-config.json` 的 SHA-256 一起写入 `state\run-identity.json`；
5. 父 PowerShell 进程记录 child PID、开始/结束时间、退出码并负责超时终止和残留检查；
6. helper 不读取 `.env`、进程内存、浏览器存储或未知凭据文件，不接受未声明目录作为输入；
7. 如执行中发现必须改仓库工具或现有 FunASR 脚本，停止并另提 R2 工具实现方案。

## 6. 审批有效期、维护窗口与执行通道

执行前必须在 §18 填写：

- 精确维护窗口，使用带 `+08:00` 的 ISO-8601 起止时间；
- 执行通道：Codex 经验证 SSH，或用户在生产机现场手动执行；
- 四个强制暂停点是否同意；
- BGE 鉴权材料处理；
- 冒烟样本 identity；
- 失败 artifact 保留/删除策略。

旧维护窗口、旧人工许可证审批和旧 DPAPI 临时令牌均已失效，不得复用。超出批准窗口后，不得开始新阶段；正在执行的下载/安装/推理应安全停止、清理进程并生成 stop report。

## 7. 前置基线（A0–A1，只读或状态记录）

### A0：批准包与运行身份

执行前创建但不运行下载：

- `config\r3a-config.json`：固定包、模型、revision、compute type、样本路径/hash、维护窗口、资源门禁、run 路径；
- `config\approval.json`：用户批准文本的结构化摘录，不包含密钥；
- `state\run-identity.json`：计划文件 hash、静态报告 hash、config hash、helper hash、run ID；
- `reports\preflight.md`：目标主机、时间、执行者、暂停点和未决事项。

若计划文件、静态报告、config、helper 的任何 identity 在执行过程中漂移，停止。

### A1：主机、仓库、BGE、GPU、磁盘基线

只读记录：

- 主机名、Windows 版本、时区和当前带 offset 时间；
- SSH host key 指纹；
- `${PRODUCTION_REPO_PATH}` HEAD、branch、工作区状态；
- Python 3.10 可执行文件绝对路径、版本、位数和文件 SHA-256；
- GPU 名称、driver、总/已用/空闲显存、运行进程；
- BGE `/health` 的 `status` 与 `model_loaded`；
- run 卷总空间、空闲空间和当前 `${QUALIFICATION_SANDBOX_ROOT}` 占用；
- 是否存在遗留 faster-whisper/CTranslate2 进程或 active-run state。

前置硬门禁：

```text
BGE status=ok
BGE model_loaded=true
目标 GPU=RTX 5060 Ti 16 GiB
ASR 进程=0
可用磁盘足以覆盖 30 GB 硬上限
生产 HEAD/工作区/SSH 指纹均匹配
```

如用户批准 BGE 鉴权探针，仅在生产机本地输入 `GPU_SERVICE_TOKEN`，生成精确路径、限时、DPAPI 保护的临时文件；不得回显、复制到聊天、写入仓库或普通日志。临时文件 identity 只记录路径、创建/到期时间和存在性，不记录明文或密文内容。没有该项批准时，只做无需鉴权的健康检查，并在报告中明确“未验证鉴权业务端点”。

**暂停点 P1：完成 A0–A1 后提交 preflight 摘要；在 wheel/model 下载前等待确认。**

## 8. Wheel、resolver 与离线安装（A2–A3）

### A2：下载固定 wheel 与 resolver 证据

在 `wheels\` 中执行网络下载，不安装到任何环境：

1. 记录执行解析的 Python/pip 绝对路径和版本；不得升级全局 pip；
2. 只请求 `faster-whisper==1.2.1` 与 `ctranslate2==4.8.1`，要求 Windows x64/Python 3.10 可安装的 binary wheel；
3. 保存完整 resolver 输出、下载 URL、HTTP final URL、文件名、大小和 SHA-256；
4. 对 `wheels\` 全部文件按名称排序生成 `evidence\wheel-manifest.json` 与 `.sha256`；
5. 记录解析出的完整传递依赖集合；出现 sdist、VCS URL、可变分支、非 HTTPS 来源或不在批准索引范围内的 artifact 立即停止；
6. 下载后断开该步骤的网络依赖，后续安装必须使用 `--no-index --find-links`。

`pip` 版本若不支持所需解析/报告能力，停止并提交修订，不得自行升级或换 resolver。

### A3：全新 venv 离线安装与依赖门禁

1. 使用 A1 记录的 Python 3.10 创建 `venv\`；
2. 禁止 `--system-site-packages`；
3. 只从本次 `wheels\` 离线安装固定顶层版本和解析出的传递依赖；
4. 记录安装前后 `sys.path`、`site.getsitepackages()`、`pip list --format=json`、`pip freeze --all`；
5. 执行 `pip check`，必须退出码 0；
6. 逐个导入但暂不加载 GPU 模型：`ctranslate2`、`av`、`onnxruntime`、`faster_whisper`；记录模块路径和版本，确认全部来自本次 venv；
7. 检查没有写入全局 site-packages、生产仓库或现有 FunASR venv。

任一 resolver 冲突、缺失 wheel、sdist 构建、`pip check` 失败、模块来自错误路径或需要修改全局环境时，立即停止。

**暂停点 P2：A2 下载和 hash 完成后、A3 离线安装前，提交 wheel manifest、resolver 与预计磁盘占用，等待确认。**

## 9. 模型下载、全文件哈希与许可门禁（A4–A5）

### A4：固定 revision 模型下载

只允许通过 immutable revision 下载：

```text
dropbox-dash/faster-whisper-large-v3-turbo@0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
```

要求：

- `HF_HOME`、`HF_HUB_CACHE`、临时目录和 local model 目录全部定向到本次 run；
- 禁止 `main`、tag、latest 或未固定 revision；
- 下载完成后枚举模型目录全部普通文件，记录相对路径、大小和 SHA-256；
- 独立核对 `model.bin` 的固定大小与 SHA-256；
- 保存模型 card、LICENSE、仓库 revision/commit 元数据和下载 provenance；
- 禁止加载模型，直到 A5 许可门禁和 P3 均通过。

### A5：最终依赖树与模型许可证

许可审计对象必须覆盖：

- `pip freeze --all` 中全部已安装包及其 dist-info license/metadata；
- `wheels\` 中实际使用的全部 wheel；
- 模型 card、LICENSE、仓库 identity 和全部模型文件 manifest；
- 一次性 helper 自身及其来源说明。

结果写入 `reports\license-matrix.json/.md`。任何许可证缺失、歧义、未知来源或政策 blocker 均停止。人工批准（如确需）必须：

- 由有权批准者在当前维护窗口内给出；
- 精确绑定 run ID、config SHA-256、wheel manifest SHA-256、model manifest SHA-256 和 blocker 列表；
- 有明确到期时间；
- 不复用此前 FunASR/Contextual Paraformer 的人工批准。

通过条件为 blocker=0，或全部 blocker 都有当前、精确、未过期批准。

**暂停点 P3：A3–A5 完成后提交 `pip check`、import、wheel/model hash 和许可摘要；在任何 CUDA 模型加载或推理前等待确认。**

## 10. CUDA/DLL 与最小 FP16 推理（A6–A7）

### A6：CUDA、cuDNN、DLL 和模型加载冒烟

在 BGE 保持在线的前提下：

1. 启动 1 秒间隔 GPU/BGE/进程监控；
2. 记录 CTranslate2 版本、支持 compute types、CUDA device count 和 GPU 名称；
3. 以 `device=cuda`、`compute_type=float16` 加载固定本地模型；
4. 禁止联网补文件；任何缺失 artifact 立即停止；
5. 记录加载 DLL、异常、加载时间、PID 和模型目录 identity；
6. 加载后先检查显存/BGE 门禁，再决定是否进入 A7；
7. 不在该步骤使用 INT8 或 CPU 作为替代成功路径。

### A7：一个自制短样本最小推理

样本必须在批准前固定绝对路径、来源声明、时长、大小和 SHA-256，且明确为自制/合成、非客户、非内部资料。建议 5–15 秒、16 kHz、mono WAV；固定中文文本可包含一般性 BIM 词语，但本步骤不做 CER/召回结论。

固定调用配置：

```text
task=transcribe
language=zh
beam_size=5
vad_filter=false
word_timestamps=false
hotwords=null
initial_prompt=null
condition_on_previous_text=false
device=cuda
compute_type=float16
```

记录：

- 加载和推理开始/结束时间、耗时、输出 segment 文本与时间边界；
- faster-whisper/CTranslate2/Python 版本和 config SHA-256；
- ASR 进程峰值显存、GPU 总已用显存和 BGE 健康；
- 退出码、异常、child PID 和清理结果。

R3-A 的推理通过仅表示“固定链路在该机器上完成最小 FP16 推理”，不表示中文质量、热词、时间戳、长音频或 Phase 0 通过。

## 11. 资源门禁与自动停止条件

父计划门禁保持不变：

```text
单 ASR 峰值显存 < 8 GiB
ASR + BGE 合计显存 < 14 GiB
本次 run 磁盘硬上限 = 30 GB
本次 run 磁盘软警告 = 25 GB
BGE status=ok
BGE model_loaded=true
```

任一发生立即停止，不自动降级或修复：

- SSH 主机指纹、主机名、生产 HEAD 或工作区不匹配；
- 维护窗口未开始、已到期或审批/config/helper identity 漂移；
- wheel/model provenance、revision、大小或 SHA-256 不一致；
- 出现 sdist/VCS/非批准来源、resolver 冲突、`pip check` 失败或许可 blocker；
- CTranslate2/CUDA/cuDNN/DLL 加载失败，或需要改系统 CUDA、PATH、驱动、BGE、PyTorch、现有 FunASR 环境；
- 发生 OOM、ASR 峰值显存达到或超过 8 GiB、ASR+BGE 达到或超过 14 GiB；
- run 使用达到 25 GB 时暂停并报告，预计或实际达到 30 GB 时立即停止；
- BGE `/health` 不是 `status=ok/model_loaded=true`，或批准的鉴权探针出现错误/明显退化；
- ASR child 超时、不能终止、残留 PID 不能确认、active-run 状态不能清理；
- 发现客户数据、未知敏感文件、凭据回显、令牌写入仓库/普通日志；
- 需要扩大到冻结 8 样本、hotwords/prompt A/B、长音频、BGE 压测、生产集成或 Phase 1；
- 用户发出停止指令。

## 12. BGE 保护与后置验证（A8）

A6/A7 前、中、后持续检查 BGE health。若用户批准本地鉴权探针，则在 A1 与 A8 使用相同的固定非敏感请求进行少量功能检查；这不是 BGE 并发压测。

A8 必须完成：

1. 停止并确认本次 faster-whisper/CTranslate2 child process 全部退出；
2. 检查无本次 active-run state、无孤儿进程、GPU 使用回落；
3. 再次验证 BGE `status=ok/model_loaded=true`；
4. 若批准了鉴权探针，比较前后错误、响应时间和响应结构；明显退化则标记失败；
5. 删除本次精确路径的 DPAPI 临时令牌文件并验证不存在；该删除须在 §18 明确批准；
6. 不删除 venv、wheel、模型、cache 或报告，除非命中用户选定的失败 artifact 策略并在 P4 再次确认。

**暂停点 P4：报告最终状态；任何递归删除失败 artifact 前再次等待确认。**

## 13. 报告与证据产物

至少生成：

```text
reports\r3a-summary.md
reports\r3a-summary.json
reports\preflight.md
reports\stop-event.md                 # 仅发生停止时
reports\license-matrix.md
reports\license-matrix.json
evidence\wheel-manifest.json
evidence\wheel-manifest.sha256
evidence\model-manifest.json
evidence\model-manifest.sha256
evidence\environment.json
evidence\pip-list.json
evidence\pip-freeze.txt
evidence\pip-check.txt
evidence\import-smoke.json
evidence\cuda-smoke.json
evidence\inference-smoke.json
logs\gpu.csv
logs\bge-health.jsonl
logs\process-lifecycle.jsonl
state\run-identity.json
```

报告只包含必要技术证据。禁止写入 Token、SSH 私钥、Bitwarden 信息、客户内容、环境变量全集、进程内存或未知文件正文。命令行与环境记录必须按白名单收集并脱敏。

R3-A 成功条件必须同时满足：

- host/repo/approval/config identity 通过；
- wheel resolver、逐文件 SHA-256、离线安装和 `pip check` 通过；
- model revision、全文件 manifest 和 `model.bin` 本地 hash 通过；
- license blocker=0 或全部有当前精确批准；
- import/DLL/CUDA/FP16 单短样本推理通过；
- 单 ASR 与合计显存门禁通过；
- child/active-run/临时令牌清理通过；
- BGE 后置健康通过。

任何一项未满足，R3-A 结论必须是 `FAILED` 或 `BLOCKED`，不得写成部分通过后继续 R3-B。

## 14. 恢复与回滚

发生停止条件时按顺序：

1. 阻止进入下一阶段并停止本次 child process；
2. 如正常终止失败，只针对已记录的本次 child PID 进行升级终止，不按进程名批量杀死；
3. 验证 GPU 回落、BGE health 恢复；
4. 删除本次精确路径的 DPAPI 临时令牌文件并验证不存在；
5. 写 `reports\stop-event.md`，记录触发条件、最后成功门禁、PID、GPU/BGE 末态和未清理项；
6. 默认保留报告、manifest 和日志；venv/cache/model 是否删除按 §18 用户选择，并在 P4 重新确认；
7. 不修改或删除生产仓库、现有 FunASR/BGE artifact，不通过 reset/卸载/驱动修改来“恢复”。

如 BGE 未恢复，立即停止所有 ASR 操作并报告，不擅自重启 BGE。生产恢复动作须另行具体审批。

## 15. 分阶段结果与强制暂停

| 阶段 | 内容 | 成功后动作 | 失败后动作 |
|---|---|---|---|
| A0–A1 | 批准/identity、主机、仓库、BGE/GPU/磁盘基线 | P1 暂停 | 停止并报 preflight blocker |
| A2 | wheel 下载、resolver、hash | P2 暂停 | 保留证据，停止 |
| A3–A5 | 离线安装、`pip check`、模型 hash、许可 | P3 暂停 | 清理进程/令牌，停止 |
| A6–A7 | CUDA/DLL、FP16 单短样本 | 不进入 R3-B；转 A8 | 立即停止并恢复检查 |
| A8 | 后置健康、进程/令牌清理、报告 | P4 报告 | 报告未恢复项；不自动修复 BGE |
| P4 后 | 可选删除失败 artifact | 必须单独确认精确 run 路径 | 默认完整保留 |

P1、P2、P3、P4 均为强制暂停；一次“批准执行 R3-A”允许按批准范围准备到下一个暂停点，但不授权跨越暂停点继续。

## 16. 与 R3-B 的边界

R3-B（不在本计划中）才可考虑：

- 同一冻结 8 样本；
- 固定 baseline 与单一 hotwords/initial prompt 策略；
- 预注册 CER、BIM 术语召回、负例不退化、时间戳与 RTF；
- 不看结果后调参再宣告原配置通过；
- 仍不自动进入长音频、BGE 并发压测或 Phase 1。

只有 R3-A 全部成功并复核报告后，才能另写 R3-B 详细计划并重新审批。R3-A 通过不等于切换后端，也不等于停用 FunASR。

## 17. 兼容性与主要风险

- CTranslate2 与现有 PyTorch 不是同一推理后端，但可能加载各自 native CUDA runtime；因此必须独立 venv/进程，不能用静态版本区间代替实机验证；
- Blackwell `sm_120`、Windows DLL/cuDNN 和 RTX 5060 Ti 真实组合仍可能失败；失败时停止，不改系统环境；
- 模型约 1.62 GB，但 wheel/cache/log/venv 会扩大磁盘占用；保持 30 GB 硬上限；
- `hotwords` 是解码提示，不等同于 Contextual Paraformer contextual biasing；R3-A 不测试也不宣称术语收益；
- 单短样本只能验证链路，不能证明中文质量、噪声鲁棒性、时间戳、长音频或共存性能；
- BGE 鉴权需要临时敏感材料；优先最小化使用，禁止读取 `.env` 或绕过鉴权；
- 一次性 run helper 比直接复用 FunASR 脚本更符合当前边界，但它仍必须 hash 固定和审计；若需要长期复用，应另立 R2 将工具正式纳入仓库并测试。

## 18. 用户必须明确决定的事项

在批准执行前，以下项目不得留空：

1. **执行通道**：Codex 经已验证 SSH 执行，或用户在生产机手动执行；
2. **维护窗口**：精确 `start/end`，Asia/Shanghai，ISO-8601 `+08:00`；
3. **BGE 鉴权探针**：是否批准用户在生产机本地重新输入 Token，并生成限时 DPAPI 临时文件；不批准则只做无鉴权 health；
4. **冒烟样本**：绝对路径、SHA-256、时长、来源声明；必须是自制/合成、非客户、非内部；
5. **失败 artifact 策略**：
   - A：完整保留本次 run 供审计；或
   - B：保留 `reports/evidence/logs/config/state`，在 P4 再确认后删除本次精确 run 下的 `venv/wheels/hf-cache/model/testdata/helpers`；
6. **暂停点**：是否同意 P1/P2/P3/P4 全部强制；本计划建议“同意”；
7. **临时令牌清理**：是否明确批准删除本次精确路径的 DPAPI 临时令牌文件；本计划建议“批准”；
8. **索引/来源范围**：允许访问的 PyPI/Hugging Face 域名或镜像清单；未列出的来源一律阻塞；
9. **人工许可批准人**：如产生 blocker，由谁有权批准；没有批准人则 blocker 必须为 0；
10. **超时**：单次下载、安装、模型加载、推理的超时上限；未明确时不得执行对应阶段。
11. **日期一致性**：说明 `2026-07-31` 与静态预检报告所记 `2026-08-01` 的时区/日期口径；未解决时不得执行。

## 19. 可复制审批模板

```text
批准执行 faster-whisper R3-A，按
${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-execution-plan.md
执行；计划 SHA-256 = <填写 Codex 提交计划时报告的值>。

执行通道 = <Codex 经验证 SSH / 我在生产机手动执行>
维护窗口 = <YYYY-MM-DDTHH:mm:ss+08:00> 至 <YYYY-MM-DDTHH:mm:ss+08:00>
批准范围 = A0–A8；不包含 R3-B、冻结 8 样本、长音频、BGE 并发压测、Phase 1 或 FunASR 替换
BGE 鉴权探针 = <批准本地重新输入并生成限时 DPAPI 临时文件 / 不批准，仅做无鉴权 health>
冒烟样本 = <绝对路径>
冒烟样本 SHA-256 = <hash>
样本声明 = 自制或合成、非客户、非内部
失败 artifact 策略 = <A 完整保留 / B P4 再确认后删除指定子目录>
暂停点 = P1/P2/P3/P4 全部强制
DPAPI 临时令牌清理 = 批准删除本次精确路径文件并验证不存在
允许下载来源 = <精确域名/镜像清单>
许可 blocker 批准人 = <姓名/角色；或“无，必须 blocker=0”>
超时 = wheel 下载 <分钟>；模型下载 <分钟>；离线安装 <分钟>；模型加载 <分钟>；推理 <分钟>
日期一致性 = <说明 2026-07-31 与预检报告 2026-08-01 的时区/日期口径；如修改预检报告则重新提交哈希并审批>
同意 §11 自动停止条件和 §14 恢复/回滚步骤。
```

只有收到填写完整、无冲突的批准文本后，R3-A 才视为获批。审批后先执行到 P1；每个暂停点仍等待用户继续确认。
