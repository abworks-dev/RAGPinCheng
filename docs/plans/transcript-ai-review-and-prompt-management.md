# 方案：转录失败原因透传 + AI 视频总结与转录稿优化建议（含对话指正）+ 管理端提示词统一管理

- 状态：候选方案（待批准）
- 风险等级：R2（跨模块：ASR 服务契约、应用 API、前端工作台、LLM 调用、提示词持久化、管理面板）
- 日期：2026-09-16（含 2026-09-16 用户新增：总结卡片 / 建议联动 / 人工修改感知 / 对话指正）

## 1. 背景与目标

1. **失败原因不透明**：`4.2.15灯具建模.mp4` 音频全程静音（mean/max volume −91 dB），
   VAD 移除全部片段后 ASR scheduler 以 `engine_failure_permanent` 失败，应用层展示
   `provider reported a controlled failure`，管理员无法判断"源视频无可转录内容"。
2. **AI 视频总结**：在转写工作台转录版本旁边新增一个"视频总结"卡片，LLM 依据
   "资料名称 + 转录稿全文"生成视频内容总结，帮助管理员快速理解视频内容并核对
   转录稿与标题是否一致。
3. **AI 转录稿优化建议**：基于"资料名称 + 视频总结 + 转录稿全文"推断可修正片段
   （错别字/识别错误/与标题不一致的表述），给出优化建议；管理员接受建议后用优化
   内容创建新修订版本（**保持"说话人 HH:MM:SS"时间戳格式不变**）。
4. **保留人工修改能力并感知更新**：沿用现有"编辑 → 保存为新草稿"的人工修订链路；
   AI 总结与建议**绑定 `markdown_sha256` 版本指纹**——转录稿一旦被人工/流程更新，
   卡片标记"基于旧版本生成"，要求重新生成，绝不把建议应用在过期内容上。
5. **对话指正**：对"视频总结"与"修改建议"均可发起多轮对话指正（例："这段总结不准确，
   实际讲的是管道避让" / "第 3 条建议改动过大了，只改错别字"），LLM 根据指正
   在**同一条对话上下文**里重新输出总结/建议，直到满意后接受。
6. **提示词统一管理**：把系统内置提示词（含新增的总结/建议/指正提示词）纳入管理面板
   统一管理，允许管理员查看/微调默认提示词/恢复默认。

## 2. 现状依据（已核对源码）

- ASR scheduler：`services/asr_service/scheduler.py:384-388` —— 全静音时
  `return self._fail(running, ServiceFailureCode.engine_failure_permanent)`，与引擎
  真实永久故障共用同一 failure code，无"全静音"区分。
- 应用映射：`src/transcription/remote_provider.py:250-251` 将
  `engine_failure_permanent → permanent_provider_error`；`api/transcription_store.py:487`
  固定写 `error_summary="provider reported a controlled failure"`。
- 转录版本修订：`api/routes_transcription.py:1748-1788` `POST /versions/{base}/revisions`
  → `service.create_revision`，保存为新受管人工修订版本（`source=manual`、
  `markdown_storage_kind=managed_artifact`、发布状态重置为待发布）；前端
  `TranscriptionVersionPanel.saveRevision`（约 290 行）已实现"编辑 → 保存为新草稿"，
  保存成功后 `loadVersions()` + `onChanged` 刷新。
- **版本指纹**：每个版本 DTO 含 `markdown_sha256`（`routes_transcription.py:1176`
  `version.markdown_ref.content_sha256`）；工作台 `previewVersion` 返回
  `result.markdown_sha256` 并存于 `EditorState.baseMarkdownSha256`（约 221 行）——
  天然可作为"内容是否变化"的校验键。
- LLM 调用模式：`src/generate.py`（`answer_*`）、`src/table_summary.py`
  （`table_summary_*`）使用智谱 OpenAI 兼容接口；`src/prompts.py` 从 `prompts/*.md`
  读取（`load_prompt` 带 `lru_cache`，运行时无法热更新）。
