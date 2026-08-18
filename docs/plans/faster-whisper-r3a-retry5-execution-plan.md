# faster-whisper Phase 0 R3-A retry5 详细执行计划（仅本地编制，待生产审批）

> 状态：**历史 retry5 计划；仅完成本地静态检查/模拟 SelfTest，未批准或执行生产操作**
> 风险等级：**R3（后续若获批将涉及生产 Windows GPU 主机、外部下载、隔离安装、模型权重、GPU/BGE、临时鉴权材料及远端进程生命周期）**  
> 编制日期与口径：**2026-08-01，Asia/Shanghai（UTC+08:00）**  
> 当前批准范围：仅本地计划、helper、foreground controller、静态检查和模拟 SelfTest。**本文件不构成生产执行授权。**  
> 完整性流程：**已取消计划/代码文件的人工 SHA 生成、复制、填写、核对和审批；仅保留模型与合成冒烟样本的自动 SHA-256 身份校验。**

## 1. 目标、结论与审批门禁

retry5 的目标是在保留 retry4 前台生命周期控制与受限查询架构的同时，解决 retry4 在 A-2 因生产机缺少 `pwsh.exe` 自动停止的问题。用户提供的生产机本地输出已证明 PowerShell `7.6.4`、`Core`、x64、Microsoft Authenticode `Valid`，且 `where.exe pwsh` 返回固定安装路径。retry5 固定以下边界：

1. 操作系统信息改用 `[Environment]::OSVersion` 与 Windows 注册表只读信息；
2. 磁盘信息改用 `.NET System.IO.DriveInfo`；
3. 普通过程清单改用 `Get-Process`，不读取全量命令行；
4. TCP 监听改用受 watchdog 控制的 `netstat.exe -ano -p tcp`；
5. TCP 连通性改用 `.NET TcpClient` 限时连接；
6. 只有 active-run JSON 提供精确 PID 时，才对该 PID 做一次定向 `Win32_Process` 身份查询；
7. 精确 WMI 查询位于独立 Windows PowerShell 子进程，CIM `OperationTimeoutSec` 和外层 watchdog 均不超过 5 秒；
8. `nvidia-smi.exe` 位于独立受控子进程，默认 watchdog 为 15 秒；
9. WMI 或 `nvidia-smi` 超时只终止本次精确查询子进程并形成 blocker，不得阻塞 controller、SSH 或后续人工恢复；
10. 保留 retry4 的 foreground controller、lease/release、heartbeat、精确 process-tree 终止和状态不明停止语义；
11. 所有 PowerShell 7 调用固定使用 `${PRODUCTION_PWSH_PATH}`，禁止依赖 PATH 或回退到其他 `pwsh.exe`；
12. A0/A1 成功后仍在 P1 强制暂停，不自动进入 A2。

本计划继续评估 `faster-whisper`，**不表示弃用、替换、下线或修改 FunASR**。

生产执行必须同时满足：

- 用户明确批准固定 retry5 identity 的无预设维护窗口、单次生产执行授权；首个生产 SSH 调用即视为授权已使用；
- 用户明确回复“批准执行 faster-whisper R3-A retry5”或同等清晰授权；
- 执行前自动完成文件存在性、源/目标字节长度、UTF-8 BOM、双版本 parser 和模拟 SelfTest 门禁；
- 合成冒烟样本与模型继续由脚本自动执行固定 SHA-256/size 身份校验；
- A-1 全部只读门禁通过。

计划、helper、controller、BGE helper、identity、范围、执行授权语义或安全语义发生实质变化时，仍须停止并重新审阅/审批，但**不再生成或要求用户填写新的计划/代码 SHA-256**。

先前为本文件计算的计划 SHA-256 `018fa06e782e05d4d1de673d8468171c526ea9ca5698d5d835552410e35f3477` 自本次修订起作废，不生成替代值。代码与文档的复制完整性改为自动检查源文件/目标文件均存在且字节长度一致，并继续叠加 BOM、parser、SelfTest、固定路径和 identity 门禁。模型与冒烟样本的自动 SHA-256 校验不变。

## 2. 现状依据与 retry3/retry4 保留边界

