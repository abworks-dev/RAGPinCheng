# 文档摄取与索引

- 状态：已实现
- 最后核对：2026-08-26

## 用户可观察能力

受管资料库功能开启后，“资料库”是普通资料唯一的网页上传、确认和发布入口，支持 PDF、Markdown、DOCX、XLSX、PPTX 和 XMind。具有独立资料权限的人员按“整理上传 → 负责人确认 → 管理员发布”处理普通资料；分类、业务资料、版本、审核、发布和正式 head 由 `app.sqlite` 管理，只有发布成功的当前版本进入正式检索。“索引任务”位于资料管理的第四个页签，只展示受管资料发布任务，不再暴露旧目录索引列表或其重试、重新索引和删除操作；旧 `/admin/index` 兼容重定向到 `/admin/content?view=index`。教学视频转录稿仍走既有媒体链路。

## 当前边界

### 已实现

- PDF 经过 MinerU 解析为 Markdown；
- XMind 使用受限 ZIP 解析器读取现代 `content.json` 或旧版 `content.xml`，按画布和主题层级转换为 Markdown；
- 非教学视频分类的 `.md` 按普通文档处理；
- `教学视频/` 下的 `.md` 按 Transcript 处理；
- Parent 写入 `parents.sqlite`，Child 写入 Qdrant；
- “索引任务”以受管资料发布任务为唯一主体，提供最新发布版本状态汇总、名称搜索、完整数据库分类路径、文件类型/来源/状态筛选、分页和历史尝试切换；默认列表和状态汇总排除已移入回收站的资料，用户可显式包含并查看“已下架”标识及不参与检索的说明；列表展示版本、文件大小、正式 head 身份和当前正式版本的 Parent 数量，并可打开原文件；拥有 `index.view` 的人员可以进入页面，管理员或同时拥有 `item.publish` 的人员可对未归档资料的最新失败尝试直接重新发布；
- `CONTENT_MANAGEMENT_ENABLED=true` 时旧 `/api/admin/upload` 在写文件前返回 `409`，避免绕过受管流程；
- 发布任务默认按资料版本聚合最新一次尝试，处理中和失败任务均可见；切换历史模式后显示全部尝试；
- 旧目录索引列表 API 和网页重试、重新索引、删除入口已经撤除；历史 `index_jobs` 仅作为内部兼容数据，不再构成管理页面能力；
- Office 文件上传、解析、预览与引用定位；DOCX 短段落使用与前端一致的文本哈希锚点，PPTX 从 OOXML 演示顺序提取每页文本，XLS/XLSX 以工作表标题和范围联合匹配，定位不再依赖长标题或长段落启发式。
- Office 新资料处理可由部署级 `OFFICE_PROCESSING_ENABLED` 独立停用；关闭不影响既有资料检索和预览。
- 受管资料库添加式 Schema、七个首批一级分类、无业务层级上限的分类树、稳定 `category_key`、显示编号/名称和导入别名；Schema v34 将既有 `category_nodes.level` 的四级 CHECK 放宽为正整数约束，升级时保留现有分类记录；
- 资料权限拆分为工作台入口、资料查看/上传/提交/移动/归档/确认/发布、已发布资料分类调整、回收站查看/恢复、分类查看/维护、目录申请/审批、服务器导入和索引查看等 20 个节点；管理员默认拥有全部资料权限；`item.reclassify_published` 只加入发布负责人和系统管理员系统模板，不自动授予既有自定义用户或自定义权限组；
- 资料权限在用户管理页按业务领域集中显示和配置；行操作中的“设置权限”可套用普通成员、资料浏览者、BIM工程师、资料负责人、发布负责人、分类管理员、系统管理员预设，也可精确勾选个人权限；前端自动维护权限依赖，后端拒绝缺少前置权限的组合；
- 权限组是可复用模板，自定义模板支持创建、复制、编辑和停用；套用时复制精确权限，后续修改模板不会改变既有用户；
- 前端在打开用户菜单、进入资料工作台、窗口重新获得焦点以及资料接口返回 `403` 时刷新当前用户权限；进入 `/admin` 前必须完成服务端复核，全部资料权限撤回后立即退出工作台，局部撤权则保留仍获授权的页面和操作；
- 网页多文件上传、服务器 `inbox/server/<batch_id>/` dry-run/apply 导入、审核发布状态机和串行候选索引；
- “资料库”支持状态汇总、搜索、分类/状态/来源筛选、分页、完整分类路径、文件预览/下载和资料详情；
- “资料库”将受控分类树呈现为网盘式目录：支持子目录卡片、面包屑、当前目录直接资料，以及拖放或选择本地文件和文件夹；文件夹由浏览器递归读取并携带相对路径，不生成或解压 ZIP，空目录不创建，不支持格式会在确认弹窗中列明并忽略；普通整理员只可复用已有目录，分类管理员可按业务需要补建任意深度的缺失目录；
- “资料库”中的文件夹排在文件资料之前，编号继续作为文件夹名称前缀显示，不新增独立“顺序”列；分类管理员可直接调整编号、移动位置、重命名或打开文件夹。编号是唯一的用户侧顺序概念：选择已占用编号时先确认，继续后目标文件夹插入该位置并自动连续重排同级编号；移动到其他目录后排在目标末尾，同时连续重排源目录和目标目录。移动选择器复用分类目录树并明确禁用自身、后代和同级重名目标，不再因层级深度或旧显示编号碰撞阻止移动；
- 普通整理员可在当前目录提交子文件夹申请，资料负责人审批后由系统在同一事务中创建受控目录；待处理申请只向确认人员和分类管理员展示，重复待审申请与重复审批会被拒绝；
- 桌面端可将允许移动的既有资料行拖入当前目录的子文件夹；移动仍由后端按资料状态和权限校验，移动端继续使用显式“移动”操作；
- 草稿和退回资料可由整理员“移动资料”，待确认资料可由确认人员移动；当前正式发布且没有待处理新版本的普通资料可由具有独立权限的人员“调整分类”，后台串行同步 `app.sqlite`、`parents.sqlite`、Qdrant payload 和只读目录视图；已确认、发布中或存在待处理新版本的资料仍不能直接调整正式分类；视频转录稿继续只“调整归档目录”，不改视频、转录发布状态或索引；
- 已发布资料分类调整使用持久化任务状态 `pending → applying → committing → succeeded`；同步期间列表保留原分类并显示“分类调整中”，失败后保留或恢复原分类并允许重新提交。进程重启不会盲目续跑非终态任务，而会按源分类补偿 Qdrant、Parent、资料记录和只读视图后标记失败；
- 确认、退回和发布支持单项或批量操作；批量结果逐项返回并展示失败原因，失败项可重试；
- 分类稳定标识由服务端生成且不在业务界面展示；“分类设置”以可搜索、可按状态筛选的树形主从工作台展示完整层级和直接资料数，桌面端在右侧维护选中分类，平板和移动端使用 Sheet 完成同一编辑流程；详情顶部集中展示目录结构元数据及新增子分类、移动和删除操作，一级分类的删除入口和删除预检仅系统管理员可用，所有下级分类仍由 `category.manage` 维护；详情只读展示当前编号，通过“调整编号”进入与资料库一致的占用确认和连续重排流程，新建分类可选择插入编号；不提供独立排序序号、上移、下移或拖动排序。跨父级移动选择目标父分类后自动追加到末尾并重新编号；含直接资料或启用子分类的分类不得停用；新建一级分类默认纳入问答且显示为筛选项，新建下级分类默认纳入问答但不显示为筛选项，不批量改写既有分类；每个活动分类可分别设置是否纳入企业知识问答、是否显示为对话筛选项，后者依赖前者，停用分类会同时关闭两项；
- SHA-256 内容寻址对象存储，物理对象可复用而业务资料记录保持独立；
- Parent、Child、parents.sqlite 和 Qdrant payload 携带 nullable 的 `content_item_id`、`content_version_id` 和 `category_key`；
- 无可识别 Markdown 标题的文档开头区段在索引内部使用稳定 `section_path="(intro)"`；该值参与既有 Parent/Child 身份与向量上下文，用户界面将其显示为“文档开头”，不要求重建旧索引；
- Parent SQLite 保存 Office 定位元数据；检索结果与来源 DTO 贯通 Excel 工作表/单元格、PowerPoint 页码和 Word 段落锚点，并在字段缺失时安全回退到章节定位；
- PDF 解析缓存同时保留版本化定位侧车，记录文本证据到原始页码的映射；Parent/Child、SQLite、Qdrant 和来源 DTO 贯通 `page_number/page_end`，预览按页跳转并在文本层可用时高亮；
- XMind 解析缓存生成版本化主题定位侧车，以完整画布/主题层级精确关联 `topic_id`；短标题和不同分支的同名主题按结构化路径区分，引用预览可定位到对应主题；
- 检索按 `content_item_heads` 过滤受管版本，`compat` 模式继续允许旧的未版本化索引；
- 发布时生成保留原扩展名的工作副本供解析和 Office 预览，引用接口通过稳定 `content://` 身份回取正式对象；
- 受管 PDF 在 `data/parsed/managed/<content_version_id>/` 中使用独立解析缓存和临时目录，不依赖旧 `docs` 相对路径；空密码加密 PDF 使用临时解密副本解析且不修改正式对象，真正需要密码的 PDF 明确失败；MinerU 云解析结果原子写入 `document.md`，同一版本重试复用该缓存；
- MinerU 请求使用确定性的短 ASCII `name/data_id`，不把超长原始文件名用作供应商任务标识；原始文件名仍保留在资料、引用和界面中；
- MinerU 的上传和排队阶段在受管发布任务中折叠为合法的 `parsing` 状态；发布失败通过稳定错误码及结构化的中文原因、可重试性和建议操作暴露给管理页，供应商响应、存储路径和完整异常仅保留在后端日志；索引任务默认每个版本只显示最新尝试，可按需查看完整历史和总尝试次数，历史模式不改变按最新尝试计算的状态汇总；
- 可重建的完整编号目录树只读视图；视图覆盖全部分类层级，是副本而不是分类或索引事实来源；
- 代码和普通环境默认关闭受管资料库；当前 Ubuntu 生产已显式设置 `CONTENT_MANAGEMENT_ENABLED=true`。T12-B 数据切换后，生产环境契约使用 `CONTENT_HEAD_ENFORCEMENT=strict` 和 `SOURCE_DECOUPLING_COMPLETE=true`。
- 当前生产根目录为 `/data/business/ragpincheng/content`（容器内 `/app/content`）；2026-08-14 已完成 117 条旧普通资料迁移记录的收口，其中 116 份建立正式 `content_item_heads`，1 份生成预览被安全排除。T11 删除了对应旧普通资料索引；T12-B 又归档 4 条旧媒体记录、删除 2 个旧 transcript head，并精确删除剩余 44 个旧 Parent 与 104 个 Qdrant Point。旧 `source/docs` 和 `source/media` 文件仍保留，未自动删除。
- 代码支持 `DOCS_DIR`、`MEDIA_DIR`、`DOCS_HOST_PATH`、`MEDIA_HOST_PATH` 和 `TRANSCRIPTION_ARTIFACT_DIR` 显式配置；本地兼容模式的 `DOCS_DIR` 和 Compose 宿主机默认值仍指向 `content/legacy-docs`，仓库 `docs/` 只存放项目文档。生产完成标记为 `true` 时，仓库内最终 Compose overlay 必须位于私有生产 override 之后，并以 `!override` 完整替换 backend volumes：重新声明 `/app/data`、`/app/content`、`/app/media`，把 `/app/docs` 替换为空的只读 tmpfs。生产不再要求 `DOCS_HOST_PATH` 或宿主机 `content/legacy-docs`；部署、失败回滚和手工恢复共用该顺序。
- `strict` 检索契约按身份分流：普通资料必须命中 `content_item_heads`，带 `transcript_version_id` 的转录由独立 `media_transcript_heads` 快照校验；双重无版本身份的旧 Parent/Child 被拒绝。生产部署会同时核对 strict 配置、受管 head、索引计数和容器 mount source，从容器 mountinfo 核对 `/app/docs` 是带 `ro` 选项的 tmpfs 并执行不可写探针，同时拒绝任何仍来自 `/data/business/ragpincheng/source` 的 `/app/media` 或 `/app/content` 挂载；失败时恢复上一镜像。
- T12-B 提供精确 plan/apply/verify 和受控生产 workflow：候选 ID、媒体/head、正式版本及审计表均指纹化，活动任务或冻结摘要漂移会在写入前失败；执行仅归档旧媒体记录、删除旧 transcript head 和精确候选 Parent/Point，不重建 collection，也不删除旧文件。
- 生产全量索引重建只允许通过 `rebuild-production-index-manual.yml`：它从 `content_item_heads` 与 `media_transcript_heads` 冻结正式可见版本，在独立 Parent SQLite、解析目录和 Qdrant collection 中强制重新解析并重建；旧解析缓存不会阻止页码、段落、工作表或主题定位 sidecar 生成。Actions 日志实时输出 head/Parent/Child 进度，并在切换前分别输出 Qdrant 精确计数与按文档类型聚合的定位覆盖；活动任务、head 指纹漂移、正式版本覆盖不完整、可定位文档缺少定位字段、Parent/Child 计数不一致或 Qdrant 非绿色状态都会终止切换。

