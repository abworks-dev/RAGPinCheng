# faster-whisper Phase 0 R3-A retry2 修订执行计划（third retry run / new identity）

> 状态：**待用户重新审批；尚未执行第三个 retry run（全局第 4 个 R3-A run）**  
> 风险等级：**R3（生产 Windows GPU 主机、外部下载、隔离安装、模型权重、GPU/BGE 与临时鉴权材料）**  
> 编制日期与口径：**2026-08-01，Asia/Shanghai（UTC+08:00）**  
> 计划性质：本文件取代旧 retry 计划作为下一次执行的唯一补充计划，但不修改、不覆盖任何历史计划、预检报告或失败 run。只有用户核对本文件 SHA-256 后明确回复“批准执行”或同等授权，Codex 才可先执行恢复核验并创建第三个 retry run（全局第 4 个 R3-A run）；本文件本身不构成生产执行授权。

## 1. 目标、适用关系与审批门禁

本计划用于在**第三个 retry run（全局第 4 个 R3-A run）的 new identity** 下重试 faster-whisper Phase 0 R3-A。它只纠正两次 retry 在 A0/A1 暴露的执行器与证据序列化问题，保持原计划 A0–A8 的阶段语义、四个强制暂停点、自动停止条件、恢复/回滚边界和 R3-B 隔离边界。

治理关系如下：

1. 原计划继续作为 A0–A8 的主体规范：
   - `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-execution-plan.md`
   - SHA-256：`e2508a827441d8e7fea61441be9e6551e4a94ee6fd1f903048b5017c8baf08d1`
2. 静态预检报告继续作为历史依据；其中旧 `model.bin` SHA-256 由旧 retry 计划和本计划共同纠正：
   - `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-phase0-precheck.md`
   - SHA-256：`2edb7c53fc9aec9818eec6be70fd1fa3873d3ce4b0900d7c53e819a9fee9717e`
3. 旧 retry 计划仅作为历史审批与失败 run 的证据，不再授权下一次生产执行：
   - `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry-execution-plan.md`
   - SHA-256：`e6fae6a0b6911de3fe32f8f790c25274f931691ea3e803af74e3f2f3ebf8e44a`
4. 本 retry2 计划与上述文件冲突时，仅对下列事项以本计划为准：
   - 两个 retry 失败 run 的保留和恢复核验；
   - 第三个 retry run/new identity（全局第 4 个 R3-A run）；
   - Windows PowerShell 5.1 `Get-Content -Raw` ETS 根因和安全 DTO 修复；
   - 新 A0/A1 helper hash；
   - SSH 有界会话门禁；
   - baseline JSON 30 秒 supervisor watchdog；
   - A1 完成后仍只停在 P1。
5. 除上述纠偏外，原计划和旧 retry 计划的固定候选、资源限制、代理、白名单、报告要求、P1/P2/P3/P4、自动停止、恢复/回滚和范围排除仍全部有效。
6. 本计划、helper 或 BGE helper 的 hash 再次变化，范围扩大、风险升高、生产身份漂移，或维护窗口过期时，必须停止并重新提交计划/hash/审批。

## 2. 两次失败 run、根因与本地修复证据

### 2.1 历史 run 保留边界

以下历史 run 均为审计 artifact，不得删除、覆盖、续写为新 identity 或作为第三次执行目录：

1. 原始 R3-A run：
   - `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-20260801-072218`
   - 状态：`STOPPED_BEFORE_P1_COMPLETE`
   - stop event SHA-256：`fadb0862384f4ee5f05f30e24e7bc98b171d7f7522a97f362200026aade9ac42`
2. 第一次 retry 失败 run：
   - `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry-20260801-084400`
   - 状态：`STOPPED_BEFORE_P1_COMPLETE`
   - `reports\stop-event.md` SHA-256：`3a6d732925b0aefe1e4cd9023a2e0d0acf6d32072bd3fdeb1929ad070b68fa68`
