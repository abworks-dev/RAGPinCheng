# 品成 BIM 知识库 — 待办事项 Roadmap

按优先级排序的功能开发计划。

---

## 🔴 高优先级 — 已有明确设计方案

### 1. 查询拆分 / 多跳检索 — Phase 2

**适用场景**：用户提问需要对比/合并多个实体或主题，但**没有规范编号**做为确定性触发信号。

示例问题：
- "对比 Revit 族与 Dynamo 节点的复用方式"
- "客户A和客户B对图层命名的不同要求"
- "钢结构和混凝土结构在BIM建模流程上的差异"

**设计方案**：
- [ ] 新增 Prompt `prompts/decompose_system.md`
- [ ] 使用便宜模型（`LLM_REWRITE_MODEL = glm-4.5-air`）判断是否需要拆分
- [ ] 返回 JSON 格式：`{"decompose": bool, "sub_queries": [...]}`，最多 3 个子查询
- [ ] 启发式触发 gate：query 包含 `对比|比较|区别|差异|分别|VS|vs` 等比较标记时才调用 LLM，预计触发率 <5%/turn
- [ ] 新增 `retrieve_multi` 多路检索、Parent 去重、覆盖约束与全局重排；当前源码尚无该入口

**涉及文件**：
- 新增 `prompts/decompose_system.md`
- 修改 `src/session.py` 或新增 `src/decompose.py`
- 补充黄金集对比型测试用例

### 2. 分层查询增强（查询拆分 + MQE + 受控 HyDE）

**状态**：候选设计，尚未实现。查询拆分为主路径，MQE 用于术语扩展，HyDE 仅作为特定场景的辅助 Dense 检索通道，不全量默认开启。

**目标**：在保留规范编号、客户名称、型号和专业术语精确性的前提下，提高口语化、术语不一致、故障现象描述及培训视频类问题的召回率，同时避免 HyDE 假设内容损害 no-answer 判断。

**分层路由**：
- [ ] 所有查询先复用现有多轮独立问题改写和查询守卫；
- [ ] 包含规范编号、条款号、客户名、型号或精确参数时，只走原始查询的 Dense + Sparse + code boost，不启用 HyDE；
- [ ] 比较、合并或分别查询多个实体时，优先进入“查询拆分 / 多跳检索”路径；
- [ ] 无强精确标识、但可能存在用户用词与文档术语错配时，生成 2～3 个语义等价 MQE 查询；
- [ ] 故障现象、操作经验或培训视频类自然语言描述，在低置信度场景下可额外生成 1 个 HyDE 假设文档，仅用于 Dense 检索；
- [ ] 原始查询始终保留为最高可信检索通道，扩展查询和 HyDE 不得覆盖或替代原始查询。

**候选融合策略**：
- [ ] 建立真实的多查询检索入口；当前源码不存在 TODO 上文所称的 `retrieve_multi`，实现前先明确接口与返回契约；
- [ ] 每个子查询保留最低候选配额，避免比较问题的一侧被高分结果完全挤出；
- [ ] 按 `parent_id` 去重，通过跨查询 RRF、命中覆盖奖励或等价方法融合，不直接比较不同检索通道的原始分数；
- [ ] HyDE 结果使用较低权重，禁止进入 Sparse 和规范编号 code boost 通道；
- [ ] 融合后统一使用原始用户意图或子查询做全局 rerank，再按现有上下文预算返回 Parent 证据；
- [ ] 增加功能开关、触发原因、生成查询、各通道命中和延迟/Token 遥测，以便灰度、回滚和问题定位。

**实施顺序**：
1. 先实现上文的比较意图查询拆分；
2. 补齐多查询检索、去重、覆盖约束和全局重排基础设施；
3. 在术语错配黄金集上试验 MQE；
4. 最后以默认关闭的实验开关加入 HyDE，并单独评估查询漂移和 no-answer 退化；
5. 只有固定黄金集证明收益且延迟、成本可接受后，才扩大触发范围。

**验证门槛**：
- [ ] 黄金集新增比较型、术语错配、模糊现象、培训视频、规范编号、精确数值和跨领域 no-answer 用例；
- [ ] 分类型对比 Recall@1、Recall@5、MRR、no-answer 保持率及多主题覆盖率；
- [ ] 记录 P50/P95 检索延迟、每轮 LLM 调用次数和 Token 成本；
- [ ] 规范编号、客户标准和精确参数类结果不得因 MQE/HyDE 出现回归；
- [ ] 不修改 Chunk、Embedding 或索引 Schema，候选实现原则上无需重建索引；若方案改变这些契约，必须重新评估并审批。

---

## 🟡 中优先级 — 已有基础实现，需完善

### 3. 跨父文档多轮上下文携带

**现状**：当前 `ChatSession` 只携带上一轮最优 2 个来源的片段，局限在同一文档内的连续追问。

