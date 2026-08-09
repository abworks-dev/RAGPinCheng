# FunASR Phase 0 预注册计划（非生产沙箱技术验证）

- 状态：预注册设计已完成；后续执行状态由 `funasr-phase0-execution-plan.md` 接管
- 父方案：`project-docs/plans/funasr-auto-transcription.md`
- 风险等级：评测设计与本地脚本验证为 **R0/R2**；公司开发兼生产 GPU 上的安装、下载、推理和压测均为待单独审批的 **R3**。
- 审批边界：本文件保留实测前预注册的方法和历史门槛，不再作为现场执行授权。R2 后代码事实、修正后的指标定义、模型身份、停止门禁和批准范围以 `scripts/funasr_phase0/` 及 `funasr-phase0-execution-plan.md` 为准。
- 2026-07-31 补充：用户已批准公司 Windows GPU 主机上的 R3 第一批，仅限 R3-0～R3-5 和 `iic/SenseVoiceSmall@v1.0.0`；维护窗口起止时间尚未补齐，因此依赖安装、下载和 GPU 实测仍阻塞。1h/2h/4h、BGE 共存及 faster-whisper 均未获批准。

## 0. 关键前提（已在父方案之外由用户口头/书面补充）

- 唯一一台 RTX 5060 Ti 位于公司生产 Windows GPU 主机（参见 `project-docs/migrations/ubuntu-app-windows-gpu-runbook.md` §1、`scripts/deploy-gpu.ps1` §6 中 `HOST=${GPU_SERVICE_IP}` / `PORT=8100`），当前承载实际 `gpu_service`。
- 本预注册计划**不**包含对生产 Windows 主机的任何访问、登录、安装、下载、启动、运行、压测、停止或修改。
- 本机（开发机）GPU 为 RTX 5070 Ti / 16 GB / Driver 610.74 / sm_120，与生产 RTX 5060 Ti 型号不同；按用户最新指示，**本机也不作为 Phase 0 沙箱**。
- 未来对生产 Windows 主机的任何测试须另行提交 **R3 生产测试方案**，含维护窗口、当前业务负载、影响范围、监控指标、自动停止条件、服务恢复步骤和负责人，逐项明确批准。

## 1. 阻塞项（实测前必须解锁，本轮保持阻塞）

| 项 | 阻塞原因 | 解锁条件 |
|---|---|---|
| 实际模型下载 | 无授权的非生产 GPU 落点 | 用户提供非生产 GPU 环境 |
| 实际 venv / 依赖安装 | 同上 | 同上 |
| GPU 兼容性冒烟 | 同上 | 同上 |
| CER / RTF / 显存 实测 | 同上 | 同上 |
| 短样本 / 1h / 2h / 4h 递进 | 同上 | 同上 |
| BGE + ASR 共存 | 同上 + 使用现有 BGE 服务与独立受监控 ASR 进程 | 同上；禁止复制、卸载或重启 BGE |
| faster-whisper 备选 | 仅在 FunASR 退出条件触发后单独审批 | 用户另行批准 |
| 单卡共享生产化 | 单卡不可达 → 保持功能关闭 | 独立 GPU 来源 |

## 2. 已批准项（本轮可交付的书面/脚本产物）

- 许可证核查方案：见父方案 §4.2 + 本预注册计划 §5；
- 非敏感样本准备：见 §6、§7；
- 评测方法设计：见 §8、§9、§11、§13；
- 文档与脚本框架（不下载、不安装）：见 §13。

## 3. 不可执行项（明确不做）

- 不下载任何模型权重、候选 Python 包、ffmpeg 二进制；
- 不创建 venv / 容器，不安装 `funasr` / `modelscope` / `torch` / `torchaudio` / `soundfile` / `av` / `ffmpeg-python`；
- 不在本机或生产 Windows 主机上启动任何 Python 推理进程；
- 不读取 `.env` 真实值；不连接生产 Qdrant、`app.sqlite`、`parents.sqlite`、`media/`、`docs/`；
- 不使用客户视频、未授权内部资料或受版权保护的样本；
- 不预热 BGE 模型、不下载 `BAAI/bge-m3` / `bge-reranker-v2-m3` 权重；
- 不进行任何形式的「先用 CPU 顶一下」、「把 ASR 加载进 gpu_service 进程」、「通过卸载 BGE 让出显存」等回退；
- 不在未拿到用户明确批准前开始 GPU 实测；
- 不把自动转录事项标记为完成；Phase 1～6 任何代码、Schema、API、数据库、配置、前端、部署修改仍各自独立审批。