3. 第二次 retry 失败 run：
   - `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry-20260801-085302`
   - 已知缺失或未确认的关键文件包括 `evidence\a1-baseline.json`、run identity/config/manifest/preflight；
   - 曾尝试写入精确 `reports\stop-event.md`，但 SSH 命令超时；随后只读核验也因远程 quoting/会话不退出而失败；
   - **当前不得声称 stop-event 已成功写入，也不得重复盲写。**

三次历史 run 均未进入 A2；未下载 wheel/model、未创建或安装本次 venv、未加载 faster-whisper 模型、未执行 CPU/GPU 推理、未修改 BGE/FunASR/CUDA/PATH/全局 Python。

### 2.2 第二次 retry 的精确失败根因

旧 A0/A1 helper 把 `Get-Content -Raw` 返回的扩展类型系统（ETS）字符串直接嵌入 `$allProxy`，再对包含该对象图的 baseline 执行 `ConvertTo-Json -Depth 16`。在 Windows PowerShell 5.1 中，该字符串除正文外还携带：

`PSPath`、`PSParentPath`、`PSChildName`、`PSDrive`、`PSProvider`、`ReadCount`、`Length`。

`ConvertTo-Json` 递归展开这些 ETS 文件属性，导致指数级 CPU/内存增长。字段级二分定位到 `hf-model-tree.final`；超时进程曾达到约 7.22 GB 工作集和约 170 秒 CPU，随后只终止了该诊断进程及其精确子进程。

真实 tree metadata 本身仅 6,739 bytes，无 NUL、无代理损坏、无异常代理字符；以纯 UTF-8 字符串读取后只保留 `Length`，同一正文可在毫秒级完成 depth-16 JSON。因此根因不是模型 metadata、Hugging Face、代理或网络，而是 Windows PowerShell 5.1 的 ETS 对象图序列化。

本地诊断目录：

`${LOCAL_USER_PATH}\.codex\visualizations\2026\07\31\019fba36-1320-7392-ac3d-fe77a62ec7e7\r3a-json-bisect-20260801-094516`

### 2.3 候选 helper 修复

候选 A0/A1 helper 仅做以下纠偏：

1. 用 `[IO.File]::ReadAllText(..., UTF8 strict)` 读取 curl body，返回无文件 ETS 属性的纯字符串；
2. 生成有限的 proxy evidence DTO，只保存标量 URL/HTTP/host/status/path/bytes/SHA 和 attempts；
3. baseline 不再嵌入 response body，也不重复保存 `final` 对象，仅保存 `final_attempt_number`；
4. SelfTest 从 8 项增至 16 项，覆盖 ETS 重现、纯字符串、正文一致、DTO 大小、禁止 ETS 属性和禁止重复 final。

本地复核结果：

- Windows PowerShell 5.1 parser errors=0；
- SelfTest：`tests=16`、`failures=[]`、`passed=true`、exit=0；
- 真实 body 离线全链路：DTO=7、depth-16 JSON=38 ms、peak private memory≈70 MB、JSON reparse=7、exit=0；
- baseline 验证 artifact：`offline-real-body-baseline.json`，SHA-256=`5fb170e2b84491c511707d367d18f810343bb4c31c3e089fbd7db1612ab4d909`；
- 本地验证未下载 wheel/model，未执行生产 A2。

### 2.4 旧审批失效

用户批准的旧 retry 计划固定 A0/A1 helper SHA-256 为 `6dd890402cc5d069c235b5028e2099957b3686805da29bc4a6cdbc0ba350d8fe`。候选 helper 已发生实质修复且 hash 改变，因此旧计划 SHA 与旧审批不能授权第三次生产执行。必须以本 retry2 计划的新 SHA 和新 helper SHA 重新审批。

## 3. 固定执行身份

### 3.1 生产主机与 SSH 门禁

执行通道固定为 Codex 经验证 SSH：

```text
Host=${PRODUCTION_HOSTNAME}
IP=${GPU_NODE_ZEROTIER_IP}
User=Administrator
SSH ED25519=${PRODUCTION_HOST_KEY_FINGERPRINT}
KexAlgorithms=curve25519-sha256
StrictHostKeyChecking=yes
UserKnownHostsFile=$env:TEMP\pincheng-gpu-known-hosts
```

