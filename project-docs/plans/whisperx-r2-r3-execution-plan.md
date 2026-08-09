# WhisperX R2+R3 一次性合并执行方案

## 状态与目标

本方案已获用户批准。目标是在不改变现有业务编排的前提下，为 WhisperX 增加独立
experimental Provider/Profile，并通过 Windows `production-asr` runner 完成隔离安装、
固定模型缓存校验和 CUDA FP16 冒烟。

业务链路继续复用 `TranscriptionProvider`、`ProviderCandidate`、normalizer、Profile
snapshot 和 Canonical 契约；WhisperX Profile 默认 `disabled`，不得自动发布、自动索引
或接入业务流量。

## 固定基线

- 仓库基线：本地 `master@f75ee7ce944bf37c54cc323e9591ca44ca0480ae`；
- Python：3.11 独立 venv；
- Windows runner：`self-hosted, Windows, X64, asr-production`；
- Torch：`2.8.0+cu128`，CUDA runtime 12.8；
- 推理：CUDA FP16，`batch_size=1`，不启用 diarization；
- ASR：`Systran/faster-whisper-large-v3`
  `@53ecf83a5bedc5597eb8c8b34eac29e5345520ff`（MIT）；
- 中文对齐：`jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn`
  `@51d27579a1040ee4e967979278d5f76b9c32c375`（Apache-2.0）；
- WhisperX：`3.8.6`（BSD-4-Clause）。

## 实施范围

1. 增加严格、可序列化的 WhisperX remote config 和 experimental Profile。
2. 复用现有 HTTP remote Provider 工厂，不新增业务流程或持久化契约。
3. ASR service 增加 lazy-load WhisperX adapter、固定 service profile 和有限错误映射。
4. ASR 与中文对齐模型分别使用固定 revision、逐文件 SHA-256 manifest 和本地只读加载。
5. 增加手动 workflow：只接受 `master` 的完整 SHA 和显式执行门禁。
6. Windows runner 仅在
   `${PRODUCTION_WHISPERX_ROOT}` 与
   `${PRODUCTION_ASR_DATA_ROOT}-WhisperX` 创建隔离文件。
7. 使用非敏感、运行时合成的中文 WAV，验证
   `Engine → ProviderCandidate → normalizer → Canonical`。

## 验证门禁

- Profile/config JSON round-trip、Provider 工厂注册和 admission/release policy；
- 引擎 lazy import、缓存未配置/损坏、CUDA/FP16 不可用、OOM 和输出边界；
- 双模型身份、revision、相对路径、完整文件集合、大小和 SHA-256；
- workflow 仅手动触发、固定 SHA、固定 runner 和 `production-asr` environment；
- Torch 必须精确为 `2.8.0+cu128` 且 `torch.version.cuda == 12.8`；
- CTranslate2 必须仅识别一个 CUDA device 且支持 FP16；
- 执行前后 Scheduled Tasks 与 Windows Firewall 状态哈希必须一致；
- 脱敏 verdict 必须声明未修改生产服务且 Profile 仍未开放。

## 明确不做

- 不注册、启动、停止或重启任何生产服务或 Scheduled Task；
- 不修改防火墙、系统 Python、CUDA、PATH、现有 BGE/FunASR 环境；
- 不使用真实业务音视频，不读取客户资料、数据库、Qdrant 或生产密钥值；
- 不开启 Profile admission，不自动发布或自动索引；
- 不进入 diarization、质量资格、长音频压测、业务灰度或流量切换。

## 风险、停止条件与回滚

依赖解析失败、Torch/CUDA 身份不符、模型 manifest 不一致、GPU/FP16 不可用、OOM、
引擎失败、Canonical 失败或系统状态哈希变化均立即失败关闭。回滚以不配置 WhisperX
缓存环境变量、保持 Profile `disabled` 为主；代码回滚为撤销合并提交。runner 产生的
独立 run 目录和模型缓存保留用于审计，不在 workflow 中自动删除。