### 未实现

- 标签和可编辑的资料版本历史；
- 统一跨 SQLite 与 Qdrant 的索引事务。
- 117 条已迁移普通资料记录的观察期验收和旧目录归档；
- 将既有视频转录状态机改写为统一内容版本状态机；视频继续由 `media_assets`、`transcript_versions` 和 `media_transcript_heads` 管理。

旧资料迁移提供离线清点、确定性规划和固定 T9 恢复点的只读 T10 预检：预检会重新核对候选普通资料的大小、SHA-256 和活动分类，但不写数据库或内容根目录。规划器将 `.preview.pdf`、`.preview.xlsx` 标为生成的派生产物，T10 apply 也会拒绝旧计划或手工计划中的同类文件。受控 staging/apply CLI 已实现，apply 会登记为 `legacy` 来源并停在 `awaiting_review`；真实执行仍须独立 R3 批准，不由代码存在或预检通过自动授权。

## 入口与调用链

```text
管理员上传 / 批量构建
→ doc_type 分类
→ index_single
→ ParsedDoc
→ chunk_document / chunk_transcript
→ Parent + Child
→ store_parents(parents.sqlite)
→ index_children(Qdrant)
```

受管普通资料：

```text
网页上传或 inbox/server 批次
→ objects/sha256 + app.sqlite content item/version
→ 提交确认 → 审核批准 → 发布任务
→ published/<item>/<version>/<original_filename>
→ index_managed_content(显式业务身份和分类)
→ Parent + Child 候选
→ content_item_heads 原子切换
→ 检索按同一 head 快照过滤 Child 和 Parent
```

