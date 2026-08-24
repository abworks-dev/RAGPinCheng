# 视频转录链路

- 状态：人工上传/播放链路与多引擎 Phase 5A/5B 应用闭环已实现；faster-whisper 与 WhisperX R3 已通过，生产 ASR 已准入 SenseVoice、faster-whisper 与 WhisperX；WhisperX 服务注册固定使用已资格的 `full-decode`
- 最后核对：2026-08-20

## 用户可观察能力

教学视频转录稿可以被索引和检索，回答能够显示带时间戳的视频引用；点击引用定位到来源卡片，引用悬浮卡和来源详情中的独立播放按钮可打开视频播放器并跳到引用时间点。系统管理员从资料管理的统一上传入口选择 MP4；视频立即以“待转录”状态进入资料列表，不在上传阶段创建任务。管理员可对单个视频、选中视频、最近上传批次或当前目录及全部子目录批量选择转录方案并启动任务，视频随后进入同页的“转录任务”子页签；该页继续提供转写工作台、取消/恢复、审核、发布、替换、归档和失败记录处理。

系统管理员可在 `/admin/asr` 的“转录配置”页比较三个服务器固定的 WhisperX v2 分段 Profile，查看固定工程词、Prompt 资产和应用/服务配置哈希，并提交只写审计记录的发布申请。页面不接受自由 Prompt、模型路径或任意解码参数；发布申请不持有部署凭据，也不直接触发生产 workflow。

Phase 5A/5B 已接通版本列表、Markdown 校对与渲染预览、人工审核、显式发布、候选索引与正式 head 检索过滤。校对保存始终创建新的受管人工修订稿，不覆盖 ASR 或历史版本；新稿必须重新审核并在候选索引成功后才能替换正式 head。转录成功、审核通过、发布中和正式检索可见仍是独立状态。真实 ASR/GPU/Qdrant 资格与生产准入状态只以当前 deployment workflow、capabilities 和本文件的“当前边界”核对，不以历史 run 数字推断。

已发布的视频转录稿会通过 `content_items(content_kind=media_transcript)` 目录壳进入受管资料库，但不替代本链路。视频原件、转录版本、审核发布和正式 head 仍以 `media_assets`、`transcript_versions`、`transcript_publication_index_jobs` 与 `media_transcript_heads` 为唯一权威；目录壳不复制文件、版本、发布或索引记录，普通资料的 `content_item_heads` 仍是另一条独立可见性边界。

## 当前边界

### 已实现

