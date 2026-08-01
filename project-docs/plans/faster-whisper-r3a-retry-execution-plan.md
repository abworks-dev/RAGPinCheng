# faster-whisper Phase 0 R3-A 重试补充执行计划（new run / new identity）

> 状态：**待用户重新审批；尚未执行生产 retry**  
> 风险等级：**R3（生产 Windows GPU 主机、外部下载、隔离安装、模型权重、GPU/BGE 与临时鉴权材料）**  
> 编制日期与口径：**2026-08-01，Asia/Shanghai（UTC+08:00）**  
> 计划性质：本文件是对已批准原计划的**补充和纠偏**，不修改、不覆盖历史原计划与静态预检报告。只有用户核对本文件 SHA-256 后明确回复“批准执行”或同等授权，Codex 才可创建新的生产 run 并进入 A0；本文件本身不构成执行授权。

## 1. 目标、适用关系与审批门禁

本补充计划用于在新的 run/new identity 下重试 faster-whisper Phase 0 R3-A，修复首次执行在 A1 暴露的前置条件和 helper 缺陷，同时保持原计划的 A0–A8 阶段语义、四个强制暂停点、自动停止条件、恢复/回滚边界以及 R3-B 隔离边界。

治理关系如下：

1. 原计划继续作为 A0–A8 的主体规范：
   - `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-execution-plan.md`
   - SHA-256：`e2508a827441d8e7fea61441be9e6551e4a94ee6fd1f903048b5017c8baf08d1`
2. 静态预检报告继续作为历史依据，但其中旧 `model.bin` SHA-256 已被本补充计划纠正：
   - `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-phase0-precheck.md`
   - SHA-256：`2edb7c53fc9aec9818eec6be70fd1fa3873d3ce4b0900d7c53e819a9fee9717e`
3. 本补充计划与原计划冲突时，仅对下列事项以本补充计划为准：
   - new run/new identity；
   - A1 进程门禁与 Windows WDDM 证据解释；
   - 代理类型、协议和允许下载来源；
   - 正确的 `model.bin` SHA-256/大小；
   - 两个固定 retry helper；
   - A6/A7 精确 child PID 管理；
   - 首次失败 run 和已删除未跟踪文件的历史处置。
4. 除上述纠偏外，原计划的资源限制、报告要求、P1/P2/P3/P4、自动停止、恢复/回滚和范围排除仍全部有效。
5. 本计划发生实质变化、helper hash 变化、范围扩大、风险升高、生产身份漂移，或维护窗口过期时，必须停止并重新提交计划/hash/审批。

## 2. 首次执行事实与已恢复前置条件

### 2.1 首次 run 的停止状态

首次 run 必须作为不可改写的历史失败 artifact 完整保留：

- run：`E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-20260801-072218`
- 状态：`STOPPED_BEFORE_P1_COMPLETE`
- preflight：`reports\preflight.md`
  - SHA-256：`221349eb3397802dc5faddcdde83af8228283716fb6c83697b1cbcedd17ed744`
- stop event：`reports\stop-event.md`
  - SHA-256：`fadb0862384f4ee5f05f30e24e7bc98b171d7f7522a97f362200026aade9ac42`

首次执行只完成 A0 和部分 A1，未进入 A2；未下载 wheel/model、未创建 venv、未安装依赖、未加载 faster-whisper 模型、未执行 CPU/GPU 推理、未修改 BGE/FunASR/CUDA/PATH/全局 Python。

### 2.2 生产工作区恢复

经用户明确同意“不用保留”后，只精确删除了以下两个生产仓库未跟踪文件：

- `D:\RAGPinCheng\data\_transfer_manifest.json`（删除前 25,285 bytes）
- `D:\RAGPinCheng\data\pincheng_docs-8226867163840074-2026-07-26-18-19-11.snapshot`（删除前 262,736,896 bytes）

删除后已验证：

- `git status --porcelain=v1` 为空；
- HEAD=`e2374e37e1357be3d8df93d6d3429bb0947fb9ba`；
- 未执行 `git reset`、`git pull`、commit 或其他生产仓库修改。

这里的“不用保留”**仅指上述两个精确路径的未跟踪文件**，不改变本次 retry 的“失败 artifact=A 完整保留”策略。

### 2.3 首次 helper 缺陷

首次执行发现并需要在 new identity 中纠正：

