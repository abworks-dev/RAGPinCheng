# faster-whisper R3 统一资格验证实施方案

> 状态：已于 2026-08-05 完成仓库实施、固定 Windows TTS 样本准备和生产 workflow
> 执行；固定 8 样本已生成并通过严格 Manifest 校验，但最终 verdict 为
> `dependency_preparation_failed`，faster-whisper 尚未取得 R3 资格，Profile 准入保持关闭。
> 日期口径：2026-08-05，Asia/Shanghai。
> 风险等级：R3（Windows 生产 GPU 主机上的依赖解析、模型下载与真实 CUDA 推理）。
> 唯一执行基线：本文件取代历史 `faster-whisper-r3a-*` 方案作为后续执行入口；
> 历史文件只保留为失败与审计记录，不得继续执行。

## 1. 目标

在 faster-whisper R2 代码已经合并但 Profile 准入保持关闭的前提下，用一个统一、
可审计、失败关闭的 R3 阶段回答以下问题：

1. Windows Python 3.11 能否解析并隔离安装固定 faster-whisper/CTranslate2 依赖；
2. 固定模型 revision 能否下载并通过全文件 Manifest 与公开 `model.bin` 身份校验；
3. RTX 5060 Ti 能否以 CUDA FP16 加载并运行当前 R2 的精确解码配置；
4. 当前 ASR service、Remote Provider、pipeline、normalizer、Canonical 和 formatter
   能否在隔离端口完成真实结果流；
5. 固定非敏感短样本是否满足预注册质量、时间戳、RTF 和资源门禁；
6. 验证全过程是否保持 BGE 优先，并且不影响现有 SenseVoice 生产服务。

R3 只形成资格结论。即使全部通过，也不自动修改
`faster-whisper-zh-experimental-v1` 的 `admission=disabled`，不自动部署到当前
ASR 生产 venv，不产生管理员可见的新建任务能力。

## 2. 已确认基线

### 2.0 最新执行结论

- 固定样本准备、编码修复和 pip 路径修复分别经 PR #36、#37、#38 合并；
- workflow `30955067671` 复用并严格校验了固定 8 样本；
- 组合依赖解析在 `requirements-windows.txt` 的 FunASR 项报告版本冲突，
  verdict 为 `dependency_preparation_failed`；
- 模型下载、临时 18200 服务、CUDA 推理和质量门禁均未开始；
- 脱敏 verdict 记录 `production_services_modified=false`、
  `profile_admission=disabled`；
- 在明确冲突包及重新批准依赖/约束方案前，不得放宽 production freeze、修改固定依赖
  或启用 faster-whisper Profile。

### 2.0.1 精确冲突诊断补充

经 2026-08-05 单独 R3 批准，精确冲突诊断只读取固定 source run
`30955067671` 的 production freeze、组合 requirements 和既有 resolver 日志。既有日志
不足时，只在独立诊断 venv 中以相同 index、约束和 requirements 执行 pip dry-run；
不安装依赖、不下载模型、不启动 ASR/CUDA，也不修改生产服务。

诊断入口为 `.github/workflows/diagnose-faster-whisper-dependencies-production.yml` 和
`scripts/diagnose-faster-whisper-dependencies.ps1`。workflow 默认关闭，只允许完整 master
SHA 和显式执行开关，仅注入 `ASR_DEPENDENCY_PROXY`。上传 artifact 只能包含冲突包、
版本约束和输入文件 SHA-256；URL、代理、Token、绝对路径和完整 freeze 禁止上传。
诊断得到精确冲突后必须停止，修改 pin、约束或隔离结构仍需重新审批。

精确诊断不得把“包依赖某名称”与该名称的兼容固定版本误判为版本冲突。只有以下证据可形成
`blocker_confirmed`：pip 明确报告同一 requirement 无可用版本且无匹配 binary distribution；
或 pip 冲突段同时给出同一 requirement 的带比较符依赖约束和 production constraint。
结果必须记录受影响 requirement 与诊断种类；证据不完整时保持失败关闭。
若完整 resolver 只给出裸依赖与同名 production constraint，可对该单一固定 constraint
执行同 index、同 freeze、`--only-binary=:all:` 的隔离 pip dry-run；只有单包探针明确返回
无匹配 binary distribution 时才能确认该 blocker，探针成功或错误种类不明确时继续失败关闭。

### 2.0.2 精确诊断结果（2026-08-05）

- source qualification run：`30955067671`；最终聚焦诊断 run：`30958705041`；
- production freeze 固定 `jieba==0.42.1`，resolver 证据显示 `funasr 1.4.1 depends on jieba`；
- 聚焦 single-requirement、binary-only dry-run 对 `jieba==0.42.1` 返回非零；
- PyPI 的 `jieba 0.42.1` release metadata 仅列出 `jieba-0.42.1.tar.gz`
  (`packagetype=sdist`)，没有 wheel；本地同参数公开索引 dry-run 复验为
  `No matching distribution found for jieba==0.42.1`；
- 因此 blocker 不是 FunASR 与 production freeze 的版本上下界冲突，而是当前
  `--only-binary=:all:` 安全策略无法接受 `jieba 0.42.1` 唯一可用的源码发行物。

诊断到此停止。未修改任何依赖 pin、production freeze、模型、服务、CUDA、Profile admission
或生产配置。后续若选择构建受控内部 wheel、放宽 binary-only 规则或调整环境隔离，必须提交新的
R3 方案并单独审批。

### 2.0.3 已批准的受控 wheel 解决方案（2026-08-05）

采用受控内部 wheel，不放宽 `--only-binary=:all:`，不修改 `jieba==0.42.1` pin 或
production freeze：

