# faster-whisper Phase 0 R3-A retry3 修订执行计划（fourth retry run / global fifth run）

> 状态：**待用户重新审批；仅已完成本地设计、helper 验证和 new identity 预留，尚未连接生产或创建远端 RunRoot/staging**  
> 风险等级：**R3（生产 Windows GPU 主机、远端进程生命周期、外部下载、隔离安装、模型权重、GPU/BGE 与临时鉴权材料）**  
> 编制日期与口径：**2026-08-01，Asia/Shanghai（UTC+08:00）**  
> 计划性质：本文件取代 retry2 计划，作为下一次生产执行的唯一补充计划。只有用户核对本文件最终 SHA-256 并明确回复“批准执行”或同等授权后，Codex 才可创建本文件预留的远端 staging/RunRoot 并执行；本文件本身不构成生产执行授权。

## 1. 目标、结论与审批门禁

本计划用于在**第四个 retry run（全局第 5 个 R3-A run）**中重新执行 faster-whisper Phase 0 R3-A。直接目标不是更换识别方案，而是修复 retry2 在 A1 暴露的 SSH 后台进程生命周期边界：

1. 不再启动脱离 SSH launcher 的后台 supervisor；
2. SSH 会话保持前台，远端 controller 也保持前台；
3. controller 创建并持有精确 child `System.Diagnostics.Process` 对象；
4. child 在 lease 原子落盘前不得进入 helper；
5. controller 持续写 heartbeat，前台等待 child 结束；
6. 超时只终止该精确 PID 对应的 process tree；
7. SSH 中断或状态不明时，先按 lease/status 做精确恢复，不复用、盲重启或模糊杀进程；
8. A0/A1 成功后仍在 P1 强制暂停，不自动进入 A2。

本计划仍然评估 `faster-whisper`，**不表示弃用 FunASR，也不授权替换现有 FunASR**。

治理关系：

1. 原始 R3-A 计划继续作为 A0–A8 主体规范：
   - `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-execution-plan.md`
   - SHA-256：`e2508a827441d8e7fea61441be9e6551e4a94ee6fd1f903048b5017c8baf08d1`
2. 静态预检继续作为历史依据：
   - `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-phase0-precheck.md`
   - SHA-256：`2edb7c53fc9aec9818eec6be70fd1fa3873d3ce4b0900d7c53e819a9fee9717e`
3. retry2 计划仅作为历史审批与失败证据，不再授权新执行：
   - `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry2-execution-plan.md`
   - SHA-256：`fd4f89d76f985539262d0524d723da46527f8300687eb7cdedf740074a93fdf0`
4. 本 retry3 计划仅在以下事项上优先：
   - 第四个 retry/new identity（全局第 5 个 R3-A run）；
   - retry2 A1 后台 supervisor 失败后的生命周期控制架构；
   - 新 foreground controller 及其固定 SHA-256；
   - ProbeSuccess、ProbeTimeout、RunA0A1 的强制顺序；
   - SSH 中断后的 lease/status 精确恢复；
   - controller 20 分钟 watchdog 与 SSH 客户端更长超时；
   - 仍在 P1/P2/P3/P4 强制暂停。
5. 本计划、controller、A0/A1 helper、BGE helper、样本或固定输入 hash 任一变化，或范围/风险扩大、维护窗口过期，必须停止并重新提交计划与 SHA-256 审批。

## 2. retry2 失败事实与安全末态

### 2.1 历史 run 只读保留

以下四个历史 run 均为审计 artifact，不得覆盖、续写、复制为新 identity 或删除：

1. 原始 R3-A run：
   - `E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-20260801-072218`
   - 状态：`STOPPED_BEFORE_P1_COMPLETE`
   - stop-event SHA-256：`fadb0862384f4ee5f05f30e24e7bc98b171d7f7522a97f362200026aade9ac42`
2. 第一次 retry run：
   - `E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-20260801-084400`
   - 状态：`STOPPED_BEFORE_P1_COMPLETE`
   - stop-event SHA-256：`3a6d732925b0aefe1e4cd9023a2e0d0acf6d32072bd3fdeb1929ad070b68fa68`
3. 第二次 retry run：
   - `E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-20260801-085302`
   - 状态：`STOPPED_BEFORE_P1_COMPLETE`
   - 经 retry2 A-1 只读确认的 stop-event SHA-256：`07c789d7c184c0dbe63d53296ac07f13cd46ec2b48626b8152e9523f9ead4502`