- 现有提示词：`prompts/` 下 10 个：answer_system/user、rewrite_system/user、
  decompose_system/user、table_summary_system/user、asr_engineering_zh_v1/v2。
- 管理面板：已有 `/admin/asr`、资料管理（`AdminMediaPage` / `AdminManagedContentPage`）、
  系统维护（`/admin/maintenance`）等 tab；未发现提示词管理区域。
- 批量操作：转录任务页已有批量菜单（开始转录、重新转录、发布所选等），后端
  `bulk-*` 端点逐项失败不阻塞其余项，可作为批量 AI 优化的交互模式参照。
- 生产问题背景：`llm_health` 报 BigModel `glm-4.6 / glm-4.5-air` APIConnectionError
  （影响回答生成，不影响检索）。本方案的 AI 功能依赖 LLM，需在部署前确认
  LLM 可用性或设计结构化降级（不可用时返回明确错误，不阻塞原功能）。

## 3. 变更范围

### 3.1 失败原因透传（R2，跨 ASR 服务契约与应用映射）

**目标**：全静音（无任何可转录内容）的音频失败时，管理员界面显示
"原视频无可转录内容（音频全程静音，可能未录制声音或源文件损坏）"。

- `services/asr_service`：新增明确的受控失败码 `no_speech`
  （`scheduler.py:384-388` 全静音分支使用），不再与引擎真实故障混同；
  `asr_service_contract.py` 同步 `ServiceFailureCode` 与 job/result 契约
  （含测试 `test_scheduler.py`、`test_api_contract.py` 更新）。
- 应用层 `src/transcription/remote_provider.py`：`_FAILURE_MAP` 增加
  `no_speech → ProviderErrorCode.no_speech_provider_error`（新增枚举值）。
- `api/transcription_store.py` / `api/transcription_service.py`：
  `record_provider_failure` 依据错误码生成 `error_summary`（`no_speech` →
  "原视频无可转录内容（音频全程静音…）"），不再固定通用文案；
  保持 `validate_single_line` 与长度上限。
- 前端失败文案映射（`routes_transcription.py` 约 202 行）与 `AdminMediaPage`
  行内 `asset.error` 照常展示；`permanent_provider_error` 与其他错误不受影响。
- 兼容：纯新增枚举，无数据库迁移；旧记录不变。

### 3.2 AI 视频总结卡片（R2，新功能）

**目标**：转写工作台（`TranscriptionVersionPanel` / `TranscriptionWorkbenchSheet`）
版本详情旁新增"视频总结"卡片，显示当前所选版本的 AI 总结。

- 新增提示词：`prompts/transcript_summary_system.md`（任务说明：基于
  "资料名称 + 转录稿全文"生成 3–8 条要点式中文总结，输出纯文本要点，
  明确"不得编造转录稿中没有的内容，不确定的点标注待核对"）+
  `prompts/transcript_summary_user.md`（`{media_title}`、`{transcript}`）。
- 后端：新增 `src/transcript_ai.py`（统一封装总结/建议/指正的 LLM 调用，
  复用 `src/config.py` ZHIPU 配置、`src/external_usage.py` 用量记录）：
  - `POST /api/admin/transcription/versions/{version_id}/summary` —— 生成总结；
    请求带 `base_markdown_sha256`；后端校验该版本当前 `markdown_sha256`，
    不一致返回 `version_stale`（409 + 提示"转录稿已更新，请基于最新版本重新生成"）。
  - 总结生成后卡片内可**对话指正**（见 3.4）。
- 前端：工作台版本详情（编辑器/时间轴旁）新增总结卡片：
  - 加载中 / 成功（要点列表）/ 失败（结构化错误）/ 过期标记 四种状态；
  - 卡片底部"重新生成"与"指正"入口；
  - 基于**当前选中版本**的 `markdown_sha256` 请求；版本切换或
    `loadVersions` 发现该版本哈希变化时卡片置"基于旧版本，点击重新生成"。

### 3.3 AI 转录稿优化建议（联动总结 + 接受生成新修订版本）（R2，新功能）