1. GitHub-hosted Ubuntu job 只从固定 `files.pythonhosted.org` URL 下载
   `jieba-0.42.1.tar.gz`，在任何源码执行前校验固定大小和 SHA-256；
2. 使用 Python 3.11、`setuptools==80.9.0`、`wheel==0.45.1` 和固定
   `SOURCE_DATE_EPOCH` 独立构建两次，只有文件名和 SHA-256 完全一致才生成 bundle；
3. bundle 只允许一个 pure-Python `*-none-any.whl` 和严格
   `asr-internal-wheel-manifest/1`，拒绝未知字段、路径逃逸、符号链接、原生二进制和身份漂移；
4. 同一 workflow 通过 artifact 将 bundle 传给 Windows `production-asr` job；
5. Windows 在创建资格 venv 前重新校验完整 SHA/run 身份和 Manifest，只把受控 bundle
   作为 `pip download --find-links` 来源，仍保留全局 `--only-binary=:all:`；
6. 内部 wheel 必须实际进入 run-local wheelhouse，并以受控内部身份、大小和 SHA-256
   记录；其他 wheel 继续绑定公开下载 URL；
7. 本方案只服务于隔离资格 run，不写入生产 ASR venv，不修改服务、防火墙、Ubuntu、
   数据库、Qdrant、模型或 Profile admission。

### 2.0.4 受控 wheel 资格重跑结果（2026-08-05）

- PR #45 的 7 项 CI 全部通过，合并 master SHA：
  `34eab650ed9402512296a835c24cef656dbcb60f`；
- qualification run `30960326875` 的 GitHub-hosted `build-internal-wheel` job 成功，
  固定 sdist 的校验、双重构建、wheel hash 一致性和 artifact 上传均通过；
- Windows job 成功下载同 run 的受控 bundle，随后隔离资格仍以
  `dependency_preparation_failed` 失败；
- 脱敏 verdict 确认 `profile_admission=disabled`、
  `production_services_modified=false`，未进入 Profile 开放；
- 当前证据不足以判定新的精确依赖 blocker。本阶段到此停止；读取更详细的依赖证据、
  修改其他 pin/约束或再次重跑须另行审批。

### 2.0.5 已批准的资格失败自诊断（2026-08-05）

为避免继续为每次依赖失败创建一次性诊断 workflow，现有统一资格脚本负责在
`dependency_preparation_failed` 时生成严格脱敏的
`faster-whisper-r3-dependency-failure/1`：

- 只输出固定阶段、固定诊断种类和经 ASCII 包名正则归一化的 requirement；
- 诊断种类限定为 `binary_distribution_unavailable`、
  `version_constraint_conflict`、`network_or_index_failure` 或
  `evidence_insufficient`；
- 不输出原始日志、冲突行、URL、代理、Token、绝对路径或完整 freeze；
- 脱敏文件与既有 verdict 同 artifact 上传，并可安全显示在 workflow summary；
- 只有依赖准备失败才生成该文件；成功或进入后续模型/CUDA/样本阶段时不生成；
- 本补充不修改 pin、production freeze、生产 venv、服务、防火墙、Ubuntu、
  数据库、Qdrant、模型 revision 或 Profile admission。

合并后只允许使用新的完整 master SHA 重跑一次统一资格 workflow；取得精确 blocker
或进入下一资格阶段后立即停止，不自动修改依赖或开放 Profile。

首次自诊断 run `30963495106` 仍失败关闭，verdict 正常上传，但依赖日志为空时
PowerShell 5.1 的 mandatory array 参数拒绝空集合，导致附加诊断未生成。同范围兼容修复
必须允许空集合并以 `evidence_insufficient` 失败关闭，同时优先写入 runner artifact 路径；
修复后只以相同参数重试，不改变资格范围。

### 2.0.6 Windows PowerShell 5.1 原生 stderr 捕获修复

同参数重试 run `30963979244` 已生成附加诊断，但只确定失败阶段为 `pip_download`，
诊断种类仍为 `evidence_insufficient`，requirement 为空；verdict 继续确认
`profile_admission=disabled`、`production_services_modified=false`。

代码核对发现，统一资格脚本全局使用 `$ErrorActionPreference = "Stop"`，而
`Invoke-External` 直接通过 `2>&1` 捕获原生命令输出。Windows PowerShell 5.1 可能把
原生 stderr 提升为终止错误，使函数在记录完整输出和退出码前中断；仓库此前的精确诊断
脚本已通过执行期间临时切换为 `Continue` 避免该行为。

获批的最小修复限定为：

1. `Invoke-External` 只在固定原生命令执行期间临时使用 `Continue`，完整捕获 stdout、
   stderr 和退出码后在 `finally` 恢复原值；
2. 日志落盘后仍以非零退出码失败关闭，不改变任何命令参数、依赖或资格顺序；
3. 资格 run 在调用真实依赖步骤前，以固定非敏感 stderr 标记和固定非零退出码自测该边界；
4. PR 和 CI 通过后，只使用新的完整 master SHA 及原固定参数重跑一次；
5. 结果仍只上传严格脱敏 verdict/diagnostic，不上传原始日志、路径、代理或 Secret；
6. 不修改 production freeze、生产 venv、服务、防火墙、Ubuntu、数据库、Qdrant、
   模型或 Profile admission。

### 2.0.7 依赖诊断 v2 闭环

原生 stderr 捕获修复 PR #49 的 7 项 CI 全部通过并合并为
`20fa332192aeb3d2bb628f7fafbbbd39e09bc1fb`。固定参数资格 run
`30966653503` 仍停在 `pip_download`，v1 诊断为 `evidence_insufficient`；
`profile_admission=disabled`、`production_services_modified=false`。

