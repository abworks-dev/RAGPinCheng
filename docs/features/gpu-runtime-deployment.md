# GPU运行时部署

## 当前状态

状态：部分实现；runtime lock 已完成 qualification，生产 promotion/实时服务状态仍以受控 workflow 和 capabilities 为准。

仓库已经具备隔离依赖解析、运行时构建、CUDA qualification、不可变 release promotion 和任务回滚代码。当前 `services/gpu_service/runtime-lock.json` 已绑定 validated qualification、源码 fingerprint、lock hash 和 Torch wheel hash；任何新的源码或依赖变化仍必须重新 qualification，不能由部署现场补做。生产部署使用带显式确认、完整 SHA、备份和实时 capabilities 检查的手动入口，不由 CI 完成事件自动创建生产运行记录。

早期候选锁曾因 Transformers/Tokenizers 约束不兼容而在资格阶段失败；该失败记录只用于解释当前锁为何固定版本。当前锁的有效性以 `runtime-lock.json`、最新 qualification workflow 和 release manifest 为准，不要复用本段历史 run ID 或旧版本组合。

## 入口与调用链

```text
手动候选解析workflow
→ Python 3.10、D盘空间、模型缓存、生产任务/8100前检
→ D盘run-local venv解析固定候选约束并执行pip check
→ 上传精确依赖锁、freeze、resolver report和脱敏preflight
→ 人工复核后将锁以candidate状态提交

手动候选资格workflow
→ GPU源码指纹 + LF规范化requirements SHA-256
→ build-gpu-runtime.ps1
→ qualify-gpu-runtime.ps1（CUDA FP16/FP32，不允许CPU）
→ 将run ID、候选commit、源码指纹和锁哈希回填为validated元数据

手动 workflow_dispatch + 完整 master SHA + DEPLOY
→ deploy-production-emergency.yml（生产确认与 Git 前检门禁）
→ deploy-gpu.ps1
→ 只复用已验证的候选release
→ promote-gpu-runtime.ps1
→ release内的start-gpu-service.ps1
→ /health + 5次embedding + 5次rerank
→ deploy-app.sh
→ cleanup-production.yml（仅复用清理）

手动与定时生产清理
→ cleanup-production-operations.yml
→ cleanup-production.yml（复用清理）
```

统一清理器将 GPU runtime 原始审计写入 `runtime/cleanup-audit/`，满足 runtime 清理器的精确根目录约束；workflow artifact 使用该审计的只读副本，不会放宽 `AuditPath` 的路径边界。

手动部署所选提交若GPU源码指纹和依赖锁均未变化，GPU job只验证当前服务健康，并通过 `/model-info` 核对正在监听的进程确实报告相同release ID、源码指纹、锁哈希和CUDA设备；它不安装依赖、不重建release、不重启任务。GPU源码或锁发生变化时，只有状态为 `validated` 且绑定资格run、源码指纹和锁哈希的完整锁才能进入promotion。

## 运行时目录

生产候选和release只能位于：

```text
${PRODUCTION_RUNTIME_ROOT}\
├── pip-cache\
├── wheel-seed\
│   └── torch-2.7.0-cu128-cp310-win_amd64\
│       ├── torch-2.7.0+cu128-cp310-cp310-win_amd64.whl
│       └── manifest.json
├── releases\<source-prefix>-<lock-prefix>\
│   ├── venv\
│   ├── wheelhouse\
│   ├── model-cache\
│   ├── source\
│   │   ├── services\gpu_service\
│   │   └── scripts\
│   ├── runtime-manifest.json
│   ├── qualification.json
│   ├── source-files.sha256.json
│   └── wheelhouse.sha256.json
└── current-release.json
```

构建过程禁止 `--system-site-packages`，不修改 `${PRODUCTION_PYTHON_ROOT}` 的全局site-packages。完整requirements必须全部使用 `name==version`；wheel和离线安装均使用 `--no-deps`，再由 `pip check` 证明锁内已经包含完整依赖闭包。构建先生成本地wheelhouse和SHA-256清单，再离线安装到release venv。受GPU源码指纹保护的服务源码、启动包装和诊断脚本会复制进release并生成文件清单；资格验证、生产启动和回滚均从该快照运行，不依赖部署后可变的仓库工作树。requirements证据哈希先统一换行为LF，避免Windows工作树换行影响跨环境证据。

### 手动 Torch wheel 输入