- `教学视频/` 分类下 `.md` 被识别为 `doc_type=”transcript”`；
- 按”说话人 + HH:MM:SS”解析发言段落；
- Parent 和 Child 携带 `start_time` 和 `media_id`；
- `doc_type`、`start_time`、`media_id` 经检索、生成、`SourceDTO` 和前端完整传递；
- 前端显示 `🎬 @HH:MM:SS` 并能定位来源卡片；
- 后台管理端上传 MP4 + Markdown 转录稿：校验文件格式、大小、编码和时间戳标记；
- 视频文件落盘到 `media/<media_id>/original.mp4`，legacy 人工转录稿落盘到 `DOCS_DIR/教学视频/`（本地默认 `content/legacy-docs/教学视频/`）；
- 自动建立 `media_assets` 登记、索引入队、索引完成后状态自动更新为 `ready`；
- 后端鉴权 HTTP Range 播放（支持无 Range、普通 Range、开放式 Range、后缀 Range）；
- 前端单实例播放器抽屉（桌面右侧、移动端底部弹层），支持 metadata seek 和自动播放降级；
- 播放器下方提供交互式转录稿，按播放进度高亮当前分段并自动跟随；点击分段可跳转，用户主动滚动时暂停跟随并可一键回到当前进度；
- 资料管理页的视频转写行在存在唯一 `media_id` 时复用同一播放器抽屉，从视频起点打开上方视频与下方转写稿；
- 正式 head 切换成功时在同一 SQLite 事务中创建或更新视频目录壳；历史正式视频由 Schema 16 幂等回填到 `05 培训资料`，之后可由具备发布权限的人员只调整资料库目录；
- 资料库列出未归档媒体目录壳；没有转录版本时显示“待转录”，转录运行中、转录完成待审核或审核退回时统一显示“转录中”，审核通过后显示“待发布”，发布任务运行/失败分别显示“发布中/发布失败”，候选索引成功并完成正式 head 原子切换后才显示“已发布”。有正式 head 后仍只展示当前正式版本；较新的待审核或待发布稿不会替换旧条目，只显示待处理提醒；媒体归档后条目从资料库和分类计数中消失；
- 普通登录用户可通过只读接口读取当前已发布转录版本；无版本头的既有人工上传媒体兼容读取其已登记、受控且完成索引的人工稿；
- 具备 `media_id` 的引用在悬浮卡中显示独立播放按钮，并跳转对应时间点；
- 来源卡片显示”从 HH:MM:SS 播放”按钮；
- 旧会话和未关联转录正常降级（无播放按钮，不报错）；
- 资料管理的文件选择、拖放和文件夹上传对系统管理员接受 MP4；混合批次复用目录、同名冲突、上传进度与任务明细流程，上传时不要求转录方案；普通账号不会获得 MP4 入口，后端也返回 403；
- 新视频上传成功后创建 `media_assets` 和 `content_items(content_kind=media_transcript)` 目录壳，状态为 `awaiting_transcription`，不会创建 `transcription_jobs`、普通 `content_versions` 或 `content_index_jobs`；管理员选择方案后才创建 `transcription_jobs`，视频与后续转录稿始终通过 `media_id` 绑定；
- “转录任务”位于 `/admin/content?view=transcription`，复用原媒体工作台但隐藏旧上传向导；旧 `/admin/media` 保留为带查询参数的兼容重定向；
- 旧式人工 MP4+Markdown API 保持兼容；统一上传入口先创建待转录媒体目录壳，只有管理员在资料列表选择方案后才创建自动转录任务，experimental Profile 强制审核策略不由前端放宽；
- 媒体列表分别展示媒体、转录、审核、发布和索引状态，并保留快捷筛选与转写版本工作台；资料库视频状态投影为“待转录、转录中、转录失败、待发布、发布中、发布失败、已发布”，播放和下载在正式 head 产生前禁用。审核通过后必须从转录任务页显式点击发布，发布任务完成后资料列表才进入“已发布”。
- 转写工作台支持桌面双栏 Markdown 编辑/渲染预览，移动端使用“编辑/预览”切换；关闭、收起或切换版本前会保护未保存内容；不启用 raw HTML、自动保存或 WYSIWYG。
- 管理员在转写工作台展开“校对内容”后可同时查看视频和指定转录版本的同步时间轴；点击时间戳跳转视频，播放时自动高亮当前段落，时间轴支持自动跟随和手动暂停跟随。
- 管理员显式“保存为新草稿”时，服务端统一 LF、校验 UTF-8 编码后的 2 MiB 上限、说话人时间戳和非空正文，并以基础版本 SHA-256 与请求幂等键防止并发误写。
- 修订稿登记为 `source=manual`、`markdown_storage_kind=managed_artifact`，记录基础版本、编辑人和保存幂等键，审核状态重置为 `awaiting_review`；legacy 人工上传稿仍保持独立且不能通过受管发布流程发布。
- WhisperX v2 提供自然、均衡和细分三个只读 Profile：自然分段不强制时长，均衡/细分最长分别为 30/15 秒，字符上限分别为 500/240/120，短段合并间隔分别为 1000/750/500 ms；超限段按换行、句末标点、逗号、空格和字符边界确定性切分，段内时间按字符比例计算。
- v2 固定工程词包括 `Revit`、`Navisworks`、`AutoCAD`、`BIM`、`BIM-2026-0805`、`12.5`、`208`、`95%`；`Auto CAD`、`B I M`、大小写、标准编号空格/连字符、小数和百分号仅按明确模式做确定性校正，不做模糊替换。
- schema 17 添加 `asr_profile_release_requests` 与 `asr_profile_audit_events`。读取要求管理员，创建要求管理员 CSRF、UUID 幂等键、实时 capability 和受认证 `/v1/profile-identities` 服务配置哈希匹配；申请与审计在同一 SQLite 事务写入。
- 管理端分别读取 capability、运行 diagnostics 与 Profile identity：capability 决定转录服务是否可用，diagnostics 缺失只降级运行状态，identity 缺失只禁用发布申请并提示兼容升级，不得把辅助发布校验失败误报为整个转录服务不可用。发布写接口继续要求实时 identity 与配置哈希匹配并失败关闭。

### 未实现（第二阶段）

- 视频字幕轨道生成与同步；

### 已接线：多引擎转录 Phase 2～5B

- Phase 2 的 `transcription_jobs`、`transcript_versions`、artifact refs、publication-only index jobs 和正式版本 head 已由应用层 Store/服务使用；
- Phase 3 remote Provider 仍只返回严格 `ProviderCandidate | ProviderFailure`，由 `pipeline.py` 独占 normalizer/Canonical 结果流；
- 管理端单 MP4 + `profile_id` 上传、任务状态/取消/恢复已接入应用 API 和后台 worker；人工 MP4+Markdown 路径保持独立；
- 管理端按媒体 lazy 加载版本历史，可校对并渲染预览不可变版本的 Markdown、将修改保存为新的受管人工修订、提交审核备注、批准/拒绝并显式发布；legacy 人工版本不提供可用的受管发布动作；
- 管理端媒体列表使用真实审核枚举计算唯一当前阶段，独立展示索引状态，并提供处理中、待审核、发布处理中和失败快捷筛选；筛选暂时只作用于最近加载的 100 条；
- 媒体列表 API 同时返回 `current_phase`、`available_actions` 和逐动作 `disabled_actions` 原因。运行中的任务可取消；失败或取消且非永久失败的任务可重试；已发布且没有待处理替换的媒体可替换或归档；只有从未创建过转录任务的失败上传可完整删除。发布索引处理中不会重复开放发布动作，前端以服务端能力为准。
- Remote Provider 的服务请求身份绑定应用任务、媒体与执行指纹；同一应用任务网络重试保持稳定，同一媒体新建应用重试任务生成新身份；
- 转录任务 API 返回安全的结构化失败 `code/message/retryable`；服务请求身份冲突与契约不匹配分别处理，前端不再直接展示 Provider 技术摘要；
- publication adapter 只走 `chunk_document → store_parents → index_children`，不调用 purge、reset 或普通 `index_single`；
- Parent、Child 和 Qdrant payload 添加 nullable `transcript_version_id` / `publication_target_id`，legacy stable ID 算法保持不变；
- 正式可见性唯一读取 `app.sqlite.media_transcript_heads.current_version_id`；Qdrant recall 和 Parent expansion 使用同一快照，损坏/缺失 head 对 versioned transcript fail closed，legacy/普通文档继续可见；
- `content_items` 对视频只承担目录放置；不会建立对应 `content_versions`、`content_publications`、`content_index_jobs`、`content_item_heads` 或 `content_objects`，因此资料库集成不触发二次索引；
- 普通索引与 publication 索引共用现有单 worker/单队列，publication job 支持幂等恢复与失败状态持久化。
- 静态 Profile catalog 保留 qualification 基线：experimental SenseVoice 为 enabled，
  faster-whisper、Qwen3-ASR 和 WhisperX 为 disabled。Phase 4 应用组装层通过严格的
  `TRANSCRIPTION_ADMITTED_PROFILE_IDS` allowlist 覆盖业务准入，不改变引擎运行时身份；
  当前生产应用的准入由受控 deployment workflow 持久化并以实时 capabilities 复核：
  SenseVoice、faster-whisper 与 WhisperX 已准入，Qwen3-ASR 保持关闭。四者复用同一
  Remote Provider 与唯一 Candidate → Canonical 结果流；