v1 的 `pip_download` 阶段同时覆盖代理设置、pip 原生命令和代理恢复，且只输出解析后的
诊断种类，无法判定 pip 是否启动、退出码、捕获行数或失败是否来自代理边界。获批的 v2
闭环一次性增加：

1. 将脱敏 Schema 固定为 `faster-whisper-r3-dependency-failure/2`；
2. 记录固定枚举 `dependency_operation`、`failure_origin`，以及整数/空值
   `native_exit_code`、非负 `captured_line_count`；
3. 将 `pip_download` 内部操作分为 `proxy_setup`、`pip_download_command` 和
   `proxy_restore`，不改变原执行顺序；
4. 增加 requirements/constraint、文件权限、磁盘、原生进程启动和代理设置/恢复的固定
   脱敏类别；
5. 只有 pip 命令明确非零退出、原始安全解析仍不足时，才在同一隔离 venv 中执行一次
   同 index、同 freeze、同 requirements、同受控 wheel 的
   `pip install --dry-run --ignore-installed --only-binary=:all: --no-cache-dir`；
6. fallback 不安装依赖、不下载模型、不启动服务；只记录是否执行、退出码以及白名单
   诊断种类/ASCII requirement；
7. workflow summary 和 artifact 不包含原始行、URL、代理、Token、绝对路径、完整 freeze
   或其他自由文本；
8. PR 和 CI 通过后只允许使用新的完整 master SHA 重跑一次，取得 v2 结果后停止。

本闭环不修改依赖、production freeze、生产 venv、服务、防火墙、Ubuntu、数据库、
Qdrant、模型或 Profile admission。

### 2.0.8 离线受限脱敏 resolver 证据提取

固定资格 run `30968517582` 的 v2 诊断确认失败来自 `pip_download_command` 的
原生命令退出码 1，共捕获 130 行，并执行了一次退出码 1 的隔离 fallback；诊断仍为
`resolver_replay_insufficient`，Profile 保持 disabled，生产服务未修改。

经单独 R3 批准，复用既有手动诊断 workflow，但将其收敛为一次纯离线提取：

1. 只读取该 run 下 `logs/pip-download.log`、`logs/pip-resolver-fallback.log` 和
   `reports/dependency-diagnostic.json`，并严格校验固定 run、commit、v2 字段和文件边界；
2. 使用 Python 3.11 标准库将 pip 固定格式归一化为包名、版本约束、依赖所有者、错误族和
   blocker，不输出原始行、URL、代理、Token 或绝对路径；
3. artifact 只允许严格 `faster-whisper-r3-resolver-evidence/1` JSON；workflow summary
   只显示固定状态、来源身份、计数和归一化 blocker；
4. workflow 不注入 Secret，不运行 pip，不访问网络，不启动或修改服务、防火墙、Ubuntu、
   数据库、Qdrant、模型或 Profile admission；
5. 旧 replay 诊断脚本保留为历史审计实现，但本次 workflow 不再调用；
6. 合并后只执行一次。若证据仍不完整则停止，不自动迭代解析器或修改依赖。

### 2.0.9 resolver 冲突误判修复

首次离线提取 run `30971872737` 成功读取固定证据，但把无版本范围的
`funasr 1.4.1 depends on oss2` 与 `oss2==2.19.1` 错判为版本冲突。无版本依赖允许该
固定版本，不能形成 blocker。

获批最小修复只在 owner 依赖自身包含明确版本比较符、production constraint 是单一精确
数字版本，且该固定版本违反 owner 的全部合取条件时输出 `version_constraint_conflict`。
缺失比较符、兼容约束、非数字版本、通配符、compatible-release 或非精确 constraint 均保持
`evidence_incomplete`，只保留规范化候选。合并后允许再执行一次固定离线提取；仍不运行 pip、
不读取或上传原始日志、不修改依赖或生产状态。

### 2.0.10 oss2 受控 wheel 与资格闭环

修复后的离线提取 run `30972229558` 确认无可证明的版本冲突：`funasr 1.4.1` 只声明
无版本范围的 `oss2`，production freeze 固定 `oss2==2.19.1`。官方 PyPI release metadata
同时确认 `oss2 2.19.1` 只发布大小 298845 bytes、SHA-256
`a8ab9ee7eb99e88a7e1382edc6ea641d219d585a7e074e3776e9dec9473e59c1` 的 sdist，
没有 wheel。因此它与此前 jieba blocker 相同：被资格流程的全局 `--only-binary=:all:` 拒绝，
而不是版本上下界冲突。

完整闭环沿用已批准的受控 wheel 安全模式：GitHub-hosted Python 3.11 job 从固定
`files.pythonhosted.org` URL 下载并校验 oss2 sdist，在网络禁用的构建阶段使用固定
setuptools/wheel 重复构建两次；只接受确定性 pure-Python `none-any` wheel。jieba 与 oss2
分别保留严格 Manifest，作为同一 artifact 交给 Windows；资格脚本逐包复验身份、哈希、大小、
标签和 run/commit，并要求两个 wheel 都实际进入 wheelhouse。wheelhouse Manifest 升级为
`faster-whisper-wheel-manifest/2` 并记录两个受控 Manifest 哈希。该修复不放宽 binary-only，
不改变 production freeze，也不写入生产 venv。

### 2.0.11 antlr4 受控 wheel 闭环

统一资格 run `30974457008` 已成功构建并复验 jieba 与 oss2 两个受控 wheel，随后依赖解析
继续在 `antlr4-python3-runtime` 停止。严格 diagnostic 保持 Profile disabled、生产服务未修改。
固定 production 版本 `4.9.3` 的官方 PyPI release 也仅发布一个 117034 bytes、SHA-256
`f224469b4168294902bb1efa80a8bf7855f24c99aef99cbefc1bcd3cce77881b` 的 sdist，
没有 wheel，属于相同 binary-only blocker。