**待实现**：
- [ ] 记录每轮所有命中父文档的元数据（doc_title、category、section_path）
- [ ] 后续轮次自动将相关历史文档的标题作为软提示注入重写器
- [ ] 极端情况下可展开历史来源的完整文本（需额外预算控制）
- [ ] 支持真正的"跨文档综合"复杂多轮对话

**参考**：`scripts/append_handwritten.py:27`

---

## 🟢 体验增强 — 无破坏性，逐步迭代

### 4. 视频播放器第二阶段（交互式转录增强）

**现状**：
- 第一阶段已完成：媒体目录配置、数据库迁移、数据契约打通、管理端上传、鉴权 Range 播放、播放器抽屉、引用点击 seek、来源卡片播放按钮
- `media_id` 已通过数据契约完整传递

**待实现**：
- [ ] 自动语音识别（Whisper）集成
- [ ] 视频播放过程中同步高亮当前说话人段落
- [ ] 播放进度持久化与恢复
- [ ] 移动端底部弹层优化

### 5. 来源面板增强功能

**文件**：`frontend/src/components/SourcesPanel.tsx`

待实现：
- [ ] "复制来源"按钮（复制文档标题 + 章节路径）
- [ ] 来源卡片直接跳转到对应 PDF 页码（需 PDF.js 集成）
- [ ] 支持批量导出引用列表

---

## 📋 运维与质量

### 6. 反馈日志分析仪表盘

**现状**：`api/feedback.py` 已实现数据收集接口，用户可报告引用错误。

**待实现**：
- [ ] 管理员后台可视化反馈趋势图表
- [ ] 按文档/章节聚合报错率
- [ ] 检索失败案例的自动聚类分析
- [ ] 定时生成检索质量报告

### 7. 检索黄金集扩展

**文件**：`src/eval/`

**当前基线**（2026-05）：R@1 = 90%, R@5 = 96%, no-answer = 100%（~97 条）

**待补充**：
- [ ] 更多对比型问题（Phase 2 上线前需要）
- [ ] 多轮对话案例
- [ ] 边界 case（纯数字、纯代码、无意义输入）
- [ ] 视频转录本相关问题

### 8. Ubuntu 应用节点 + Windows GPU 节点迁移 ✅

**状态**：已完成，迁移实施中

**参考**：`project-docs/migrations/ubuntu-app-windows-gpu-runbook.md`

**已完成**：
- [x] 阶段 0 — 基线采集与决策
- [x] 阶段 1 — GPU 推理接口（21/21 测试通过，GPU 冒烟通过）
- [x] 阶段 2 — Provider 抽象（22/22 测试通过）
- [x] 阶段 3 — 容器拆分（镜像构建成功）
- [x] 阶段 4 — CI 扩展
- [x] 阶段 5 — CD 拆分
- [x] Ubuntu 基础设施搭建（Docker、Runner、目录结构、Compose 覆盖）
- [x] Qdrant snapshot 恢复（38488 points）
- [x] Backend 启动并验证（浏览器登录成功）
- [x] CD 全流程跑通（deploy-gpu → deploy-app）
- [x] GitHub Actions Secrets 配置
- [x] docs/ 和 media/ 迁移完成
- [ ] 稳定观察期

---

## ✅ 最近已完成（已从待办移除）

按时间倒序：

1. ✅ **来源面板分类分组显示** — 按分类分组、可折叠、组标题带图标和数量（2026-07-25）
2. ✅ **来源面板关键词高亮** — 匹配检索词加黄色背景标记（2026-07-25）
3. ✅ **来源预览文本移除 HTML 标签** — `stripMarkdown()` 增加 `<[^>]*>` 清理，覆盖面包屑和 Tooltip（2026-07-25）
4. ✅ **视频播放器第一阶段（基础设施）** — 媒体目录配置、数据库迁移、数据契约打通、鉴权 Range 播放、管理端上传、视频播放器抽屉、引用点击 seek（2026-07-23）
2. ✅ **SSE 心跳机制防止流超时断开** — `api/routes_chat.py`（2026-07-22）
3. ✅ **纯数字输入无论第几轮对话都拦截** — `src/query_guard.py`（2026-07-22）
4. ✅ **引用角标 tooltip 闪烁、遮挡、溢出修复** — `Message.tsx`（2026-07-22）
5. ✅ **WPS 风格引用角标样式** — `Message.tsx`（2026-07-22）
6. ✅ **移除 LLM 输出中重复的"资料来源"小节** — `prompts/answer_system.md`（2026-07-22）
7. ✅ **查询验证守卫（无意义输入拦截）** — `src/query_guard.py`（2026-07-20）
8. ✅ **Docker 构建缓存优化与依赖清理** — `requirements-prod.txt`、`docker/*`（2026-07-20）
9. ✅ **Code-boost 规范编号检索增强** — `src/retrieve.py`（2026-05）
10. ✅ **表格摘要索引增强** — `src/table_summary.py`（2026-05）
11. ✅ **每轮遥测 + 每用户聊天限流** — `api/rate_limit.py`（2026-05）