- 管理端转录方案以 `scheme_id` 作为用户选择和幂等身份，通过受控 Base 映射到固定运行 Profile；任务同时保存实际 `profile_id`、原始 `scheme_id` 与不可变参数快照。WhisperX 自然、均衡、精细及基于 `whisperx-v2` 创建的自定义方案统一使用已准入的均衡运行 Profile，再由快照覆盖分段参数，因此新增自定义方案无需修改环境 allowlist。SenseVoice 与 faster-whisper 方案采用相同映射原则；Qwen3 Base 保持禁用，未知、禁用或归档项失败关闭。重试复用原任务运行配置和方案快照，不读取方案当前状态。
- 静态目录同时保留三个 `qualification_approved` WhisperX v2 Profile，默认均为 disabled；只有实时服务暴露 `whisperx-large-v3-zh-align-v2` 且运行身份哈希与仓库固定配置一致时，管理页才允许创建发布申请。`/v1/diagnostics` 继续只返回有界运行状态，不承载 Prompt 或配置身份。标准应用部署动作只准入作为固定运行身份的均衡 Profile。
- ASR service 注册四个固定 service Profile；faster-whisper、Qwen3-ASR 或 WhisperX
  缓存/依赖缺失时仅相应 Profile 不可用，不阻止现有 SenseVoice 服务启动；
- Windows ASR candidate promotion 的 Ubuntu 跨节点门禁严格验证 `/health`、`/v1/capabilities`、`/v1/diagnostics` 与 `/v1/profile-identities`；管理页依赖的身份契约不得只由基础 health 或 capability 代替。
- faster-whisper adapter 固定
  `dropbox-dash/faster-whisper-large-v3-turbo@0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`
  与 CUDA FP16 参数；生产资格只接受
  `${PRODUCTION_ASR_DATA_ROOT}\models` 下已持久化且通过完整 Manifest、文件集合、
  大小和 SHA-256 校验的本地制品，不在资格运行中访问 Hugging Face。首次填充或
  恢复模型由独立手动 workflow 完成；准备入口使用单一 TLS 1.2 Session 按固定
  7 文件清单顺序流式下载，限制重定向到 Hugging Face 官方 HTTPS 域，保留
  `.partial` 供同一 run 重试续传，并在原子发布前完成身份校验。生产应用 admission
  仅由应用 allowlist 控制，不修改该静态资格基线。
- Qwen3-ASR adapter 固定
  `Qwen/Qwen3-ASR-0.6B@5eb144179a02acc5e5ba31e748d22b0cf3e303b0`
  与
  `Qwen/Qwen3-ForcedAligner-0.6B@c7cbfc2048c462b0d63a45797104fc9db3ad62b7`，
  仅允许 Windows Transformers/CUDA BF16 候选参数。服务默认继续强制
  `Chinese`；资格脚本可显式启用固定 `auto-zh-en` 候选，模型输出必须包含
  Chinese 且语言集合只能由 Chinese/English 组成，Canonical 语言仍为 `zh-CN`。
  未知策略、English-only 或其他语言均失败关闭，application Profile 保持 disabled。

> 以下资格 run、显存、样本和清理细节是历史审计摘要，不是当前运行状态或生产准入依据；当前状态以受控 workflow、capabilities 和 runtime manifest 为准。

