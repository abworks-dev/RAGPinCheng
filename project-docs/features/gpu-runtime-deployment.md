# GPU运行时部署

## 当前状态

状态：部分实现。

仓库已经具备D盘隔离依赖解析、运行时构建、CUDA候选验证、不可变release promotion和任务回滚代码，但 `gpu_service/runtime-lock.json` 当前为 `unvalidated`，`runtime-lock.txt` 为空。自动生产部署仍被外部禁用，尚未执行R3-2B生产候选解析与验证或恢复GPU服务。

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

CI成功
→ deploy-production.yml（仓库变量门禁）
→ deploy-gpu.ps1
→ 只复用已验证的候选release
→ promote-gpu-runtime.ps1
→ release内的start-gpu-service.ps1
→ /health + 5次embedding + 5次rerank
→ deploy-app.sh
```

普通提交若GPU源码指纹和依赖锁均未变化，GPU job只验证当前服务健康，并通过 `/model-info` 核对正在监听的进程确实报告相同release ID、源码指纹、锁哈希和CUDA设备；它不安装依赖、不重建release、不重启任务。GPU源码或锁发生变化时，只有状态为 `validated` 且绑定资格run、源码指纹和锁哈希的完整锁才能进入自动promotion。

## 运行时目录

生产候选和release只能位于：

```text
${PRODUCTION_REPO_PATH}\runtime\
├── pip-cache\
├── releases\<source-prefix>-<lock-prefix>\
│   ├── venv\
│   ├── wheelhouse\
│   ├── model-cache\
│   ├── source\
│   │   ├── gpu_service\
│   │   └── scripts\
│   ├── runtime-manifest.json
│   ├── qualification.json
│   ├── source-files.sha256.json
│   └── wheelhouse.sha256.json
└── current-release.json
```

构建过程禁止 `--system-site-packages`，不修改 `C:\Program Files\Python310` 的全局site-packages。完整requirements必须全部使用 `name==version`；wheel和离线安装均使用 `--no-deps`，再由 `pip check` 证明锁内已经包含完整依赖闭包。构建先生成本地wheelhouse和SHA-256清单，再离线安装到release venv。受GPU源码指纹保护的服务源码、启动包装和诊断脚本会复制进release并生成文件清单；资格验证、生产启动和回滚均从该快照运行，不依赖部署后可变的仓库工作树。requirements证据哈希先统一换行为LF，避免Windows工作树换行影响跨环境证据。

## 资格门禁

候选必须在与生产任务一致的 `Administrator` / S4U / Highest上下文中完成：

- CUDA可用；
- BGE-M3 CUDA FP16加载和真实推理；
- reranker CUDA FP16或CUDA FP32加载和真实推理；
- 不存在CPU fallback；
- 临时任务无论成功或失败都注销；
- 资格结果绑定workflow run ID、源码指纹和依赖锁SHA-256。

`validation_status` 状态流为：

```text
unvalidated → candidate → validated
```

`unvalidated` 不可构建，`candidate` 只能由手动资格workflow验证，`validated` 只能复用已经存在且证据完全匹配的候选release进行promotion；自动部署不会现场补做qualification。

`runtime-lock.json` 的 `allowed_reranker_precisions` 不是可配置开关：精度白名单在 qualify、promote 和 start 中硬编码为CUDA `fp16`/`fp32`，构建脚本只校验该字段与硬编码集合完全一致，因此元数据无法放宽白名单，字段漂移会fail-closed。

### 候选锁解析

`resolve-gpu-runtime-candidate.yml` 只接受人工确认的手动触发，在GPU Runner上使用 `${PRODUCTION_REPO_PATH}\runtime\resolver\<run-id>-<attempt>` 下的隔离venv、TEMP、pip cache和输出。解析约束固定为CUDA `torch==2.7.0+cu128`、`FlagEmbedding>=1.3,<2`、`transformers>=4.47,<5`、`tokenizers>=0.21,<0.22`以及现有FastAPI运行依赖；已证明崩溃的 `transformers==4.46.3` / `tokenizers==0.20.3` 不具备候选资格。Torch单独从已批准的官方cu128索引解析，其域名不经过已证明会产生TLS EOF的部署代理，但仍执行正常TLS证书校验；其余完整依赖闭包继续通过部署代理从已批准的清华镜像解析，禁止 `trusted-host` 或关闭证书校验。

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
- 应用部署必须同时验证GPU health和model-info契约；
- 候选workflow不promotion、不修改全局包、不注册生产任务；
- 候选解析workflow不加载或下载模型、不直接写正式锁，只产出待人工复核的证据artifact；
- 当前文档不表示生产GPU服务已经恢复。

## 验证入口

- `pytest gpu_service/tests/test_contract.py`
- `pytest tests/test_gpu_runtime_deployment_static.py tests/test_deploy_git_safety.py`
- `pytest tests/test_asr_deployment_static.py`
- PowerShell AST解析：`scripts/*gpu-runtime*.ps1`、`scripts/deploy-gpu.ps1`、`scripts/start-gpu-service.ps1`
- YAML解析：`.github/workflows/*.yml`