---

## 📝 备注

- 本文件替代了原有的纯文本 TODO 文件
- 所有 RAG 相关修改必须遵循 `.claude/rules/rag-pipeline.md` 中的黄金集验证规则
- 涉及 Schema 变更必须说明索引兼容性和是否需要 `--reset`
- 新增 Prompt 统一放在 `prompts/*.md` 并通过 `src/prompts.py` 加载

## 🔷 Office 文档支持（待审批方案）

> 状态：已完成调查，本阶段仅限方案设计，未实施任何代码。
> 方案依据：`project-docs/migrations/office-document-support-plan.md`

### 调查结论：当前调用链

```
上传（POST /api/admin/upload）
  → _classify_doc_type() 拒绝非 .pdf/.md 文件
  → 写入 docs/<category>/<filename>
  → create_job() + enqueue()
  → run_index_job() → index_single()
    → _build_pdf_doc() / _build_markdown_doc() / _build_transcript_doc()
    → _purge_existing() 清理旧数据
    → chunk_document() 分块
    → store_parents() + index_children()
    → 写 Qdrant + parents.sqlite

预览（GET /api/pdf/{parent_id}）
  → 查 parents.sqlite → source_path → 返回 PDF 文件
  → 前端 react-pdf 渲染

数据契约：
  ParsedDoc: source_path, markdown_path, doc_type("pdf"|"transcript")
  Parent: parent_id, text, doc_title, section_path, source_path, doc_type
  Child: child_id, parent_id, text, embed_text, content_type, source_path, doc_type
  Qdrant payload: source_path, doc_title, category, section_path, doc_type, content_type, ...
  parents.sqlite: parent_id, doc_title, category, section_path, source_path, text, doc_type, ...
  SourceDTO: parent_id, doc_title, section_path, category, score, text, doc_type, start_time, media_id
  IndexJobDTO: id, filename, category, doc_type, source_path, file_size, status, error, ...
```

### 待决策事项

1. **DOCX 结构锚点** — 用于引用定位的 `paragraph_anchor` 如何生成并长期稳定使用什么策略（段落 ID / 文本哈希 / 序号偏移）？
2. **XLSX 虚拟表格组件** — 选用哪个 React 虚拟表格库（`@tanstack/react-virtual` + 自行渲染 / `react-data-grid` / AG Grid Community），需核验许可证是否满足生产使用要求
3. **SheetJS 授权** — SheetJS Community Edition 是否满足生产使用（非 GPL，需确认）
4. **LibreOffice 部署方式** — 安装进现有 backend 镜像 / 独立转换容器 / 独立转换 worker，需从镜像体积、进程隔离、并发、字体安装、升级和故障影响综合评估
5. **旧版 .doc/.xls/.ppt** — 是否第一阶段支持（需要 LibreOffice 全职转换，且预览无法用原生格式）
6. **部分成功状态** — 索引成功但预览失败时，是否允许状态为"部分成功"（检索可用 + 预览不可用 + 可单独重试预览）
7. **Schema 迁移** — Office 文档是否需要新的 `doc_type` 值（如 `docx`/`xlsx`/`pptx`），以及是否需要新增字段（`preview_path`、`page_number`、`sheet_name`、`cell_range`、`paragraph_anchor` 等），是否需要 `parents.sqlite` 迁移
8. **复杂 DOCX 自动降级** — 当 Docling Slim 解析失败时，是否允许自动降级为 PDF → MinerU 管道（需显式标记，避免静默行为）

### 阶段 1：契约与样本基线

**目标**：定义 Office 文档的数据契约，收集测试样本，建立验收标准。

**当前代码依据**：
- `src/ingest.py` — `ParsedDoc` 只有 `doc_type="pdf"|"transcript"`，需要新增类型
- `src/chunk.py` — `Parent` 和 `Child` 的 `doc_type` 字段，需要扩展
- `api/schemas.py` — `SourceDTO` 和 `IndexJobDTO` 的 `doc_type` 字段
- `src/index.py` — `parents.sqlite` schema 和 `fetch_parents` 返回的字段
- `frontend/src/types.ts` — `Source` 类型

**拟修改文件**：
- `src/ingest.py`：`ParsedDoc` 新增 `doc_type` 枚举值
- `src/chunk.py`：`Parent`、`Child` 新增 Office 相关字段
- `src/index.py`：`parents.sqlite` schema 迁移
- `api/schemas.py`：`SourceDTO` 新增 Office 预览字段
- `frontend/src/types.ts`：同步更新 `Source` 类型
- `tests/`：新增 Office 测试样本

**前置依赖**：无

**实施要点**：
- 新增 `doc_type` 值：`docx`、`xlsx`、`pptx`，不再复用 `doc_type="pdf"`
- 评估 `ParsedDoc` 是否需要新增 `preview_path`、`parsed_path` 等字段
- 评估 `Parent` 是否需要新增 `sheet_name`、`cell_range`、`slide_number`、`paragraph_anchor` 等字段
- 明确这些字段是否需要写入 Qdrant payload
- 说明兼容旧索引的方式

