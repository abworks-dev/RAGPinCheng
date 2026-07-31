# FunASR Phase 0 执行计划（生产 Windows GPU 主机开发调试窗口）

- 状态：**R3 第一批已批准，待补充维护窗口后执行**
- 父方案：`project-docs/plans/funasr-auto-transcription.md`
- 前置预注册计划：`project-docs/plans/funasr-phase0-pre-registration.md`
- 风险等级：**R2 → 已升格为 R3**（实际 GPU 实测、生产主机依赖安装、模型下载都属于 R3）；第三轮审查及 R2 收口已完成本地 CPU 验证，但不代表获准实测。
- 范围：**仅 R3-0～R3-5（主机预检、隔离环境、单一模型与许可证、BGE 基线、兼容性冒烟、8 个短样本）**；不进入 1h/2h/4h 或 BGE 共存测试；不进入 Phase 1；不改业务代码、API、数据库 Schema、Prompt、部署、索引或生产开关；不使用真实客户资料。

## 0.1 本次批准记录（2026-07-31）

- 同步方式：GitHub `master` 推送后，由现场负责人在生产 Windows 主机执行 `git pull`；拉取前必须确认工作区干净并记录旧 SHA，出现非快进或本地修改立即停止。
- 现场负责人：用户本人。
- 模型范围：仅 `iic/SenseVoiceSmall@v1.0.0`；VAD 固定 `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch@v2.0.4`；标点固定 `iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch@v2.0.4`。
- 下载范围：允许批准的 PyTorch 索引、PyPI 镜像与 ModelScope；模型和缓存只能进入 `${QUALIFICATION_SANDBOX_ROOT}\models`。
- 样本：8 个短样本及人工 reference 已准备，且确认非敏感。
- 已批准维护窗口：`2026-07-31 18:57:06` 至 `2026-07-31 22:57:06`（Asia/Shanghai）。外部配置必须使用等价的带 `+08:00` ISO-8601 时间；到期后 R3-1～R3-5 自动重新阻塞。
- R3-1 首次执行在 pip 自升级处安全停止：Windows venv 必须通过 `python.exe -m pip` 更新 pip，且 PowerShell 5.1 会把原生 stderr 提升为 `NativeCommandError`。修复后的脚本统一使用 venv Python 模块调用、按原生退出码判定，并复用已创建但未完成的 venv。

## 0. 用户已明确的硬约束（执行前再次确认）

- 当前公司 Windows GPU 主机处于**开发调试窗口**，无其他用户使用线上服务；
- 仅使用公开或专门制作的非敏感样本；
- **ASR 必须在独立进程 / 虚拟环境 / 容器中**，不得修改现有 `gpu_service` 依赖；
- **不得停止、重启或卸载 BGE**；
- BGE 在线请求始终优先；ASR 仅在音频块边界让出 GPU；
- 出现 OOM、BGE 健康异常、明显延迟退化或磁盘不足时**立即停止**；
- 短样本通过后继续 1h；2h / 4h 须根据前序结果再决定；
- 不得执行 Phase 1 / 数据库修改 / 索引操作 / 生产功能开启 / 真实资料转录；
- 不静默 CPU 回退；不卸载 BGE 让出显存；不挤占生产 `gpu_service` 进程。

## 1. 环境隔离方式

### 1.1 路径与进程隔离