4. 第三次 retry run（全局第 4 个 R3-A run）：
   - `E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-20260801-104725`
   - 状态：`STOPPED_BEFORE_P1_COMPLETE`
   - stop-event SHA-256：`4258ca4ea9f85fc2fd298ea90404ee87e61c0fe257ee492c0c413c755427efb1`
   - A1 supervisor recovery SHA-256：`e7c103913442b091bc9f36e6858960222e7dcbfb8c2ae000636fce718751f274`

四个历史 run 均未进入 A2。最新生产安全末态已于 `2026-08-01T11:06:23+08:00` 验证：生产 HEAD/worktree 未漂移，BGE `status=ok/model_loaded=true`，GPU 利用率 0%，相关 curl/faster-whisper/CTranslate2/supervisor 进程为 0，本次 wheel/model/venv/DPAPI 文件为 0。

### 2.2 retry2 A1 的有限结论

retry2 的后台 supervisor PID `23548` 在发布 `a1-supervisor-status.json` 和生成 `evidence\a1-baseline.json` 前退出，stdout/stderr 为空且不再被持有。

可证明的结论仅为：

- 该后台进程未可靠跨越 SSH launcher 生命周期完成；
- 原后台架构无法提供可靠的前台等待、heartbeat、终态和超时终止证据。

不得据此断言 Win32-OpenSSH 本身存在确定缺陷，也不得在旧 RunRoot 内重启 supervisor。

## 3. 固定生产身份与预留 new identity

### 3.1 SSH 身份门禁

```text
Host=FJPCSEVER
IP=10.205.165.105
User=Administrator
SSH ED25519=SHA256:nRSpKS3UAsE2IecHqyxSryD4Q9Af1piSF4siM+LTS9M
KexAlgorithms=curve25519-sha256
StrictHostKeyChecking=yes
UserKnownHostsFile=$env:TEMP\pincheng-gpu-known-hosts
```

只使用 Bitwarden SSH Agent。不得导出私钥，不得把私钥、Token、Cookie 或明文凭据写入聊天、仓库、普通日志或 run artifact。

所有远端 PowerShell `-EncodedCommand` 必须由**调用端字面量脚本**生成 UTF-16LE Base64；禁止用可提前展开 `$env:*`、`$PID` 或远端变量的双引号脚本拼接。

### 3.2 本计划预留的精确 identity

本计划固定并预留以下唯一 identity：

```text
RunId=phase0-fw-r3a-retry-20260801-113717
RunRoot=E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-20260801-113717
StagingRoot=E:\FunASR-Phase0\faster-whisper-inputs\phase0-fw-r3a-retry-20260801-113717
```

说明：本轮“创建 new identity”是把上述 identity 固定进受哈希保护的计划。由于这是 R3 生产写入，**计划获批前不得实际创建远端 RunRoot 或 StagingRoot**。获批后创建前必须再次只读确认两条路径均不存在；任一路径已存在、状态不明或不再唯一，立即停止并生成新计划/新 identity/新 SHA-256，不能自动改时间戳继续。

路径硬门禁：

- RunRoot 必须是 `E:\FunASR-Phase0\faster-whisper-runs` 的直接子目录；
- StagingRoot 必须是 `E:\FunASR-Phase0\faster-whisper-inputs` 的直接子目录；
- 目录名必须精确等于 `phase0-fw-r3a-retry-20260801-113717`；
- RunRoot 由 `RunA0A1` child 内的固定 helper 首次创建；controller 启动前 RunRoot 必须不存在；
- StagingRoot 只能在计划批准后用于上传本次固定输入和 controller artifact；
- 不得复用或修改任何历史 run/staging。

## 4. 固定输入、helper 与哈希

| 输入 | 本地源路径 | SHA-256 |
|---|---|---|
| 原始 R3-A 计划 | `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-execution-plan.md` | `e2508a827441d8e7fea61441be9e6551e4a94ee6fd1f903048b5017c8baf08d1` |
| 静态预检 | `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-phase0-precheck.md` | `2edb7c53fc9aec9818eec6be70fd1fa3873d3ce4b0900d7c53e819a9fee9717e` |
| A0/A1 helper | `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry-a0-a1.ps1` | `11635a071fc56d8a5a8a4b2fe9a89c3516b7702b02dffa90fb140d8cd7f03be5` |
| BGE 鉴权 helper | `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry-bge-auth-probe.ps1` | `758eabc198e94c339a59bce29fa7258410a04d2f2e5ff295528e2d2d4304ef98` |
| retry3 foreground controller | `E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry3-foreground-controller.ps1` | `aeee89d8cc7f7c1edfd8b7f632d574a1bc5c82c0745c9effa8a9a25fdaef8515` |
| 合成冒烟样本 | `E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-20260801-072218\testdata\r3a-synthetic-zh.wav`（生产历史 run 内只读源） | `af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9` |
| 本 retry3 计划 | 本文件 | `<以用户批准时的最终 SHA-256 为准>` |