**验证方法**：无代码变更，仅文档

**验收标准**：数据契约文档评审通过

**风险**：新增字段可能影响现有 `sqlite3.Row` 访问模式

**回滚方式**：不涉及代码修改，无需回滚

**是否改变索引契约**：是，新增 `doc_type` 值和字段

**是否需要重建索引**：否，仅对新上传的 Office 文档生效



### 阶段 2：Office 上传和公共解析接口

**目标**：扩展上传接口接受 `.docx`/`.xlsx`/`.pptx`，实现文件类型校验、安全检查和解析任务分发。

**当前代码依据**：
- `api/routes_admin.py` — `_classify_doc_type()` 当前只接受 `.pdf` 和 `.md`
- `api/routes_admin.py` — `upload_documents()` 写入 `docs/` 目录
- `api/indexing.py` — `run_index_job()` 分发到 `index_single()`
- `src/indexing_pipeline.py` — `index_single()` 当前只接受 `doc_type="pdf"|"transcript"`
- `src/config.py` — `MAX_UPLOAD_BYTES` 当前对应 200MB

**前置依赖**：阶段 1（契约定义）

**实施的 CheckList**：
- [ ] 扩展 `_classify_doc_type()` 支持 `.docx`/`.xlsx`/`.pptx`（延后 `.doc`/`.xls`/`.ppt`）
- [ ] 评估：是否第一版支持 `.doc`/`.xls`/`.ppt`
- [ ] 扩展名、MIME 类型和文件签名（magic bytes）校验
- [ ] 压缩炸弹防护（检测 zip bomb）
- [ ] 超大工作簿/超多 Sheet/Slide 检测
- [ ] 恶意外部链接和宏文件检测（先拒绝带宏的 Office 文件）
- [ ] 上传大小限制（复用现有 `MAX_UPLOAD_BYTES`）
- [ ] 解析超时和并发限制
- [ ] 临时目录隔离与清理
- [ ] 原始文件（`docs/`）、解析缓存（`data/parsed/`）和预览产物命名冲突防护
- [ ] 删除文档时同步清理原始文件、解析缓存、预览产物、Qdrant 点和 parents.sqlite 行
- [ ] 扩展 `index_single()` 的 `doc_type` 校验，分发到对应的解析器
- [ ] 扩展 `IndexJobDTO` 和 `IndexedDocumentDTO` 的 `doc_type` 类型

**拟修改文件**：
- `api/routes_admin.py`、`api/indexing.py`、`src/indexing_pipeline.py`、`src/config.py`、`api/schemas.py`

**明确不修改**：
- 不分发到 MinerU（Office 不使用 MinerU 解析）
- 不修改前端上传 UI（本阶段只完成后端）

**验证方法**：
- 上传 `.docx`/`.xlsx`/`.pptx` 文件，确认被接受并创建索引任务
- 上传 `.doc`/`.xls`/`.ppt` 文件，确认被拒绝（或接受，取决于待决策）
- 上传带宏的文件，确认被拒绝
- 上传超大文件，确认被拒绝

**验收标准**：Office 文件上传后进入索引队列，状态正确

**风险**：zip bomb 可能消耗大量内存，需要流式检测

**回滚方式**：回退 `_classify_doc_type()` 即可阻止 Office 上传

**是否改变索引契约**：否（本阶段仅上传，不分发解析）

**是否需要重建索引**：否



### 阶段 3：DOCX 索引解析

**目标**：接入 Docling Slim 将 DOCX 解析为结构化 Markdown，供现有分块管道使用。

**当前代码依据**：
- `src/indexing_pipeline.py` — `index_single()` 分发文档到各解析器
- `src/indexing_pipeline.py` — `_build_pdf_doc()` 模式，DOCX 需要类似的新函数
- `src/chunk.py` — `chunk_document()` 接收 `ParsedDoc` 的 Markdown 进行分块

**前置依赖**：阶段 2（上传接口）

**实施的 CheckList**：
- [ ] 安装 Docling（仅 Office 所需的最小 extras）
- [ ] 实现 `_build_docx_doc()` 或类似函数
- [ ] 标题、段落、列表、表格、图片说明和公式的 Markdown 输出质量验证
- [ ] 页眉页脚、目录、批注、修订和隐藏内容的处理策略（是否保留/丢弃）
- [ ] 图片是否提取/丢弃
- [ ] 复杂 DOCX 失败时是否允许转 PDF 后走 MinerU（待决策 8）
- [ ] 输出确定性（相同输入是否产生相同输出）和缓存失效策略
- [ ] 解析缓存路径和命名规则
- [ ] 状态机：`pending → parsing → chunking → summarizing → embedding → done`

**待决策**：DOCX 结构锚点如何生成并长期稳定（段落 ID / 文本哈希 / 序号偏移），用于前端引用定位