闭环继续复用同一安全模式构建第三个 pure-Python 受控 wheel；资格脚本逐包验证 antlr4 的固定
来源、重复构建一致性、Manifest、哈希、大小和标签，并要求 jieba、oss2、antlr4 三个内部
wheel 全部进入 run-local wheelhouse。不调整 pin、production freeze 或 binary-only 策略。

### 2.0.12 crcmod pure-Python 受控 wheel

最终绑定 SHA 的资格 run `31008453083` 已通过 jieba、oss2、antlr4 三个受控 wheel，下一门禁
明确为 `crcmod`。固定 1.7 release 只有 SHA-256
`dc7051a0db5f2bd48665a990d3ec1cc305a466a77358ca4492826f41f283601e`、大小 89670 bytes
的 sdist。其官方安装契约允许 C 扩展编译失败后使用纯 Python 实现。受控构建显式禁用编译器，
并继续拒绝任何 native wheel 内容，只接受重复构建一致的 `none-any` wheel。该 wheel 与前三个
包一同执行严格来源、Manifest 和 wheelhouse 校验，不改变 binary-only 或 production freeze。

### 2.0.13 资格依赖范围修正

连续出现的 oss2、antlr4、crcmod、aliyun SDK 并非 faster-whisper 自身依赖，而是资格脚本
把 `requirements-windows.txt` 整套 FunASR 生产依赖重新作为安装目标，同时又以 production
freeze 约束并强制 binary-only，导致已经存在于生产 venv 的 legacy sdist 链被无意义重建。

正确资格边界改为：先对真实生产 venv 执行既有 `pip check` 并生成不可变 freeze；隔离 venv
只请求固定 torch/torchaudio 与 `requirements-faster-whisper.txt`，仍使用完整 production
freeze 作为 constraint。这样任何共享依赖都不得改变生产版本，但 FunASR 私有传递依赖不会被
重复安装。受控 legacy wheel Manifest 仅作为已验证兼容性参考，不要求进入 faster-whisper
wheelhouse；wheelhouse Schema 升级为 `/3` 明确记录 reference Manifest 哈希。生产 venv、freeze、
binary-only 和 Profile admission 均不改变。

### 2.0.14 可校验持久 wheelhouse 缓存

Windows 自托管 runner 不启用 pip 默认缓存。资格脚本使用固定
`${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\qualification\wheel-cache`，按 Python 版本与 ABI、
Windows 架构、pip 版本、CUDA/torch/torchaudio、production freeze、faster-whisper
requirements 及四个受控 wheel 的稳定 Manifest 身份计算缓存键。

缓存 miss 仍以 `--no-cache-dir --only-binary=:all:` 下载到 run-local wheelhouse，
先用同约束的 pip `--dry-run --ignore-installed --report` JSON 按归档 SHA-256 绑定公开
下载 URL，再生成严格来源 URL、大小和 SHA-256 Manifest，并在同盘 staging 内复验后原子发布。
缓存 hit 必须重新计算缓存键，拒绝未知、多余、缺失、reparse point、大小或哈希不符的文件，
再复制到本轮隔离 wheelhouse；损坏项移动到隔离目录后重新下载，不原位修补或静默覆盖。
每轮仍创建全新 venv，使用 `--no-index` 离线安装，并执行 `pip check`、freeze、模块来源、
许可证、模型和真实 8 样本 GPU 门禁。缓存、Manifest 和阶段日志不得包含 Token 或代理地址。
workflow 对脱敏 verdict artifact 提供一次受控上传重试；内部 wheel artifact 链路优化不属于
本次范围。

### 2.0.15 jieba 固定 sdist 预置输入

资格 Run `31287507974` 在 Windows runner 通过依赖代理下载固定
`jieba-0.42.1.tar.gz` 时发生流截断，未进入样本准备、模型或 CUDA 资格阶段。后续不再由
builder 在线获取该 sdist，改为只读取固定 staging 路径
`${PRODUCTION_REPO_PATH}\runtime\qualification-inputs\faster-whisper\jieba-0.42.1.tar.gz`。
workflow 和 builder 分别校验普通文件、固定 19,214,172 字节、固定 SHA-256 与 archive
结构，再复制到 run-local 临时目录并执行既有双重可复现构建。路径不是 dispatch 输入，
不得指向现有 ASR venv、模型或服务目录；缺失或身份不符时立即失败，不回退在线下载。

### 2.1 仓库基线

- PR #34 已合并到 `master`；
- merge commit：
  `43996495c018c00fa6e473c418f57732a08744ea`；
- PR 头提交：
  `343fb68208a465eaf6a8367db63e1d0ed9549aaf`；
- CI run `30948925502` 的 7 个检查全部成功；
- faster-whisper R2 Profile 固定为：

```text
provider_key=faster-whisper
application_profile_id=faster-whisper-zh-experimental-v1
service_profile_id=faster-whisper-large-v3-turbo-v1
qualification=experimental
admission=disabled
```

### 2.2 固定候选

```text
python=3.11
faster-whisper=1.2.1
ctranslate2=4.8.1
model_id=dropbox-dash/faster-whisper-large-v3-turbo
model_revision=0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
device=cuda
compute_type=float16
language=zh
task=transcribe
beam_size=1
temperature=0.0
vad_filter=false
condition_on_previous_text=false
word_timestamps=false
hotwords=null
local_files_only=true
```

模型 `model.bin` 的冻结公开身份为：

```text
size_bytes=1617884929
sha256=e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31
```

R3 必须在本机重新计算大小与 SHA-256；公开值只作为比对基线。