## 4. 测试机器与隔离方式（**待批准时再激活**）

- **目标形态**：独立 Python 进程（不进入 `gpu_service` 进程），启用 1 张非生产同型号 GPU；Docker 仅作为可选次选。
- **首选候选**（待用户确认是否提供）：
  - 候选 A：用户临时接入的第二张同型号 RTX 5060 Ti；
  - 候选 B：可断网、可停服、可白名单防火墙的非生产验证机；
  - 候选 C：基于 CUDA `CUDA_VISIBLE_DEVICES` 切片的「同一张卡但只允许 ASR 使用」实验环境（不能挤占生产 `gpu_service` 的显存/算力）。
- **隔离边界**：
  - venv：`${QUALIFICATION_SANDBOX_ROOT}\.venv`（与本仓库 `.venv` 完全隔离，永不合并）；
  - 模型缓存：`${QUALIFICATION_SANDBOX_ROOT}\models`（HF / ModelScope / funasr cache）；
  - 测试音频与提取中间产物：`${QUALIFICATION_SANDBOX_ROOT}\testdata`；
  - 标注：`${QUALIFICATION_SANDBOX_ROOT}\annotations`（UTF-8、JSONL+CSV 双格式）；
  - 日志：`${QUALIFICATION_SANDBOX_ROOT}\logs`（按 `short/`、`1h/`、`2h/`、`4h/`、`bge-coexist/` 子目录分桶）；
  - 报告：`${QUALIFICATION_SANDBOX_ROOT}\reports\phase0-report.md`（结构化 Markdown + 原始数据 `metrics.csv` + 评估脚本 `eval.py`）；
  - **绝不**写入生产 `media/`、`docs/`、`data/`、`qdrant_storage/`、Ubuntu 节点目录。
- **磁盘上限**：30 GB 硬上限；`du` 超过 25 GB 时停止新下载并报告；评测完成或失败时按 §12 流程清理。

## 5. 候选 FunASR 模型 / 版本 / 来源 / 缓存（未下载）

| 候选 ID | 名称 | Hugging Face / ModelScope 源 | 大致磁盘占用 | 默认用途 |
|---|---|---|---|---|
| `paraformer-large-zh` | `damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | https://www.modelscope.cn/models/damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch | ~1.0 GB | 中文主测试 |
| `paraformer-large-zh-hotword` | 同上 + `hotword.txt` | 同上 | 同上 + <1 MB | 热词收益测试 |
| `paraformer-large-ctxual` | `damo/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | ModelScope 仓库 | ~1.0 GB | 上下文 ASR（候选 B） |
| `sensevoice-small` | `iic/SenseVoiceSmall` | https://www.modelscope.cn/models/iic/SenseVoiceSmall | ~0.2 GB | 多语种/低显存基线 |
| `ct-punc` | `damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch` | ModelScope | ~0.6 GB | 中文标点（若未内置） |
| `fwer-campplus` | `damo/speech_campplus_sv_zh-cn_16k-common` | ModelScope | ~0.05 GB | 候选说话人（**不测试**，仅列出以拒绝使用） |

引擎：`funasr`（PyPI + ModelScope）；热词开关仅作为配置项测试一次。

## 6. 依赖与许可证矩阵（**仅研究公开 LICENSE 文件，不下载**）

