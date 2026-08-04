# 文档摄取与索引

- 状态：已实现
- 最后核对：2026-08-05

## 用户可观察能力

管理员可以上传并索引 PDF、Markdown、DOCX、XLSX、PPTX 和教学视频转录稿；系统将内容解析、分块、向量化并写入检索存储。资料管理页以资料列表为主视图，并合并展示已索引状态与最近一次索引任务状态。

## 当前边界

### 已实现

- PDF 经过 MinerU 解析为 Markdown；
- 非教学视频分类的 `.md` 按普通文档处理；
- `教学视频/` 下的 `.md` 按 Transcript 处理；
- Parent 写入 `parents.sqlite`，Child 写入 Qdrant；
- 管理端资料总览、搜索、分类/类型/状态筛选、分页、重试、重新索引与安全删除；
- 上传抽屉支持拖放、多文件队列、逐文件移除及前置格式校验；
- 资料列表合并 Parent 索引与最近一次任务生命周期，处理中或失败但尚未进入 Parent 的资料也可见；
- 索引任务保留为折叠的辅助活动视图；
- Office 文件上传、解析、预览与引用定位。

### 未实现

- 资料详情预览、目录树、标签和批量资料操作；
- 统一跨 SQLite 与 Qdrant 的索引事务。

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

## 关键文件

- `api/routes_admin.py`
- `api/indexing.py`
- `src/ingest.py`
- `src/indexing_pipeline.py`
- `src/chunk.py`
- `src/index.py`
- `scripts/build_index.py`

## 数据契约

- `ParsedDoc → Parent/Child`；
- Child 必须保存稳定 `parent_id`；
- Parent SQLite 与 Qdrant Child 使用确定性 ID 关联；
- 管理列表的 `document_id` 由规范化源路径哈希派生，不新增持久化字段；
- 主列表状态取同一源路径的最新索引任务，并保留 `is_indexed` 区分是否已有可检索版本；
- 已完成但已无 Parent 索引的历史任务只保留在索引活动中，不再生成资料条或计入可检索统计；
- 删除源文件结果区分 `not_requested`、`deleted`、`missing` 和 `failed`；`missing` 作为幂等成功，`failed` 必须向管理员明确提示；
- `data/parents.sqlite` 是可重建索引状态，`data/app.sqlite` 不是。

## 依赖与下游消费者

- 依赖 MinerU、BGE-M3、Qdrant 和配置目录；
- 下游为检索、回答引用、管理员资料管理和黄金集。

## 不变量与安全边界

- 表格、公式和标题上下文不得被错误拆散；
- 表格摘要只增强 Child 检索文本，不修改 Parent 原始证据；
- 真实业务资料送往外部 MinerU 前必须确认授权；
- Reset、资料删除和运行中存储操作必须按专项规则确认。

## 验证

- 对目标类型执行局部索引与检索冒烟；
- 涉及 Chunk、ID、Embedding 或 Payload 时运行固定黄金集并说明重建要求；
- 管理 API 变化验证权限、失败状态、重试和持久化。
- 资料列表聚合变化验证最新任务选择、未索引任务可见性、服务端筛选/分页以及错误信息脱敏。

## 已知限制

- 索引没有统一事务覆盖 SQLite 与 Qdrant 两种存储，失败恢复依赖现有任务状态与重试流程。
- 当前资料 ID 来自部署内源路径的稳定哈希；跨部署移动源目录后不保证保持相同 ID。

## 相关决策

- PR1 实施说明：`project-docs/plans/admin-document-management-pr1.md`。