## 关键文件

- `api/routes_admin.py`
- `api/indexing.py`
- `src/ingest.py`
- `src/indexing_pipeline.py`
- `src/chunk.py`
- `src/index.py`
- `scripts/build_index.py`
- `api/routes_content.py`
- `api/content_store.py`
- `api/content_storage.py`
- `api/content_publication.py`
- `api/content_reclassification.py`
- `api/content_import.py`
- `api/content_view.py`
- `src/content_retrieval_visibility.py`
- `scripts/import_content_batch.py`
- `scripts/inventory_legacy_content.py`
- `scripts/plan_legacy_content_migration.py`
- `scripts/legacy_content_migration.py`
- `scripts/preflight_legacy_content_t10.py`
- `scripts/stage_legacy_content_t10.py`
- `scripts/apply_legacy_content_t10.py`
- `scripts/preflight_source_decoupling_t12.py`
- `scripts/source_decoupling_t12.py`
- `scripts/plan_source_decoupling_t12.py`
- `scripts/apply_source_decoupling_t12.py`
- `scripts/rebuild_content_view.py`
- `scripts/rebuild_managed_index.py`
- `.github/workflows/rebuild-production-index-manual.yml`
- `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- `frontend/src/pages/admin/AdminCategoriesPage.tsx`

## 数据契约

- `ParsedDoc → Parent/Child`；
- Child 必须保存稳定 `parent_id`；
- Parent SQLite 与 Qdrant Child 使用确定性 ID 关联；
- 管理列表的 `document_id` 由规范化源路径哈希派生，不新增持久化字段；列表和删除请求均使用该不透明句柄，不向浏览器返回物理路径或 `content://` 对象路径；
- 管理列表只为已有 Parent 的资料返回按 `parent_id` 排序的代表性 `preview_parent_id`，仅用于复用现有源文件预览接口；该句柄在当前索引快照内确定，重新索引后允许变化；同一资料仅有一个非空 `media_id` 时返回媒体关联并复用视频与转写稿抽屉，缺失或混杂关联时关闭视频预览；
- 管理页内预览支持 PDF、DOCX、XLSX、PPTX 和 XMind：PPTX 依赖已生成的 `.preview.pdf`，XLSX 优先使用 `.preview.xlsx`；XMind 直接从受管版本对象生成受限主题树，草稿和已发布版本均可在统一预览抽屉中切换画布、展开或收起主题；Markdown 可被索引但当前不提供管理页内渲染；源文件或转换产物缺失、浏览器解析失败时显示脱敏的可关闭错误状态；
- 主列表状态取同一源路径的最新索引任务，并保留 `is_indexed` 区分是否已有可检索版本；
- 已完成但已无 Parent 索引的历史任务只保留在索引活动中，不再生成资料条或计入可检索统计；
- 索引任务 DTO 提供 `source_exists`，仅表达源文件当前是否仍为普通文件，不暴露额外路径信息；
- 删除源文件结果区分 `not_requested`、`deleted`、`missing` 和 `failed`；`missing` 作为幂等成功，`failed` 必须向管理员明确提示；
- `data/parents.sqlite` 是可重建索引状态，`data/app.sqlite` 不是。
- 受管资料的分类身份使用数据库 ID 和稳定 `category_key`；显示编号、显示名称与物理目录名可以调整；分类列表 DTO 保持扁平兼容，服务端按树的深度优先顺序返回，所有同级节点依次按显示编号、显示名称和 ID 稳定排序。同一父分类下的显示编号由数据库唯一索引约束，不同父分类可复用编号；`sort_order` 仅作为服务端维护的兼容字段保留，不再由业务界面控制；
- `category_nodes.chat_search_enabled` 控制目录内容是否进入企业知识问答，`chat_filter_selectable` 控制目录是否出现在登录用户的范围筛选器；分类只有在自身及全部祖先均启用且纳入问答时，才进入默认检索、对话范围或直接分类检索。两项状态均受祖先约束：上级关闭问答会使整棵子树退出问答和筛选，上级仅关闭筛选会使整棵子树退出筛选但仍可参与问答。关闭父分类会排除整个子树，重新开启父分类不会改写子节点自身设置；既有迁移数据保持原值，由分类管理员逐项调整；分类管理树展示最终有效状态及“继承关闭”原因；
- 分类移动使用 `POST /api/admin/content/categories/{category_id}/move` 和 `expected_version` 乐观并发契约；服务端在单个 SQLite 写事务中校验父分类启用状态、循环引用和同级重名，按显示编号重新规范化受影响同级节点的兼容顺序、同步后代层级并记录 `category.moved` 审计事件。旧客户端的 `before_category_id` 仍可传入并接受合法性校验，但不再改变显示顺序。移动不改变 `category_key`、资料归属或索引身份，因此不触发资料移动、数据库迁移或重建索引；
- 网盘式目录复用 `category_nodes`，不新增第二套文件夹事实来源；`content_items.category_id` 表达当前目录，状态和版本不编码进目录名称；
- 文件夹上传使用 multipart `upload_mode=folder`，每个 `files` 字段都有同序 `relative_paths`；服务端在创建批次和写对象前统一拒绝绝对路径、路径穿越、NUL、文件名不一致、重复路径、非法文件夹名称和超过相对路径长度上限的路径，但不限制目录业务深度。系统维护页持久化配置单文件、单批数量和单批总大小限制，默认分别为 2000 MB、5000 个和 10240 MB；JSON 预检、multipart 正式上传、资料更新和 capabilities 每次请求读取同一份配置，保存后无需重启；
- `content_objects` 允许 SHA-256 物理去重，`content_items` 不做跨项目强制合并；
- `content_item_heads.current_version_id` 是普通受管资料正式可见性的唯一事实；候选索引完成前不切换；
- 已发布资料分类调整只修改 Qdrant Child payload 和 Parent 的 `category/category_key`、`content_items.category_id` 以及可重建只读视图；向量、Point/Parent ID、Point 数量、正式 head、版本、发布记录和索引任务保持不变。接口为 `POST /api/admin/content/items/{item_id}/reclassify`、`GET/POST /api/admin/content/reclassification-jobs/{job_id}` 与 `POST /api/admin/content/bulk-reclassify`；写接口同时要求 Cookie、CSRF 和 `item.reclassify_published`；
- `strict` 只把 `content_version_id` 要求施加到普通资料；带 `transcript_version_id` 的候选仍必须通过独立 transcript head 快照，不能因缺少 `content_version_id` 被误杀；
- `CONTENT_ROOT/views/current` 和 `inbox` 仅为导入/导出视图，不得被索引器当作事实来源。