SenseVoice 已完成独立生产短媒体验收。Qwen3-ASR 已具备统一 R3 仓库资格工具，
baseline R3 Run `31356827072` 已通过 Windows 隔离依赖、许可证、固定双模型、CUDA
BF16、临时服务、8 样本完整执行、确定性和清理，但未通过质量/性能门禁：RTF 为
`2.764423–4.035362`，术语召回为 `0.571429`，编号召回为 `0`，时间戳 P95 为
`2240 ms`。因此 Qwen 尚无正式资格结论。自动语言候选继续使用相同 8 样本和原阈值，
并额外输出不含音频或文本的模型调用耗时及端到端 RTF 汇总；该能力不代表候选已经
通过。自动语言候选 R3 Run `31360543685` 在许可证审计阶段因未分类的元数据异常
停止，未进入双模型准备、临时服务或 8 样本推理。许可证审计现在对 distribution
枚举、身份和许可证元数据异常分别失败关闭，始终先写结构化矩阵，并仅向脱敏诊断
暴露规范化包名、固定原因码、审计阶段和异常类型；异常消息、路径、URL 和许可证正文
不会进入 artifact。faster-whisper R3 Run `31486600594` 已在
`1b75034f679b415a65aad4182286408d6983f467` 上通过：固定 8 个非敏感样本全部通过，
canonical、Markdown 与 parser turns 两遍结果一致，固定模型 revision 和 wheel cache
身份均通过校验。该资格不自动开放应用 Profile，也不等于生产部署或生产流量验收。

WhisperX R3 Run `31889569116` 已在
`befa81db9df9333b0e44823208390d5dacc2d2bb` 上通过：固定 8 个非敏感样本的三组解码矩阵
选择 `full-decode`，原有质量、确定性、时间戳、资源和许可证门禁全部通过，峰值 GPU
显存为 `1323.55 MiB`，且资格过程未修改生产服务。该记录是 v1 `full-decode` 的历史资格
证据。v2 服务身份扩充固定工程词与版本化 Prompt，因此 runtime contract 已变化，必须在
同一目标 master revision 重新运行 faster-whisper 与 WhisperX R3，不能复用 v1 资格；
资格结论仍不替代 active release 的部署、promotion 与实时验收。

faster-whisper、Qwen3-ASR 和 WhisperX qualification 统一读取
`asr-qualification-corpus/1` 只读 manifest，并沿用已 PASS 的
`sample_set_id=self-made-faster-whisper-r3`。共享契约固定 8 个 WAV 的相对 POSIX 路径、
大小、SHA-256、时长、场景、参考标注、BIM 术语/标准编号和非敏感来源声明；严格拒绝
未知字段、路径越界、symlink/reparse point 及 WAV 身份或格式变化。中性变量
`PRODUCTION_ASR_QUALIFICATION_ROOT` 与
`PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH` 必须成对配置；qualification 解析和
workflow 只接受这两个中性变量，不再读取引擎专属语料变量。三引擎的运行环境、依赖、
模型、缓存、报告和 admission 仍相互隔离。workflow 的 manifest preflight 与真实
qualification 互斥，前者不安装依赖、不加载模型、不读取密钥或执行推理，只输出脱敏
语料身份。首次三引擎中性 preflight Run `31361349395`、`31361423431` 和
`31361505382` 已确认相同 manifest SHA-256、历史 sample set、annotation version、样本数
及八项 WAV 身份；这不构成任一引擎的 GPU qualification 或 Profile admission。

三引擎 qualification 的持久运行目录采用明确的 `runs\<run_id>` 布局。证据上传后，
workflow 对本次 run 生成收缩审计；只有任务成功且
`PRODUCTION_ASR_RUN_COMPACTION_ENABLED=true` 时，才删除 `venv`、`wheelhouse`、
`shared-wheel-seed`、`model-staging`、`spool`、`temp` 六类可重建重资产。失败 run 完整
保留 24 小时后才进入同一白名单收缩，`reports/evidence/logs/state/config` 始终保留。
已收缩 run 默认保留 30 天且至少保留最新 3 次；ASR 部署成功后可按精确 commit 收缩
对应 dependency run。周期清理与这些 workflow 共用生产 GPU 互斥组，默认只 DryRun。

生产部署采用引擎通用的两层身份：`runtime_contract_sha256` 是某一引擎运行闭包
（固定模型 revision 与明确源码文件的 Git blob 身份），`deployment_contract_sha256`
是部署/预检行为闭包。两者都不依赖 Windows 工作树的 CRLF 字节。旧 R3 只能在当前
部署的同引擎 runtime contract 完全一致时复用，资格记录的 source commit 仍须可审计；
不能再仅凭完整 master SHA 相等或不相等判断。faster-whisper 与 WhisperX 具备已启用的
生产 admission adapter；Qwen3-ASR 的通用契约仍被部署预检明确 fail closed。WhisperX
candidate 必须同时提交同一部署 revision 对应的 faster-whisper 与 WhisperX R3 身份，
不得用单引擎资格替换当前已准入的 faster-whisper 能力。

应用业务准入是独立于上述两层 GPU 身份的第三方应用配置，不进入
`runtime_contract_sha256`。默认 allowlist 仅启用 SenseVoice；生产 app-only workflow
可通过显式 `ENABLE_FASTER_WHISPER` 动作向 Compose 注入 `ASR_ENABLED=true` 以及
SenseVoice + faster-whisper allowlist，验证通过后以 `production-asr` GitHub environment
variables 持久化；部署或健康验证失败时使用运行前的 enable 状态与 allowlist 重建上一
backend 镜像。未知、重复或格式错误的 Profile ID 均失败关闭。该动作不修改生产 secret
env 文件、不重部署 Windows ASR、不自动开放其他引擎，也不绕过实时
capabilities/health 可用性检查。