| 资源 | 生产 `gpu_service` | ASR 沙箱 | 隔离手段 |
|---|---|---|---|
| Python 解释器 | `${PRODUCTION_PYTHON_PATH}`（见 `scripts/deploy-gpu.ps1` §5） | 新建 `C:\FunASR-Phase0\venv\python.exe`（基于 `C:\Python311\python.exe -m venv`） | **不同解释器路径**，互不污染 |
| 进程 | `python -m gpu_service.app` 后台进程 | `python -m funasr_phase0.runner ...` 前台/可终止 | **独立进程**，不 `import gpu_service` |
| 工作目录 | `E:\Repository\Github\RAGPinCheng\gpu_service` | `${QUALIFICATION_SANDBOX_ROOT}\runner` | 物理目录分离 |
| 依赖 | `gpu_service/requirements.txt`（FlagEmbedding + transformers） | ASR 独立 venv 安装 `funasr / modelscope / torch-cu128 / torchaudio / soundfile / PyAV / numpy` | **venv 隔离**，`pip install` 不会动 gpu_service venv |
| 模型缓存 | `gpu_service/.cache/huggingface`（生产） | `${QUALIFICATION_SANDBOX_ROOT}\models`（`HF_HOME` / `MODELSCOPE_CACHE` 指向） | 不读生产缓存；不复用生产权重 |
| BGE 权重 | 现有 BGE 服务已经加载 `BAAI/bge-m3` | ASR 沙箱不读取、不复制、不下载 BGE 权重 | 权重与进程边界隔离 |
| 端口 | 由审批配置中的 `bge_base_url` 指定 | ASR Phase 0 不启动 HTTP 服务或第二个 BGE 端口 | 不在代码中回退到固定生产地址 |
| 资源 | GPU 0 | GPU 0（`CUDA_VISIBLE_DEVICES=0` 与 gpu_service 共用同一张卡） | **显存让出**（见 §4） |

### 1.2 不修改项（黑名单）

- 不修改 `gpu_service/app.py`、`models.py`、`config.py`、`requirements.txt`、`.env`、`schemas.py`、测试。
- 不修改 `src/`、`api/`、`frontend/`、`prompts/`、`docker/`、`requirements*.txt`、根 `CLAUDE.md` / `AGENTS.md` / `TODO.md`（除本计划相关注释）。
- 不修改 `data/*.sqlite*`、Qdrant volume、`media/`、`docs/`、`data/parsed/`。
- 不动 NSSM / 计划任务 / Windows 防火墙 / 启动项。
- 不重启 `gpu_service`；不发送其重启信号；不动其 `gunicorn` / `uvicorn` worker。

### 1.3 沙箱目录

```text
C:\FunASR-Phase0\venv\            # 独立虚拟环境
${QUALIFICATION_SANDBOX_ROOT}\models\          # HF_HOME / MODELSCOPE_CACHE / funasr cache
${QUALIFICATION_SANDBOX_ROOT}\testdata\        # 测试样本与抽取音频
${QUALIFICATION_SANDBOX_ROOT}\annotations\      # 人工标注 JSONL/CSV
${QUALIFICATION_SANDBOX_ROOT}\logs\            # raw-logs/
${QUALIFICATION_SANDBOX_ROOT}\reports\         # 阶段报告 + metrics.csv
${QUALIFICATION_SANDBOX_ROOT}\runner\          # 入口脚本与评测脚本
```

- 总磁盘上限 30 GB；25 GB 软警告；超阈值立即停。

## 2. 当前 GPU / BGE 基线（**待测**）

> 本节是「**执行第一步**」的测量项；先在 §1.3 沙箱目录之外，用只读命令记录基线。

### 2.1 GPU 基线

```powershell
# 1) 驱动 / CUDA / 卡名
nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu --format=csv
# 2) 现有 GPU 进程
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
# 3) 1 分钟平均利用率
nvidia-smi dmon -s pucm -c 60 -d 1 | Tee-Object ${QUALIFICATION_SANDBOX_ROOT}\logs\baseline-gpu-dmon.txt
```

期望记录：`RTX 5060 Ti` / Driver `591.74` / CUDA `13.1` / `16311 MiB` / 当前 BGE 占用 / 当前温度 / 当前 `utilization.gpu` 基线。

### 2.2 BGE 基线

```powershell
# 1) /health
curl -s -H "Authorization: Bearer $env:GPU_SERVICE_TOKEN" http://${PRIVATE_IPV4}:8100/health
# 2) /model-info
curl -s -H "Authorization: Bearer $env:GPU_SERVICE_TOKEN" http://${PRIVATE_IPV4}:8100/model-info
```

期望记录：`status=ok` / `model_loaded=true` / `embedding_model=BAAI/bge-m3` / `reranker_model=BAAI/bge-reranker-v2-m3` / `torch_version=2.7.0+cu128` / `device=cuda`。

### 2.3 BGE 端到端延迟基线（5 分钟合成流量，30 req/min）