retry3 A-1 诊断表明，简单 SSH/cmd 传输可以返回，但宽泛或不受控的 WMI/CIM 进程查询可能长时间不返回；因此不能再把全量 WMI/CIM 当作系统、磁盘、普通进程和端口预检的基础通道。

retry5 不授权修复、重建、重置或重新注册 Windows WMI Repository，也不把 WMI 服务状态变更作为本轮目标。若生产机 WMI 本身存在系统级故障，应作为独立 R3 运维事项另行调查和审批。

以下 retry3 本地文件必须原样保留，不得修改或删除：

- `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry3-execution-plan.md`
- `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry3-foreground-controller.ps1`
- `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry-a0-a1.ps1`

所有 retry3 生产 artifacts 继续作为只读审计证据；retry5 不覆盖、续写、清理、迁移或复用其 identity。

以下 retry4 本地文件和生产失败证据也必须原样保留：

- `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry4-execution-plan.md`
- `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry4-a0-a1.ps1`
- `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry4-foreground-controller.ps1`
- `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-inputs\phase0-fw-r3a-retry4-20260801-191451\a2-stop-event.json`

retry4 已在 A-2 因找不到 `pwsh.exe` 自动停止，未到 P1。用户随后在生产机本地验证 `${PRODUCTION_PWSH_PATH}` 为 PowerShell `7.6.4`、`Core`、x64，签名状态 `Valid` 且签名者为 Microsoft；该输出是 retry5 的现状依据，但仍须在获批后的 A-1 通过固定 SSH 只读重验。retry5 不覆盖、续写、删除或复用 retry4 的 RunRoot、StagingRoot、stop-event 或 identity。

## 3. 治理关系与范围

1. 原始 R3-A 主计划继续规定 A0–A8 的主体边界：
   - `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-execution-plan.md`
2. 静态预检继续作为历史依据：
   - `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-phase0-precheck.md`
3. retry3/retry4 计划仅作为历史设计、审批和故障证据；不得凭历史审批直接执行 retry5。
4. 本 retry5 计划在以下事项上优先：
   - retry5 new identity；
   - 固定 PowerShell 7 路径 `${PRODUCTION_PWSH_PATH}` 及安装后只读门禁；
   - `.NET/Get-Process/netstat/TcpClient` 替换；
   - exact-PID WMI 例外及 5 秒双重超时；
   - `nvidia-smi` 15 秒 watchdog；
   - foreground controller 和禁止 detached/background supervisor；
   - `A-1 -> A-2 -> ProbeSuccess -> ProbeTimeout -> RunA0A1 -> P1` 顺序；
   - P1/P2/P3/P4 强制暂停；
   - 状态不明时不杀、不重跑。

计划范围为新的 retry5 identity 下 A-1、A-2、ProbeSuccess、ProbeTimeout 和 A0–A8。明确排除 R3-B、冻结 8 样本、长音频、BGE 并发压测、Phase 1、FunASR 替换及生产业务流量切换。

## 4. 预留 new identity

本计划固定并预留：

```text
RunId=phase0-fw-r3a-retry5-20260801-231730
RunRoot=${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-20260801-231730
StagingRoot=${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-inputs\phase0-fw-r3a-retry5-20260801-231730
```

当前仅把 identity 固定写入本地计划；**尚未在生产机创建任何目录**。

生产创建前的硬门禁：

- RunRoot 必须是 `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs` 的直接子目录；
- StagingRoot 必须是 `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-inputs` 的直接子目录；
- 两个目录名必须精确等于 `phase0-fw-r3a-retry5-20260801-231730`；
- A-1 必须再次只读确认两条路径均不存在；
- 任一路径存在、状态不明、identity 不唯一或已被其他进程引用时立即停止；
- 不得自动改时间戳继续，必须重新编制计划/new identity 并审批；
- RunRoot 只能由 `RunA0A1` child 内固定 helper 首次创建；
- StagingRoot 只能在 A-1 全通过且生产执行获批后由 A-2 创建。

## 5. 固定输入与自动完整性校验