### 2.3 当前生产拓扑

执行前必须重新只读核验，不能只依赖历史记录：

```text
Windows GPU / ASR: ${PRIVATE_IPV4}
Ubuntu backend:    ${PRIVATE_IPV4}
GPU service:       RAGPinCheng-GPU / TCP 8100
ASR service:       RAGPinCheng-ASR / TCP 8200
GitHub runner:     ${PRODUCTION_HOSTNAME} / Administrator / asr-production
ASR program root:  ${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR
ASR data root:     ${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR
```

## 3. 统一执行原则

本方案只有一个 R3 执行单元：

```text
仓库工具实现、测试、PR、CI、合并
→ 一次显式 production-asr workflow
→ 自动 preflight
→ 隔离依赖与模型准备
→ CUDA / 全链路 / 样本 / 资源验证
→ 自动清理临时进程
→ 单一 PASS / FAIL 报告
```

不再拆成 B1/B2、retry1～retry5 或多个手工暂停点。同一获批范围内的代码修复、
CI 修复和可证明幂等的 workflow 重试属于同一交付；只有出现新增权限、路径、
Secret、模型、依赖、样本来源、生产服务切换或回滚失效时才重新审批。

## 4. 隔离策略

### 4.1 固定目录

```text
资格程序根：
${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper

每次运行：
${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper\runs\<github.run_id>\

固定样本入口：
${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper\inputs\

固定样本 Manifest：
${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\qualification\faster-whisper\inputs\manifest.json

模型最终目录：
${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\models\faster-whisper-large-v3-turbo\
0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf\

模型 Manifest：
上述目录下的 model-manifest.json

隔离 ASR 地址：
http://127.0.0.1:18200
```

每次运行的 venv、wheelhouse、spool、日志、报告和临时配置全部位于该 run 目录。
不得写入全局 Python、用户级 Hugging Face cache、现有 ASR venv、GPU service
venv 或仓库工作区。

### 4.2 现有生产服务

R3 不停止、不重启、不重新注册以下 Scheduled Task：

- `RAGPinCheng-GPU`；
- `RAGPinCheng-ASR`。

R3 不修改：

- TCP 8100/8200 防火墙；
- 当前 ASR 生产 venv；
- `${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR\config\asr.env`；
- Ubuntu `prod.env`；
- Ubuntu `ASR_ENABLED`；
- backend 容器。

隔离服务只绑定 `127.0.0.1:18200`，不增加防火墙规则，不接受其他主机连接。

## 5. 固定样本包

推荐复用已经审核过的 8 个非敏感短样本语义与阈值，但复制到 §4.1 的固定输入目录，
不得直接依赖历史 run 的可变路径。

固定样本由独立、默认关闭的 workflow 开关调用 Windows 内置
`System.Speech.Synthesis.SpeechSynthesizer` 生成；只选择已启用的 `zh-CN` voice，
并固定输出为 16 kHz、mono、PCM16 WAV。八段正文、ID、场景、reference、
expected terms/codes 和来源声明均固化在仓库脚本中，不接受 workflow 自由输入。
`noisy-bim-zh` 在 TTS PCM 上叠加固定种子的低幅背景噪声，其余样本保持原始 TTS
输出。

生成过程先写入每次运行的 staging，计算实际时长和小写 SHA-256，写出严格
Manifest，再调用资格运行器的 `--validate-manifest-only`。固定输入目录不存在时才
原子提升 staging；目录为空时先把空目录移动到本次审计目录；已有内容只有在严格
Schema 和固定语义均匹配时才复用，其他情况失败关闭且不覆盖、不删除。

Manifest 必须是严格 JSON，至少包含：

```text
schema_version=faster-whisper-qualification-samples/1
sample_set_id
annotation_version
samples[8]
```

每个样本必须包含：

- 安全 slug `id`；
- 相对 WAV 路径；
- 小写 SHA-256；
- `duration_ms`；
- `scenario`；
- `reference_text`；
- `reference_segments`：五个正向样本必须至少一段，三个负向控制必须为空；
- `self_made=true`；
- `is_internal_recording=false`；
- `contains_customer_data=false`。

样本集合固定为 5 个正向样本和 3 个负向控制，至少覆盖：

- 清晰普通中文；
- BIM 术语；
- 规范编号；
- 带噪声 BIM 中文；
- 中英混合；
- 不含 BIM 术语和规范编号的负向语音。

音频必须是 16 kHz、mono、PCM WAV，单条不超过 60 秒。Manifest、音频数目、
路径、hash 或 reference 任一不匹配即停止。R3 不从应用媒体目录、客户文件、
数据库、artifact 或 Qdrant 中提取样本。

## 6. 拟新增和修改文件

### 6.1 R3 实施时新增

| 文件 | 职责 |
|---|---|
| `.github/workflows/qualify-faster-whisper-production.yml` | 单一手动 R3 workflow；绑定完整 master SHA、`production-asr` Environment、Windows runner、并发锁和显式执行开关 |
| `scripts/build_internal_jieba_wheel.py` | 固定 staging 输入、双重可复现构建和严格校验 `jieba==0.42.1` 受控 pure-Python wheel bundle |
| `scripts/qualify-faster-whisper-production.ps1` | Windows 总编排：preflight、wheelhouse、隔离 venv、模型准备、临时服务、GPU/BGE 监控、清理和最终 verdict |
| `scripts/prepare_faster_whisper_model.py` | 固定 Hugging Face repo/revision 下载、staging、普通文件/无 symlink 检查、全文件 Manifest 和本机 hash 校验 |
| `scripts/run_faster_whisper_qualification.py` | 严格读取 8 样本 Manifest，通过隔离 ASR HTTP + Remote Provider + pipeline 生成 Canonical/Markdown，计算质量与时间戳指标 |
| `scripts/prepare-faster-whisper-qualification-samples.ps1` | 使用 Windows 内置中文 TTS 生成固定 8 样本、确定性噪声、实际时长/SHA-256 和严格 Manifest，并在 staging 校验后提升 |
| `asr_service/faster-whisper-qualification-manifest.example.json` | 不含真实音频或内部文本的严格 Manifest 模板 |
| `asr_service/tests/test_faster_whisper_qualification.py` | 模型准备、Manifest、指标、报告、失败关闭和无真实依赖测试 |

