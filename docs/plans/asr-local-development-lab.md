# Qwen3-ASR / WhisperX 本地快速实验室

- 状态：已批准并实施；本机 bootstrap、smoke、focus、full 已完成，本地评审修复已收口
- 风险等级：R2（隔离依赖、模型下载、GPU 推理与跨模块 qualification 复用）
- 默认实验室根目录：`E:\RAGPinCheng-ASR-Lab`

## 目标

在 Windows 开发机快速迭代 Qwen3-ASR 与 WhisperX，复用生产固定模型、固定非敏感语料、
Provider/Canonical 和质量门禁代码，但不把本地结果当成生产 qualification，也不改变任何
应用 Profile admission。

快速通道分为三层：

| 模式 | 样本/重复 | 用途 | 大致耗时（RTX 5070 Ti，模型已缓存） |
|---|---|---|---|
| `smoke` | 1 个 clear-zh、单遍 | 验证 CUDA、模型加载和真实结果流 | 本机约 40～62 秒 |
| `focus` | Qwen 4 个、WhisperX 3 个；单遍 | 集中检查编号、BIM 噪声、混合语言和负样本 | 本机约 40～43 秒/候选组 |
| `full` | 固定 8 样本、两遍 | 本地复现完整质量门禁；WhisperX 运行三候选矩阵 | 本机 Qwen 双候选约 105 秒，WhisperX 三候选约 54 秒 |

以上为 RTX 5070 Ti、模型与依赖已缓存时的实测，首次模型加载、显存争用和音频长度会造成
明显波动。首次 bootstrap 还需
下载依赖和固定 revision 模型，通常每个引擎 20～60 分钟；网络较慢时可能更久。

## 隔离边界

脚本只在当前 PowerShell 进程和其子进程设置缓存变量，退出后恢复原值。它不修改系统或
用户 PATH、注册表、PowerShell Profile、Windows 服务、Scheduled Task、防火墙、Docker、
全局 Python 或用户 site-packages。

实验室仅管理以下目录；根目录必须带有绑定当前 source root 的 marker，未带 marker 的
非空目录拒绝接管；实验室根的现存祖先、受管目录以及模型 staging/发布路径包含
symlink/reparse point 时均失败关闭。

```text
E:\RAGPinCheng-ASR-Lab\
  .ragpincheng-asr-lab.json
  envs\{lab-tools,qwen3-asr,whisperx}\
  wheel-cache\{qwen3-asr,whisperx}\
  models\{qwen3-asr,whisperx}\
  corpus\
  caches\{pip,huggingface,torch,torch-extensions,cuda,nltk,temp,pycache}\
  runs\
  tools\
```

- venv 互相独立；所有 `pip install` 均使用 venv Python、`--isolated` 和实验室内显式 cache。
- bootstrap 是唯一允许联网的模式；评测设置 Hugging Face/Transformers/Datasets offline。
- Qwen 临时 HTTP 服务仅绑定 `127.0.0.1:18310`，使用每次随机 token；清理前重新核验监听端口、
  launcher 祖先、PID 和进程创建时间，不按缓存 PID 直接终止进程。
- 本地进程在保留既有 `NO_PROXY` 的同时追加 loopback；纯 HTTP 的 `127.0.0.1` 客户端跳过
  无意义的 TLS context 初始化。该开关拒绝用于非 loopback 或 HTTPS 地址，生产默认仍验证 TLS。
- WhisperX 在隔离进程内执行，预留端口为 `127.0.0.1:18320`，当前不启动 HTTP 服务。
- 生产端口 `8100` / `8200` 只读观察，不绑定、不停止、不重启。
- 不自动终止任何未知 GPU 进程；显存不足时由 doctor/运行时失败并保留报告与日志。
- 报告固定 `scope=local-development`、`qualification_eligible=false`。执行完成与质量结论分离：
  `status=complete`，`gate_status=pass|fail`；质量失败返回退出码 `2`，运行异常使用其他非零退出码。
- 模型 staging 使用固定模型 ID/revision 路径和非阻塞锁。失败保留 `.partial` 供下一次相同 bootstrap
  续传；成功后 candidate 原子发布，不保留完整下载副本。

磁盘最低门禁为 40 GiB，两个引擎完整 bootstrap 建议预留 60 GiB。删除实验室根目录即可回收
所有新增依赖、模型、缓存和报告；执行删除前必须核对 marker 内容和目标绝对路径，仓库代码
及系统 Python 不需要恢复。

## 使用顺序

从仓库根目录、非管理员 PowerShell 运行：

```powershell
.\scripts\run-asr-local-lab.ps1 -Mode init
.\scripts\run-asr-local-lab.ps1 -Mode doctor

# 可分引擎 bootstrap；失败后相同命令会复用已发布模型、完整文件和 .partial。
.\scripts\run-asr-local-lab.ps1 -Mode bootstrap -Engine qwen3-asr
.\scripts\run-asr-local-lab.ps1 -Mode bootstrap -Engine whisperx

.\scripts\run-asr-local-lab.ps1 -Mode unit
.\scripts\run-asr-local-lab.ps1 -Mode smoke -Engine qwen3-asr -QwenCandidate forced-chinese-baseline
.\scripts\run-asr-local-lab.ps1 -Mode focus -Engine qwen3-asr -QwenCandidate all
.\scripts\run-asr-local-lab.ps1 -Mode focus -Engine whisperx
```