| 输入 | 本地源路径 | 自动校验 |
|---|---|---|
| 原始 R3-A 计划 | `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-execution-plan.md` | 文件存在；复制后源/目标字节长度一致 |
| 静态预检 | `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-phase0-precheck.md` | 文件存在；复制后源/目标字节长度一致 |
| retry5 A0/A1 helper | `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry5-a0-a1.ps1` | 文件存在；UTF-8 BOM；双版本 parser；SelfTest 19/19；复制后字节长度一致 |
| retry5 foreground controller | `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry5-foreground-controller.ps1` | 文件存在；UTF-8 BOM；双版本 parser；SelfTest 9/9；记录文件字节长度 |
| BGE 鉴权 helper | `${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry-bge-auth-probe.ps1` | 文件存在；复制后源/目标字节长度一致 |
| 合成冒烟样本 | `${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-20260801-072218\testdata\r3a-synthetic-zh.wav`（历史 run 内只读源） | 自动 SHA-256=`af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9`；复制后字节长度一致 |
| 本 retry5 计划 | 本文件 | 文件存在；复制后源/目标字节长度一致；不生成或审批 SHA-256 |

样本声明固定为：**自制或合成、非客户、非内部**。

上传后必须在 StagingRoot 自动验证源文件和目标文件均存在、对应字节长度一致、脚本 UTF-8 BOM、双版本 parser 和 SelfTest；冒烟样本继续自动校验固定 SHA-256。任一门禁漂移都立即停止，不得在生产 staging 现场编辑后继续。

## 6. retry5 查询与 watchdog 设计

### 6.1 普通系统信息：禁止宽泛 WMI/CIM

| 目标 | retry5 方法 | watchdog/边界 |
|---|---|---|
| 操作系统版本 | `[Environment]::OSVersion.Version` + `HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion` | 只读本地 API/注册表；不用 `Win32_OperatingSystem` |
| 磁盘容量 | `.NET System.IO.DriveInfo` | 只读；不用 `Win32_LogicalDisk` |
| 普通过程清单 | `Get-Process` | 只取 PID、名称、可读的 executable/StartTime；不做全量命令行 WMI |
| TCP 监听 | `netstat.exe -ano -p tcp` | 独立子进程，默认 5 秒 watchdog；超时返回 `NETSTAT_TIMEOUT` |
| TCP 连接探针 | `.NET Net.Sockets.TcpClient.BeginConnect/WaitOne` | 默认 5 秒；超时返回 `TCP_CONNECT_TIMEOUT` |

禁止使用 `Get-CimInstance Win32_OperatingSystem`、`Get-CimInstance Win32_LogicalDisk`、`Get-NetTCPConnection` 或全量 `Win32_Process` 扫描。

### 6.2 exact-PID WMI 例外

WMI/CIM 只允许用于“确实需要核对残留进程命令行”的精确身份查询：

1. 候选 PID 必须来自固定 active-run/status/lease JSON 中的 `pid`、`process_id`、`child_pid`、`controller_pid` 或 `worker_pid`；
2. 最多提取 8 个去重正整数 PID；
3. 每个 PID 单独查询，禁止全表查询、名称搜索或模糊命令行搜索；
4. 唯一允许的查询形式为：

```powershell
Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId=<exact PID>' -OperationTimeoutSec 5
```

5. 查询运行在独立 Windows PowerShell 子进程；外层 `.NET Process` watchdog 最长 5 秒；
6. 成功只返回 PID、ParentPID、CreationDate、ExecutablePath、CommandLine；
7. 超时只终止本次精确查询子进程并返回 blocker=`WMI_EXACT_QUERY_TIMEOUT`；
8. 非零退出返回 `WMI_EXACT_QUERY_FAILED`；JSON 无法解析返回 `WMI_EXACT_QUERY_INVALID_JSON`；
9. 任一 blocker 都使当前阶段自动停止；不得把“查询失败”解释为“远端目标进程不存在”；
10. 目标进程身份不完整或状态不明时不杀、不重跑，需另行报告审批。

### 6.3 nvidia-smi watchdog