**目标**：基于"资料名称 + 视频总结 + 转录稿全文"生成可修正片段建议；
管理员预览/勾选/接受，生成新修订版本（保持时间戳与说话人格式）。

- 新增提示词：`prompts/transcript_review_system.md`（输出结构化 JSON：
  片段序号/时间戳/原文/建议修正/理由/置信度；规则见下方"修正规则（基于公开
  方法论梳理）"；不增删事实信息、不改时间戳与说话人标记、宁可少改不错改、
  输出必须可解析）+ `prompts/transcript_review_user.md`
  （`{media_title}`、`{summary}`、`{transcript}`）。

**修正规则（基于公开方法论梳理）**：

> 参考来源：[AWS Chime Transcript Cleaning 提示词工程](https://aws-samples.github.io/amazon-chime-sdk-meeting-summarizer/usage/transcription/#prompt-engineering-for-transcript-cleaning)、[Text-Transformation-Prompt-Stack（Gemini 分层方法论）](https://github.com/danielrosehill/Text-Transformation-Prompt-Stack)、[IDIAP：结合置信度与提示的 ASR+LLM 修正（ICASSP 2025 论文页）](https://publications.idiap.ch/publications/show/5380)、[OpenAI Whisper 纠错：提示 vs 后处理](https://superjuzi.blog.csdn.net/article/details/135338044)。

1. **只做"清理"，不做重写、不总结**：输出必须是对原文的逐段修正，保持逐句一一对应；
   绝不允许编造转录稿中不存在的句子/事实，绝不允许删除有意义的实体、数据、规范编号
   或专业术语（对齐 AWS："您不是在总结，而是在清理转录稿"）。
2. **同音/近音错别字修正**：仅当依据**句子上下文与资料名称**能高置信度判断为
   同音字/近音字误识时修正（如 "油网"↔"油烟"、"阀"↔"发"、"广进"→"管径"、
   "1：1" 数值误识），并在"理由"中说明依据；不确定时**不改**（宁可保留原词）。
3. **语气词与无意义词删除**：删除纯填充语（"嗯、呃、啊、然后的话、就是说、
   这个这个、对吧、你知道吗、好吧、基本上、实际上"等）与识别错误产生的无意义
   碎片串（如连续同字、音频噪声误识的乱码词）；删除时保持所在句子其余内容
   完整，不因删除而吞并相邻语义。
4. **自我纠正还原**（对齐 Text-Transformation-Prompt-Stack 的 inferred
   instructions）：说话人自己纠正的表述（"去商店——不，是药店"）修正为最终
   意图（"去药店"），但只在说话人明确否定时处理，不得自行推断改写。
5. **标点与分段**：仅修正明显错误的标点与两个说话人段之间的粘连；不擅自为
   长句重排结构、不加小标题、不改写为书面语。
6. **格式保真**：每条建议的 `timestamp` 必须对应原"说话人 HH:MM:SS"行，且
   提示词明确"时间戳与说话人行不属于可改写内容"；应用后成稿仍由后端
   `create_revision` 的 `_parse_transcript_turns` 校验（含时间戳合法性）。
7. **置信度分级**：`confidence: high|medium|low`；前端默认只高亮 high；
   medium/low 由管理员勾选决定是否应用（管理员始终掌握最终决定权）。
- 后端：
  - `POST /api/admin/transcription/versions/{version_id}/review-suggestions`
    —— 生成建议（`base_markdown_sha256` 校验同 3.2；大转录稿按 token 预算
    截断，超限返回明确提示；只读不落库）。
  - `POST /api/admin/transcription/versions/{base_version_id}/revisions-from-suggestions`
    —— 前端应用后成稿 → 复用既有 `create_revision` 契约与校验，产生受管人工
    修订版本（待发布，须重新决策，不自动发布）。
  - 批量：`POST /api/admin/transcription/media/bulk-review-optimize` —— items
    `{media_id}` 列表，后端取该媒体**最后一个成功转录版本**（job succeeded 且
    `result_version_id` 最新），逐项：校验版本未变化 → 生成总结+建议 → 落为新
    修订版本；返回逐项 succeeded/failed 与结构化原因（对齐
    `BulkTranscriptionActionResponse`）。
- 前端（工作台）：
  - 版本详情"AI 优化建议"入口打开建议抽屉：建议按时间戳排序，逐条
    原文 → 建议修正 → 理由，可勾选；【全部应用】【应用勾选】生成成稿 diff
    预览（保持"说话人 HH:MM:SS"行原样）；【接受并保存为新草稿】→
    `revisions-from-suggestions`，成功后沿用"新草稿已保存，发布状态已重置为
    待发布"提示与刷新。
  - 批量菜单"AI 优化所选"（确认影响说明 → 逐项处理 → 成功 N / 失败 M 及原因）。
  - LLM 不可用 / 生成失败 / 版本过期：结构化提示，不阻塞原工作台；版本过期时
    要求重新生成。

### 3.4 对话指正（总结 / 建议通用）（R2，新功能）

**目标**：对"视频总结"和"修改建议"都支持多轮对话指正，LLM 在同一条上下文里
按指正重新输出结果。

- 设计：前端在总结卡片 / 建议抽屉内维护**该版本的一次指正对话**（不落库，
  刷新或切换版本清空；如需审计复用既有 audit 表另行方案）：
  - 消息结构：`[{role:"system", content: 对应 system 提示词 + 资料名称与转录稿断言},
    {role:"user", content:"请生成总结/建议"}, {assistant: 上次结果}, {user: 指正},
    ...]`；
  - 每条指正后重新调用 LLM 输出**完整结果**（不是增量 diff），保证可解析
    为结构化总结/建议 JSON。
- 新增/复用提示词：`transcript_summary_system.md`、`transcript_review_system.md`
  末尾追加"指正处理"规则（识别用户指正 → 依据指正修订对应要点/片段 → 其余
  保持）；`transcript_summary_user.md` / `transcript_review_user.md` 保留
  `{media_title}`、`{summary}`（建议场景）、`{transcript}` 占位。
- 后端：`POST /api/admin/transcription/versions/{version_id}/summary/talk` 与
  `.../review-suggestions/talk` —— body：`{base_markdown_sha256, history:
  [{role, content}], latest_user}`；校验版本指纹（过期 409）；把
  `history + latest_user` 追加到提示词上下文调用 LLM，返回完整最新结果。
  （history 由前端持有；服务端不落库。）
- 前端：总结卡片与建议抽屉内嵌对话输入框（"指正…"回车发送）；展示对话气泡
  （用户指正 → AI 修订后的结果）；支持清空对话重新开始；接受建议仍走 3.3。

### 3.5 人工修改感知（贯穿 3.2–3.4）

- **校验键**：所有 AI 生成/指正/接受请求带 `base_markdown_sha256`；后端对当前
  版本 `markdown_sha256` 校验，不一致一律 409 `version_stale`（不阻塞，提示
  重新加载/重新生成）。
- **前端联动**：`saveRevision` 保存人工草稿后 `loadVersions()` 刷新 → 当前选中
  版本哈希变化 → 卡片/抽屉自动置过期状态；`refreshToken`/`onChanged` 已有
  机制复用；版本切换时按新版本哈希处理。人工"编辑 → 保存为新草稿"能力
  **完全保留**，AI 功能只是附加。

### 3.6 管理端提示词统一管理（R2，新功能）

**目标**：管理面板新增"提示词管理"区域（建议挂 `/admin/maintenance`，仅系统管理员），
列出系统内置提示词（含新增 summary/review 共 12+ 条），允许查看/修改/恢复默认。

- 数据：新增 `app.sqlite` 表 `system_prompts`（migration，新增式）：
  `key`（唯一，如 `answer_system`、`transcript_summary_system`）、`title`、
  `description`、`default_body`（内置快照）、`custom_body`（可空）、`updated_by`、
  `updated_at`；种子从当前 `prompts/*.md` 导入。
- 运行时加载（`src/prompts.py`）：`load_prompt` 先查库（custom 非空用 custom，
  否则 `default_body`），镜像内 `prompts/*.md` 保留为离线兜底；`lru_cache`
  按版本戳失效（修改后失效）。
- API（`routes_prompts.py` 或并入 `routes_admin.py`）：
  `GET /api/admin/prompts`、`PUT /api/admin/prompts/{key}`（管理员+CSRF+审计+长度上限）、
  `POST /api/admin/prompts/{key}/restore`（清空自定义回落默认）。
- 前端：维护页"提示词与 AI 模型"区域（表格 + 展开编辑 + 保存 + 恢复默认）。
  改动限于该区域，不动问答/检索路径。

## 4. 待用户决策点

1. **失败文案归属**：默认无语音与引擎真实故障分开两种文案（独立 `no_speech` 枚举）。
2. **批量 AI 优化落地方式**：
   a) 只生成建议，由管理员逐个在工作台接受；
   b) **直接生成并落为新修订版本（推荐）**——每项产生一个待发布新版本，
   管理员后续可在工作台复核/拒绝/发布。