样本声明继续固定为：**自制或合成、非客户、非内部**。

执行前必须：

1. 本地重新计算所有固定文件 SHA-256；
2. 上传后在生产 staging 再计算 SHA-256；
3. A0/A1 helper 验证 UTF-8 BOM、Windows PowerShell 5.1 parser errors=0、SelfTest 16/16；
4. foreground controller 验证 UTF-8 BOM、Windows PowerShell 5.1 parser errors=0、SelfTest 9/9；
5. 任一 hash、编码、parser 或 SelfTest 不匹配立即停止；不得现场编辑后继续。

controller 本地验证证据：

- UTF-8 BOM：true；
- Windows PowerShell 5.1 parser errors=0；
- SelfTest：9/9、failures=[]、exit=0；
- 完整本机 `ProbeSuccess`：status=`probe-success`、child exit=0、controller exit=0；
- 完整本机 `ProbeTimeout`：status=`probe-timeout-controlled`、child exit=124、`taskkill /PID <exact PID> /T /F` exit=0、controller exit=0；
- 使用无生产副作用 stub 的完整本机 `RunA0A1`：status=`p1-ready`、child/controller exit=0、timed_out=false、必需 artifact 与 RunRoot 终态复制均通过；
- 测试仅使用 GUID 临时目录，已验证后清理，未连接生产。

## 5. 前台 SSH 生命周期控制通道

### 5.1 禁止的旧模式

以下方式全部禁止：

- 通过 SSH 启动 `Start-Process` 后立即让 SSH launcher 结束；
- `Start-Job`、后台 job、计划任务、服务化或其他脱离本次前台 SSH 的方式；
- 仅记录 PID、不持有 `Process` 对象且无 heartbeat/终态；
- 按进程名批量终止 `powershell`、`python`、`curl`、`faster-whisper` 或 `ctranslate2`；
- `taskkill /IM ...`、模糊命令行匹配、批量杀 Python；
- SSH 超时后直接用同一 RunRoot 重跑 helper。

### 5.2 controller 状态机

固定 controller 支持三种模式，必须按顺序执行：

```text
ProbeSuccess -> ProbeTimeout -> RunA0A1 -> P1
```

controller 的每次模式执行：

1. 保持 SSH 客户端前台运行；
2. 远端 controller 作为 SSH 的前台进程；
3. 创建精确 child `System.Diagnostics.Process`，重定向 stdout/stderr；
4. child 先等待本模式 release 文件，最长 60 秒；
5. controller 获取 child PID、UTC StartTime、executable；
6. controller 原子写入 lease 后才原子写 release；
7. child 收到 release 后才执行 probe 或 A0/A1 helper；
8. controller 每 5 秒原子写 status heartbeat；
9. controller 前台等待 child 结束；
10. 超时只对持有的精确 Process 执行 `taskkill /PID <exact PID> /T /F`；
11. 写 stdout、stderr、terminal status；
12. RunRoot 存在时把终态复制为 `evidence\retry3-foreground-controller-status.json`。

每个模式使用不同 artifact，已存在即停止，禁止覆盖：

```text
StagingRoot\controller\<mode>-lease.json
StagingRoot\controller\<mode>-release.txt
StagingRoot\controller\<mode>-status.json
StagingRoot\controller\<mode>-stdout.log
StagingRoot\controller\<mode>-stderr.log
```

### 5.3 release-before-work 不变量

child 在 lease 成功落盘前不得执行 payload。若 controller 在写 lease 前失败：

- release 文件不存在；
- child 最多等待 60 秒后以 125 退出；
- 不得创建 RunRoot 或进入 helper。

若 lease 已写但 release 未写，恢复时只读核对；不得手工补 release。等待 child 自行到达 60 秒门禁并验证退出，状态不明即停止。

### 5.4 watchdog 与 SSH 客户端超时