- 用一个 5 分钟循环脚本（**非敏感固定文本**，来自 `data/parsed` 已索引的人工转录中已脱敏片段或自合成；不读 `docs/` 客户原文）注入：
  - embedding：每分钟 20 个 1000-char 中文；
  - rerank：每分钟 10 个 50-candidate 短文。
- 记录 `p50 / p95 / p99 / max / 错误率`，写到 `${QUALIFICATION_SANDBOX_ROOT}\logs\baseline-bge-latency.csv`。
- 5 分钟结束前**不**启动 ASR；任何时刻 BGE 错误率 > 0.5% 立刻停止本次基线测量并报告。

### 2.4 ASR 基线

- ASR 进程未运行；`nvidia-smi` 显示 BGE 占用 + 系统 UI 占用。
- 启动 ASR 前再次抓一次 `nvidia-smi` 快照作为 t0。

## 3. 测试顺序（本次只批准 R3-0～R3-5；前序失败不进入后续）

| R3 步骤 | 名称 | 本批状态 | 主要产出 / 门禁 |
|---|---|---|---|
| R3-0 | 主机只读预检与 Git 同步核对 | 已批准 | GPU/BGE/磁盘/进程快照；旧 SHA；干净工作区；不改服务 |
| R3-1 | 独立 venv 与依赖 | 执行中；首次 pip 自升级失败后待修复重试 | `pip freeze`、requirements SHA、CUDA 可用；不触碰 `gpu_service` 环境 |
| R3-2 | 单一模型下载与许可证门禁 | 已批准但等待维护窗口 | 仅 SenseVoiceSmall/VAD/punc 固定 revision；license blocker 为 0 |
| R3-3 | BGE-only 5 分钟基线 | 已批准但等待维护窗口 | embed 20 rpm + rerank 10 rpm；错误或健康异常立即停止 |
| R3-4 | 受控 CUDA 兼容性冒烟 | 已批准但等待维护窗口 | CUDA、模型身份、真实加载/推理链路和恢复验证通过 |
| R3-5 | 8 个短样本 | 已批准但等待维护窗口 | 人工标注校验、CER/术语/规范/时间戳/RTF/显存报告 |

以下原始递进表仅作为后续候选路线。1h、2h、4h 与 BGE 共存均未获本次批准，R3-5 后必须停止并重新审批。

| 步 | 名称 | 触发条件 | 主要产出 | 通过判据 |
|---|---|---|---|---|
| 0 | 许可证与依赖核查 | §2.1–§2.3 全部通过 | `reports/license-matrix.md` | LGPL 链路单独标注并提交用户；其余记录 |
| 1 | 兼容性冒烟 | 步 0 通过 | `logs/smoke/` | `torch.cuda.is_available()==True`、模型元信息打印、空转 30s 退出 |
| 2 | 短样本（s-clean / s-multi / s-noise / s-accent / s-music / s-silence / s-bim-terms / s-noise-bim） | 步 1 通过 | `logs/short/`、`reports/metrics.csv` | §4 阈值全部满足 |
| 3 | 1 小时（m-1h） | 步 2 通过 | `logs/1h/` | §4 阈值 |
| 4 | 2 小时（m-2h） | 步 3 通过 + 用户确认 | `logs/2h/` | §4 阈值 |
| 5 | 4 小时（m-4h） | 步 4 通过 + 用户确认 | `logs/4h/` | §4 阈值 |
| 6 | 现有 BGE + 独立 ASR 进程共存（30 req/min + ASR 1h） | 步 3 通过 + 用户确认 | `logs/bge-coexist/` | BGE 错误率及 p95 满足批准阈值；ASR 完成 |

- 每步结束立即把 `metrics.csv` 行 + `nvidia-smi` 末态快照写入 `reports/`；不并行启动下一步。
- 步 4、5、6 进入前必须先暂停并把本步报告发回给你，让你确认。

## 4. 资源限制