### 6.2 R3 实施时最小修改

| 文件 | 修改内容 |
|---|---|
| `tests/test_asr_deployment_static.py` | 静态验证 workflow 默认关闭、固定 runner/目录/端口、无服务激活/防火墙/Ubuntu 改写、Secret 不回显 |
| `project-docs/features/transcript-pipeline.md` | 仅在真实执行后按证据记录资格状态；失败不得写成可用 |
| `TODO.md` | 维护 R3 审批、执行和后续 admission 决策 |
| `WORKLOG.md` | 记录实际实现、CI 和真实执行结论 |

现有 `.github/workflows/ci.yml` 的 `test-asr-service-contract` 已收集全部
`asr_service/tests`，因此预计不需要修改；若新增测试没有被实际收集，才允许在同一
R3 范围内做最小 CI 接线。

## 7. 明确不修改的文件和能力

R3 不修改：

- `src/transcription/profile.py`；
- `src/transcription/profile_catalog.py`；
- `src/transcription/pipeline.py`；
- Candidate、Canonical、normalizer、formatter；
- 数据库 Schema、迁移、Store 和状态模型；
- 上传 API、管理端 UI、worker；
- Qdrant、BGE 模型和检索链路；
- `scripts/deploy-asr.ps1`；
- `scripts/activate-asr-production.ps1`；
- `scripts/prepare-asr-model.ps1`；
- 现有 ASR/GPU Scheduled Task 和防火墙；
- 根 requirements、生产 backend requirements；
- 当前 `asr_service/requirements-windows.txt`。

R3 不安装到现有生产 ASR venv，不创建真实应用转录任务，不审核、发布或索引稿件。

## 8. Workflow 输入与 Secret

workflow 只接受：

```text
commit_sha=<完整 40 位、已合并 master SHA>
execute_qualification=false|true（默认 false）
prepare_synthetic_samples=false|true（默认 false）
```

`commit_sha` 必须等于 workflow dispatch revision。不得接受自由模型 ID、revision、
设备、compute type、解码参数、目录、端口、voice、样本文本或样本路径。启用
`prepare_synthetic_samples` 只准备固定合成样本；R3 实测仍必须显式启用
`execute_qualification`。

只允许使用既有 `production-asr` Environment 中的：

- `ASR_DEPENDENCY_PROXY`；
- `ASR_MODEL_DOWNLOAD_PROXY`；
- `GPU_SERVICE_TOKEN`。

不需要 `ASR_SERVICE_TOKEN`。隔离服务使用进程内随机临时 Token，运行结束即失效。
所有代理只作用于下载子进程，finally 中恢复 runner 环境。任何 Token 不得写入报告、
日志、命令行或 GitHub step summary。

## 9. 统一执行步骤

### 9.1 Preflight

1. 校验完整 SHA、checkout HEAD、分支为已合并 master；
2. 校验 runner 服务账户为 `Administrator`；
3. 校验机器级 Python 3.11、64 位、pip 可运行；
4. 校验目标 GPU 为 RTX 5060 Ti，CTranslate2 可见 CUDA 前不得运行模型；
5. 校验 D 盘至少 30 GiB 可用；
6. 校验 8100、8200 当前健康；
7. 校验 BGE `model_loaded=true`、`inflight_requests=0`、
   `asr_chunk_allowed=true`；
8. 校验 `RAGPinCheng-GPU`、`RAGPinCheng-ASR` 均在运行；
9. 校验 18200 未监听，且不存在本方案拥有的活动资格进程；
10. 校验固定样本包和 Manifest；
11. 快照生产 ASR capabilities、Scheduled Task 定义、监听端口和防火墙相关规则，
    只记录非敏感摘要。

任一失败直接结束，不创建 venv、不下载依赖或模型。

### 9.2 Wheel、依赖与许可证

1. 只读记录当前生产 ASR venv 的 `pip freeze --all` 和 `pip check`；
2. 在 run 目录创建全新 Python 3.11 venv；
3. 解析：
   - `torch==2.7.0`、`torchaudio==2.7.0` 的 cu128 Windows wheel；
   - `asr_service/requirements-windows.txt`；
   - `asr_service/requirements-faster-whisper.txt`；
4. 现有生产 freeze 作为共享包约束，证明 FunASR 与 faster-whisper 可以共存；
5. 只接受 binary wheel，禁止安装 sdist、使用 VCS URL、editable 或可变 branch；唯一
   例外是 Windows 资格 job 从固定 staging 路径读取经大小/SHA-256/archive 双重验证的
   `jieba 0.42.1` sdist，并在网络禁用的构建阶段重复构建得到受控 pure-Python wheel；
6. wheelhouse 按文件名排序记录 URL、大小和 SHA-256；
7. 从同一 wheelhouse 离线安装资格 venv；
8. 运行 `pip check`、完整 freeze、模块来源校验和许可证审计；
9. 顶层固定版本必须精确匹配，依赖不得来自生产 venv、用户 site 或全局 site。

以下任一情况判定失败：