1. Windows WDDM 环境下，`nvidia-smi --query-compute-apps` 可能包含桌面/GUI 进程，不能把所有 GPU 进程行都解释为 ASR 冲突；
2. 代理失败分支在 StrictMode 下可能读取空对象的 `.result`，导致报告路径自身非零退出；
3. 首次 identity 已固定，禁止在旧 run 内静默替换 helper。

因此本次只能创建新 run，使用本计划固定的新 helper，不能续写或复用旧 run identity。

## 3. 固定执行身份

### 3.1 生产主机与 SSH 门禁

执行通道固定为 Codex 经验证 SSH：

```text
Host=FJPCSEVER
IP=10.205.165.105
User=Administrator
SSH ED25519=SHA256:nRSpKS3UAsE2IecHqyxSryD4Q9Af1piSF4siM+LTS9M
KexAlgorithms=curve25519-sha256
StrictHostKeyChecking=yes
UserKnownHostsFile=$env:TEMP\pincheng-gpu-known-hosts
```

只使用 Bitwarden SSH Agent，不导出私钥，不把私钥、Token、Cookie 或明文凭据写入聊天、仓库、run artifact 或普通日志。

### 3.2 new run/new identity

新 run 必须是尚不存在的直接子目录：

```text
E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-YYYYMMDD-HHMMSS
```

硬门禁：

- 目录名必须匹配 `^phase0-fw-r3a-retry-\d{8}-\d{6}$`；
- 父目录必须精确为 `E:\FunASR-Phase0\faster-whisper-runs`；
- 创建前路径必须不存在；
- 不得以复制整个旧 run、覆盖旧 run 或修改旧 identity 的方式重试；
- 新 run 的 config、approval、helper manifest 和 run identity 均重新生成并互相绑定 SHA-256。

### 3.3 固定 retry helper

A0/A1 helper：

- 源文件：`E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry-a0-a1.ps1`
- SHA-256：`6dd890402cc5d069c235b5028e2099957b3686805da29bc4a6cdbc0ba350d8fe`
- 大小：31,261 bytes

BGE 鉴权 helper：

- 源文件：`E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry-bge-auth-probe.ps1`
- SHA-256：`758eabc198e94c339a59bce29fa7258410a04d2f2e5ff295528e2d2d4304ef98`
- 大小：7,223 bytes

执行前后均须重新计算两个源 helper 与 run 内副本的 SHA-256；任一不匹配立即停止。不得临时编辑 helper 后继续执行；任何修改都必须生成新 hash 并重新审批。

## 4. 固定候选、模型 identity 与纠错

固定候选保持不变：

```text
faster-whisper==1.2.1
ctranslate2==4.8.1
model=dropbox-dash/faster-whisper-large-v3-turbo
revision=0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
compute_type=float16
language=zh
beam_size=1
vad_filter=false
condition_on_previous_text=false
```

正确的 `model.bin` identity 为：

```text
SHA-256=e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da
size=1617884929 bytes
```

旧计划/静态报告中的下列值作废，不得作为 A4 验收值：

```text
e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31
```

纠错依据是在生产机经批准代理对 Hugging Face 固定 revision 的三个公开只读来源进行核验，三者一致：

1. repository tree API 的 `lfs.oid` 与 `lfs.size`；
2. raw Git LFS pointer 的 `oid sha256` 与 `size`；
3. resolve HEAD 的 `X-Linked-ETag` 与大小证据。

A4 实际下载后仍必须对本地 `model.bin` 独立计算完整 SHA-256 和大小；不得仅凭远端 metadata 放行。大小或 SHA 任一不匹配，立即停止并完整保留证据，不加载模型。

历史原计划和静态预检报告不回写，以免破坏已审批/已引用 hash；本补充计划作为纠错链路。

## 5. 代理与允许下载来源

### 5.1 代理类型结论

用户提供的 `10.205.165.230:7897` 是开启局域网访问的 Clash Verge/Mihomo `mixed-port`。Mihomo 官方配置语义中，`mixed-port` 可同时接受 HTTP(S) 和 SOCKS 代理连接；生产机只读实测也确认以下两种方式当前可用：

```text
http://10.205.165.230:7897
socks5h://10.205.165.230:7897
```

本计划固定：

- 自动下载主代理：`http://10.205.165.230:7897`；
- 代理类型：Clash Verge/Mihomo mixed-port；
- HTTP 是唯一自动下载协议；
- `socks5h://10.205.165.230:7897` 仅用于人工诊断，不得自动切换或写成已批准下载路径；
- 不使用 `--ssl-no-revoke`，不禁用 TLS 证书或吊销检查；
- 首次 curl exit 35 只记录为当时的暂时性 TLS 状态，当前链路已恢复，但根因未被证明，不得写成已确定根因。