3. **优化后是否自动发布**：默认**不自动发布**（走既有允许/发布决策，符合门禁）。
4. **提示词管理位置**：默认挂 `/admin/maintenance`（系统维护）；如需资料管理页
   新增 tab 亦可。
5. **LLM 模型**：沿用 `LLM_MODEL`（glm），不新增；LLM 不可用时功能返回
   "模型服务不可用"并建议先修复 LLM。
6. **指正对话持久化**：默认不落库（刷新/切换版本清空）；如需保存对话审计另行确认。
7. **视频总结放置**：总结卡片放版本详情旁（编辑器/时间轴同区），接受默认即可。

## 5. 实施步骤与验证

1. ASR 服务契约（no_speech）→ 服务端单测（scheduler/api_contract）。
2. 应用映射 + error_summary → `test_transcription_*` 定向用例。
3. 提示词管理（migration + prompts.py 加载 + admin API）→ `test_admin_prompts*`、
   既有 `test_answer_sources.py` 等适配。
4. `src/transcript_ai.py`（总结/建议/指正统一封装 + JSON 解析降级）→ 定向单测
   （mock LLM、对话重生成、版本过期 409、批量逐项失败、幂等键、格式校验）。
5. 前端：总结卡片 + 建议抽屉（勾选/应用/接受保存）+ 指正对话 + 批量菜单 +
   维护页提示词区域 → 组件单测 + `npm run build` + Playwright 定向用例。