- `nvidia-smi.exe` 只能通过 `.NET Diagnostics.ProcessStartInfo` 启动；
- stdout/stderr 必须重定向并由 controller/helper 持有；
- 默认 watchdog=15 秒；
- 超时只对本次精确 `nvidia-smi` Process 调用 `.Kill()`，等待其退出并返回 `NVIDIA_SMI_TIMEOUT`；
- 不允许直接 `& nvidia-smi.exe`、无限等待、后台启动或按进程名批量终止；
- `NVIDIA_SMI_TIMEOUT` 是硬 blocker，不能凭旧 GPU 证据继续。

## 7. foreground controller 生命周期通道

固定顺序：

```text
A-1 -> A-2 -> ProbeSuccess -> ProbeTimeout -> RunA0A1 -> P1
```

禁止 `Start-Job`、后台 job、计划任务、服务化、detached/background supervisor，或经 SSH 启动 `Start-Process` 后立即退出 launcher。

controller 每次模式执行：

1. SSH 客户端保持前台；
2. retry5 controller 作为远端 SSH 前台进程；
3. controller 创建并持有精确 child `System.Diagnostics.Process`；
4. child 先等待本模式 release 文件，最长 60 秒；
5. controller 获取 child PID、UTC StartTime、executable；
6. controller 原子写 lease 后才写 release；
7. child 收到 release 后才执行 probe/helper；
8. controller 每 5 秒原子写 heartbeat；
9. controller 前台等待 child；
10. controller watchdog 超时只执行 `taskkill /PID <exact child PID> /T /F`；
11. 写 stdout、stderr 和 terminal status；
12. RunRoot 存在时复制终态到 `evidence\retry5-foreground-controller-status.json`。

每个 mode 使用独立 artifact，存在即停止，禁止覆盖：

```text
StagingRoot\controller\<mode>-lease.json
StagingRoot\controller\<mode>-release.txt
StagingRoot\controller\<mode>-status.json
StagingRoot\controller\<mode>-stdout.log
StagingRoot\controller\<mode>-stderr.log
```

固定超时：

| 模式 | child timeout | SSH/Codex 客户端最短等待 |
|---|---:|---:|
| ProbeSuccess | 10 秒 | 100 秒 |
| ProbeTimeout | 2 秒 | 100 秒 |
| RunA0A1 | 1,200 秒 | 至少 1,290 秒；推荐 1,320 秒（22 分钟） |

调用端必须使用固定 host key、`curve25519-sha256`、`StrictHostKeyChecking=yes`、固定 known-hosts 和 Bitwarden SSH Agent。远端 PowerShell `-EncodedCommand` 必须由调用端字面量脚本生成 UTF-16LE Base64，禁止本地提前展开 `$env:*`、`$PID` 或远端变量。

## 8. 获批后的阶段步骤

### A-1：只读恢复与身份门禁

任何生产写入前必须：

1. 验证固定 SSH 主机身份、用户、host key、KEX 和 known-hosts；
2. 验证 `COMPUTERNAME=${PRODUCTION_HOSTNAME}`、用户=`Administrator`；
3. 只读验证 `${PRODUCTION_PWSH_PATH}` 存在，并通过该绝对路径确认 Version=`7.6.4`、PSEdition=`Core`、Is64Bit=`true`、Executable 为同一绝对路径；
4. 只读验证该 `pwsh.exe` Authenticode `Status=Valid` 且 signer subject 精确包含 `CN=Microsoft Corporation`；`where.exe pwsh` 必须返回同一绝对路径，但后续仍禁止依赖 PATH；
5. 通过 .NET 验证生产机本地时区 ID=`China Standard Time`、当前偏移=`+08:00`，并记录实际本地/UTC 执行时间；不再校验预设日历维护窗口；
6. 验证生产仓库 HEAD=`e2374e37e1357be3d8df93d6d3429bb0947fb9ba`、branch=`master`、worktree=0；
7. 用 `.NET/注册表/DriveInfo/Get-Process/netstat/TcpClient` 完成普通系统门禁；
8. 只有发现 active-run JSON 精确 PID 时才触发 §6.2 的定向 WMI；
9. 验证 BGE `status=ok/model_loaded=true`；
10. 通过受控 `nvidia-smi` 获取 GPU 证据；
11. 验证固定 RunRoot/StagingRoot 均不存在；
12. 验证无与 retry5 identity 精确绑定的 active controller/helper；
13. 历史 retry3/retry4 文件和 artifacts 只读保留。