由于 Helios 经部署代理访问官方 PyTorch wheel 重定向域名会出现 TLS EOF，R3-2B 使用人工下载的单一 CUDA Torch wheel 作为受控输入。管理员在可直连官方站点的电脑下载精确文件 `torch-2.7.0+cu128-cp310-cp310-win_amd64.whl`，运行 `scripts/new-gpu-torch-wheel-seed-manifest.ps1` 生成同目录 `manifest.json`，再将两个文件传入 `${PRODUCTION_RUNTIME_ROOT}\wheel-seed\torch-2.7.0-cu128-cp310-win_amd64\`。校验器固化PyTorch官方索引公布的SHA-256 `c52c4b869742f00b12cb34521d1381be6119fa46244791704b00cc4a3cb06850`；Helios只接受精确文件集合、Python 3.10/`win_amd64`标签、官方cu128索引标识、文件长度和发布者哈希均匹配的目录，校验失败不会安装。

候选解析器以 `--no-index --no-deps` 从该 wheel 安装 Torch，其他依赖仍从批准的清华镜像解析；资格构建将同一已验证 wheel 复制进 release wheelhouse，只对非 Torch 锁项联网构建，之后完全离线安装并执行 `pip check`。resolver report、runtime manifest、qualification evidence 和 wheel 清单均绑定 Torch SHA-256；未变化部署、promotion 与启动也要求相同哈希。wheel 不提交仓库、不上传 GitHub Artifact，validated release 复用时不要求重新提供 seed 目录。

## 资格门禁

候选必须在与生产任务一致的 `Administrator` / S4U / Highest上下文中完成：

- CUDA可用；
- BGE-M3 CUDA FP16加载和真实推理；
- reranker CUDA FP16与CUDA FP32**两种精度都**加载并完成真实推理；
- 不存在CPU fallback；
- 临时任务无论成功或失败都注销；
- 资格结果绑定workflow run ID、源码指纹和依赖锁SHA-256。

`qualify-gpu-runtime.ps1` 逐个执行 `allowed_reranker_precisions` 中的每种精度，不再在第一种成功后 `break`；`qualification.json` 以 `requested_precisions` 和 `qualified_precisions` 分别记录请求集合与实际到达 `stage=complete` 的集合。只有两者完全覆盖才判定 `qualified`，否则抛出并列出失败精度。`reranker_precision` 保持标量并确定性优先 `fp16`：`promote-gpu-runtime.ps1` 与 `start-gpu-service.ps1` 按单值消费该字段（start 由 `-eq "fp16"` 推导 `RERANKER_USE_FP16`），改成数组会让这些比较静默失真。

`validation_status` 状态流为：

```text
unvalidated → candidate → validated
```

`unvalidated` 不可构建，`candidate` 只能由手动资格workflow验证，`validated` 只能导入唯一且证据完全匹配的 `runtime\qualification\<run>\releases\<release-id>`，materialize 到确定性的 `runtime\releases\<release-id>` 后再进行promotion；零个或多个匹配均fail-closed，生产部署不会现场补做qualification。

`runtime-lock.json` 的 `allowed_reranker_precisions` 不是可配置开关：精度白名单在 qualify、promote 和 start 中硬编码为CUDA `fp16`/`fp32`，构建脚本只校验该字段与硬编码集合完全一致，因此元数据无法放宽白名单，字段漂移会fail-closed。

### 候选锁解析

`resolve-gpu-runtime-candidate.yml` 只接受人工确认的手动触发，在GPU Runner上使用 `${PRODUCTION_RUNTIME_ROOT}\resolver\<run-id>-<attempt>` 下的隔离venv、TEMP、pip cache和输出。解析约束固定为CUDA `torch==2.7.0+cu128`、`FlagEmbedding==1.4.0`、`transformers>=4.56,<5`、`tokenizers>=0.22,<0.23`以及现有FastAPI运行依赖；解析后还会逐项断言这三个版本落在批准范围内，并拒绝已证明崩溃的 `transformers==4.46.3` / `tokenizers==0.20.3` 与资格失败的 `transformers==4.55.4` / `tokenizers==0.21.4`。FlagEmbedding固定为精确版本，因为结论只针对1.4.0的加载调用；另一个1.x可能改变该调用，届时测试的就不是本次确认过的组合。`tokenizers` 上界必须与 `transformers` 自身的pin一致，否则会重现4.55.4封顶。Torch不再从 Helios 联网解析，而是只接受上节所述的官方 wheel seed；其余完整依赖闭包继续通过部署代理从已批准的清华镜像解析，禁止 `trusted-host` 或关闭证书校验。

模型缓存源优先使用已配置的 `GPU_MODEL_CACHE_SOURCE`；未配置时，只在仓库既有缓存位置和 `C:\Users\<profile>\.cache\huggingface` 的有限候选集中寻找同时包含BGE-M3与reranker完整离线快照的唯一根目录。零个或多个匹配均fail-closed，不读取 `.env`、不递归搜索整盘，也不下载或修改模型。资格成功后workflow上传manifest、qualification、源码与wheel清单及freeze，供validated元数据提交前独立复核。

解析器不导入或加载模型，显式设置Hugging Face/Transformers离线模式，不修改全局Python，不使用 `--system-site-packages`，不写仓库中的正式锁。它只上传由索引包组成的完整 `name==version` 闭包、`pip check` 结果状态、固定约束和脱敏前检；原始pip日志保留在run-local目录且不上传，失败时只将经过URL凭据、敏感字段、本机路径和长度过滤的有限日志尾部写入workflow日志。上传的锁仍须人工检查后才可作为 `candidate` 提交，解析成功本身不等于CUDA资格通过。

### R3-2B 前置条件

依赖解析器与 `qualify-gpu-runtime.ps1` 都在 TCP 8100 处于Listen或 `RAGPinCheng-GPU` 任务存在时拒绝执行，以避免候选工作与生产争用同一主机资源。它们不会自行停止服务或修改任务；若前检发现任一对象存在，必须按独立生产操作审批处理。

## 回滚

promotion前备份当前任务XML、GPU环境文件和release指针。新release健康检查或任一embedding/rerank冒烟失败时，删除新任务、恢复旧环境、release指针与旧任务并尝试恢复健康；原文件不存在时恢复为不存在。任务动作必须指向受管D盘release内的启动包装，任务和监听进程必须先通过所有权检查，不操作旧式或其他不符合预期的任务及8100监听进程。

首次恢复前尚无已知健康release，因此首次promotion失败最多恢复到当前离线状态；建立第一个健康release后，后续promotion才具备完整在线回滚目标。

## 不变量与边界

- embedding模型仍为 `BAAI/bge-m3`，维度仍为1024，不需要索引Reset；
- reranker仍为 `BAAI/bge-reranker-v2-m3`，不得静默禁用或切换CPU；
- `/health` 在模型未加载时返回HTTP 503；既有ASR资格脚本（`qualify-faster-whisper-production.ps1`、`qualify-qwen3-asr-production.ps1`、`funasr_phase0/07_verify_bge.ps1`）用 `Invoke-RestMethod`/`Invoke-WebRequest` 读该端点，模型未加载时会抛HTTP异常而非命中脚本自有报错；均仍fail-closed，`src/providers.py` 只消费 `/model-info` 不受影响；
- `/v1/system-metrics` 使用现有 `GPU_SERVICE_TOKEN` 鉴权，只执行固定参数、2 秒超时的 `nvidia-smi` 查询并返回脱敏资源摘要；该管理指标探测不参与 embedding/rerank 健康门禁，失败时不影响推理请求；
- 应用部署必须同时验证GPU health和model-info契约；
- 候选workflow不promotion、不修改全局包、不注册生产任务；
- 候选解析workflow不加载或下载模型、不直接写正式锁，只产出待人工复核的证据artifact；
- 当前文档不表示生产GPU服务已经恢复。

## 显式放弃旧版本回滚的手动清理

`cleanup-production-current-only.yml` 是独立的手动维护入口，不替代现有保留回滚能力的清理策略，也不定期执行。必须先批准具体生产范围并明确接受无备份、旧版本不可回滚，再使用已合并的完整 master SHA 和当前 ASR candidate/GPU release 身份执行 preview。

preview 只生成目录元数据清单和审计记录，不删除文件。apply 必须绑定同一实现的成功 preview run、包含 attempt 的精确 artifact 名称及清单原始字节 SHA-256，并显式确认 `confirm_no_backup`。脚本重新发现候选并验证当前版本指针、清单、进程/计划任务引用和目录快照；身份变化、路径注入、目录漂移、重解析点或服务不健康均拒绝继续。健康探测使用生产声明的服务地址；除回环地址外，仅接受经本机网卡地址清单确认的 IPv4，固定 ASR 8200/GPU 8100 和无凭据 `/health`，拒绝远端地址、DNS、查询参数和重定向。workflow 另行输出明确标为 `loopback-only` 的回环绑定证据；未绑定回环不等同于声明地址上的服务不健康。

preview 审计中的 `skipped` 最多保留 200 条已识别历史目录的保留原因，超额数量记入 `skipped_dropped_count`。只输出受限目录元数据和固定保护类别，不输出原始异常、进程/任务参数或配置内容；虚拟环境结构观察不构成删除授权。这些诊断不进入批准清单，不改变候选选择或 apply 校验。

对于非当前 `candidate-<数字>`，除既有构建载荷外，只额外识别 `shared-wheel-seed`、`qualified-faster-whisper-wheel-seed`、`qualified-whisperx-wheel-seed` 三种部署脚本生成的 wheel 副本目录。必须通过原有载荷保护检查，且目录非空、无子目录、仅包含普通 `.whl` 文件；未知内容保留。所属 candidate 目录的活跃标记和进程/计划任务引用也在每次删除前重新检查；不会清理当前 candidate、全局 wheel 缓存或因目录名保护而保留的历史虚拟环境。

清理只选择可识别的非当前版本构建载荷；模型、配置、资格目录、历史状态、报告、审计和未知用途目录保留。历史审计中的旧版本身份不会被删除或改写来绕过保护。删除前和每个对象之后保存审计，结束后复查 ASR/GPU 健康；失败可能已产生部分不可逆删除，应以审计中的 `deleted` 和 `pending` 为准，不能假设可恢复。代码入口的存在不代表已经在生产执行或释放了磁盘空间。

### 保留目录的独立元数据观察

`diagnose-retained-production-layout.yml` 是单独的手动只读入口，调用 `scripts/diagnose-retained-production-layout.ps1`，观察已有审计限定的 31 个目录位置。它不调用清理入口或发布清单校验器，不读取任何载荷文件内容，不读取配置、包清单、业务数据或模型，也不探测、启动或重启服务。报告只写入 runner 临时目录，使用 CreateNew 拒绝覆盖。

观察器逐层枚举目录元数据，遇到保护目录即停止该分支，不进入其中。固定分类 `source_shaped` 仅指程序目录下的 `src/transcription` 边界，`package_shaped_unverified` 仅指虚拟环境 `Lib/site-packages` 下的保护边界，其他位置计入 `unknown`；不输出后代名称、路径或原始异常。分类只说明布局，不证明文件属于公开代码、可信包或无业务数据，不能作为删除授权。所有行始终 `retained=true`，现有候选和删除规则不变。

缺失、重解析点、访问错误、条目/深度/时间预算耗尽均保留并明确标记；时间预算是协作式检查，外层 workflow 另有 20 分钟上限。预期身份只用于排除冲突目录，不校验实际线上身份。此入口存在不表示已在生产运行，也不表示剩余历史环境已完成分类或允许删除。

### 明确批准的 31 项整目录清理例外

`cleanup-production-exact31.yml` / `scripts/cleanup-production-exact31.ps1` 是独立、仅手动的 R3 入口；必须另行明确批准已列出的 31 项整目录及其内部原受通用名称保护的内容，并接受无备份、无旧版本回滚。它不修改前述通用清理或只读观察器的规则。范围固定为脚本列出的 14 个旧 ASR release 的 `app`/`venv`、一个旧 GPU release 的 `venv`、两个旧 resolver 的 `venv`，不删除父目录、其他版本、父级审计、模型或配置目录。生产 workflow 将三个根目录固定校验为已批准的 D 盘目录，不允许变量改值重定向删除范围。

Preview 只读取四份明确的当前公共身份 JSON 和目录元数据，既不调用会校验应用文件内容的发布验证器，也不读取载荷、`.env`、包 RECORD、客户资料或模型文件。固定 31 项全部进入清单，缺失项明确记为无操作；隐藏文件与子目录的元数据也进入快照，不输出后代名称。当前 ASR/GPU 身份与公共清单、配置模型/资格路径闭包、进程和所有计划任务（包括禁用任务）、所属版本的引用和活跃标记均仍受保护。任务参数中的旧 candidate/release 身份令牌和配置路径间接引用也阻止删除；任何重解析点、扫描错误、超过 600 GiB 上限或不健康均停止。

Apply 仅接受同一已合并 master 实现、同一入口的**成功 preview attempt**、精确 attempt artifact 及清单原始 SHA-256，必须显式确认无备份。执行前完整重新发现；逐项重新检查身份、引用、标记及快照，持久保存 `pending` 后再次检查才删除。审计更新采用同目录 CreateNew 临时文件、Flush 和原子替换，替换失败保留上次有效审计。完成必须验证当前身份未变、固定 31 项全部不存在及 ASR/GPU 健康；失败可能已部分不可逆，必须检查 `deleted`、`pending`、`absent_after` / `remaining_after`，不能假设可恢复。此能力的源码存在不表示已合并、生产已执行或当前磁盘空间已变化。

## 验证入口

- `pytest services/gpu_service/tests/test_contract.py`
- `pytest tests/test_gpu_runtime_deployment_static.py tests/test_deploy_git_safety.py`
- `pytest tests/test_asr_deployment_static.py`
- PowerShell AST解析：`scripts/*gpu-runtime*.ps1`、`scripts/deploy-gpu.ps1`、`scripts/start-gpu-service.ps1`
- YAML解析：`.github/workflows/*.yml`