**拟修改文件**：
- 新增 `src/office_convert.py` 或 `src/docling_convert.py`
- 修改 `src/indexing_pipeline.py`，新增 `_build_docx_doc()`
- 修改 `src/indexing_pipeline.py`，`index_single()` 的 `doc_type` 分发

**明确不修改**：
- 不修改现有 PDF/MD/Transcript 解析路径
- 不修改 `src/chunk.py`（分块逻辑复用）

**验证方法**：
- 上传 DOCX 后确认 Markdown 缓存文件生成
- 确认标题层级、表格、列表结构正确
- 对比 Docling 输出与原始 DOCX 的内容一致性

**验收标准**：DOCX 解析后可在 Qdrant 中检索到对应内容

**风险**：Docling 对中文支持可能不完善，需用中文样本验证

**回滚方式**：回退 `index_single()` 的分发逻辑，DOCX 上传后仍停留在 `pending`

**是否改变索引契约**：否（复用现有 Markdown→分块管道）

**是否需要重建索引**：否



### 阶段 4：DOCX 前端预览

**目标**：在右侧预览面板中支持 DOCX 原生渲染，提供加载、缩放、滚动和段落定位功能。

**当前代码依据**：
- `frontend/src/components/PdfPreview.tsx` — 现有 PDF 预览组件
- `frontend/src/hooks/usePdfPreview.tsx` — 预览状态管理
- `frontend/src/components/SourcesPanel.tsx` — 来源卡片上的 PDF 预览按钮
- `frontend/src/components/ChatLayout.tsx` — 布局中集成预览面板
- `api/routes.py` — `GET /api/pdf/{parent_id}` PDF 文件接口

**前置依赖**：阶段 3（DOCX 索引解析）

**实施的 CheckList**：
- [ ] 安装 `docx-preview` 前端依赖
- [ ] 新增或扩展 `GET /api/source/{parent_id}/original` 受鉴权接口，返回原始 Office 文件
- [ ] 评估是否将 `PdfPreview` 重构为统一 `DocumentPreview`（待决策，见架构说明）
- [ ] DOCX 渲染器组件：加载、失败、超时和不支持状态
- [ ] 容器尺寸、缩放、搜索和滚动
- [ ] 外部链接和嵌入对象安全处理
- [ ] 根据 `paragraph_anchor` 或其他锚点滚动并高亮特定段落
- [ ] 原生渲染失真时显示"使用兼容 PDF 预览"入口
- [ ] 原始文件下载按钮
- [ ] 引用定位：点击来源面板中的引用 → 打开 DOCX 预览 → 定位到对应段落

**拟修改文件**：
- `frontend/src/components/`：新增 `DocxPreview.tsx`，修改 `PdfPreview.tsx` → `DocumentPreview.tsx`
- `frontend/src/hooks/usePdfPreview.tsx`：扩展为通用预览状态
- `api/routes.py`：新增原始文件接口
- `frontend/src/components/SourcesPanel.tsx`：预览按钮根据 `doc_type` 分发

**明确不修改**：
- 不修改现有 PDF 预览逻辑
- 不修改引用的数据契约

**验证方法**：
- 点击 DOCX 来源卡片 → 右侧打开 DOCX 预览
- 确认缩放、滚动正常
- 确认段落引用可定位

**验收标准**：DOCX 预览可用，引用定位正常

**风险**：`docx-preview` 对复杂格式（公式、SmartArt）支持有限

**回滚方式**：回退前端组件，预览按钮恢复为仅 PDF 显示

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 5：XLSX 索引解析

**目标**：基于 openpyxl 实现 XLSX 专用转换器，按工作表输出结构化 Markdown，每个 Chunk 保留 `sheet_name` + `cell_range`。

**当前代码依据**：
- `src/indexing_pipeline.py` — `index_single()` 分发文档
- `src/chunk.py` — `Parent` 和 `Child` 的现有字段
- `src/chunk.py` — `chunk_document()` 接收 `ParsedDoc` 的 Markdown

**前置依赖**：阶段 2（上传接口）

**实施的 CheckList**：
- [ ] 安装 openpyxl（纯 Python，无需系统依赖）
- [ ] 实现基于 openpyxl 的专用转换器
- [ ] 按工作表输出 Markdown 表格
- [ ] 有效数据区域识别（跳过空行/空列）
- [ ] 空白和纯装饰区域过滤
- [ ] 合并单元格处理
- [ ] 隐藏 Sheet、行和列的处理（是否跳过）
- [ ] 公式表达式与缓存值（优先取缓存值）
- [ ] 日期、百分数、金额和其他格式化值的 Markdown 输出格式
- [ ] 超宽、超长表格分块策略（避免单个 Parent/Child 过大）
- [ ] 多个独立表格区域识别
- [ ] 图表、图片、批注、命名区域和数据透视表的处理策略（丢弃/保留）
- [ ] 每个 Chunk 保存准确的 `sheet_name` + `cell_range`
- [ ] 避免一个大表整体进入单个 Parent/Child
- [ ] 状态机集成