固定超时：

| 模式 | controller child timeout | SSH/Codex 客户端最短等待 |
|---|---:|---:|
| ProbeSuccess | 10 秒 | 100 秒 |
| ProbeTimeout | 2 秒 | 100 秒 |
| RunA0A1 | 1,200 秒（20 分钟） | 1,290 秒以上；推荐 1,320 秒（22 分钟） |

SSH/Codex 客户端超时必须比 controller watchdog 至少长 90 秒，避免调用端先超时而使远端状态失去前台持有。不得为了“避免等待”缩短客户端超时，也不得把 controller watchdog 增至 30 分钟以上。

## 6. 获批后的执行顺序

### A-1：只读恢复与身份门禁

在任何生产写入前：

1. 使用固定 host key、KEX、known-hosts 和 Bitwarden Agent 连接；
2. 用调用端字面量 UTF-16LE Base64 执行远端 PowerShell 返回门禁；
3. 验证 `COMPUTERNAME=FJPCSEVER`、用户为 `Administrator`；
4. 验证当前上海时间位于批准维护窗口；
5. 验证生产仓库 HEAD=`e2374e37e1357be3d8df93d6d3429bb0947fb9ba`、branch=`master`、worktree=0；
6. 验证 BGE 健康、GPU/磁盘满足原计划门禁；
7. 验证本次 RunRoot/StagingRoot 均不存在；
8. 验证无 active-run、无绑定本次 identity 的进程或残留 controller；
9. 历史四个 run 只读，不再补写、覆盖或清理。

任一门禁失败立即停止，不创建 staging。

### A-2：创建 staging 并上传固定输入

只有 A-1 全通过后，才允许创建精确 StagingRoot。上传本计划、原始计划、静态预检、A0/A1 helper、BGE helper 和 foreground controller。固定样本不重生成、不写回旧 run；只读复核原始 run 内的精确绝对路径与 SHA-256，并把该路径作为 `SamplePath` 传给 controller/helper。

上传后必须：

- 逐文件计算 SHA-256 并与 §4 一致；
- 对两个 PowerShell helper/controller 执行编码、parser 和 SelfTest 门禁；
- 记录 `staging-manifest.json`，但不得创建 RunRoot；
- 上传或验证失败时保留 staging，写 stop artifact，停止。

### A-3：ProbeSuccess

通过同一前台 SSH 通道运行 controller `ProbeSuccess`：

- `ChildTimeoutSeconds=10`；
- `ReleaseWaitSeconds=60`；
- 要求 controller exit=0；
- terminal status=`probe-success`；
- child exit=0；
- stdout 包含 `R3A_RETRY3_PROBE_SUCCESS`；
- terminal 后对应 PID/process tree 为 0。

失败即停止，不运行 ProbeTimeout 或 RunA0A1。

### A-4：ProbeTimeout

仅在 ProbeSuccess 通过后运行 `ProbeTimeout`：

- `ChildTimeoutSeconds=2`；
- `ReleaseWaitSeconds=60`；
- 要求 controller exit=0；
- terminal status=`probe-timeout-controlled`；
- child exit=124；
- timed_out=true；
- `taskkill` exit=0；
- terminal 后 child 及其 probe process tree 为 0。

此 probe 是对本次精确 child tree 的受控超时终止验证，不得扩展到其他进程。失败即停止，不创建 RunRoot。

### A0–A1：RunA0A1 前台执行

仅在两个 probe 均通过后，运行 controller `RunA0A1`：

- 固定 RunRoot/StagingRoot；
- 固定 controller/helper/plan/BGE helper/sample SHA-256；
- `ChildTimeoutSeconds=1200`；
- `ReleaseWaitSeconds=60`；
- SSH/Codex 客户端等待至少 1,290 秒，推荐 1,320 秒；
- RunRoot 必须由 child 中的固定 A0/A1 helper 首次创建；
- helper 内继续执行原计划 A0/A1 的生产 HEAD/worktree、BGE、GPU、磁盘、代理、白名单、模型 metadata、固定样本和证据门禁。