只有 focus 证明运行时间和显存可接受后再执行：

```powershell
.\scripts\run-asr-local-lab.ps1 -Mode full -Engine qwen3-asr -QwenCandidate all
.\scripts\run-asr-local-lab.ps1 -Mode full -Engine whisperx
```

每次运行的报告、`run-summary.json` 和 service stdout/stderr 位于带毫秒时间、PID 与随机后缀的
唯一 `runs\local-<run-key>` 目录。
Qwen `all` 会完成全部请求候选后汇总；WhisperX smoke 以 baseline 为目标，focus 以
full-decode 为目标、baseline 只作对照，full 沿用三候选选择矩阵。任一请求目标未通过时
PowerShell 最终返回 `2`。`doctor` 检查 Python 环境、双模型 manifest、单卡 GPU、剩余显存、
磁盘和端口占用；它不会清理其他进程。

## 生产边界

本实验室可以跑通两个引擎的真实 adapter、CUDA 推理、Provider → Canonical、固定语料和
质量矩阵，适合在提交 GitHub Actions 前发现依赖、模型、显存、质量与性能问题。它不执行
production deploy，不注册生产服务，不写生产模型根目录，不读取密钥/真实媒体，也不开放
Qwen3-ASR 或 WhisperX application Profile。

正式 qualification、生产部署和 Profile admission 仍使用既有 workflow、固定证据契约和
独立审批。本地 full 即使门禁显示 pass，也不构成 production qualification。
Qwen runtime contract 覆盖模型准备与共享下载模块；WhisperX contract 覆盖 qualification、
CUDA smoke、runtime preflight 与共享下载模块。上述文件变化会使旧 qualification contract
失效并要求重新执行正式 qualification。

## 2026-08-11 本机实测

- RTX 5070 Ti 16 GiB，驱动 `610.88`；Qwen 使用 `torch 2.7.0+cu128`，WhisperX 使用
  `torch 2.8.0+cu128`。三个 venv、四个固定模型 manifest 和两套许可证审计均通过；实验室
  占用约 32 GiB。
- Qwen forced-chinese smoke 通过，clear-zh CER `0`、RTF `0.270402`。full 中
  forced-chinese 与 auto-zh-en 结果一致：均 6/8 样本通过，双遍确定性零失败，最大 RTF
  分别为 `0.326087` / `0.335072`；但术语召回 `0.571429`、标准编码召回 `0`、时间戳
  P95 `2240 ms`，两个候选都未通过本地质量门禁。失败集中在 standard-codes 与 mixed-zh-en。
- WhisperX smoke 通过。本次 focus 中 baseline 受噪声样本波动影响未通过，full-decode 因负样本
  未通过，命令正确返回 `2`；full 三候选矩阵随后选择 `full-decode`：8/8 样本通过，双遍确定性零失败，
  术语与标准编码召回均为 `1.0`，噪声 CER `0`，时间戳 P95 `175 ms`，最大 RTF
  `0.119068`，峰值 GPU 分配 `1323.55 MiB`。baseline 为 7/8，hotwords 虽为 8/8，
  但标准编码召回仍为 `0`，因此只有 full-decode 满足候选选择门禁。
- WhisperX 的 pyannote 在导入时提示 TorchCodec DLL 不可用；当前引擎传入内存 waveform，
  smoke/focus/full 均成功，因此该警告不阻塞现有路径。若未来改为 pyannote 内建文件解码，
  必须重新验证 TorchCodec/FFmpeg 兼容性。
- 运行结束后 `18310`、`18320`、`8100`、`8200` 均无本实验室 listener。最新真实报告位于：
  Qwen smoke `local-20260811104714644-88516-1eb957d1`、focus
  `local-20260811104825615-81576-16ccdfa7`、full
  `local-20260811105026040-91844-fa9a5391`；WhisperX smoke
  `local-20260811105308891-82208-a976fbc0`、focus
  `local-20260811105419548-66024-473c3431`、full
  `local-20260811105541273-84316-2cb6b9be`。它们均位于
  `E:\RAGPinCheng-ASR-Lab\runs`，并固定为 `scope=local-development`、
  `qualification_eligible=false`。
- 修复前一次成功 Qwen bootstrap 遗留约 `3.46 GiB` 的旧 run-specific staging；实现不会自动
  删除该历史目录。新 bootstrap 不再生成同类完整副本，旧目录只能在单独核对 marker 和精确
  路径后人工清理。

## 验证与回滚

仓库验证至少包括 PowerShell AST、Python compile、隔离边界专项测试及既有 Qwen/WhisperX
qualification 回归。真实运行依次执行 doctor、单引擎 bootstrap、smoke、focus；只有资源
允许时执行 full。

2026-08-11 收口验证：local launcher `78 passed`，local lab/Qwen qualification/部署专项
`115 passed`，Provider/Profile/部署边界 `194 passed`，完整 `asr_service/tests` `265 passed`，
WhisperX production static `5 passed`；
PowerShell AST、Python compileall 和 `git diff --check` 均通过。

回滚分两部分：仓库改动通过本分支 revert；本地产物通过核对 marker 后删除精确实验室根
目录。两者都不需要停止或还原系统服务，因为本方案不会安装系统服务。