## 依赖与下游消费者

- 依赖 MinerU、BGE-M3、Qdrant 和配置目录；
- 下游为检索、回答引用、管理员资料管理和黄金集。

## 不变量与安全边界

- 表格、公式和标题上下文不得被错误拆散；
- 表格摘要只增强 Child 检索文本，不修改 Parent 原始证据；
- 最终 `Child.embed_text` 必须满足 GPU Embedding 服务的 8192 字符上限；超长 HTML/Markdown 表格在 Child 层按行、单元格或安全文本边界拆分并传播表头，Parent 原始证据保持不变；表格摘要只能使用剩余字符预算。超长公式不静默截断，发布任务返回明确的不可重试原因；
- 真实业务资料送往外部 MinerU 前必须确认授权；
- Reset、资料删除和运行中存储操作必须按专项规则确认。
- 全量重建 workflow 在影子构建期间保持线上索引可用，只在最终切换时短暂停止 backend；切换前独立备份两个 SQLite 和正式 Qdrant snapshot。切换后验证失败时自动恢复旧 `parents.sqlite` 与正式 collection，且不会恢复或覆盖持续变化的 `app.sqlite`。
- 服务器 apply 导入只允许位于 `CONTENT_ROOT/inbox/server` 下的批次；dry-run 不写数据库或对象存储；
- T10 旧资料预检使用 SQLite `mode=ro` 并只生成脱敏聚合摘要；真实 apply 必须匹配已批准 plan 指纹、候选数、执行身份和确认词；
- 受管对象路径拒绝越界和符号链接；网页变更必须同时通过 Cookie 鉴权、资料权限和 CSRF；
- 资料权限和权限组模板仅允许全局管理员维护；`category.manage` 不能授予自身或他人的资料确认、发布或恢复权限，也不能预检或删除一级分类；
- 新能力在代码和普通环境中默认关闭；生产已经显式启用基础能力。真实资料迁移、启用严格 head、移动旧目录或清理旧索引仍属于独立 R3。