**前置依赖**：阶段 1 确定 `Parent`/`Child` 新增字段

**拟修改文件**：
- `src/office_convert.py` 或 `src/xlsx_converter.py`（新增）
- `src/indexing_pipeline.py` — 新增 `_build_xlsx_doc()`，`index_single()` 分发

**明确不修改**：
- 不将 XLSX 转为 PDF 作为默认预览或索引方式
- 不修改前端预览（本阶段仅后端解析）

**验证方法**：
- 上传多 Sheet XLSX → 确认每个 Sheet 独立分块
- 宽表/万行级 XLSX → 确认分块策略正确
- 合并单元格、隐藏行列、公式 → 确认值正确

**验收标准**：XLSX 内容可检索，每个 Chunk 有正确的 `sheet_name` + `cell_range`

**风险**：超大 XLSX（百万行）可能消耗大量内存，需限制

**回滚方式**：回退 `index_single()` 分发逻辑

**是否改变索引契约**：是，新增 `sheet_name`、`cell_range` 字段

**是否需要重建索引**：否



### 阶段 6：XLSX 前端预览

**目标**：使用 SheetJS + 虚拟表格组件在浏览器中渲染 XLSX，支持工作表切换、冻结表头、虚拟滚动和引用定位。

**当前代码依据**：
- `frontend/src/components/PdfPreview.tsx` — 可作为统一预览面板的入口
- `api/routes.py` — 新增原始文件接口（阶段 4 已实现）

**前置依赖**：阶段 4（原始文件接口）、阶段 5（XLSX 索引解析）

**待决策**：
- 选用哪个 React 虚拟表格组件（`@tanstack/react-virtual` + 自行渲染 / `react-data-grid` / AG Grid Community）
- 核验 SheetJS Community Edition 许可证是否满足生产使用要求

**实施的 CheckList**：
- [ ] 安装 SheetJS 和选定的虚拟表格组件
- [ ] 工作表标签切换 UI
- [ ] 虚拟行列渲染（避免一次性渲染全部行/列）
- [ ] 冻结表头区域
- [ ] 合并单元格展示
- [ ] 基础格式展示（日期、百分数、金额等）
- [ ] 超大文件性能限制（显示行数上限 + 提示"仅显示前 N 行"）
- [ ] 点击引用后切换工作表、滚动到目标区域并高亮指定单元格范围
- [ ] 公式、图表、图片和数据透视表不完整时的用户提示
- [ ] 下载原文件按钮
- [ ] 加载状态、失败状态、不支持状态

**拟修改文件**：
- `frontend/src/components/`：新增 `SpreadsheetPreview.tsx`
- `frontend/src/components/`：修改 `DocumentPreview.tsx` 集成 XLSX 渲染器
- `frontend/package.json`：新增 SheetJS 和表格组件依赖

**明确不修改**：
- 不将 XLSX 转为 PDF 作为默认预览

**验证方法**：
- 多 Sheet XLSX → 工作表切换正常
- 万行级 XLSX → 虚拟滚动正常
- 引用定位 → 切换到指定 Sheet 并高亮单元格

**验收标准**：XLSX 预览可用，引用定位正常

**风险**：SheetJS Community Edition 可能限制某些功能（如条件格式、数据透视表渲染）

**回滚方式**：回退前端组件

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 7：PPTX 索引与预览

**目标**：Docling Slim 提取 PPTX 内容为 Markdown，LibreOffice 转为 PDF 供预览，确保幻灯片编号与 PDF 页码一一对应。

**当前代码依据**：
- `src/indexing_pipeline.py` — `index_single()` 分发文档
- `frontend/src/components/PdfPreview.tsx` — 复用现有 PDF 渲染
- `api/routes.py` — `GET /api/pdf/{parent_id}` PDF 文件接口

**前置依赖**：阶段 2（上传接口），阶段 8（LibreOffice 转换服务）

**实施的 CheckList**：
- [ ] Docling Slim 提取：幻灯片标题、正文、备注、表格和图片说明
- [ ] Markdown 中明确保留幻灯片边界（`---` 或 `<!-- slide N -->`）
- [ ] 每个 Chunk 保存 `slide_number`
- [ ] LibreOffice 将 PPTX 转换为 PDF（使用阶段 8 的转换服务）
- [ ] 验证幻灯片编号与 PDF 页码的映射关系
- [ ] 字体替换、公式、SmartArt、动画和媒体对象的降级方式
- [ ] 点击引用后使用现有 react-pdf 跳转到对应页
- [ ] 转换失败时允许下载原文件并显示明确错误
- [ ] 状态机集成

**拟修改文件**：
- `src/indexing_pipeline.py` — 新增 `_build_pptx_doc()`
- `src/office_convert.py` — 集成 LibreOffice 转换
- `frontend/src/components/` — 复用 `PdfPreview.tsx`（无需修改）