手动 `Preflight ASR Production Deployment` workflow 按 engine 选择 faster-whisper 或
WhisperX evidence adapter，只在 Windows runner temp 下创建验证 wheelhouse 和 venv，
读取资格证据与模型缓存并验证在线解析、离线安装、`pip check`
和 runtime；它不修改生产 app/venv/config、Scheduled Task、服务、模型缓存或共享 wheel
cache，并输出包含两层 hash 的脱敏 artifact。预检结果目前不自动触发或授权部署。

显式启用 faster-whisper 生产准备时，部署代码绑定持久化 R3 verdict/diagnostic、资格
run ID、资格 commit、runtime contract、固定 wheel cache 和模型 Manifest。WhisperX
candidate 额外绑定 WhisperX verdict、wheel cache、ASR/中文 aligner 双模型 Manifest，
并固定使用兼容 WhisperX 3.8.6 的 Torch/Torchaudio 2.8 cu128 与 NumPy 2.x 组合环境；
faster-whisper 的新资格也必须绑定相同 Torch 基线。部署强制创建新的组合 candidate venv，
下载阶段必须保留两引擎全部已资格 wheel，安装阶段仅从完整 wheelhouse 离线安装，并验证
SenseVoice、faster-whisper 与 WhisperX 模块来源及模型缓存。三 Profile 顺序固定为
`faster-whisper-large-v3-turbo-v1`、`funasr-sensevoice-small-v1`、
`whisperx-large-v3-zh-align-v2`。candidate
以 workflow run ID 为不可变身份，应用、venv、受 ACL 保护的独立配置和 release manifest
分别发布到版本化目录；manifest 绑定 deployment contract、qualification identity、wheel
依赖身份、`pip freeze` 身份及应用文件 hash。写入前要求每个受管卷至少有 20 GiB 可用
空间。旁路预置不读写 active release、不停止或
注册 Scheduled Task，也不自动执行 dependency compaction。

candidate promotion 由独立手动 workflow 执行，使用 manifest SHA-256 重新验证目录、
reparse-point、应用文件、Python 环境、模型缓存和当前 task 所有权；promotion 才将 candidate
配置启用、停止经验证的旧 listener、写入原子 active state 并启动 candidate。任何本机或
Ubuntu 跨节点验证失败都会使用受保护的 activation state 恢复旧 task action、active state
和 candidate 配置并重启旧 release。当前 active release 的实际身份以受保护的 active state、
candidate manifest 和 promotion workflow evidence 为准，不能把代码就绪描述为生产启用。

## 入口与调用链

```text
资料库统一上传 MP4
→ POST /api/admin/content/uploads (管理员权限、目录预检、落盘、登记)
→ media_assets (app.sqlite) + media/<media_id>/ + media_transcript 目录壳
→ 资料列表显示 awaiting_transcription
→ 管理员选择 scheme_id（单项或批量）
→ POST /api/admin/transcription/media/{media_id}/start 或 /bulk-start
→ transcription_jobs / audio preparation / ASR worker
→ _build_transcript_doc(media_id)
→ chunk_transcript
→ Parent/Child(doc_type, start_time, media_id)
→ parents.sqlite (media_id 列) + Qdrant
→ RetrievedParent(media_id)
→ <source media_id=... time=... type="transcript">
→ SourceDTO(media_id)
→ 视频引用角标 (悬浮卡播放按钮 → 播放器 seek)
→ 来源卡片 (播放按钮)
→ GET /api/media/{media_id} (鉴权 Range 播放)
→ GET /api/media/{media_id}/transcript（鉴权读取正式/兼容人工转录稿）
```

自动转录与版本发布结果流：

```text
资料统一上传后的待转录 MP4 + scheme_id + 幂等键
→ 应用转录任务/worker
→ audio preparation port
→ independent asr_service create/upload/start/poll/result
→ RemoteAsrProvider
→ ProviderCandidate | ProviderFailure
→ pipeline.py → normalize_candidate → CanonicalTranscript
→ deterministic Markdown + candidate transcript version
→ 管理员审核
→ publication-only candidate index
→ SQLiteTranscriptionStore.promote（单事务 head 切换）
→ 检索按同一 head snapshot 过滤 Qdrant Child 与 parents.sqlite Parent
```

## 关键文件

- `src/ingest.py`
- `src/indexing_pipeline.py`
- `src/chunk.py`
- `src/index.py`
- `src/retrieve.py`
- `src/generate.py`
- `api/schemas.py`
- `api/routes_media.py`（新增）
- `api/routes_content.py`（统一上传、预检和上传任务分流）
- `api/routes_admin.py`（媒体落盘、转录任务和动作能力）
- `api/indexing.py`（media_id 传递）
- `api/db.py`、`api/db_migrations.py`、`api/db_backup.py`（Phase 2 添加式 Schema 与备份）
- `api/transcription_store.py`、`api/transcription_artifacts.py`、`api/transcription_publication.py`（版本、artifact 与发布应用服务）
- `src/transcription/persistence.py`、`src/transcription/workflow.py`（Phase 2 领域端口与编排）
- `src/transcription/asr_service_contract.py`、`provider_registry.py`、`remote_provider.py`、`runtime_ports.py`（Phase 3 后端契约）
- `src/transcription/profile_catalog.py`（Phase 3 experimental Profile catalog）
- `src/transcription_retrieval_visibility.py`（Phase 5 SQLite head 只读快照，位于 Phase 1 纯契约核心之外）
- `api/routes_transcription.py`、`api/indexing.py`（转录/版本 API 与共享 worker）
- `frontend/src/components/TranscriptionVersionPanel.tsx`（版本审阅与发布）
- `services/asr_service/`（Phase 3 独立服务、存储、调度和 engine adapter）
- `services/asr_service/requirements-service-core.txt`、`requirements-windows.txt`
  （ASR HTTP 服务基础依赖与 Windows 生产引擎依赖）
