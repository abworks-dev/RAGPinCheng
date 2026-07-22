# 文档摄取与索引

- 状态：已实现
- 最后核对：2026-07-22

## 用户可观察能力

管理员可以上传并索引 PDF、普通 Markdown 和教学视频转录稿；系统将内容解析、分块、向量化并写入检索存储。

## 当前边界

### 已实现

- PDF 经过 MinerU 解析为 Markdown；
- 非教学视频分类的 `.md` 按普通文档处理；
- `教学视频/` 下的 `.md` 按 Transcript 处理；
- Parent 写入 `parents.sqlite`，Child 写入 Qdrant；
- 管理端索引任务、状态、重试、资料列表与删除入口。

### 未实现

- 视频媒体文件上传、绑定和播放服务；
- 通用 Office 文件直接摄取。

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

## 已知限制

- 索引没有统一事务覆盖 SQLite 与 Qdrant 两种存储，失败恢复依赖现有任务状态与重试流程。

## 相关决策

- 暂无独立 ADR。