成功条件必须同时满足：

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
RunRoot\evidence\retry3-foreground-controller-status.json exists
```

任一条件不满足，状态为 `STOPPED_BEFORE_P1_COMPLETE`，完整保留本次 RunRoot/staging，不进入 A2。

### P1：强制暂停

A0/A1 通过后必须停止并向用户提交：

- RunRoot/StagingRoot；
- controller、helper、计划、样本和 run identity hash；
- ProbeSuccess/ProbeTimeout 结果；
- A1 baseline、preflight、BGE/GPU/磁盘/代理门禁；
- 当前精确进程计数和生产安全末态；
- 是否建议进入 A2。

没有用户对 P1 的明确继续授权，不得下载 wheel/model。

## 7. SSH 中断与状态不明的精确恢复

### 7.1 总原则

SSH 客户端非零、超时、网络断开、窗口关闭或 terminal 未返回时，立即把本次执行视为**状态不明并自动停止推进**：

- 不重跑同一 mode；
- 不写新的 release；
- 不启动第二个 controller/helper；
- 不创建新 RunRoot；
- 不进入下一阶段；
- 先重连做只读 lease/status 恢复。

### 7.2 只读判定顺序

1. 读取该 mode 的 status、lease、release、stdout、stderr；
2. 若 status 是 terminal（`probe-success`、`probe-timeout-controlled`、`p1-ready`、`stopped-before-p1-complete`、`controller-failed` 等），不得再杀或重跑；按 terminal 与必需 artifact 判定；
3. 若只有 pending lease 且无 child PID，确认 release 不存在，等待 release gate 最长 60 秒后只读验证无 child；
4. 若 lease 有 child PID，读取：
   - lease 中的 child PID；
   - `child_started_at_utc`；
   - `child_executable`；
   - controller PID、mode、StagingRoot、RunRoot、controller path/hash；
5. 用 CIM/Get-Process 只读核对 PID、StartTime、executable、parent/controller 和命令行绑定；任一不一致或无法读取，**禁止终止该 PID**，立即停止并请求人工判断。

### 7.3 精确停止顺序

只有 PID、StartTime、executable、parent/controller 和本次 StagingRoot/mode 全部一致时，才允许：

1. 对 lease 中的精确 child PID 执行：
   - `taskkill /PID <exact child PID> /T /F`
2. 等待最多 15 秒，让仍存活的 controller观察 child 终态并写 status；
3. 若 controller 仍存活且其 PID、CreationTime、command line、controller path/hash、mode、StagingRoot 全部精确匹配，再终止该精确 controller PID；
4. 验证两个精确 PID/process tree 均不存在；
5. 在本次 staging 原子写 `controller\retry3-channel-recovery.json`；
6. 若 RunRoot 已存在且 `reports\stop-event.md` 不存在，才原子创建该精确 stop-event；已存在不覆盖；状态不明不写；
7. 完整保留 artifact，停止在 P1 前。

禁止使用进程名、宽泛命令行搜索结果或猜测 PID 执行终止。若无法证明精确身份，宁可停止并报告，也不得冒险杀生产进程。

## 8. P1 后 A2–A8 继承边界

P1 获得明确继续授权后，A2–A8 继续按原始 R3-A 计划和 retry2 的未冲突条款执行：

1. **A2 wheel 下载**：只写本次 RunRoot `wheels`，30 分钟；HTTP/HTTPS proxy 固定为 `http://10.205.165.230:7897`；只允许批准域名；不自动切换 SOCKS。
2. **P2 强制暂停**：提交 wheel 清单、hash、许可证据和 blocker；必须 `blocker=0`。
3. **A3 离线安装**：只安装到本次隔离 venv，30 分钟；禁止全局 Python、PATH 和生产服务修改。
4. **A4 模型下载**：120 分钟；固定 revision；独立计算全文件 hash/大小；`model.bin` 必须为：
   - SHA-256=`e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`
   - size=`1617884929` bytes
5. **P3 强制暂停**：提交 venv、wheel、模型完整性和磁盘/GPU/BGE 末态。
6. **A5 模型加载**：15 分钟；精确 child PID/watchdog；BGE 不健康、显存门禁不满足或模型加载异常即停止。
7. **A6 单样本推理**：10 分钟；仅固定合成短样本；不执行冻结 8 样本、长音频或并发压测。
8. **A7 BGE 鉴权探针**：经用户本地重新输入生产 Token，只生成 15 分钟 DPAPI 临时文件；禁止读取 `.env`、进程内存或回显 Token。
9. **A8 报告与清理**：生成证据报告，清理本次精确 DPAPI 文件并验证不存在；不自动删除失败 artifact。
10. **P4 强制暂停**：提交最终结果、安全末态、残留进程/文件和是否建议 R3-B；P4 不授权 R3-B。