| 组件 | 角色 | 来源 | 已知 / 待查许可证 | 备注 |
|---|---|---|---|---|
| `funasr` (PyPI) | 主引擎 | https://github.com/modelscope/FunASR | MIT（仓库 LICENSE） | 与 ModelScope 同步 |
| `modelscope` | 模型下载/中心 | https://github.com/modelscope/modelscope | Apache-2.0 | 候选；若改走 HF 镜像则可省略 |
| `torch >= 2.7` | 推理 | https://download.pytorch.org/whl/cu128 | BSD-3-Clause | 必须用 cu128 / sm_120 轮子；与生产镜像同源 |
| `torchaudio` | 音频 I/O | https://pytorch.org/audio | BSD-2-Clause | 版本需与 torch 严格匹配 |
| `transformers >= 4.46, < 5` | 模型加载 | https://github.com/huggingface/transformers | Apache-2.0 | 复用生产栈 |
| `tokenizers` | 文本分词 | https://github.com/huggingface/tokenizers | Apache-2.0 | 传递依赖 |
| `onnxruntime` | ONNX 推理 | https://github.com/microsoft/onnxruntime | MIT | Paraformer-ONNX 备选路径 |
| `PyAV (av)` | 音频解封装 | https://github.com/PyAV-Org/PyAV | BSD-3-Clause | FFmpeg 链接方式需复核 |
| `ffmpeg` 二进制 | 音视频解码 | https://ffmpeg.org / BtbN 静态构建 | LGPL / GPL（按编译开关） | **LGPL 触发人工合规审查**，不自动通过也不直接淘汰 |
| `soundfile` / `libsndfile` | WAV I/O | https://github.com/bastibe/python-soundfile | BSD-3-Clause | 副路径 |
| `numpy` / `scipy` | 数值 | PyPI | BSD-3-Clause | 通用 |
| `modelscope` 模型权重 | 中文 ASR 权重 | 见 §5 | 多为 Apache-2.0；个别子模型 Apache-2.0 + 署名 | 需逐 model card 复核 |
| `huggingface_hub` | HF 拉取 | PyPI | Apache-2.0 | 备选下载通道 |

> 动作：在批准后**逐项**去对应官方仓库与 PyPI 拉取 LICENSE 文本（仅文件名 + URL + SPDX 标识），记入 `reports/license-matrix.md`；LGPL 链路单独标注并提交用户做合规决定。本轮不预取任何副本。

## 7. 测试样本清单 / 来源 / 授权 / 时长 / 声音场景

| 样本 ID | 内容 | 来源（候选） | 授权 | 时长 | 声音场景 | 用途 |
|---|---|---|---|---|---|---|
| `s-clean` | 单人普通话讲解（无专业术语） | 公开 AISHELL-1 dev 子集（CC BY 4.0）或自合成 | 开源 / 自制 | ≤ 30 s | 清晰单人 | 兼容性 / 短样本质量 |
| `s-multi` | 多人会议（中文 2–3 人） | AISHELL-4 试听片段（Apache-2.0）或自合成 | 开源 / 自制 | 1–2 min | 多人 | segment 切分连续性 |
| `s-noise` | 噪声环境讲解 | AISHELL-1 + 人为混噪（白噪 + 设备噪） | 自制 | ≤ 30 s | 噪声 | 噪声鲁棒 |
| `s-accent` | 中速/较快语速 | AISHELL-1 抽样 | 开源 | ≤ 30 s | 较快语速 | RTF、漏字 |
| `s-music` | 带背景音乐讲解 | AISHELL-1 + 自加 CC0 背景音乐 | 自制 | ≤ 30 s | 音乐 | 噪声+音乐叠加 |
| `s-silence` | 长静默 + 短讲 | 自合成 | 自制 | 1 min | 静默 | 静默段不生成空 segment |
| `s-bim-terms` | 钢结构 / 焊缝 / 螺栓 / 规范编号密集 | **人工朗读 + 人工标注**（来自同事自愿录制并签署内部使用同意），或自合成 TTS | 内部 / 自制 | 30 s – 1 min | BIM 术语 | 术语召回、规范编号识别 |
| `s-noise-bim` | 噪声 + BIM 术语 | 上述噪声 + BIM 混叠 | 自制 | 30 s | 噪声+BIM | 噪声下术语表现 |
| `m-1h` | 1 小时中文讲解 | 公开教学/演讲录音（CC BY / CC BY-SA / CC0 候选），或自合成拼接 | 开源 / 自制 | 60 min | 综合 | 显存稳态、RTF、checkpoint |
| `m-2h` | 2 小时中文 | 同上长版 | 同上 | 120 min | 综合 | 长任务 OOM、显存漂移 |
| `m-4h` | 4 小时中文 | 同上更长版 | 同上 | 240 min | 综合 | 极限稳定性、failure rate |

> 4 小时样本若用户认为非必要，**可缩短为 2 小时封顶**——按用户最新决定（待回复）。

## 8. 人工标注样本制作方式