- `services/asr_service/engines/faster_whisper.py`、`requirements-faster-whisper.txt`
  （准入关闭的可选引擎 adapter 与隔离引擎依赖声明；生产资格组合安装基础服务依赖）
- `scripts/prepare_faster_whisper_model.py`、
  `scripts/prepare-faster-whisper-model-production.ps1`、
  `.github/workflows/prepare-faster-whisper-model-production.yml`
  （固定 revision 的持久模型制品准备与严格离线校验入口）
- `scripts/run_whisperx_qualification.py`、
  `scripts/qualify-whisperx-production.ps1`、
  `.github/workflows/qualify-whisperx-production.yml`
  （WhisperX 固定非敏感样本、质量/资源/许可证门禁与手动隔离执行入口）
- `scripts/asr_qualification_manifest.py`、
  `services/asr_service/asr-qualification-manifest.example.json`
  （三引擎共享的只读八样本 manifest 解析、严格文件身份校验与中性变量门禁）
- `scripts/compact-asr-run.ps1`、`scripts/cleanup-asr-storage.ps1`、
  `.github/workflows/cleanup-production.yml`
  （单次 run 精确收缩、周期保留策略、路径安全检查与 JSON 审计）
- `frontend/src/components/citations.ts`
- `frontend/src/components/SourceWorkspace.tsx`（来源详情播放按钮）
- `frontend/src/components/Message.tsx`（引用悬浮卡播放按钮）
- `frontend/src/components/VideoPlayerDrawer.tsx`（新增）
- `frontend/src/components/TranscriptPanel.tsx`（同步转录列表）
- `frontend/src/hooks/useVideoPlayer.tsx`（新增）

## 数据契约

| 字段 | 来源 | 可选 | 说明 |
|------|------|------|------|
| `doc_type=”transcript”` | 上传/分类推导 | 否 | 触发转录分块逻辑 |
| `start_time` | 转录稿解析 | 是 | 引用时间戳 HH:MM:SS |
| `media_id` | 上传时生成 UUID | 是 | 关联 media_assets 表，驱动播放器 |
| `media_url`（API 动态构造） | 由 media_id 生成 | 是 | 不存储，动态构造 `/api/media/{media_id}` |
| `transcript_version_id` | transcript version | 是 | immutable 候选/历史版本身份；进入 Parent/Child 与 Qdrant payload |
| `current_version_id` | `media_transcript_heads` | 是 | SQLite 当前正式版本指针；legacy media 可以没有 head |
| `application_job_id` | `transcription_jobs.id` | 否 | 仅经 `ProviderRuntimePorts` 传递，参与服务请求身份；不进入 InputRef 或 Canonical |

## 依赖与下游消费者

- 依赖文档索引、检索、回答生成、引用与来源面板；
- 依赖认证（`require_user`）和可配置媒体存储；代码默认仍使用 `media/` 兼容目录，T12-B 受控切换目标为 `CONTENT_ROOT/media`；旧媒体记录归档后从管理列表隐藏，历史 transcript version 和任务继续保留审计；
- 可选的只读外部媒体源通过服务端根别名和相对路径解析原视频；ASR 仍只接收应用在本地媒体目录准备的音频，Provider、Canonical、Profile admission、审核、发布和索引契约不变；
- 依赖 `media_assets` 表（`app.sqlite`）和 `media_id` 列迁移。

## 不变量与安全边界