## 9. 固定候选、代理、下载与许可

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

代理：

```text
http://10.205.165.230:7897
Clash Verge/Mihomo mixed-port
HTTP primary
不自动切换 SOCKS
```

允许下载来源仅限：

```text
pypi.org
files.pythonhosted.org
huggingface.co
us.aws.cdn.hf.co
```

禁止关闭 TLS 校验、吊销检查或 host key 校验；禁止访问未批准镜像。重定向到非白名单 host 时立即停止。

许可 blocker 批准人固定为 `bim-admin`，只能精确批准具体 blocker；不得用角色名泛化豁免。P2 放行前必须 `blocker=0`。

## 10. BGE、GPU、DPAPI 与并发保护

- BGE 全程必须 `status=ok/model_loaded=true`；
- 不重启、不重载、不改配置、不升级 BGE；
- GPU/BGE 资源门禁沿用原计划；
- 本次不做 BGE 并发压测；
- BGE 鉴权只允许用户在生产机本地重新输入；
- DPAPI 临时文件精确路径沿用：
  - `E:\FunASR-Phase0\secrets\gpu-service-token.dpapi`
- TTL=15 分钟；结束、失败、超时或用户停止时删除该精确文件并验证不存在；
- 不输出明文、解密内容或可复用 Token；
- 不读取 `.env`、进程内存、浏览器存储或其他凭据来源。

## 11. 自动停止条件

出现任一条件立即停止，不进入下一阶段：

- 固定计划/controller/helper/sample/hash/编码/parser/SelfTest 漂移；
- 维护窗口未开始、已结束或不足以安全完成下一阶段；
- 固定 RunRoot/StagingRoot 已存在、路径不匹配或 identity 不唯一；
- SSH host key、主机名、用户、HEAD、branch、worktree 漂移；
- 前台 SSH 客户端超时小于 controller watchdog+90 秒；
- 任一 probe 非零、terminal 不匹配、精确 process tree 未归零；
- 同一 mode artifact 已存在；
- SSH 中断后 lease/status 无法精确判定；
- 发现第二个 controller/helper 或 detached/background 进程；
- A0/A1 超过 20 分钟；
- RunA0A1 terminal 非 `p1-ready` 或必需 artifact 缺失；
- BGE 不健康、GPU/磁盘门禁失败、生产 worktree 漂移；
- 代理、TLS、白名单、重定向或模型 identity 异常；
- wheel/model/venv 超时或 hash/size 不匹配；
- 许可 blocker 非 0；
- DPAPI 文件超时未清理或凭据边界异常；
- 任一 P1/P2/P3/P4 未获得明确继续授权；
- 用户要求停止。

## 12. artifact 策略、恢复与回滚

失败 artifact 策略固定为：**A 完整保留**。

允许的恢复/回滚动作仅限：

1. 终止本次 lease 精确绑定且身份完全匹配的 child/controller process tree；
2. 删除本次精确 DPAPI 临时文件并验证不存在；
3. 清除本次 active-run 指针或本次精确临时锁（若原计划明确创建且身份匹配）；
4. 不再向失败 RunRoot 写入新阶段结果；
5. 仅在 stop-event 明确不存在且状态确定时原子写本次精确 stop-event，已存在不覆盖；
6. 完整保留本次 RunRoot、staging、wheel、model、logs、evidence 和 reports；
7. 不删除、不修改四个历史失败 run；
8. 不执行生产仓库 reset/pull/commit；
9. 不修改 FunASR、BGE、CUDA、driver、PATH、全局 Python、服务或计划任务。

本计划不授权自动删除本次失败 RunRoot/staging。若未来要删除，必须另行提交精确路径、目录清单和独立 R3 删除审批。

## 13. 明确不做

本 retry3 计划不授权：

- R3-B；
- 冻结 8 样本评测；
- 长音频；
- BGE 并发压测；
- Phase 1；
- FunASR 替换、下线或配置修改；
- Contextual Paraformer 重跑或热词/阈值调整；
- 生产部署或业务流量切换；
- 修改生产仓库源码、提交、分支或工作区；
- 全局安装 Python 包；
- 创建服务、计划任务、后台 job 或 detached supervisor；
- 读取未批准凭据；
- 删除历史或本次失败 artifact；
- 以模糊条件终止任何生产进程。

## 14. 维护窗口与日期一致性

批准维护窗口候选继续沿用：