### 5.2 精确允许来源

只允许访问以下主机：

```text
pypi.org
files.pythonhosted.org
huggingface.co
us.aws.cdn.hf.co
```

要求：

- 请求 URL、最终 URL、redirect、HTTP code、observed host 和响应头证据必须记录；
- 所有 observed host 必须属于上述清单；
- 出现其他 CDN、镜像、VCS、HTTP 明文、未知 host 或代理自动切换时立即停止；
- 网络只用于 A1 小型连通性/metadata 探针及获 P1 后的 A2/A4 固定 artifact 下载；
- 不把代理写入系统级配置、WinHTTP、全局 pip 配置、仓库 `.env` 或持久 shell profile。

## 6. 固定样本与数据边界

唯一冒烟样本继续使用首次 run 内已固定的合成 WAV：

```text
E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-20260801-072218\testdata\r3a-synthetic-zh.wav
SHA-256=af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9
时长约=11.415 秒
声明=自制或合成、非客户、非内部
```

A0 只读复制到新 run 的 `testdata\`，复制前后均计算 SHA-256。不得重新生成、替换、转码或添加其他音频。任何 hash 不匹配立即停止。

## 7. 维护窗口、超时与许可决定

### 7.1 当前审批候选窗口

```text
开始=2026-08-01T07:06:00+08:00
结束=2026-08-01T17:06:00+08:00
时区=Asia/Shanghai
```

- 用户批准时若当前时间已达到或超过 `2026-08-01T17:06:00+08:00`，本窗口自动失效；不得创建新 run，必须先提交新的精确维护窗口重新审批。
- 窗口内不得因剩余时间不足而跳过暂停点；如果下一阶段无法在其单项超时和窗口内安全完成，则在阶段开始前停止。
- UTC `2026-07-31` 与 Asia/Shanghai `2026-08-01` 是同一时刻的时区日期差；本项目此次执行、run identity、报告和 `WORKLOG.md` 一律使用 Asia/Shanghai `+08:00`。

### 7.2 单项超时

```text
wheel 下载=30 分钟
模型下载=120 分钟
离线安装=30 分钟
模型加载=15 分钟
推理=10 分钟
```

超时由父进程记录并终止本次精确 child PID，写 stop report，检查残留并完整保留 artifact；不得用名称模糊匹配批量终止无关进程。

### 7.3 许可 blocker

目标仍为 `blocker=0`。如出现 blocker：

- P3 必须列出具体 artifact、版本、许可证证据、使用方式、风险和建议；
- `bim-admin` 仅可针对该次 P3 中的**具体 blocker**逐项批准；
- “bim-admin”“随便一点”或既往批准不构成 blanket approval；
- 未取得精确批准时不得进入 A6；
- blocker 内容或依赖树变化后，原批准失效。

## 8. 分阶段执行与强制暂停

### A0：创建新批准包和运行身份

仅在本补充计划/hash 获明确批准且维护窗口有效后：

1. 通过固定 SSH 门禁再次核对 host、IP、user、host key 和生产 HEAD；
2. 生成尚不存在的 new RunRoot；
3. 将原计划、静态预检、本补充计划、两个固定 helper 和固定 WAV 复制到新 run；
4. 对全部批准输入计算 SHA-256，与本计划列出的值逐项核对；
5. 生成 `config\r3a-config.json`、`config\approval.json`、`state\helper-manifest.json`、`state\run-identity.json` 和 `reports\preflight.md`；
6. identity 必须记录 new run 路径、计划/hash、两个 helper/hash、原计划/hash、静态报告/hash、样本/hash、模型 identity、代理、允许来源、维护窗口、超时、暂停点和失败 artifact 策略；
7. 不下载 wheel/model，不创建 venv，不输入 Token。

任一 identity 漂移、源文件缺失、RunRoot 已存在或 hash 不匹配时，写 `STOPPED_BEFORE_P1_COMPLETE` 并停止。

### A1：主机、仓库、BGE、GPU、磁盘与网络基线

运行固定 A0/A1 retry helper，记录：

- 主机、Windows、Asia/Shanghai 时间与 offset；
- SSH host key；
- `D:\RAGPinCheng` HEAD/branch/工作区；
- Python 3.10 绝对路径、版本、位数和文件 hash；
- RTX 5060 Ti 16 GiB、driver、显存和 `nvidia-smi` 证据；
- BGE `/health` 的 `status=ok`、`model_loaded=true`；
- run 卷空间；
- active-run state；
- faster-whisper/CTranslate2 命名进程；
- 命令行明确绑定本次 RunRoot 的 ASR/Python 进程；
- 代理 TCP、HEAD 和小型 metadata GET 证据；
- 模型 tree API、raw LFS pointer、HEAD ETag 和大小的一致性。

新的进程硬门禁：

1. active-run 文件必须为 0；
2. faster-whisper/CTranslate2 命名进程必须为 0；
3. 命令行绑定本次 RunRoot 的其他 ASR/Python 进程必须为 0；
4. helper 自身 PID 必须排除；
5. `nvidia-smi --query-compute-apps` 在 WDDM 下只作为证据，GUI/桌面进程本身不阻塞；
6. 不允许根据 WDDM GPU 进程列表批量杀进程。

网络探针每项最多有限重试 3 次；失败时必须输出顶层归一化结果，不得因空结果触发二次异常。

A1 硬门禁包括：

```text
生产 HEAD 精确匹配
生产工作区干净
BGE status=ok 且 model_loaded=true
目标 GPU=RTX 5060 Ti 16 GiB
上述三类 ASR/active-run 冲突=0
磁盘满足原计划 30 GB 硬上限
代理与允许来源探针通过
三个远端模型 identity 证据一致且等于本计划正确 SHA/大小
```

**暂停点 P1（强制）：A0–A1 完成后提交 preflight 摘要并停止。未经新的明确继续确认，不得进入 A2，不得下载 wheel/model。**

### A2：固定 wheel 与 resolver 证据

获得 P1 确认后，按原计划执行，仅使用 HTTP 主代理和允许来源：

- 只请求 `faster-whisper==1.2.1` 与 `ctranslate2==4.8.1`；
- 保存完整 resolver 输出、URL/final URL/redirect/host、文件名、大小和 SHA-256；
- 只接受 Windows x64/Python 3.10 binary wheel；
- 出现 sdist、VCS、可变分支、非 HTTPS、未知 host 或超时立即停止；
- 不安装到全局 Python或现有环境。

### A3：全新 venv 离线安装

- 在 new run 内创建全新 venv；
- 从 `wheels\` 离线安装，禁止联网补包；
- 记录安装日志、`pip freeze --all`、`pip check`、import origin 和逐文件 hash；
- 不复用或修改现有 FunASR/BGE/业务环境；
- `pip check` 非零、import 越界、联网补包或超时立即停止。

**暂停点 P2（强制）：A2–A3 完成后提交 wheel manifest、resolver、离线安装、`pip check` 与依赖树摘要并停止。未经明确确认，不得进入 A4。**

### A4：固定 revision 模型下载与本地全文件校验

获得 P2 确认后：

- 只下载固定 model/revision 到 new run；
- 禁止使用浮动 branch/tag；
- 记录每个请求的来源、redirect、observed host、大小和 hash；
- 对模型目录全部文件按稳定顺序生成 manifest；
- 对本地 `model.bin` 计算完整 SHA-256 与大小，必须精确等于：
  - `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`
  - `1617884929 bytes`
- 旧错误 SHA 必须被明确拒绝；
- 下载失败、未知 host、hash/大小不匹配或超过 30 GB 硬上限时立即停止，不加载模型。

### A5：最终依赖树与模型许可门禁

- 固定 wheel 和模型的许可证文本、包 metadata、来源与 SHA-256；
- 输出 blocker 清单；
- blocker=0 才可自动满足许可门禁；
- blocker>0 时停在 P3，等待 `bim-admin` 对具体 blocker 的精确批准。

**暂停点 P3（强制）：提交最终依赖、模型 manifest、许可与 blocker 摘要并停止。未经明确确认或具体 blocker 批准，不得进入 A6。**

### A6：CUDA/DLL 与模型加载冒烟

获得 P3 确认后：

1. 父进程先记录 BGE health、GPU baseline、active-run 和本次 child command；
2. 创建仅绑定本次 RunRoot 的 active-run state；
3. 启动模型加载 child，父进程记录精确 PID、开始/结束、退出码和超时；
4. 验证 import origin、CTranslate2 device/compute type、PyAV/ONNX Runtime DLL 与 CUDA/cuDNN；
5. 只加载固定本地模型，`device=cuda`、`compute_type=float16`；
6. 不执行长音频或并发压测。

恢复或超时时只允许处理父进程记录的本次精确 child PID。不得按 `nvidia-smi` WDDM GUI 行、模糊进程名或全部 Python 进程批量终止。

### A7：一个固定合成样本最小推理

- 仅运行固定 SHA 的约 11.415 秒合成 WAV；
- 参数固定为本计划 §4；
- 推理 child PID 由父进程精确记录；
- 记录文本、segment、语言概率、加载/推理耗时、峰值显存、退出码和残留；
- 不以单样本证明生产质量，不进行热词、长音频、8 样本或参数调优。

**暂停点 P4（强制）：A6–A7 后提交 CUDA/DLL、模型加载、单样本推理、GPU/BGE 和残留进程摘要并停止。未经明确确认，不得进入 A8。**

### A8：BGE 后置验证与安全末态

获得 P4 确认后：

1. 若执行 BGE 鉴权探针，用户必须在 FJPCSEVER 桌面本地重新输入 Token；
2. 固定 BGE helper 仅创建本次 new run 精确路径、15 分钟有效的 DPAPI 临时文件；
3. 不记录 Token、密文内容或完整业务响应正文；
4. 执行固定 embedding/rerank 最小鉴权探针和 health 检查；
5. `finally` 精确删除本次 Token 文件并验证不存在；
6. 再次验证 BGE `status=ok/model_loaded=true`、GPU 回落、active-run=0、本次精确 child PID=0；
7. 输出最终报告和 artifact manifest。

即使鉴权探针失败，也必须执行精确 DPAPI 文件清理、残留检查和 stop/final report。不得搜索或删除其他 Token 文件。

## 9. 自动停止条件

除原计划 §11 外，本次补充以下自动停止条件：

- 本补充计划、原计划、静态报告、任一 helper、config、approval、manifest、identity 或样本 hash 漂移；
- new RunRoot 已存在、路径不匹配或旧 run 被修改；
- 维护窗口未开始、已过期或不足以安全完成下一阶段；
- SSH host/IP/user/fingerprint、生产 HEAD 或工作区不匹配；
- active-run、faster-whisper/CTranslate2 命名进程或绑定本次 RunRoot 的 ASR/Python 进程冲突；
- BGE 不健康或 GPU/磁盘不满足原计划门禁；
- 代理失败、observed host 越界、TLS 校验被要求禁用、出现非 HTTPS/VCS/sdist/未批准镜像；
- 远端三个模型 identity 证据不一致；
- 本地 `model.bin` SHA 或大小不匹配；
- `pip check` 非零、import origin 越界、离线安装联网补包；
- 许可 blocker 未获具体批准；
- A6/A7 需要模糊或批量终止进程才能恢复；
- 任一单项超时、30 GB 硬上限触发、BGE health 恶化、active-run 清理失败；
- DPAPI 临时文件在 finally 后仍存在；
- 发现必须修改业务代码、现有 FunASR/BGE 环境、系统 CUDA/driver/PATH/全局 Python 才能继续。

停止后不得自动绕过、降级 TLS、切换 SOCKS、换模型、换版本、改参数或进入下一阶段。

## 10. artifact、报告与失败策略

失败 artifact 策略固定为 **A：完整保留**。每个阶段至少记录：

- 开始/结束的 Asia/Shanghai 时间；
- 输入 identity 和 SHA-256；
- 命令摘要、精确 child PID、退出码、超时；
- 网络请求 URL/final URL/redirect/observed host/HTTP code/header 摘要；
- wheel/model/许可 manifest；
- BGE/GPU/active-run 前后状态；
- 自动停止条件、恢复动作、残留检查；
- DPAPI 文件只记录精确路径、创建/到期时间和最终不存在，不记录内容。

失败时写 `STOPPED_*` 状态和 stop report，完整保留 new run。不得自动删除失败 run、wheel、模型或报告；若未来要删除，必须对精确路径另行获得 R3 破坏性操作批准。

## 11. 恢复与回滚

本次仍遵守原计划 §14，并补充：

1. 停止新下载和新阶段启动；
2. 只终止父进程记录的本次精确 child PID；
3. 不根据 WDDM `nvidia-smi` GUI 行、模糊进程名或全部 Python 进程批量杀进程；
4. 清理本次 active-run state，验证不存在；
5. 精确删除本次 BGE DPAPI 临时文件，验证不存在；
6. 验证 BGE `status=ok/model_loaded=true`；
7. 验证本次 child PID=0，记录 GPU 回落；
8. 保留完整 new run artifact 和 stop report；
9. 不修改、不删除旧失败 run；
10. 不执行生产仓库 reset/pull/commit，不修改 FunASR/BGE 服务、CUDA、driver、PATH 或全局 Python。

由于所有新依赖、venv、wheel 和模型均限定在 new run 内，正常回滚是停止本次精确进程并恢复 BGE/active-run 安全末态；artifact 保留供审计，不在本次计划内删除。

## 12. 明确不做

本补充计划不授权：

- R3-B；
- 冻结 8 样本；
- 客户/内部音频；
- 长音频；
- BGE 并发压测；
- 热词 A/B、质量阈值或参数调优；
- Phase 1；
- 替换、卸载、禁用或修改 FunASR；
- 修改业务代码、API、依赖锁文件、Docker、系统服务；
- 修改 BGE 配置或重启/停止 BGE；
- 修改 CUDA、driver、PATH、系统/全局 Python；
- 禁用 TLS 校验或吊销检查；
- 自动切换 SOCKS 或增加未批准下载来源；
- 删除旧失败 run 或本次失败 artifact；
- 使用真实客户数据或读取未知凭据文件。

R3-A 成功只表示固定候选在隔离环境完成 artifact、许可、CUDA/DLL 和单个合成样本最小冒烟；不等于 FunASR 已被替换，也不等于生产质量、长音频、并发或 Phase 1 已通过。

## 13. 已固定的用户决定

本次补充计划按用户现有决定固定：

| 项目 | 决定 |
|---|---|
| 执行通道 | Codex 经验证 SSH |
| 维护窗口 | `2026-08-01T07:06:00+08:00` 至 `2026-08-01T17:06:00+08:00`；过期必须重批 |
| 范围 | new run/new identity 下 A0–A8 |
| BGE 鉴权 | 批准生产机本地重新输入，创建 15 分钟 DPAPI 临时文件 |
| DPAPI 清理 | 批准删除本次精确路径文件并验证不存在 |
| 样本 | 固定合成 WAV，SHA-256=`af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9` |
| 数据声明 | 自制或合成、非客户、非内部 |
| 失败 artifact | A：完整保留 |
| 暂停点 | P1/P2/P3/P4 全部强制 |
| 代理 | `http://10.205.165.230:7897`，mixed-port，HTTP primary；SOCKS 仅诊断 |
| 允许来源 | `pypi.org`、`files.pythonhosted.org`、`huggingface.co`、`us.aws.cdn.hf.co` |
| 许可 blocker | 目标 blocker=0；`bim-admin` 只可精确批准具体 blocker |
| 超时 | wheel 30m；模型 120m；离线安装 30m；模型加载 15m；推理 10m |
| 日期口径 | Asia/Shanghai `+08:00` |
| 自动停止/回滚 | 同意原计划 §11/§14 及本补充计划 §9/§11 |