6. CI：validate / test-transcription-contracts / test-transcription-phase5 /
   test-admin-visual / delivery-policy 全绿。
7. 生产部署（Deploy Production App + Content/ASR Manual，APPLY_PENDING 建表）→
   冒烟：无声音频显示"无可转录内容"；真实转录稿生成总结/建议并接受保存；
   人工改名后 AI 结果置过期；指正对话可修订结果；管理面板改提示词生效且可恢复默认。

## 6. 风险、兼容性与回滚

- 新增枚举/新表均为新增式，旧数据兼容；ASR 服务契约与应用同步部署，回滚整体进行。
- AI 功能强依赖 LLM：不可用/超时/解析失败/版本过期均结构化降级，绝不破坏原转录
  版本；建议只读不落库，接受保存才写新版本（幂等键防重复）。
- 指正对话不落库，避免状态膨胀与审计盲区（如需审计另行方案）。
- 提示词管理：默认 body 存库快照 + 镜像兜底，回滚 = 清空自定义；
  migration 仅新增表（`app.sqlite` 备份遵循既有流程）。
- 回滚：整体 revert 独立 PR；生产恢复沿用既有 deploy / 镜像回滚路径。

## 7. 明确不做

- 不做转录稿自动发布（接受建议后仍待发布审核）。
- 不做指正对话与建议审计的持久化表（本次不落库；如需审计复用既有 audit 表）。
- 不改 `prompts/` 问答类提示词在镜像内的默认内容（仅新增管理能力）。
- 不做历史转录稿批量重转。
- 不引入新 LLM 提供商/新模型；不自动"全文重写"（只做片段级修正建议）。