```text
2026-08-01T07:06:00+08:00 至 2026-08-01T17:06:00+08:00
Asia/Shanghai
```

UTC `2026-07-31` 与 Asia/Shanghai `2026-08-01` 可以是同一时刻的时区日期差。执行、artifact、报告和审批均以 Asia/Shanghai `+08:00` 为准。

若用户批准时已超过 `2026-08-01T17:06:00+08:00`，或剩余窗口不足以完成下一阶段和安全回滚，必须停止并重新审批新窗口；不得把历史窗口自动顺延。

## 15. 审批前本地验证与未执行事项

本计划提交前仅完成：

- 设计 foreground SSH/controller/lease/release/heartbeat/watchdog 通道；
- 编写并本地验证 foreground controller；
- 编写本 retry3 计划；
- 固定并预留 new identity；
- 生成 controller 与计划 SHA-256。

尚未执行：

- 未连接生产；
- 未创建远端 RunRoot/StagingRoot；
- 未上传文件；
- 未启动远端 controller/helper/process；
- 未下载或安装 wheel/model；
- 未加载模型或推理；
- 未输入 BGE Token；
- 未创建 DPAPI 文件；
- 未修改生产仓库、服务或配置。

## 16. 可复制审批模板

```text
批准执行 faster-whisper R3-A retry3，按
E:\Repository\Github\RAGPinCheng\project-docs\plans\faster-whisper-r3a-retry3-execution-plan.md
执行；计划 SHA-256 = <填写本文件最终 SHA-256>。

执行通道 = Codex 经验证 SSH；必须使用 retry3 foreground controller，禁止 detached/background supervisor
维护窗口 = 2026-08-01T07:06:00+08:00 至 2026-08-01T17:06:00+08:00
批准范围 = 第四个 retry run（全局第 5 个 R3-A run）预留 identity 下的 A-1、A-2、A-3、A-4、A0–A8；不包含 R3-B、冻结 8 样本、长音频、BGE 并发压测、Phase 1 或 FunASR 替换
RunRoot = E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-20260801-113717
StagingRoot = E:\FunASR-Phase0\faster-whisper-inputs\phase0-fw-r3a-retry-20260801-113717
controller SHA-256 = aeee89d8cc7f7c1edfd8b7f632d574a1bc5c82c0745c9effa8a9a25fdaef8515
A0/A1 helper SHA-256 = 11635a071fc56d8a5a8a4b2fe9a89c3516b7702b02dffa90fb140d8cd7f03be5
BGE helper SHA-256 = 758eabc198e94c339a59bce29fa7258410a04d2f2e5ff295528e2d2d4304ef98
冒烟样本 = E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-20260801-072218\testdata\r3a-synthetic-zh.wav
冒烟样本 SHA-256 = af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9
样本声明 = 自制或合成、非客户、非内部
前台门禁 = ProbeSuccess -> ProbeTimeout -> RunA0A1 -> P1；任一步失败自动停止
A0/A1 controller watchdog = 20 分钟；SSH/Codex 客户端等待至少 21.5 分钟，推荐 22 分钟
SSH 中断恢复 = 先读 lease/status；仅在 PID、StartTime、executable、parent/controller、command line、mode、StagingRoot 全部精确匹配时终止精确 process tree；状态不明不杀、不重跑
BGE 鉴权探针 = 批准本地重新输入并生成 15 分钟 DPAPI 临时文件
失败 artifact 策略 = A 完整保留
暂停点 = P1/P2/P3/P4 全部强制
代理 = http://10.205.165.230:7897，Clash Verge/Mihomo mixed-port，HTTP primary；不自动切换 SOCKS
允许下载来源 = pypi.org、files.pythonhosted.org、huggingface.co、us.aws.cdn.hf.co
许可 blocker 批准人 = bim-admin，仅可精确批准具体 blocker
超时 = wheel 30 分钟；模型 120 分钟；离线安装 30 分钟；模型加载 15 分钟；推理 10 分钟
日期一致性 = UTC 2026-07-31 与 Asia/Shanghai 2026-08-01 是同一时刻的时区日期差；执行与报告使用 Asia/Shanghai +08:00
同意自动停止、精确进程恢复/回滚和本次精确 DPAPI 文件清理。
```

审批后仍必须按 `ProbeSuccess -> ProbeTimeout -> RunA0A1 -> P1` 顺序执行并在 P1 强制停止。对整份计划的批准不等于允许越过 P1/P2/P3/P4 连续执行。