只使用 Bitwarden SSH Agent，不导出私钥，不把私钥、Token、Cookie 或明文凭据写入聊天、仓库、run artifact 或普通日志。

### 3.2 new run/new identity

下一次生产 retry 的 new run 必须是尚不存在的直接子目录：

```text
${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry-YYYYMMDD-HHMMSS
```

硬门禁：

- 目录名必须匹配 `^phase0-fw-r3a-retry-\d{8}-\d{6}$`；
- 父目录必须精确为 `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs`；
- 创建前路径必须不存在；
- 不得以复制任何历史 run、覆盖历史 run 或修改旧 identity 的方式重试；
- 必须与 `phase0-fw-r3a-retry-20260801-084400` 和 `phase0-fw-r3a-retry-20260801-085302` 均不同；
- 新 run 的 config、approval、helper manifest 和 run identity 均重新生成并互相绑定 SHA-256。

### 3.3 固定 retry helper

A0/A1 helper：

- 源文件：`${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry-a0-a1.ps1`
- SHA-256：`11635a071fc56d8a5a8a4b2fe9a89c3516b7702b02dffa90fb140d8cd7f03be5`
- 大小：34,345 bytes
- 文件编码：UTF-8 BOM（Windows PowerShell 5.1 中文解析门禁）

BGE 鉴权 helper：

- 源文件：`${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry-bge-auth-probe.ps1`
- SHA-256：`758eabc198e94c339a59bce29fa7258410a04d2f2e5ff295528e2d2d4304ef98`
- 大小：7,223 bytes

执行前后均须重新计算两个源 helper 与 run 内副本的 SHA-256；任一不匹配立即停止。A0/A1 helper 还必须验证 UTF-8 BOM、Windows PowerShell 5.1 parser errors=0 和 16 项 SelfTest 通过。不得临时编辑 helper 后继续执行；任何修改都必须生成新 hash 并重新审批。

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

历史原计划和静态预检报告不回写，以免破坏已审批/已引用 hash；本 retry2 计划作为纠错链路。

## 5. 代理与允许下载来源

### 5.1 代理类型结论

用户提供的 `${PROXY_HOST}:${PROXY_PORT}` 是开启局域网访问的 Clash Verge/Mihomo `mixed-port`。Mihomo 官方配置语义中，`mixed-port` 可同时接受 HTTP(S) 和 SOCKS 代理连接；生产机只读实测也确认以下两种方式当前可用：

```text
${PROXY_URI}
${SOCKS_PROXY_URI}
```

本计划固定：