- 标注工具：仓库新文件 `scripts/funasr_phase0/annotate.py`（仅基于 `wavefile` / `soundfile` 读时长 + 文本比对；**不**调任何 ASR）；
- 标注格式：UTF-8 JSON Lines，每行 `{ "id": "s-bim-terms", "audio": "s-bim-terms.wav", "transcript": "…", "segments": [{"start_ms": 0, "end_ms": 1240, "text": "…"}, …] }`；
- BIM 术语字典：固定列表，10–30 个，覆盖 `钢结构`、`螺栓连接`、`焊缝`、`高强螺栓`、`抗滑移系数`、`GB 50017`、`GB 50205`、`JGJ 99`、`Q355`、`Q460`、`摩擦型`、`承压型`、`端距`、`边距`、`摩擦面`、`扭矩系数`、`初拧`、`终拧`、`火焰切割`、`超声波探伤`；该字典只用于人工标注对比，**不进 funasr 热词**，直到热词收益测试单列；
- 标注人员：自标注 + 1 名同事复核；标注脚本对 `text` 与 `segments[].text` 一致性做硬校验。

## 9. 预注册硬门槛与停止条件（**实测前确定**）

### 9.1 硬门槛（任何 1 项不达标即视为该阶段失败）

| 维度 | 短样本通过阈值 | 1h 通过阈值 | 2h/4h 通过阈值 |
|---|---|---|---|
| 启动 | `torch.cuda.is_available()==True` 且 `get_device_name` 显示目标 GPU；无 `no kernel image`；无静默 CPU 回退 | 同左 | 同左 |
| CER（清晰单人） | ≤ 10% | ≤ 12% | ≤ 15% |
| BIM 术语召回（无热词） | ≥ 70% | — | — |
| BIM 术语召回（开热词） | ≥ 90% 或相对 +10pp | — | — |
| 规范编号（GB/JGJ/Q） | ≥ 95% 字符级 | — | — |
| 时间戳偏差 \|Δ\| | 中位数 ≤ 500 ms，P95 ≤ 1.5 s | P95 ≤ 2 s | P95 ≤ 3 s |
| segment 重复率 | ≤ 1% | ≤ 1% | ≤ 2% |
| segment 遗漏率 | ≤ 2% | ≤ 2% | ≤ 3% |
| RTF（实时系数） | ≤ 0.5（清晰单人） | ≤ 0.6 平均 | ≤ 0.7 平均；峰值 ≤ 1.0 |
| 峰值显存（单独） | ≤ 10 GB | ≤ 11 GB | ≤ 12 GB |
| 稳态显存（单独） | ≤ 6 GB | ≤ 8 GB | ≤ 10 GB |
| 任务失败率 | 0%（短样本） | ≤ 5% | ≤ 10% |
| BGE p95 延迟变化（共存） | — | +50% 以内 | +100% 以内 |
| BGE 错误率变化 | — | 0 | 0 |
| `gpu_service` 健康变化 | 不可崩、不可被重启、不可写 | 同左 | 同左 |

### 9.2 触发「测试 faster-whisper 备选」的退出条件

以下任一发生即视为 FunASR 失败：

1. 许可证 / 模型权重无法通过人工合规审查；
2. 引擎无法在目标 GPU 环境稳定推理（含 `no kernel image`、静默回退 CPU、首次推理崩溃）；
3. 短样本 CER > 15% 或 BIM 术语召回 < 60% 且开热词无显著改善；
4. 单卡稳态显存 > 12 GB 或 OOM；
5. BGE 在线 p95 延迟在 ASR 运行下增加 > 100%，或 ASR OOM/异常导致 `gpu_service` 健康变化。

### 9.3 触发「保持关闭」的条件

- §9.2 任意一项成立，**且** 拟以单卡共享承载 ASR + BGE；
- 退路：不增加 GPU、不卸载 BGE、不静默 CPU 回退；功能保持关闭。

## 10. 指标计算方法（书面定义，未实测）