| 维度 | 阈值 |
|---|---|
| ASR 显存峰值 | < 8 GB（保留 ≥ 8 GB 给 BGE） |
| ASR 显存稳态 | < 6 GB |
| ASR CPU 亲和 | 4 核（`start /affinity 0xF`） |
| ASR RTF 平均 | ≤ 0.6 |
| ASR 任务失败率 | 0%（短样本） / ≤ 5%（1h） / ≤ 10%（2h / 4h） |
| BGE p95 延迟变化 | ≤ +50%（共存测试） |
| BGE 错误率 | 0% |
| `gpu_service` 健康 | 必须始终 `status=ok & model_loaded=true` |
| ASR 不动 BGE 权重 | 不下载、不预热、不卸载 |
| 磁盘 | 30 GB 硬上限 / 25 GB 软警告 |
| 音频块大小 | 60 s（待步 1 短样本结果微调） |

## 5. 自动停止条件（任一触发立即停）

| 触发 | 检测方式 | 动作 |
|---|---|---|
| ASR OOM | `nvidia-smi` 报 `OutOfMemory` / `torch.cuda.OutOfMemoryError` | §6 步骤 1–4 |
| ASR 异常退出（除正常结束） | 进程退出码 ≠ 0 / 未抛 `StopIteration` | §6 步骤 1–4 |
| BGE `/health` → `status=error` 或 `model_loaded=false` | 每 5 s 探测一次 | §6 步骤 1–5，立即报告 |
| BGE 错误率 > 0.5% | 共存测试滑动窗口 | §6 步骤 1–5 |
| BGE p95 延迟 > 基线 + 100% | 共存测试滑动窗口 | §6 步骤 1–5 |
| BGE `/v1/embeddings` 或 `/v1/rerank` 返回 5xx | 单点错误 | 记 WARN；连续 3 次 5xx → §6 步骤 1–5 |
| 磁盘可用 < 5 GB | `Get-PSDrive` | §6 步骤 1–5 |
| ASR 连续失败 3 次 | 评测脚本累计 | §6 步骤 1–5 |
| 用户在任一时刻发出停止指令 | 外部信号 | §6 步骤 1–4 |
| BGE 显存占用 + ASR 显存占用 > 14 GB（安全余量） | `nvidia-smi dmon` | §6 步骤 1–4 |

## 6. 恢复步骤（按顺序执行）

1. **杀进程**：`Stop-Process -Id $asrPid -Force`（`$asrPid` 由评测脚本记录）；不向 `gpu_service` 发送任何信号。
2. **释放显存**：`C:\FunASR-Phase0\venv\python.exe -c "import torch; torch.cuda.empty_cache()"`（可选；进程杀掉后自动释放）。
3. **验证 BGE**：
   - `curl /health` → 必须 `status=ok` & `model_loaded=true`；
   - `curl /model-info` → 记录版本与 device；
   - 5 个 embedding 冒烟请求（合成的非敏感固定文本）→ 必须 200；
   - 1 个 rerank 冒烟请求（合成的非敏感固定文本）→ 必须 200。
4. **抓末态**：`nvidia-smi --query-gpu=... --format=csv` 写 `logs/stop-YYYYMMDD-HHMMSS-snapshot.txt`。
5. **写停机报告**：`reports/stop-events/stop-YYYYMMDD-HHMMSS.md` 含触发条件、最后指标、显存末态、BGE 健康、是否需要你裁决。
6. **向你报告**：列出停止原因、是否影响 BGE、下一步建议（继续 / 降级 / 终止 Phase 0）。
7. **等待**：在你回复前不重启 ASR、不改任何配置、不重新下载模型。

## 7. 执行通道（已确认 GitHub 同步 + 现场手动执行）

Codex 当前会话没有已确认的生产主机远程命令通道。代码经 GitHub 同步，现场负责人在生产 Windows 主机逐条手动执行并回传报告。Codex 不远程登录、不代填 Token，也不触碰生产服务进程。

- **通道 A**：Windows OpenSSH（已开 `PasswordAuthentication no`，默认走密钥）。请告知：
  - 我能否以当前 Windows 用户 `${LOCAL_HOSTNAME}` 凭私钥登录？
  - 若需在 `${PRIVATE_IPV4}` 放置公钥，请把授权公钥路径告知。
