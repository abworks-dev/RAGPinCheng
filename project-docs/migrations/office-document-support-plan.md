# Office 文档上传、解析与只读预览支持方案

> 状态：候选方案，尚未实施。  
> 适用项目：RAGPinCheng  
> 使用者：Claude Code、Codex 和项目维护人员。

## 1. 目标

在现有 RAG 管道中增加对 Word（.docx）、Excel（.xlsx）、PowerPoint（.pptx）文档的支持，包括：
- 上传与安全校验
- 索引解析（供 RAG 检索）
- 前端只读预览（右侧面板）
- 引用定位（点击正文中的引用跳转到对应位置）

## 2. 产品定位

- **只预览，不编辑**：不需要在线编辑或多人协作，不引入 ONLYOFFICE Docs、Collabora Online 等完整 Web Office 服务
- **保持现有管道**：PDF 仍走 MinerU，Office 采用适配层接入，不推翻现有架构
- **三种产物区分**：原始文件（原始 Office 文件）、解析文件（规范化 Markdown 供索引）、预览文件（必要时生成的 PDF 等只读预览产物）

## 3. 架构

```text
DOCX → Docling Slim ─────────────────────┐
PPTX → Docling Slim ─────────────────────┼→ Markdown
XLSX → openpyxl 专用转换器 ───────────────┘
                                            ↓
                         现有分块 → 表格摘要 → Embedding → Qdrant
                                            ↓
                                      前端预览面板
                                      ├─ docx-preview（DOCX 原生渲染）
                                      ├─ SheetJS + 虚拟表格（XLSX 原生渲染）
                                      └─ react-pdf（PPTX 转换 PDF 后渲染）
```

## 4. 数据契约变更

### 新增字段

| 字段 | 所属对象 | 说明 |
|------|---------|------|
| `doc_type` 扩展值 | `ParsedDoc`/`Parent`/`Child`/`SourceDTO` | 新增 `docx`/`xlsx`/`pptx` |
| `sheet_name` | `Parent`/`Child`/Qdrant payload | XLSX 工作表名 |
| `cell_range` | `Parent`/`Child`/Qdrant payload | XLSX 单元格区域（如 `A1:F12`） |
| `slide_number` | `Parent`/`Child`/Qdrant payload | PPTX 幻灯片编号 |
| `paragraph_anchor` | `Parent`/`Child`/Qdrant payload | DOCX 段落锚点（待决策具体方案） |
| `preview_path` | `ParsedDoc` | 预览文件路径（如 PPTX 转换的 PDF） |
| `preview_status` | 索引任务 | 预览生成状态 |

### 兼容性

- 新增字段不影响旧索引数据
- 旧索引的 `doc_type="pdf"` 和 `doc_type="transcript"` 保持不变
- 不需要全量重建

## 5. 上传与安全校验

### 支持的文件格式

| 格式 | 第一版 | 解析方式 | 预览方式 |
|------|--------|---------|---------|
| `.docx` | ✅ | Docling Slim → Markdown | docx-preview |
| `.xlsx` | ✅ | openpyxl → Markdown | SheetJS + 虚拟表格 |
| `.pptx` | ✅ | Docling Slim → Markdown | LibreOffice → PDF → react-pdf |
| `.doc` | ❌ 待决策 | LibreOffice 转换 | 同 .docx 但在服务器端先转 .docx |
| `.xls` | ❌ 待决策 | LibreOffice 转换 | 同 .xlsx 但在服务器端先转 .xlsx |
| `.ppt` | ❌ 待决策 | LibreOffice 转换 | 同 .pptx 但在服务器端先转 .pptx |

### 安全校验

- 扩展名 + MIME 类型 + 文件签名（magic bytes）三重校验
- 压缩炸弹检测（zip bomb）
- 宏文件检测（拒绝带 VBA 的 Office 文件）
- 外部链接检测
- 超大文件检测（复用现有 `MAX_UPLOAD_BYTES`）

## 6. 索引解析

### DOCX（Docling Slim）

- 标题、段落、列表、表格、图片说明和公式 → Markdown
- 页眉页脚、目录、批注、修订：丢弃
- 隐藏内容：丢弃
- 图片：评估是否提取（影响 Markdown 可读性）
- 输出确定性：基于 Docling 固定版本

### XLSX（openpyxl 专用转换器）

- 按工作表输出 Markdown 表格
- 有效数据区域识别
- 合并单元格处理
- 隐藏 Sheet、行和列：跳过
- 公式取缓存值
- 超宽/超长表格分块
- 每个 Chunk 保存 `sheet_name` + `cell_range`

### PPTX（Docling Slim）

- 幻灯片标题、正文、备注、表格、图片说明 → Markdown
- Markdown 中保留幻灯片边界（`---`）
- 每个 Chunk 保存 `slide_number`

## 7. 前端预览架构

### 统一预览面板

```tsx
DocumentPreview
├─ PdfPreview          ← 现有（PDF，PPTX 转换后）
├─ DocxPreview         ← 新增（docx-preview）
├─ SpreadsheetPreview  ← 新增（SheetJS + 虚拟表格）
└─ UnsupportedPreview  ← 新增（提示不支持）
```

### 统一定义

- 打开/关闭
- 标题
- 加载状态
- 缩放
- 下载原文件
- 引用定位
- 错误处理
- 兼容预览切换（原生渲染失真时切换为 PDF 预览）
- 移动端行为

## 8. 已确认决策

| # | 问题 | 决策 |
|---|------|------|
| 1 | DOCX 结构锚点 | 文本哈希（段落前 50 字符的哈希值） |
| 2 | XLSX 虚拟表格组件 | `@tanstack/react-virtual` + 自行渲染（MIT 许可证） |
| 3 | SheetJS 授权 | ✅ CE 版（Apache 2.0）满足生产使用要求 |
| 4 | LibreOffice 部署方式 | 独立容器（新增 `libreoffice` 服务，不放入 backend 镜像） |
| 5 | 旧版 .doc/.xls/.ppt | 第一版不支持，延后到第二版 |
| 6 | 部分成功状态 | 允许，索引成功 + 预览失败显示"预览暂不可用"，可单独重试预览 |
| 7 | Schema 迁移 | 需要，新增 `doc_type` 值：`docx`/`xlsx`/`pptx`，不再复用 `doc_type="pdf"` |
| 8 | 复杂 DOCX 自动降级 | 允许，但需显式标记 `parsed_via="mineru_fallback"`，`doc_type` 仍标记为 `docx` |

## 9. 实施阶段

| 阶段 | 内容 | 依赖 |
|------|------|------|
| 1 | 契约与样本基线 | 无 |
| 2 | Office 上传和公共解析接口 | 1 |
| 3 | DOCX 索引解析 | 2 |
| 4 | DOCX 前端预览 | 3 |
| 5 | XLSX 索引解析 | 2 |
| 6 | XLSX 前端预览 | 5 |
| 7 | PPTX 索引与预览 | 2, 8 |
| 8 | LibreOffice 转换服务 | 7 |
| 9 | 引用定位 | 4, 6, 7 |
| 10 | 安全、性能和故障恢复 | 2-8 |
| 11 | Docker 与生产发布 | 2-8, 10 |
| 12 | 自动化测试和用户验收 | 2-11 |