**明确不修改**：
- 不引入新的前端预览组件（复用 PDF 预览）

**验证方法**：
- 上传 PPTX → 确认幻灯片内容可检索
- 确认 PDF 预览页码与幻灯片编号一致
- 引用定位到正确幻灯片

**验收标准**：PPTX 内容可检索，预览可翻页，引用定位到正确幻灯片

**风险**：LibreOffice 转换 PDF 时字体替换可能导致排版偏差

**回滚方式**：回退 `index_single()` 分发逻辑

**是否改变索引契约**：是，新增 `slide_number` 字段

**是否需要重建索引**：否



### 阶段 8：LibreOffice 转换服务

**目标**：实现 LibreOffice 转换服务，为 PPTX 和复杂 DOCX 提供 PDF 预览兼容兜底。

**当前代码依据**：
- `docker/Dockerfile.backend` — 当前镜像不含 LibreOffice
- `docker/docker-compose.yml` — 仅定义 backend 和 qdrant 服务
- `src/indexing_pipeline.py` — 索引管道

**前置依赖**：阶段 7（PPTX 索引与预览）

**待决策**：LibreOffice 放入 backend 镜像还是独立服务

**实施的 CheckList**：
- [ ] 评估并决定 LibreOffice 部署方式（待决策 4）
- [ ] 安装 LibreOffice（含中文字体）
- [ ] 独立用户 home 目录（profile）隔离
- [ ] 输入/输出目录隔离
- [ ] 超时和进程终止
- [ ] 并发限制（限制同时运行的 LibreOffice 进程数）
- [ ] 缓存键设计（基于原始文件哈希）
- [ ] 健康检查（LibreOffice 是否可用）
- [ ] PDF 有效性检查（产物是否有效 PDF）
- [ ] 失败重试策略
- [ ] 临时文件清理策略
- [ ] 不允许 LibreOffice 宏执行
- [ ] 生产环境资源上限（CPU、内存）
- [ ] 状态机集成

**拟修改文件/配置**：
- `docker/Dockerfile.backend` 或新增 `docker/Dockerfile.libreoffice`
- `docker/docker-compose.yml` 或新增服务
- 新增 `src/libreoffice.py` 或 `src/office_convert.py`
- `src/config.py` — 新增 LibreOffice 相关配置

**明确不修改**：
- 不修改现有 PDF/MD/Transcript 解析路径
- 预览 PDF 不作为索引输入

**验证方法**：
- PPTX → PDF 转换成功率
- 中文字体渲染正确
- 并发转换不冲突
- 超时进程被正确终止

**验收标准**：LibreOffice 转换稳定可靠，PDF 产物可用

**风险**：LibreOffice 子进程可能僵尸化，需要进程管理

**回滚方式**：移除 LibreOffice 安装或停止独立服务

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 9：引用定位

**目标**：Office 文档的引用（citation）点击后可定位到对应段落/单元格/幻灯片，前端同步切换预览状态。

**当前代码依据**：
- `frontend/src/components/citations.ts` — `resolveCitation()` 当前按 `doc_title` + `section_path` 匹配
- `frontend/src/components/Message.tsx` — `CitationMarker` 处理引用点击
- `frontend/src/components/SourcesPanel.tsx` — 来源卡片
- `frontend/src/hooks/usePdfPreview.tsx` — 预览状态管理

**前置依赖**：阶段 4、6、7（各类文档的预览组件）

**实施的 CheckList**：
- [ ] 扩展 `resolveCitation()` 支持 Office 文档的定位参数
- [ ] DOCX：`paragraph_anchor` → 打开预览 → 滚动到对应段落并高亮
- [ ] XLSX：`sheet_name` + `cell_range` → 切换工作表 → 滚动到目标区域并高亮
- [ ] PPTX：`slide_number` → 打开 PDF 预览 → 跳转到对应页码
- [ ] 统一预览状态管理：`useDocumentPreview`（或扩展 `usePdfPreview`）支持 Office 文档类型

**拟修改文件**：
- `frontend/src/components/citations.ts` — 扩展 `resolveCitation()`
- `frontend/src/hooks/usePdfPreview.tsx` — 扩展为通用预览状态
- `frontend/src/components/Message.tsx` — 处理新引用类型

**明确不修改**：
- 不修改引用数据流（前端事件分发机制不变）

**验证方法**：
- 点击 DOCX 引用 → 定位到对应段落
- 点击 XLSX 引用 → 定位到对应单元格区域
- 点击 PPTX 引用 → 跳转到对应幻灯片

**验收标准**：三种 Office 文档的引用定位均正常

**风险**：DOCX 段落锚点稳定性和 XLSX 行列号在文件修改后可能失效

**回滚方式**：回退前端引用定位逻辑，引用退化为仅打开预览面板

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 10：安全、性能和故障恢复

**目标**：确保 Office 文档处理的安全性和性能，以及故障恢复能力。

**当前代码依据**：
- `api/indexing.py` — `run_index_job()` 的异常处理模式
- `src/indexing_pipeline.py` — `_purge_existing()` 清理逻辑