## 14. 可复制审批模板

```text
批准执行 faster-whisper R3-A retry，按
E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry-execution-plan.md
执行；计划 SHA-256 = <填写本文件最终 SHA-256>。

执行通道 = Codex 经验证 SSH
维护窗口 = 2026-08-01T07:06:00+08:00 至 2026-08-01T17:06:00+08:00
批准范围 = 新 run/new identity 下的 A0–A8；不包含 R3-B、冻结 8 样本、长音频、BGE 并发压测、Phase 1 或 FunASR 替换
BGE 鉴权探针 = 批准本地重新输入并生成 15 分钟 DPAPI 临时文件
冒烟样本 SHA-256 = af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9
样本声明 = 自制或合成、非客户、非内部
失败 artifact 策略 = A 完整保留
暂停点 = P1/P2/P3/P4 全部强制
代理 = http://10.205.165.230:7897，Clash Verge/Mihomo mixed-port，HTTP primary；不自动切换 SOCKS
允许下载来源 = pypi.org、files.pythonhosted.org、huggingface.co、us.aws.cdn.hf.co
许可 blocker 批准人 = bim-admin，仅可精确批准具体 blocker
超时 = wheel 30 分钟；模型 120 分钟；离线安装 30 分钟；模型加载 15 分钟；推理 10 分钟
日期一致性 = UTC 2026-07-31 与 Asia/Shanghai 2026-08-01 是同一时刻的时区日期差；执行与报告使用 Asia/Shanghai +08:00
同意自动停止、恢复/回滚和本次精确 DPAPI 文件清理。
```

审批后仍必须先执行 A0–A1，并在 P1 强制停止；不得把对整份计划的批准解释为越过 P1/P2/P3/P4 的连续执行授权。