- **通道 B**：PowerShell Remoting（WinRM）。请告知是否启用以及授权账号。
- **已选通道 C**：你在生产主机上手动执行仓库中的 `scripts\funasr_phase0\*.ps1` / `*.py`，把 stdout / stderr / 产物回贴给我；报告、Token、样本和模型不得提交回 Git。
- **通道 D**：你直接把生产主机的 PowerShell / Bash 终端贴给我（每个命令逐条执行），结果实时回传。

我**不会**尝试：
- 任何 `ssh-keyscan` / 弱口令爆破；
- 任何 RDP 暴力连接；
- 任何不通过你确认的远程命令通道。

## 8. 明确不做的（再次列示，与父方案一致）

- 不进入 Phase 1（音频提取适配器 / Canonical JSON / formatter）；
- 不写 `transcription_jobs` Schema 迁移；不修改 `app.sqlite`；不写 `asr_service`；
- 不修改 BGE Embedding / Rerank 任何实现、模型 revision、Token、端口、防火墙；
- 不使用客户视频、真实敏感培训资料；
- 不执行索引 Reset、数据库迁移、生产部署、密钥轮换；
- 不在 `gpu_service` 进程内加载 ASR；
- 不 CPU 回退、不卸载 BGE；
- 不把 ASR 加载进 `gpu_service` 进程；共存测试使用同一个现有 BGE 服务，但 ASR 始终是受监控的独立进程；
- 不擅自扩大样本时长或模型候选；不擅自测试 faster-whisper；
- 不修改父方案与预注册计划的状态字段（仅追加本计划文件 + WORKLOG + TODO 注释）；
- 不把自动转录事项标记为完成。

## 9. 报告与归档

- 每完成一步立即在 `WORKLOG.md` 追加 `### HH:mm — 步 N 名称`；
- 最终报告：`${QUALIFICATION_SANDBOX_ROOT}\reports\phase0-report.md` + `metrics.csv` + `license-matrix.md` + `raw-logs/`；
- 通过条件全部满足后给三选一建议：
  1. FunASR 通过 → 提交 Phase 1 方案；
  2. 触发退出条件 → 建议另批 faster-whisper；
  3. 单卡不可达 → 保持关闭。

## 10. 尚待用户补充

维护窗口已批准为 `2026-07-31 18:57:06` 至 `22:57:06`（Asia/Shanghai）。窗口之外不得执行 R3-1～R3-5；如修复和安装未能在窗口内完成，须重新批准新窗口。R3-5 报告复核前，不得进入 1h、2h、4h 或 BGE 共存测试。

以下表格保留为历史决策记录，其中超出 R3-0～R3-5 的事项不构成本次授权。

| # | 项目 | 候选 |
|---|---|---|
| 1 | 执行通道 | A / B / C / D（§7） |
| 2 | 是否同意 §1 隔离方式 | 同意 / 修改意见 |
| 3 | 是否同意 §3 测试顺序（短 → 1h → 2h → 4h） | 同意 / 修改意见 |
| 4 | 是否同意 §4 资源限制（ASR 峰值 < 8GB / 稳态 < 6GB / 磁盘 30GB） | 同意 / 修改意见 |
| 5 | 是否同意 §5 自动停止条件（含 BGE p95 +100% 立即停） | 同意 / 修改意见 |
| 6 | 是否同意 §6 恢复步骤 | 同意 / 修改意见 |
| 7 | 是否同意先做 §2 GPU/BGE 基线测量（含 5 分钟 30 req/min BGE 合成流量） | 同意 / 修改意见 |
| 8 | 4 小时样本是否保留 | 保留 / 改为 2h 封顶 |
| 9 | 步 4、5、6 之前的暂停确认 | 强制 / 可选 |
| 10 | 若基线显示 BGE 实际显存比预注册计划高（例如 > 6 GB），是否仍以「ASR 峰值 < 8 GB」为限 | 是 / 否 / 改阈值 |

## 11. 批准模板

如同意本计划并指定执行通道，请回复（示例）：

> 批准按该 Phase 0 执行计划执行。执行通道 = C（我手动执行你提交的脚本）。同意 §1–§6 全部条款。4 小时样本保留。步 4 / 5 / 6 前必须暂停确认。基线 BGE 显存若 > 6 GB 仍以 8 GB 峰值为限。

否则请指出需要修改的章节或新增的决策项。