- 自动下载主代理：`${PROXY_URI}`；
- 代理类型：Clash Verge/Mihomo mixed-port；
- HTTP 是唯一自动下载协议；
- `${SOCKS_PROXY_URI}` 仅用于人工诊断，不得自动切换或写成已批准下载路径；
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
${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-20260801-072218\testdata\r3a-synthetic-zh.wav
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

### A-1：恢复核验（只读优先，不创建新 run）

获得本 retry2 计划/hash 的明确批准后，先通过固定 SSH 门禁执行以下有界恢复核验；完成前不得创建第三个 retry run：

1. 用 30 秒上限验证一个原生命令和一个显式 `exit 0` 的 `-EncodedCommand` PowerShell 命令均能返回；任一超时或非零即停止，不进行远程写入；
2. 对第二次 retry run 的精确路径 `reports\stop-event.md` 先执行 `Test-Path -PathType Leaf`、大小、SHA-256 和有限文本只读核验；
3. 若文件已存在，只记录其 hash/状态，不覆盖、不追加；
4. 仅当只读核验明确证明文件不存在时，才允许在该**精确路径**原子写入一份 `STOPPED_BEFORE_P1_COMPLETE` stop report，然后重新计算 hash、读取有限内容并验证存在；
5. 若存在但内容不完整、路径类型异常、SSH 再次超时或无法判定，则停止并提交 blocker，不得猜测、盲写或改动其他历史 artifact；
6. 不修改或删除两个失败 run 的其他任何文件。

恢复核验属于 A0 前置恢复动作，不代表 P1 完成，也不授权 A2。

### A0：创建第三个 retry 新批准包和运行身份

仅在本 retry2 计划/hash 获明确批准、A-1 通过且维护窗口有效后：

1. 通过固定 SSH 门禁再次核对 host、IP、user、host key 和生产 HEAD；
2. 生成尚不存在的 new RunRoot；
3. 将原计划、静态预检、本 retry2 计划、两个固定 helper 和固定 WAV 复制到新 run；helper 为兼容旧 schema 使用的 run 内快照文件名不改变，但其内容/hash 必须绑定本 retry2 计划；
4. 对全部批准输入计算 SHA-256，与本计划列出的值逐项核对；
5. 生成 `config\r3a-config.json`、`config\approval.json`、`state\helper-manifest.json`、`state\run-identity.json` 和 `reports\preflight.md`；
6. identity 必须记录 new run 路径、计划/hash、两个 helper/hash、原计划/hash、静态报告/hash、样本/hash、模型 identity、代理、允许来源、维护窗口、超时、暂停点和失败 artifact 策略；
7. 不下载 wheel/model，不创建 venv，不输入 Token。

任一 identity 漂移、源文件缺失、RunRoot 已存在或 hash 不匹配时，写 `STOPPED_BEFORE_P1_COMPLETE` 并停止。

### A1：主机、仓库、BGE、GPU、磁盘与网络基线

由精确 PID supervisor 运行固定 A0/A1 retry helper，记录：

- 主机、Windows、Asia/Shanghai 时间与 offset；
- SSH host key；
- `${PRODUCTION_REPO_PATH}` HEAD/branch/工作区；
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

A1 增加 baseline JSON 30 秒 supervisor watchdog：

- supervisor 只跟踪本次 helper 精确 PID 及其精确 `curl.exe` child PID；
- 当最后一个 `hf-model-pointer` 请求 artifact 已稳定、对应 curl child 已退出后开始 30 秒计时；
- 30 秒内必须生成可解析的 `evidence\a1-baseline.json`，且 `proxy.checks` 数量为 7、不含 `PSPath/PSDrive/PSProvider`、文件大小不超过 1 MiB；
- 超时、JSON 不可解析、字段数量异常、ETS 属性泄漏或文件过大时，只终止本次精确 helper PID，写 `STOPPED_BEFORE_P1_COMPLETE` 和 supervisor 证据，完整保留 run；
- 不得通过延长 SSH 无限等待、批量杀 PowerShell/Python 或删除 artifact 的方式恢复。

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

1. 若执行 BGE 鉴权探针，用户必须在 ${PRODUCTION_HOSTNAME} 桌面本地重新输入 Token；
2. 固定 BGE helper 仅创建本次 new run 精确路径、15 分钟有效的 DPAPI 临时文件；
3. 不记录 Token、密文内容或完整业务响应正文；
4. 执行固定 embedding/rerank 最小鉴权探针和 health 检查；
5. `finally` 精确删除本次 Token 文件并验证不存在；
6. 再次验证 BGE `status=ok/model_loaded=true`、GPU 回落、active-run=0、本次精确 child PID=0；
7. 输出最终报告和 artifact manifest。

即使鉴权探针失败，也必须执行精确 DPAPI 文件清理、残留检查和 stop/final report。不得搜索或删除其他 Token 文件。

## 9. 自动停止条件

除原计划 §11 外，本次补充以下自动停止条件：

- 本 retry2 计划、原计划、静态报告、任一 helper、config、approval、manifest、identity 或样本 hash 漂移；
- new RunRoot 已存在、路径不匹配、复用了任一历史 run 或旧 run 被非恢复条款修改；
- 维护窗口未开始、已过期或不足以安全完成下一阶段；
- SSH host/IP/user/fingerprint、生产 HEAD 或工作区不匹配；原生命令或显式退出的远程 PowerShell 在 30 秒内不返回；
- active-run、faster-whisper/CTranslate2 命名进程或绑定本次 RunRoot 的 ASR/Python 进程冲突；
- BGE 不健康或 GPU/磁盘不满足原计划门禁；
- 代理失败、observed host 越界、TLS 校验被要求禁用、出现非 HTTPS/VCS/sdist/未批准镜像；
- 远端三个模型 identity 证据不一致；
- 本地 `model.bin` SHA 或大小不匹配；
- `pip check` 非零、import origin 越界、离线安装联网补包；
- 许可 blocker 未获具体批准；
- A6/A7 需要模糊或批量终止进程才能恢复；
- 任一单项超时、baseline JSON 30 秒 watchdog 触发、30 GB 硬上限触发、BGE health 恶化、active-run 清理失败；
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
- A1 supervisor 的 helper/child PID、watchdog 起止时间、baseline 字节数、SHA-256 与 JSON reparse 结果；
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
9. 除 A-1 对第二次 retry 的精确 stop-event“先查后补”外，不修改、不删除任何历史失败 run；
10. 不执行生产仓库 reset/pull/commit，不修改 FunASR/BGE 服务、CUDA、driver、PATH 或全局 Python。

由于所有新依赖、venv、wheel 和模型均限定在 new run 内，正常回滚是停止本次精确进程并恢复 BGE/active-run 安全末态；artifact 保留供审计，不在本次计划内删除。

## 12. 明确不做

本 retry2 计划不授权：

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

## 13. 已固定的用户决定（保持旧审批参数不变）

本次 retry2 计划沿用用户现有决定，除重新绑定计划/helper hash 与第三个 new identity 外不改变参数：

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
| 代理 | `${PROXY_URI}`，mixed-port，HTTP primary；SOCKS 仅诊断 |
| 允许来源 | `pypi.org`、`files.pythonhosted.org`、`huggingface.co`、`us.aws.cdn.hf.co` |
| 许可 blocker | 目标 blocker=0；`bim-admin` 只可精确批准具体 blocker |
| 超时 | wheel 30m；模型 120m；离线安装 30m；模型加载 15m；推理 10m |
| 日期口径 | Asia/Shanghai `+08:00` |
| 自动停止/回滚 | 同意原计划 §11/§14 及本 retry2 计划 §9/§11 |

## 14. 可复制审批模板

```text
批准执行 faster-whisper R3-A retry2，按
${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry2-execution-plan.md
执行；计划 SHA-256 = <填写本文件最终 SHA-256>。

执行通道 = Codex 经验证 SSH
维护窗口 = 2026-08-01T07:06:00+08:00 至 2026-08-01T17:06:00+08:00
批准范围 = A-1 恢复核验，以及第三个 retry run/new identity 下的 A0–A8；不包含 R3-B、冻结 8 样本、长音频、BGE 并发压测、Phase 1 或 FunASR 替换
恢复动作 = 批准 A-1 先只读核验第二次 retry 的精确 stop-event；仅当明确不存在时原子补写该精确文件，已存在或状态不明时不覆盖并停止
BGE 鉴权探针 = 批准本地重新输入并生成 15 分钟 DPAPI 临时文件
冒烟样本 SHA-256 = af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9
样本声明 = 自制或合成、非客户、非内部
失败 artifact 策略 = A 完整保留
暂停点 = P1/P2/P3/P4 全部强制
代理 = ${PROXY_URI}，Clash Verge/Mihomo mixed-port，HTTP primary；不自动切换 SOCKS
允许下载来源 = pypi.org、files.pythonhosted.org、huggingface.co、us.aws.cdn.hf.co
许可 blocker 批准人 = bim-admin，仅可精确批准具体 blocker
超时 = wheel 30 分钟；模型 120 分钟；离线安装 30 分钟；模型加载 15 分钟；推理 10 分钟
日期一致性 = UTC 2026-07-31 与 Asia/Shanghai 2026-08-01 是同一时刻的时区日期差；执行与报告使用 Asia/Shanghai +08:00
同意自动停止、恢复/回滚和本次精确 DPAPI 文件清理。
```

审批后仍必须先执行 A-1 与第三个 new run 的 A0–A1，并在 P1 强制停止；不得把对整份计划的批准解释为越过 P1/P2/P3/P4 的连续执行授权。