- resolver 冲突；
- 需要修改现有生产 venv；
- 缺少 binary wheel；
- `pip check` 失败；
- 顶层版本漂移；
- 出现 GPL/AGPL/SSPL 或未知/无法确认许可证；
- wheel hash 在下载与安装间变化。

### 9.3 固定模型准备

1. 只通过 `huggingface_hub.snapshot_download` 请求固定 repo/revision；
2. 下载目录限定在本次 run staging；
3. 拒绝 symlink、非常规文件、空目录和下载器返回 staging 外路径；
4. 对全部文件计算大小与小写 SHA-256；
5. 独立核对 `model.bin` 固定大小与 SHA-256；
6. 生成严格 `asr-model-manifest/1`；
7. 使用当前 `validate_faster_whisper_cache()` 对 staging 和最终目录各验证一次；
8. 最终目录已存在且有效则幂等复用；已存在但无效则停止，不覆盖、不删除。

失败 staging 和证据保留在本次 run，不自动删除；不得把部分下载目录发布为模型缓存。

### 9.4 CUDA 与临时 ASR service

1. 资格 venv 中验证：
   - `ctranslate2==4.8.1`；
   - CUDA device count 大于 0；
   - CUDA 支持 `float16`；
   - 无 CPU/INT8 fallback；
2. 创建 run-local env、spool、日志和临时 Token；
3. 以精确 PID 启动：

```text
python -m uvicorn asr_service.app:create_app
--factory --host 127.0.0.1 --port 18200
```

4. 隔离配置指向：
   - 当前有效 SenseVoice Manifest；
   - 新 faster-whisper Manifest；
   - run-local spool/log；
   - 现有 BGE `/v1/activity`；
5. `/health` 必须为 `asr-service/1`；
6. `/v1/capabilities` 必须按排序暴露两个精确 service Profile；
7. 生产 8200 的 health/capabilities 必须保持与 preflight 快照一致。

### 9.5 8 样本全链路资格验证

每条样本都必须走：

```text
WAV bytes
→ loopback ASR service
→ faster-whisper engine
→ ProviderCandidate
→ RemoteAsrProvider
→ pipeline.py
→ normalizer
→ Canonical Transcript
→ Markdown formatter
→ 现有 transcript parser
```

不得直接调用模型后跳过 Provider/pipeline，也不得用测试 Fake 替代真实引擎。

固定通过阈值：

| 门禁 | 阈值 |
|---|---|
| 处理失败率 | `0%` |
| 清晰样本 CER | `<= 0.10` |
| BIM/噪声样本 CER | `<= 0.15` |
| BIM 术语召回 | `>= 0.70` |
| 规范编号召回 | `>= 0.95` |
| 短样本起始时间戳 P95 漂移 | `<= 1500 ms` |
| 每条样本 RTF | `<= 0.60` |
| 负向样本 BIM/编号新增误报 | `0` |
| Canonical/Markdown/parser | 全部成功且确定性复跑一致 |

阈值在执行前冻结。不得看结果后修改 reference、归一化、参数或阈值再宣告同一轮通过。

### 9.6 GPU、BGE 与末态

运行期间每秒采集：

- 整卡显存；
- 资格进程 PID；
- GPU utilization；
- BGE activity；
- 生产 8100/8200 健康。

固定资源门禁：

```text
资格运行相对 baseline 的峰值显存增量 < 8 GiB
整卡峰值显存 < 14 GiB
BGE 全程 model_loaded=true
BGE 全程 inflight_requests=0 且 asr_chunk_allowed=true
无 CUDA OOM
```

若 BGE 变忙、健康失败、达到显存门禁或出现 OOM，立即取消当前资格任务并终止本方案
精确拥有的 18200 子进程；不得终止 GPU service、生产 ASR 或其他 Python 进程。

finally 必须：

1. 终止并等待精确资格 PID；
2. 确认 18200 已释放；
3. 确认无资格子进程残留；
4. 确认 8100/8200 正常；
5. 确认两个 Scheduled Task 定义和状态未变；
6. 确认防火墙快照未变；
7. 确认 faster-whisper application Profile 仍为 disabled。

## 10. 报告与隐私

完整报告保存在本次 run：

```text
evidence\preflight.json
evidence\production-freeze.txt
evidence\wheel-manifest.json
evidence\license-matrix.json
evidence\model-manifest.json
evidence\gpu-samples.jsonl
reports\sample-results.json
reports\qualification-summary.json
logs\qualification-service.log
```

GitHub Actions 只上传和展示脱敏摘要：

- commit/model/package identity；
- 文件数、总大小和 Manifest SHA-256；
- 指标 observed/threshold/pass；
- 资源峰值；
- 最终 PASS/FAIL 和失败代码。

不上传音频、reference、hypothesis、Token、完整环境变量、进程命令行或生产路径之外的
文件内容。完整文本只留在 Administrator ACL 保护的本机 run 目录。

## 11. 自动停止条件

任一条件触发统一 `FAIL`：

- checkout/identity/runner/目录不符；
- 生产 8100/8200 或 Scheduled Task 基线异常；
- BGE 忙或不健康；
- 样本身份、来源声明或 hash 不符；
- resolver、wheel、许可证或 `pip check` 失败；
- 模型 revision、Manifest、大小或 hash 不符；
- CUDA/FP16/DLL 不可用；
- 临时服务契约不符；
- 任一质量、时间戳、RTF 或资源门禁不通过；
- 需要 CPU、INT8、其他模型、其他 revision、VAD、hotwords 或参数调整；
- 无法证明只终止本次资格进程；
- 用户要求停止。

失败只保留证据并恢复临时进程末态，不自动换模型、降级、调参、开放 Profile 或进入
新的 retry 方案。

## 12. 测试矩阵

### 12.1 本地/CI，不安装真实引擎