- **CER** = `SEDIM(ser)+D+I / N_ref`，用 `python-Levenshtein` 或自写 dynamic programming；中文先做 Unicode 归一化（NFKC）+ 去标点 + 去空白；统一小写。
- **BIM 术语召回** = 在 `ground_truth` 中以词典字符串为单位（前后加 `^|$` 匹配，且与中文字符边界一致），`recall = hits / terms_in_gt`。
- **规范编号** = 正则 `(GB|JGJ|CJJ|JGJ/T) ?\d{3,5}(-\d{4})?`；字符级准确率 = `正确字符 / 参考字符`。
- **时间戳偏差** = 对 `ground_truth` 与模型 segment 做最优对齐（用 `start_ms` 单调最近邻），输出中位数、P95、P99 与缺失率。
- **segment 重复率** = 文本归一化后，与前一个 segment 重复的 segment 数 / segment 总数。
- **segment 遗漏率** = ground truth 中应出现的 segment 在模型输出中未对齐到任何 segment 的比例。
- **RTF** = 音频时长 / 端到端 wall-clock 推理耗时（不含音频抽取、模型加载、IO）。
- **显存**：`torch.cuda.max_memory_allocated()` / `nvidia-smi dmon` 同步；`peak` / `steady` / `after_free` 三个值。
- **失败率** = (返回非 0 / 异常 / 0 输出的任务数) / 任务总数。
- **BGE 延迟变化** = `p95(共存下) / p95(基线)`，基线为该机器在 ASR 启动前 5 分钟的同口径 p95；不可跨机器对比。
- **不静默回退 CPU 的证据**：
  - 每个推理脚本首行打印 `torch.cuda.get_device_name(0)` + `torch.cuda.is_available()`；
  - 关键函数内周期性 `assert tensor.device.type == 'cuda'`，违反即抛错；
  - 同时 `nvidia-smi pmon -c 1` 截图作为旁证。

## 11. 递进顺序（**实测前定义**）

1. **许可证与依赖核查**（仅离线）：逐项记录 LICENSE 与 SPDX；LGPL 链路标黄。
2. **兼容性冒烟**（非音频）：仅 `funasr` 导入、模型元信息打印、CUDA 真假验证；空转 30 秒后退出。
3. **短样本**：`s-*` 全部跑过；C99 / 术语 / 时间戳 / 重复 / 遗漏 / 显存 / RTF；任何 1 项不达 §9.1 → 记录结论、停止扩大测试。
4. **1 小时**：`m-1h` 端到端；分块大小待 §12 决定。
5. **2 小时**（仅在 1h 通过后）。
6. **4 小时**（仅在 2h 通过后且用户同意保留）。
7. **BGE 共存**：在非生产同型号 GPU 上加载 BGE-M3 + BGE reranker，注入 30 req/min 合成 BGE 流量；启动 ASR 1h 任务；记录 BGE 延迟、错误率、`gpu_service` 健康。

每步必须在前一步通过后再开始；不并行测试 faster-whisper。

## 12. BGE 与 ASR 共存测试设计（**实测前定义**）

- **不**在生产 `gpu_service` 上做共存测试；生产 `gpu_service` 仅作为「存在性」参考，**不在 Phase 0 测试链路**。
- 共存测试使用当前现有 BGE 服务作为唯一对照目标；ASR 使用单独 venv、独立受监控进程和独立模型缓存。不得启动第二个 BGE、复制 BGE 权重、创建测试端口或测试 Token。
- 共存测试先跑 5 分钟 BGE-only 基线（请求体为合成的非敏感固定文本）；再启动 ASR 1h 任务；BGE 流量保持相同强度；记录基线 vs 共存下 p50/p95/p99 延迟、错误率、`/health` 返回值、`model_loaded` 字段、显存。
- ASR 失败或 OOM 时，现有 BGE 必须保持 `model_loaded=True`，model-info 不变，并通过 5 次 embedding 与 1 次 rerank 恢复验证；这是硬门槛。

## 13. 日志 / 模型缓存 / 临时音频 / 报告 / 清理