A-1 不创建 staging、不上传文件、不创建 RunRoot。任一超时、blocker、状态不明或门禁失败立即停止。

### A-2：创建 staging 与上传固定输入

仅在 A-1 全通过后创建固定 StagingRoot，上传 §5 的计划、静态预检、retry5 helper/controller、BGE helper；样本只读复核历史路径并自动校验固定 SHA-256，不回写历史 run。

上传后验证：

- UTF-8 BOM；
- Windows PowerShell 5.1 parser errors=0；
- `${PRODUCTION_PWSH_PATH}` parser errors=0；禁止使用 PATH 解析或其他 `pwsh.exe`；
- retry5 helper SelfTest 19/19；
- retry5 controller SelfTest 9/9；
- 所有代码/文档源文件与 staging 目标文件均存在且字节长度一致；
- 冒烟样本固定 SHA-256 自动校验通过；
- staging manifest 写入成功；
- RunRoot 仍不存在。

失败时完整保留 staging，写精确 stop artifact 后停止。

### ProbeSuccess

- `ChildTimeoutSeconds=10`；
- controller exit=0；
- terminal status=`probe-success`；
- child exit=0；
- stdout 包含 `R3A_RETRY5_PROBE_SUCCESS`；
- terminal 后精确 child/process tree 为 0。

失败即停止。

### ProbeTimeout

仅在 ProbeSuccess 通过后执行：

- `ChildTimeoutSeconds=2`；
- controller exit=0；
- terminal status=`probe-timeout-controlled`；
- child exit=124；
- timed_out=true；
- 精确 `taskkill /PID <child PID> /T /F` 成功；
- terminal 后 probe process tree 为 0。

失败即停止，不创建 RunRoot。

### RunA0A1

仅在两个 probe 均通过后执行：

- 固定 RunRoot/StagingRoot 与全部源文件路径；
- controller watchdog=1,200 秒；
- SSH/Codex 等待至少 1,290 秒，推荐 1,320 秒；
- RunRoot 由固定 retry5 helper 首次创建；
- helper 重新验证生产 HEAD/worktree、BGE、GPU、磁盘、代理、下载白名单、模型 metadata 和样本；
- 日期口径由生产机实际执行时刻动态生成；要求本地时区 ID=`China Standard Time` 且偏移=`+08:00`，报告同时记录本地和 UTC 时间，不复用 retry3 的固定日期文本。

成功条件：

```text
controller exit=0
terminal status=p1-ready
child exit=0
timed_out=false
RunRoot\evidence\a1-baseline.json exists
RunRoot\config\approval.json exists
RunRoot\config\r3a-config.json exists
RunRoot\state\run-identity.json exists
RunRoot\reports\preflight.md exists
RunRoot\evidence\retry5-foreground-controller-status.json exists
```

任一条件不满足则标记 `STOPPED_BEFORE_P1_COMPLETE`，完整保留本次 RunRoot/StagingRoot，不进入 A2 wheel 下载。

### P1：强制暂停

提交 RunRoot/StagingRoot、自动文件完整性结果、样本/模型自动身份校验、两个 probe、A1 baseline/preflight、BGE/GPU/磁盘/代理门禁、WMI/nvidia watchdog 证据和当前精确进程末态。没有 P1 明确继续授权，不得下载 wheel/model。

## 9. SSH 中断与状态不明恢复

SSH 非零、超时、网络断开、窗口关闭或 terminal 未返回时：

- 不重跑同一 mode；
- 不写新 release；
- 不启动第二个 controller/helper；
- 不创建新 RunRoot；
- 不进入下一阶段；
- 先只读读取 lease/status/release/stdout/stderr。

只有 lease/status 提供精确 PID，并且 PID、StartTime/CreationDate、executable、parent/controller、command line、controller path/bytes、mode、RunRoot、StagingRoot 全部匹配时，才可另行请求批准终止该精确 process tree。

