# 共享 ASR Qualification 语料迁移

## 状态与边界

- 风险：R3。
- 数据集身份：沿用 faster-whisper 已通过资格测试的
  `sample_set_id=self-made-faster-whisper-r3`，固定 8 个非敏感 WAV。
- 本计划只统一 qualification 语料契约和定位方式。三个引擎的 venv、依赖、wheel
  cache、模型 cache/revision、运行目录、报告和 admission 结论继续隔离。
- 不启用 Profile，不注册或控制服务，不修改数据库、Qdrant、防火墙或计划任务。
- GitHub `production-asr` 变量写入、Windows runner preflight 和任一真实 GPU
  qualification 均需在代码合并后另行取得 R3 批准。

## 统一 Manifest

schema version 为 `asr-qualification-corpus/1`。根对象严格包含：

| 字段 | 契约 |
| --- | --- |
| `schema_version` | 固定为 `asr-qualification-corpus/1` |
| `sample_set_id` | 固定历史身份 `self-made-faster-whisper-r3` |
| `annotation_version` | 正整数版本字符串 |
| `source` | 固定声明 `self_made=true`、`is_internal_recording=false`、`contains_customer_data=false` |
| `samples` | 按 ID 排序的固定 8 项 |

每个 sample 严格包含 `id`、`path`、`size_bytes`、`sha256`、`duration_ms`、
`scenario`、`reference_text`、`reference_segments`、`expected_terms` 和
`expected_codes`。未知字段、重复 JSON key、字段缺失或额外字段均失败关闭。

文件层约束：

- manifest 必须是共享 root 的直接普通文件；sample 只能使用相对 POSIX `.wav` 路径；
- root、manifest 及 sample 的任一路径组件不得是 symlink 或 Windows reparse point；
- 路径解析后必须仍在共享 root 内；
- WAV 必须是 16 kHz、单声道、PCM16；大小、SHA-256 和四舍五入后的毫秒时长
  必须与 manifest 完全一致；
- 固定 5 个正向场景各 1 项，`negative-control` 3 项；
- validation 只读文件，并在每个 WAV 校验前后比较大小与修改时间。

示例位于 `asr_service/asr-qualification-manifest.example.json`。其中大小和 SHA 是
占位值，不能直接作为生产 manifest；实际共享 manifest 应由现有已 PASS 的
faster-whisper 八样本身份生成并冻结，不得在 qualification workflow 中重新生成。

## 变量迁移

| 引擎 | 中性配置 | 第一阶段 legacy 回退 |
| --- | --- | --- |
| faster-whisper | `PRODUCTION_ASR_QUALIFICATION_ROOT` + `PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH` | `PRODUCTION_FASTER_WHISPER_INPUT_ROOT\manifest.json` |
| Qwen3-ASR | 同上 | `PRODUCTION_QWEN3_ASR_INPUT_ROOT\manifest.json` |
| WhisperX | 同上 | `PRODUCTION_QWEN3_ASR_MANIFEST_PATH` |

中性变量必须成对存在。只配置其中一个时，即使 legacy 可用也失败关闭。中性与 legacy
同时存在时会完整校验两份 manifest，并要求原始 manifest SHA-256、`sample_set_id` 和
`annotation_version` 全部相同；不一致时不选择任何一方。身份相同时选择中性配置并记录
`manifest_source=neutral`，仅使用 legacy 时记录 `manifest_source=legacy`。

中性路径只接受新 schema。legacy 路径在迁移期接受新 schema 或对应的旧 schema，便于
先迁移变量再冻结旧回退。三引擎报告 schema version 不变，但都增加
`manifest_source`、`manifest_sha256` 和 `qualification_corpus` 身份投影；不会把引擎
输出写入共享 manifest。

## 实施与验证顺序

1. 合并共享 loader、三个 runner/wrapper、四个 workflow 和离线测试。
2. 另行批准后，运行
   `.github/workflows/materialize-asr-qualification-corpus-production.yml`，从已 PASS 的
   faster-whisper root 逐字节复制 8 个 WAV，在固定共享 root 原子发布新 schema
   manifest；旧目录与文件保持不变，共享文件发布后设置为只读。
3. 在 `production-asr` 写入两个中性变量，并将三个 legacy 变量暂时对齐到同一共享
   root/manifest；修改前在操作者的受限本地临时文件中保存旧值用于回滚，不输出路径。
4. 逐个运行 workflow 的 `manifest_preflight=true`；该路径只 checkout 和运行机器
   Python，不创建 venv、不运行 pip、不读取密钥、不加载模型、不执行推理。
5. 人工核对三份脱敏 artifact 的 manifest SHA-256、`sample_set_id`、
   `annotation_version`、sample count 及八项 WAV SHA-256 完全一致。
6. 三个只读 preflight 均通过后，另开审批删除代码中的 legacy fallback；环境变量删除
   也单独审批。
7. 真实 GPU qualification 每个 workflow 单独列出完整 master SHA、样本准备状态与
   预期副作用后重新审批；共享语料迁移通过不授权推理。

## 测试矩阵

| 范围 | 成功条件 | 失败关闭条件 |
| --- | --- | --- |
| JSON/schema | 新 schema 严格 round-trip | 重复 key、未知/缺失字段、错误来源声明 |
| 路径 | root 内相对 POSIX WAV | 绝对路径、穿越、反斜杠、symlink/reparse |
| 文件身份 | 8 项大小、SHA、时长和 WAV 格式一致 | 任一大小、SHA、时长、采样率、声道或位宽不符 |
| 场景 | 5 个正向场景各 1 项、3 个负样本 | 数量、顺序、场景分布或负样本期望项不符 |
| 变量解析 | 中性独立工作；相同 legacy 可并存 | 中性变量不成对；新旧身份冲突 |
| 引擎绑定 | 三 runner 得到同一 corpus identity | 报告未绑定 sample set/annotation/manifest SHA |
| 只读性 | 校验前后共享目录快照不变 | validation 或 qualification 期间身份变化 |
| workflow | preflight 只输出脱敏身份 | 输出路径/参考文本，或执行 pip、模型、服务操作 |

## 回滚

- 代码：revert 合并提交，三个 workflow 恢复旧变量读取。
- 环境：删除新增的两个中性变量并保留旧变量。
- 数据：保留共享语料、旧样本、缓存、运行目录和历史报告，不做删除或重生成。
- 任一步失败时保持所有 ASR Profile admission 原状态，不继续 GPU qualification，不修改
  样本、阈值或 manifest 以获取通过结果。