- 模型缓存：`${QUALIFICATION_SANDBOX_ROOT}\models\…`（与生产 `hf_cache` 卷隔离，不写入）。
- 临时音频与抽取 WAV：`${QUALIFICATION_SANDBOX_ROOT}\testdata\extracted\`；任务完成后立即删除，但 `audio_sha256` 保留在 `metrics.csv`。
- 报告：`${QUALIFICATION_SANDBOX_ROOT}\reports\phase0-report.md` + `metrics.csv` + `license-matrix.md` + `raw-logs/`。
- **清理方式**：
  - 测试全部完成后：`rm -rf ${QUALIFICATION_SANDBOX_ROOT}\models ${QUALIFICATION_SANDBOX_ROOT}\.venv ${QUALIFICATION_SANDBOX_ROOT}\testdata\extracted`；
  - 报告与标注保留 90 天（默认 `${QUALIFICATION_SANDBOX_ROOT}\reports\archive\`），到期后由本人手动清理；
  - 若需在 30 天内删除报告与缓存，须走 R3 删除审批。
- **磁盘上限**：30 GB 硬上限，25 GB 软警告；超阈值时停止下载并报告。
- **失败恢复**：失败时只清缓存和临时音频；保留 `raw-logs/` 与 `metrics.csv` 以供复盘。
- **回滚**：Phase 0 沙箱完全自包含；不影响任何业务、数据库、索引、生产服务；删除 `${QUALIFICATION_SANDBOX_ROOT}` 目录即视为 Phase 0 全部回滚完成。

## 14. 评测方法设计交付物（本轮可立即完成）

| 交付物 | 路径 | 形式 | 何时可写 |
|---|---|---|---|
| 预注册计划（本文件） | `project-docs/plans/funasr-phase0-pre-registration.md` | Markdown | 本轮已写 |
| 评测指标定义 | `project-docs/plans/funasr-phase0-metrics.md` | Markdown | 批准后 |
| 硬门槛与停止条件 | 纳入上同文件 | 同上 | 批准后 |
| 标注脚本 | `scripts/funasr_phase0/annotate.py` | Python | 批准后 |
| 评测脚本 | `scripts/funasr_phase0/eval.py` | Python | 批准后 |
| 许可证矩阵脚本（只读 LICENSE 文件，不下载） | `scripts/funasr_phase0/license_audit.py` | Python | 批准后 |
| 报告模板 | `${QUALIFICATION_SANDBOX_ROOT}\reports\phase0-report.md`（由评测脚本生成） | Markdown | 实际测试时 |

> 上述 `scripts/funasr_phase0/*` 文件**仅在用户批准 Phase 0 后才落地**；本轮不创建。

## 15. 风险与回滚（本轮范围）

- **回滚**：`${QUALIFICATION_SANDBOX_ROOT}` 自包含；本轮范围内不创建任何文件，因此**回滚成本 = 0**。
- **风险 1：用户误以为 Phase 0 已经在跑 GPU 实测**——本轮明示「仅书面 / 阻塞 / 不下载」，避免歧义。
- **风险 2：若后续非生产 GPU 仍未到位而用户坚持启动 Phase 0**——按用户最新指示，必须保持阻塞，不允许 CPU 回退、不允许挤占生产 `gpu_service`、不允许卸载 BGE。
- **风险 3：未来生产测试**——R3 方案须单列维护窗口、当前业务负载、影响范围、监控、自动停止、服务恢复、负责人；未批准前不动。

## 16. 明确需要用户决定的事项

| # | 决策项 | 我的建议 | 状态 |
|---|---|---|---|
| 1 | 是否接受本预注册计划（仅交付许可证核查方案 / 样本准备 / 评测方法设计） | 推荐接受 | **待回复** |
| 2 | 是否提供非生产 RTX 5060 Ti 或等效非生产 GPU 环境以解锁 GPU 实测项 | 推荐提供；否则保持阻塞 | **待回复** |
| 3 | 4 小时样本是否必要（建议最长 2 小时） | 建议 4h 可选 | **待回复** |
| 4 | 样本来源是否同意使用 AISHELL-1/4（CC BY / Apache-2.0）以及自合成 + 1 名同事录制 | 建议同意 | **待回复** |
| 5 | 热词词典与 `iic/SenseVoiceSmall` 是否纳入测试矩阵 | 建议都纳入 | **待回复** |
| 6 | 若 FFmpeg 链接方式落在 LGPL，是否走人工合规审查通道 | 建议是 | **待回复** |
| 7 | 未来若需在生产 Windows 主机测试，是否同意先走 R3 单独审批 | 必须 | **待回复** |
| 8 | 30 GB 磁盘上限是否可接受 | 建议接受 | **待回复** |
| 9 | Phase 0 测试结果「三选一建议」的最终裁定人 | 你本人 | **待回复** |
| 10 | 报告与标注保留期 90 天是否合适 | 建议 90 天 | **待回复** |

## 17. 待办

- [ ] 用户回复 §16 决策项，并明确「批准按该 Phase 0 详细计划执行（仅许可证核查方案 / 样本准备 / 评测方法设计；GPU 实测项保持阻塞）」。
- [ ] 批准后将 §14 中「批准后」可写的交付物落入项目（仅文档 / 脚本框架，不下载、不安装）。
- [ ] 非生产 GPU 来源到位后，再独立审批 GPU 实测实施（重新提交包含维护窗口、影响范围、监控、自动停止、负责人、备份与回滚的 R2/R3 方案）。
- [ ] Phase 0 完成后，提交「进入 Phase 1 / 测试 faster-whisper / 保持关闭」三选一建议。