为核对命令行而使用 WMI 时，必须遵守 §6.2 的单 PID、5 秒子进程 watchdog。WMI 超时或身份字段不完整即视为状态不明：**不杀、不重跑、不猜测不存在**。

禁止按进程名、宽泛命令行结果、模糊 PID 或历史 PID 终止 `powershell`、`pwsh`、`python`、`curl`、`nvidia-smi`、`faster-whisper`、`ctranslate2` 或 SSH。

## 10. P1 后 A2–A8 继承边界

P1 明确放行后才允许：

1. A2 wheel 下载：30 分钟，只写本次 RunRoot/wheels；
2. P2 强制暂停：提交 wheel 清单/hash/许可证据，要求 blocker=0；
3. A3 离线安装：30 分钟，只安装本次隔离 venv；
4. A4 模型下载：120 分钟，固定 revision；`model.bin` SHA-256=`e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`，size=`1617884929` bytes；
5. P3 强制暂停：提交 venv/wheel/model 完整性和 GPU/BGE/磁盘末态；
6. A5 模型加载：15 分钟，精确 child/watchdog；
7. A6 单样本推理：10 分钟，仅固定合成短样本；
8. A7 BGE 鉴权探针：用户在生产机本地重新输入 Token，生成 15 分钟 DPAPI 临时文件；
9. A8 报告与清理：清理本次精确 DPAPI 文件并验证不存在；
10. P4 强制暂停：提交最终证据和建议；P4 不授权 R3-B。

固定候选：

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

## 11. 代理、下载、许可与鉴权

代理固定为：

```text
${PROXY_URI}
Clash Verge/Mihomo mixed-port
HTTP primary
不自动切换 SOCKS
```

下载域名仅限：`pypi.org`、`files.pythonhosted.org`、`huggingface.co`、`us.aws.cdn.hf.co`。禁止关闭 TLS 校验、吊销检查、SSH host key 校验或访问未批准镜像；重定向到非白名单 host 时立即停止。

许可 blocker 批准人=`bim-admin`，只能精确批准具体 blocker；P2 放行前必须 `blocker=0`。

BGE 必须全程 `status=ok/model_loaded=true`，不重启、不重载、不改配置、不升级、不做并发压测。DPAPI 临时文件仅允许精确路径 `${QUALIFICATION_SANDBOX_ROOT}\secrets\gpu-service-token.dpapi`，TTL=15 分钟；结束、失败、超时或停止时删除并验证不存在。禁止读取 `.env`、进程内存、浏览器存储或回显明文 Token。

## 12. 自动停止条件

任一条件触发立即停止：

- 固定计划/helper/controller/BGE helper 缺失、源/目标字节长度不一致、BOM/parser/SelfTest 漂移，或样本自动 SHA-256 不匹配；
- 生产机时区不是 `China Standard Time`、当前偏移不是 `+08:00`，或执行授权不是固定 retry5 identity 的单次授权；
- RunRoot/StagingRoot 已存在或 identity 不唯一；
- SSH host key、主机名、用户、HEAD、branch、worktree 漂移；
- 普通 .NET/注册表/DriveInfo/Get-Process/netstat/TcpClient 门禁失败；
- `NETSTAT_TIMEOUT`、`TCP_CONNECT_TIMEOUT`、`WMI_EXACT_QUERY_TIMEOUT`、`WMI_EXACT_QUERY_FAILED`、`WMI_EXACT_QUERY_INVALID_JSON` 或 `NVIDIA_SMI_TIMEOUT`；
- exact-PID 来源无法证明、超过 8 个或目标进程身份不完整；
- 任一 probe 非零、terminal 不匹配或 process tree 未归零；
- 同一 mode artifact 已存在；
- SSH 中断后 lease/status 无法精确判定；
- 发现第二个 controller/helper 或 detached/background 进程；
- A0/A1 超过 20 分钟；
- RunA0A1 terminal 非 `p1-ready` 或必需 artifact 缺失；
- BGE/GPU/磁盘/代理/TLS/白名单/模型 identity 异常；
- wheel/model/venv 超时或 hash/size 不匹配；
- 许可 blocker 非 0；
- DPAPI 超时未清理或凭据边界异常；
- P1/P2/P3/P4 未明确放行；
- 用户要求停止。