- 不得向客户端暴露服务器绝对路径（`storage_rel_path` 对外隐藏）；
- 视频访问必须经过 `require_user` 鉴权和路径穿越防护（`safe_join`）；
- 转录读取必须经过 `require_user` 鉴权，只返回当前已发布版本；legacy 回退只允许读取 `DOCS_DIR` 内媒体已登记的人工稿；
- `media_id` 不存在或状态不是 `ready` 时返回 404；
- 非法 Range 返回 416；
- `media_id` 不进入 Embedding 文本，不影响检索排序；
- 旧会话缺少 `media_id` 时正常降级（无播放按钮，不报错）；
- 新媒体和版本字段是向前兼容的 nullable 列，不需要索引 Reset；legacy stable ID 保持。
- Schema 31 只为上传任务明细添加类型和视频/转录任务关联字段；不回填视频业务对象，不改变 RAG 索引 Schema，也不需要索引 Reset。
- Phase 2 新表为添加式迁移；不删除旧表、不回填人工稿、不触发索引 Reset。
- Markdown 修订字段通过 schema 11 添加式迁移加入 `transcript_versions`；自动版本和 legacy 人工版本的已有字段与制品保持不可变，不需要索引 Reset。
- `app.sqlite` transcript head 是版本化转录唯一正式可见性事实；versioned transcript 在 head DB 损坏/缺失时 fail closed。`strict` 下版本化转录独立于普通资料 head 放行，双重无版本身份的 legacy 转录不可见；生产尚未切换到 `strict`。
- `DOCS_DIR`、`MEDIA_DIR` 和 `TRANSCRIPTION_ARTIFACT_DIR` 均可显式配置；旧式人工 Markdown 上传在 `strict` 下拒绝，避免创建无法进入正式版本可见性的源目录稿，自动转录继续使用 managed artifact。
- T12-B 仅删除既有 `media_transcript_heads` 正式指针和其精确旧索引，不删除 `transcript_versions`、`transcription_jobs` 或 `transcript_publication_index_jobs`；后续视频需在新媒体目录重新上传、转录和发布后才产生新的正式 head。
- experimental Profile 不能自动发布或自动索引；人工 Markdown 路径不经过 Provider。
- `published` 只在候选索引成功并完成正式 head 原子切换后成立；不存在“已发布、稍后再手动索引”的稳定状态。
- 受管人工修订必须审核通过并由管理员显式发布；创建修订、审核通过或候选索引处理中均不改变既有正式 head，发布失败时旧 head 继续可见。

## 验证

- 当前链路：转录解析、时间戳索引、检索命中、回答引用、前端匹配；
- 播放链路：匿名 401、登录用户 200/206、未知媒体 404、路径穿越 404；
- 转录播放同步：正式稿/legacy 人工稿读取、候选稿不可见、分段高亮、点击跳转、用户滚动暂停跟随和恢复跟随；
- Phase 2：临时 SQLite migration/backup、Store 事务、artifact hash、publication head、recovery、人工稿不回填和静态依赖边界；
- Phase 3/4：纯 Python service/remote、应用任务/worker、mock engine、存储恢复、取消/恢复和静态依赖边界；
- Phase 5：Store/事务/manual/visibility/index metadata/static 本地 29 项通过；版本管理定向前端 31 项通过；API、worker、candidate index、Qdrant Filter 与完整前端 build 由独立 CI job 验证；
- Markdown 校对：schema 10→11、修订幂等/冲突、格式验证、审核发布、旧 head 保留和公开读取后端定向通过；前端完整 259 项通过，production build 通过；媒体页 Playwright 12 项在 `1440x900`、`1280x720`、`768x1024` 和 `390x844` 全部通过，并完成校对抽屉截图复核。
- Phase 5C 真实 ffmpeg/ASR/GPU/Qdrant E2E 未运行。
- 管理流程加固：Provider/应用/API 定向 40 项通过；变基到最新 master 后 Provider/应用身份定向 31 项与前端定向 34 项通过，前端 production build 通过；远端 CI、真实服务和生产回归未执行。
- faster-whisper R2：无 FastAPI、无真实引擎的 ASR/Provider/应用回归
  187 项通过；Phase 1 核心契约与静态边界 189 项通过；PR #34 的 7 个远端 CI
  检查全部成功并已合并。
- faster-whisper R3：Run `31486600594` 在 master
  `1b75034f679b415a65aad4182286408d6983f467` 上通过依赖、固定模型、CUDA、8 个非敏感
  样本质量、资源与确定性门禁。应用 admission 与该 runtime contract 解耦；生产启用
  仍须通过带配置回滚、镜像回滚和实时 capability 验证的 app-only workflow。
- WhisperX R2/R3：已实现复用 remote Provider/Canonical 契约的 experimental Profile、
  lazy-load service adapter、ASR/中文对齐双模型 manifest 门禁及手动 Windows CUDA
  冒烟 workflow；另已实现复用既有 8 个自制中文样本和统一阈值的资格 workflow，
  通过现有 `TranscriptionProvider → ProviderCandidate → normalizer → Canonical`
  契约计算质量、确定性、时间戳、RTF、显存和许可证门禁。Run `31889569116` 已通过
  `production-asr` workflow 的脱敏审计并选择 `full-decode`；生产服务注册使用该配置，
  应用 admission 仍由独立 allowlist 控制。资格失败诊断只输出文本哈希、字符类别、长度、
  token 形状、编辑类型计数、期望项命中布尔值和固定分类，不输出参考文本、原始
  ProviderCandidate 文本或 Canonical 文本，也不改变模型、样本或准入阈值。
  后续解码评估固定为同一次隔离 qualification 内的三组顺序对照：当前 WhisperX
  默认解码、仅复用 faster-whisper 已验证的 12 项热词、以及同时覆盖 12 项热词、
  `beam_size=10`、`temperatures=[0.1]` 和 `initial_prompt` 的完整候选。参数通过
  WhisperX `3.8.6` 公开的 `load_model(asr_options=...)` 接口设置；切换候选时复用
  已加载的底层 ASR 权重和中文 aligner。只有完整候选通过原有全部门禁、标准编号
  召回严格优于基线、噪声 BIM CER 严格低于基线且负样本误命中为零，矩阵结论才
  可为通过。正式资格不等于生产部署或真实业务媒体验收。

## 已知限制