- workflow 默认关闭、完整 SHA、runner、concurrency、timeout；
- PowerShell 路径、PID ownership、finally 清理和禁止命令扫描；
- 代理仅作用于下载；
- Secret 不进入命令行或报告；
- sample Manifest 所有对象严格未知字段拒绝；
- 路径逃逸、symlink、hash/size/revision 错误；
- wheelhouse 拒绝 sdist/VCS/editable；
- license blocker；
- 模型 staging/final 幂等与无效目录失败关闭；
- 质量阈值边界；
- 脱敏报告不含文本和 Token；
- 模拟 BGE busy、OOM、超时、孤儿进程和 cleanup failure；
- 现有 SenseVoice/Profile/ASR 部署测试不退化。

建议验证命令：

```text
python -m pytest asr_service/tests/test_faster_whisper_qualification.py -v
python -m pytest tests/test_asr_deployment_static.py tests/test_asr_activation.py -v
python -m pytest asr_service/tests tests/test_transcription_asr_service_contract.py
  tests/test_transcription_profile_catalog.py
  tests/test_transcription_remote_provider.py -v
python -m compileall -q scripts asr_service tests
git diff --check
```

### 12.2 真实 R3

- workflow 使用新合并的完整 master SHA；
- 一次完整运行；
- PASS 后复跑只允许验证幂等模型缓存和新 run 隔离，不覆盖旧 run；
- GitHub workflow、Windows 本机报告和最终 commit identity 三方一致。

## 13. 完成标准

只有以下全部满足，R3 才为 `PASS`：

1. 仓库实现经过 scoped review、CI、独立 PR 并合并；
2. workflow 绑定合并后的完整 master SHA；
3. 生产 freeze、资格 venv、wheel Manifest、license audit 全部通过；
4. 固定模型全文件 Manifest 和 `model.bin` 身份通过；
5. RTX 5060 Ti CUDA FP16 加载与精确参数通过；
6. 隔离服务同时暴露两个固定 service Profile；
7. 8/8 样本完成且所有质量、时间戳和 RTF 门禁通过；
8. Candidate → Canonical → Markdown → parser 全链路通过；
9. GPU/BGE 资源门禁通过；
10. 无业务媒体、数据库、Qdrant、发布或索引操作；
11. 生产 8100/8200、Scheduled Task、防火墙和 Ubuntu 配置保持不变；
12. 18200 关闭、资格进程无残留；
13. application Profile 仍为 `experimental + disabled`；
14. 形成脱敏单一 PASS/FAIL 报告并记录 WORKLOG。

部分通过、工具通过但样本失败、单样本成功或资源未测均不得写成 R3 通过。

## 14. 风险与回滚

### 风险

- 约 1.6 GiB 模型和完整 wheelhouse 会占用磁盘并依赖外网代理；
- CTranslate2 CUDA wheel、驱动和 Blackwell 支持可能在真实主机失败；
- 隔离推理仍与 BGE、生产 ASR 共享同一 GPU；
- 现有 8 样本可能证明模型质量不足；
- Windows WDDM 下进程级显存可能不可用，因此以整卡 baseline 增量作为硬门禁。

### 回滚

- 仓库：revert R3 工具提交；R2 Provider 代码无需回滚；
- 进程：finally 只终止本次精确 PID，释放 18200；
- venv/wheel/report：均为 run-local、未被生产服务引用，失败后保留审计；
- 模型：新增模型目录在 Profile disabled 时不会被生产使用；失败 staging 不提升；
- 生产：现有 ASR/GPU 服务、env、防火墙、Ubuntu backend 不发生写入，无需数据恢复；
- 数据：不接触 SQLite/Qdrant，因此无数据库或索引回滚。

R3 不自动递归删除 run、wheelhouse、模型或失败 staging。后续清理需按精确目录另行确认。

## 15. R3 通过后的下一步

R3 PASS 后只提交结论，不自动继续。若用户决定开放 faster-whisper，需要另行批准一个
最小生产启用变更：

1. 用 R3 wheel Manifest 将已验证依赖接入生产 ASR venv 的可回滚部署；
2. 将固定模型路径写入 ASR 生产 env；
3. 热更新并验证 8200 同时暴露两个 Profile；
4. 将 application Profile `admission` 从 `disabled` 改为 `enabled`；
5. 仍保持 `experimental`、强制人工审核、禁止自动发布和自动索引；
6. 用一个非敏感应用任务做最终验收。

若 R3 FAIL，则保持当前生产状态，不进入启用阶段。

## 16. 审批时需确认

批准本方案时需同时确认：

1. 使用固定 8 样本结构和 §9.5 的既有阈值；
2. 样本只允许自制/公开授权、非客户、非内部资料；
3. 允许在 Windows 生产 GPU 主机创建隔离 run、资格 venv、wheelhouse 和固定模型缓存；
4. 允许通过既有两个代理 Secret 下载 wheel 与固定模型；
5. 允许在 BGE 空闲时运行真实 CUDA FP16，但不制造 BGE 并发压测；
6. 允许失败时保留审计 artifact，不自动删除；
7. 同意 R3 PASS 后仍需另批 production admission，不自动开放。

建议审批语句：

```text
批准 faster-whisper R3 统一资格验证实施方案；按固定 8 个非敏感样本和既有阈值，
完成仓库工具、测试、PR、CI、合并及一次 production-asr 资格 workflow。
允许隔离依赖安装、固定模型下载和真实 CUDA FP16 推理；不修改现有生产 ASR/GPU
服务、Ubuntu 配置、防火墙、数据库、Qdrant 或 Profile admission。失败按方案自动
停止并保留审计，R3 通过后仍不自动启用 faster-whisper。
```