## 13. artifact、恢复与回滚

失败 artifact 策略固定为：**A 完整保留**。

允许动作仅限：

1. 在身份完全匹配且另获具体批准时终止本次精确 child/controller process tree；
2. 删除本次精确 DPAPI 临时文件并验证不存在；
3. 清除本次精确 active-run 指针/临时锁（仅在身份完全匹配时）；
4. stop-event 明确不存在且状态确定时原子写入，已存在不覆盖；
5. 完整保留本次 RunRoot、StagingRoot、wheel、model、logs、evidence、reports；
6. 不修改或删除 retry3 文件/artifacts；
7. 不执行生产仓库 reset/pull/commit；
8. 不修改 FunASR、BGE、CUDA、driver、PATH、全局 Python、服务、计划任务或 WMI Repository。

删除任何 RunRoot/StagingRoot 必须另行提交精确路径和独立 R3 删除审批。

## 14. 无预设维护窗口、单次授权与日期记录

本计划取消预先填写的日历维护窗口，不再接收或校验 `WindowStart/WindowEnd`。这不取消任何步骤级 watchdog、下载/安装/加载/推理超时、自动停止条件或 P1/P2/P3/P4 强制暂停点。

生产授权采用以下语义：

- 仅适用于固定 identity `phase0-fw-r3a-retry5-20260801-231730` 及本计划明确范围；
- 用户明确批准后，由 Codex 在同一任务中开始执行；首个生产 SSH 调用即视为本次单次授权已使用；
- 到达任一暂停点、自动停止、SSH 中断、状态不明或 Codex 任务结束后，不得把该授权用于重试、恢复写入或新 identity；后续动作必须重新报告并取得精确批准；
- 生产机必须由 .NET 确认本地时区 ID=`China Standard Time` 且当前偏移=`+08:00`；artifact 动态记录实际本地时间、UTC 时间和偏移，不硬编码历史日期。

## 15. 本地验证证据

本地完成且未连接生产：

- retry5 helper/controller 均为 UTF-8 BOM；本地使用 PowerShell 7.6.4 Core 完成 parser，计划内所有生产 PowerShell 7 调用仍固定使用 `${PRODUCTION_PWSH_PATH}`；
- Windows PowerShell 5.1 parser：两个文件均 `PARSE_OK`；
- PowerShell 7 parser：两个文件均 `PARSE_OK`；
- helper 模拟 SelfTest：`tests=19`、`failures=[]`、`passed=true`、`real_wmi_invoked=false`、`real_nvidia_smi_invoked=false`；
- controller 模拟 SelfTest：`tests=9`、`failures=[]`、`passed=true`；
- 29 项静态安全门禁全部通过：原人工 SHA 参数、固定 SHA 门禁、controller SHA 状态字段及人工审批模板字段均已移除；controller 改为记录 `controller_bytes`；
- 代码和文档复制完整性改为自动验证源/目标文件存在且字节长度一致；模型与合成冒烟样本的固定 SHA-256/size 自动身份校验仍保留；
- 静态检查确认：无 `Win32_LogicalDisk`、无 `Win32_OperatingSystem`、无 `Get-NetTCPConnection`、无直接 `& nvidia-smi.exe`、无 retry3 marker；
- `Get-CimInstance` 仅出现 1 次，且仅为带 `ProcessId=<exact PID>` filter 和 `OperationTimeoutSec` 的定向查询；
- SelfTest 的 WMI/nvidia 成功与永久阻塞均通过模拟 PowerShell 子进程实现，不调用真实 WMI/nvidia-smi；timeout SelfTest 验证精确子进程已退出且无孤儿；
- 本次取消人工 SHA 流程的修订未计算、未生成、未填写任何新的计划/helper/controller SHA-256。

本轮未执行：