- 第一阶段只支持 MP4 格式，不支持其他视频容器；
- 自动稿按 Canonical 起止时间精确同步；旧人工稿仅有起始时间，结束时间按下一段起点推断，最后一段持续到视频结束；
- SenseVoice 短媒体自动转录已完成生产验收，但候选稿发布/Qdrant 正式可见性 E2E
  尚未执行；faster-whisper 已完成隔离 R3、Windows promotion 和应用 admission 激活，
  尚未使用真实业务媒体执行生产流量验收；
- WhisperX `full-decode` 已通过隔离 R3 并成为服务注册配置；每次 runtime contract 变化后
  仍须在同一 master SHA 分别重跑 faster-whisper 与 WhisperX R3，再执行只读部署预检、
  旁路 candidate staging、promotion 和实时验收；尚未使用真实业务媒体完成质量验收；
- 当前静态目录包含三个 `qualification_approved` WhisperX v2 Profile，但默认关闭；生产应用动作仅在 v2 R3、candidate promotion 和实时哈希验证通过后准入均衡 Profile。Qwen3-ASR experimental Profile 继续为 disabled；
- 支持范围播放但无 HLS 自适应码率。
- 媒体快捷筛选是最近 100 条的客户端筛选，不是服务端全库查询；转写工作台的视频校对区域目前只支持已登记媒体的受控视频播放，不生成独立字幕轨道。

## 相关决策

- [0001 — 视频转录播放器与媒体资产流水线](../decisions/0001-video-transcript-player.md) 第一阶段已实施完成。
- [0002 — 多引擎视频自动转录与管理员选择](../decisions/0002-multi-engine-transcription.md)；实施基线见 [Phase 2](../plans/multi-engine-transcription-phase2.md)、[Phase 3](../plans/multi-engine-transcription-phase3.md) 与 [Phase 5](../plans/multi-engine-transcription-phase5.md) 详细计划。
- [0006 — 统一资料上传与视频转录任务分流](../decisions/0006-unified-managed-upload-routing.md) 规定统一入口、独立领域状态和系统管理员边界。
- [转录管理流程加固](../plans/transcription-admin-workflow-hardening.md) 记录 Phase 5 后续 PR 1/PR 2 的独立范围。
- [faster-whisper Provider 接入](../plans/faster-whisper-provider-integration.md)
  记录 R2 代码边界与后续 R3 资格门禁。
- [faster-whisper R3 统一资格验证](../plans/faster-whisper-r3-unified-qualification.md)
  是后续依赖、模型、CUDA、质量与资源实测的唯一执行基线。
- [WhisperX R2+R3 一次性合并执行方案](../plans/whisperx-r2-r3-execution-plan.md)
  固定 Provider/Profile、双模型缓存、Windows runner 与回滚边界。
- [共享 ASR Qualification 语料迁移](../plans/shared-asr-qualification-corpus-migration.md)
  固定三引擎统一 manifest schema、中性变量迁移、只读 preflight 和回滚边界。

WhisperX 另提供互斥的 `runtime_preflight`：只读检查 Python、WhisperX/中文 aligner
模型 manifest、`PRODUCTION_WHISPERX_ROOT` 下固定的 `models`、`nltk`、`qualification`、
`wheel-cache` 与 `reports` 隔离目录、GPU 单卡身份和 Profile disabled 状态，结果写入 runner temp
的脱敏 artifact；不安装依赖、不下载模型、不启动服务。真实 qualification job 只有在
runtime preflight 成功后才运行；缺少 Python 路径时也会生成固定失败报告，避免空 report
根目录导致无证据退出。

开发机另提供 `scripts/run-asr-local-lab.ps1` 本地快速实验室。它在仓库外固定根目录中为
Qwen3-ASR、WhisperX 和测试工具创建三个独立 venv，并重定向 pip/Hugging Face/Torch/
CUDA/NLTK/temp/pycache；bootstrap 可联网准备固定 revision 模型，smoke/focus/full 评测
强制离线。Qwen 临时服务只绑定 `127.0.0.1:18310`，生产端口和现有 GPU 进程不被修改。
模型下载使用稳定 staging、跨命令 `.partial` 续传和原子发布，成功后不保留完整 staging
副本；所有写入路径解析真实目标并拒绝 reparse 逃逸。每次运行写入 `run-summary.json`，
质量门禁失败返回退出码 `2`。本地报告固定为 development-only 且永不具备 qualification 资格；当前真实依赖、模型和
GPU 已在 RTX 5070 Ti 上完成 bootstrap、smoke、focus 和 full。Qwen smoke 通过，但
forced-chinese 与 auto-zh-en 的 full 均仅 6/8 样本通过，标准编号与中英混合仍未达到原门禁；
WhisperX full 三候选矩阵通过并选择 full-decode，8/8 样本、双遍确定性、术语/编号召回和
负样本门禁均通过，峰值 GPU 分配约 `1323.55 MiB`。这些结果仅证明本地开发链路与候选
差异，不构成 production qualification，Profile admission 仍为 disabled。实施和操作边界见
[Qwen3-ASR / WhisperX 本地快速实验室](../plans/asr-local-development-lab.md)。

Qwen3-ASR 与 WhisperX 的 `runtime_contract_sha256` 同时绑定各自模型准备/qualification 入口
和共享 `scripts/asr_model_download.py`；下载或准备实现变化不会复用旧 qualification 证据。