## 验证

- 对目标类型执行局部索引与检索冒烟；
- 涉及 Chunk、ID、Embedding 或 Payload 时运行固定黄金集并说明重建要求；
- 管理 API 变化验证权限、组合筛选、分页统计、失败状态和历史尝试；旧索引管理路由保持不可访问。
- 发布任务聚合变化验证最新尝试选择、历史尝试、服务端组合筛选/分页、状态统计以及错误信息脱敏。
- 超长结构化内容验证 Parent 原文不变、每个最终 `embed_text` 不超过服务上限、写 Parent/Qdrant 前完成预检，并区分可自动修复的表格与需人工处理的超长公式。
- 受管资料验证匿名/无权限/整理/确认/发布矩阵、CSRF、分类并发版本、multipart、多版本 head、对象去重、路径边界、后台 dry-run、只读视图和索引 payload。
- 目录工作区验证面包屑和直接子项、当前目录上传、文件夹选择与递归拖放、相对路径深度和文件名一致性、批次数量/容量限制、未批准目录拒绝、移动权限、移动审计，以及 1440、1280、768 和 390px 布局。
- 分类设置验证深度优先层级、显示编号排序和同父级唯一性、搜索/状态筛选、直接资料数、并发版本、停用原因、问答范围依赖、键盘树导航、可搜索目标目录树，以及 1440、1280、768 和 390px 的桌面详情/移动 Sheet 路径。

## 已知限制

- SQLite 与 Qdrant 之间仍没有原生分布式事务；已发布资料分类调整使用持久化阶段标记、写后核验和反向补偿，在短暂同步窗口内不同存储可能先后可见，任务只有在四处状态全部核验后才报告成功。
- 当前资料 ID 来自部署内源路径的稳定哈希；跨部署移动源目录后不保证保持相同 ID。
- 该路径身份限制只适用于旧索引；受管资料使用稳定业务 ID。当前生产功能开关已启用，117 份普通资料已迁移并发布，T11 已下线对应旧普通资料索引，但仍以 `compat` 模式保留尚待 T12 清点的旧视频转录和其他无版本索引。
- 只读目录视图使用文件副本以避免权限修改污染正式对象，重建时需要额外临时磁盘空间。

## 相关决策

- PR1 实施说明：`docs/plans/admin-document-management-pr1.md`。
- 受管资料库决策：[0003 — 数据库分类与受管内容资料库](../decisions/0003-managed-content-library.md)。
- R2 实施方案：[受管知识资料库实施方案](../plans/managed-content-library.md)。
- 生产运行与迁移：[受管知识资料库生产运行与旧资料迁移手册](../migrations/managed-content-production-runbook.md)。