**前置依赖**：阶段 2-8

**实施的 CheckList**：
- [ ] 压缩炸弹检测（zip bomb）
- [ ] 宏文件检测（拒绝带 VBA 的 Office 文件）
- [ ] 恶意外部链接检测
- [ ] 超大文件检测（复用 `MAX_UPLOAD_BYTES`）
- [ ] 解析超时限制
- [ ] 临时目录隔离
- [ ] 并发解析限制
- [ ] 删除文档时同步清理所有派生产物（原始文件、解析缓存、预览产物、Qdrant 点、parents.sqlite 行）
- [ ] 失败重试策略
- [ ] 磁盘空间告警

**拟修改文件**：
- `api/routes_admin.py` — 上传校验
- `api/indexing.py` — 索引任务状态管理
- `src/indexing_pipeline.py` — 清理逻辑

**明确不修改**：无

**验证方法**：
- 上传带宏文件 → 被拒绝
- 上传超大文件 → 被拒绝
- 删除文档 → 所有派生产物被清理

**验收标准**：安全防护到位，删除操作完整

**风险**：zip bomb 检测增加上传延迟

**回滚方式**：回退安全校验逻辑

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 11：Docker 与生产发布

**目标**：更新 Docker 构建、Compose 配置和部署文档，确保 Office 功能在生产环境可运行。

**当前代码依据**：
- `docker/Dockerfile.backend` — 单阶段构建
- `docker/docker-compose.yml` — 仅 backend + qdrant
- `scripts/deploy-app.sh` — 部署脚本
- `requirements-prod.txt` — 生产依赖

**前置依赖**：阶段 2-8、阶段 10

**待决策**：LibreOffice 放入 backend 还是独立服务

**实施的 CheckList**：
- [ ] 安装新 Python 依赖（Docling、openpyxl、SheetJS 等）
- [ ] 安装 LibreOffice（如果选择放入 backend 镜像）
- [ ] 更新 `requirements-prod.txt`
- [ ] 更新 `frontend/package.json`
- [ ] 更新 `docker/Dockerfile.backend` 或新增 Dockerfile
- [ ] 更新 `docker/docker-compose.yml` 或新增服务
- [ ] 更新 `scripts/deploy-app.sh`
- [ ] 更新 `.env.example`
- [ ] 评估镜像体积影响
- [ ] 评估是否需要数据库迁移
- [ ] 评估是否影响现有 PDF、Markdown 和视频转写稿
- [ ] 评估是否需要重建现有索引
- [ ] Office 功能开关设计
- [ ] 分阶段灰度策略
- [ ] 回滚时如何保留原始 Office 文件并停止新解析
- [ ] 如何清理可重建的解析与预览缓存

**拟修改文件**：
- `docker/`、`requirements-prod.txt`、`frontend/package.json`、`.env.example`、`scripts/deploy-app.sh`

**明确不修改**：
- 不修改现有 PDF/MD/Transcript 的容器配置

**验证方法**：
- Docker 构建成功
- 镜像体积符合预期
- 部署后 Office 功能正常

**验收标准**：生产环境可部署，Office 功能可用

**风险**：LibreOffice 安装可能增加镜像体积 300MB+

**回滚方式**：回退 Dockerfile 和 Compose，使用旧镜像

**是否改变索引契约**：否

**是否需要重建索引**：否



### 阶段 12：自动化测试和用户验收

**目标**：编写 Office 文档处理的自动化测试和手工验收矩阵。

**当前代码依据**：
- `tests/test_providers.py` — 现有测试模式
- `tests/` — 测试目录

**前置依赖**：阶段 2-11

**测试样本**：
- 中文普通 DOCX
- 带标题层级和表格的 DOCX
- 带公式、SmartArt 或复杂浮动对象的 DOCX
- 多 Sheet XLSX
- 宽表和万行级 XLSX
- 合并单元格、隐藏行列、公式和格式化值的 XLSX
- 中文 PPTX
- 带表格、备注、图表和特殊字体的 PPTX
- 损坏文件
- 伪造扩展名
- 超大文件
- LibreOffice 转换超时
- 同名文件重传
- 删除文件及缓存

**验收矩阵**：
- 点击引用后的 DOCX 段落定位
- 点击引用后的 XLSX 单元格区域定位
- 点击引用后的 PPTX 页码定位
- 匿名用户、普通用户和管理员的文件访问权限
- 原生渲染失败时显示兼容预览入口
- 下载原文件按钮可用

**拟修改文件**：
- `tests/test_office_convert.py`（新增）
- `tests/test_office_upload.py`（新增）
- `frontend/` — 前端测试（如有）

**验证方法**：`pytest tests/` 通过，前端构建通过

**验收标准**：所有测试用例通过

**风险**：测试样本可能占用大量存储空间

**回滚方式**：不涉及

**是否改变索引契约**：否

**是否需要重建索引**：否