- 未连接生产机 `${GPU_NODE_ZEROTIER_IP}`；
- 未执行 retry5；
- 未运行真实 WMI/CIM；
- 未运行真实 `nvidia-smi`；
- 未运行 faster-whisper；
- 未创建远端 RunRoot/StagingRoot；
- 未上传、下载或安装任何文件/依赖；
- 未输入 BGE Token 或创建 DPAPI 文件；
- 未修改或删除 retry3/retry4 文件及 artifacts。

## 16. 可复制的后续审批模板

```text
批准执行 faster-whisper R3-A retry5，按
${REPOSITORY_CHECKOUT_PATH}\project-docs\plans\faster-whisper-r3a-retry5-execution-plan.md
执行；本计划不生成或填写人工 SHA-256。

执行通道 = Codex 经验证 SSH；必须使用 retry5 foreground controller，禁止 detached/background supervisor
执行授权 = 无预设维护窗口；固定 retry5 identity 的单次授权，首个生产 SSH 调用即视为已使用；中断、停止、暂停点之后的继续或重试均须重新批准
时区 = Asia/Shanghai；生产机必须为 China Standard Time / +08:00，执行时自动记录本地与 UTC 时间
批准范围 = retry5 预留 identity 下的 A-1、A-2、ProbeSuccess、ProbeTimeout、A0–A8；不包含 R3-B、冻结 8 样本、长音频、BGE 并发压测、Phase 1 或 FunASR 替换
RunRoot = ${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-20260801-231730
StagingRoot = ${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-inputs\phase0-fw-r3a-retry5-20260801-231730
代码/计划完整性 = 自动检查文件存在、源/目标字节长度、UTF-8 BOM、双版本 parser 和模拟 SelfTest；无需填写代码或计划 SHA-256
冒烟样本 = ${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-20260801-072218\testdata\r3a-synthetic-zh.wav
冒烟样本 SHA-256 = af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9
样本声明 = 自制或合成、非客户、非内部
PowerShell 7 = 固定 ${PRODUCTION_PWSH_PATH}，Version=7.6.4、Core、x64、Microsoft Authenticode Valid；所有调用禁止依赖 PATH
普通查询 = OS/磁盘/普通进程/端口使用 .NET、注册表、DriveInfo、Get-Process、netstat、TcpClient；禁止宽泛 WMI/CIM
WMI 例外 = 仅 active-run JSON 提供的精确 PID，逐 PID 查询，最多 8 个；OperationTimeout 和子进程 watchdog 最长 5 秒；超时状态不明，不杀、不重跑
nvidia-smi = 独立 .NET 子进程，watchdog 15 秒；超时只终止本次精确查询进程并自动停止
前台门禁 = A-1 -> A-2 -> ProbeSuccess -> ProbeTimeout -> RunA0A1 -> P1；任一步失败自动停止
A0/A1 controller watchdog = 20 分钟；SSH/Codex 客户端等待至少 21.5 分钟，推荐 22 分钟
SSH 中断恢复 = 先读 lease/status；仅在 PID、StartTime/CreationDate、executable、parent/controller、command line、mode、RunRoot、StagingRoot 全部精确匹配且另获具体批准时终止精确 process tree；状态不明不杀、不重跑
BGE 鉴权探针 = 批准本地重新输入并生成 15 分钟 DPAPI 临时文件
失败 artifact 策略 = A 完整保留
暂停点 = P1/P2/P3/P4 全部强制
代理 = ${PROXY_URI}，Clash Verge/Mihomo mixed-port，HTTP primary；不自动切换 SOCKS
允许下载来源 = pypi.org、files.pythonhosted.org、huggingface.co、us.aws.cdn.hf.co
许可 blocker 批准人 = bim-admin，仅可精确批准具体 blocker
超时 = wheel 30 分钟；模型 120 分钟；离线安装 30 分钟；模型加载 15 分钟；推理 10 分钟
日期一致性 = 不使用预设维护窗口；执行与报告动态记录生产机 Asia/Shanghai +08:00 本地时间和 UTC 时间
同意自动停止、精确进程恢复/回滚和本次精确 DPAPI 文件清理。
```

即使整份 retry5 计划获得批准，也必须按暂停点逐段执行；不得以整份计划批准为由越过 P1/P2/P3/P4 连续执行。
