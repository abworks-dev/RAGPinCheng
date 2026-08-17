import type { Page, Route } from "@playwright/test";

export type AdminScenario = "normal" | "loading" | "empty" | "error" | "disabled" | "publication_failure" | "media_progress" | "media_upload" | "media_library";
export type WorkspaceUser = "admin" | "bim_engineer" | "member";

const admin = {
  id: 9001,
  employee_id: "TEST-ADMIN",
  real_name: "合成管理员",
  role: "admin",
  csrf_token: "synthetic-csrf-token",
  content_permissions: [
    "workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit",
    "item.move_draft", "item.archive_draft", "item.review", "item.move_review",
    "item.publish", "item.reclassify_published", "item.archive_published", "trash.view", "trash.restore",
    "category.manage", "folder.request", "folder.review", "import.server", "index.view",
  ],
};

const workspaceUsers = {
  admin,
  bim_engineer: {
    ...admin,
    id: 9002,
    employee_id: "TEST-EDITOR",
    real_name: "合成资料员",
    role: "user",
    content_permissions: [
      "workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit",
      "item.move_draft", "item.archive_draft", "folder.request",
    ],
  },
  member: { ...admin, id: 9003, employee_id: "TEST-MEMBER", real_name: "合成成员", role: "user", content_permissions: [] },
};

export const categories = [
  { id: "cat-company", category_key: "company_standard", parent_id: null, display_code: "03", display_name: "公司内部标准", sort_order: 10, level: 1, is_active: true, version: 3, created_at: 1700000000, updated_at: 1700000000, full_path: "03 公司内部标准", item_count: 3 },
  { id: "cat-project", category_key: "project_delivery", parent_id: null, display_code: "04", display_name: "项目资料", sort_order: 20, level: 1, is_active: true, version: 2, created_at: 1700000000, updated_at: 1700000000, full_path: "04 项目资料", item_count: 2 },
  { id: "cat-archive", category_key: "archived", parent_id: null, display_code: "99", display_name: "待确认资料", sort_order: 90, level: 1, is_active: false, version: 1, created_at: 1700000000, updated_at: 1700000000, full_path: "99 待确认资料", item_count: 0 },
];

const folderRequests = [{
  id: "folder-request-1", parent_category_id: "cat-company", parent_label: "03 公司内部标准",
  display_name: "审核标准", status: "pending", requester_name: "合成资料员", review_note: null,
  created_category_id: null, created_at: 1700000000, updated_at: 1700000000, reviewed_at: null,
}];

export const items = [
  ["draft", "建筑信息模型交付标准（合成长文件名用于响应式检查）.pdf"],
  ["awaiting_review", "机电专业协同检查清单.docx"],
  ["approved", "项目资料归档指引.xlsx"],
  ["publication_failed", "培训资料发布演练.pptx"],
  ["published", "企业知识库使用规范.md"],
].map(([status, filename], index) => ({
  item_id: `item-${index + 1}`,
  title: filename.replace(/\.[^.]+$/, ""),
  content_kind: "document",
  category_id: index % 2 ? "cat-project" : "cat-company",
  category_key: index % 2 ? "project_delivery" : "company_standard",
  category_label: index % 2 ? "04 项目资料" : "03 公司内部标准",
  category_path: index % 2 ? "04 项目资料 / 02 竣工交付 / 01 模型成果" : "03 公司内部标准 / 01 建模 / 02 机电",
  media_id: null,
  preview_parent_id: index <= 1 ? "parent-ready" : null,
  preview_status: index <= 1 ? "ready" : filename.endsWith(".pptx") || filename.endsWith(".xlsx") ? "pending" : "not_applicable",
  version_id: `version-${index + 1}`,
  version_number: index + 1,
  original_filename: filename,
  doc_type: filename.split(".").pop(),
  lifecycle_status: status,
  object_sha256: null,
  source_origin: index === 4 ? "legacy" : "web",
  source_batch_id: null,
  is_current: true,
  has_published_head: status === "published",
  latest_publication_status: null,
  publication_attempt_count: 0,
  publication_failure: null,
  latest_reviewed_by_name: index === 1 ? "合成审核员" : null,
  latest_reviewed_at: index === 1 ? 1700000500 : null,
  latest_review_decision: index === 1 ? "rejected" : null,
  latest_review_note: index === 1 ? "请补充机电碰撞检查范围" : null,
  reclassification_job_id: null,
  reclassification_status: null,
  created_at: 1700000000,
  updated_at: 1700000000,
}));

const mediaLibraryItem = {
  item_id: "media-transcript-media-library-1",
  title: "BIM 项目交付培训视频（合成长标题用于响应式检查）",
  content_kind: "media_transcript",
  category_id: "cat-company",
  category_key: "company_standard",
  category_label: "03 公司内部标准",
  category_path: "03 公司内部标准 / 01 建模 / 02 培训视频",
  media_id: "media-library-1",
  preview_parent_id: null,
  preview_status: "not_applicable",
  version_id: "66666666-6666-4666-8666-666666666666",
  version_number: 3,
  original_filename: "bim-project-delivery-training-long-responsive-name.mp4",
  doc_type: "transcript",
  lifecycle_status: "published",
  object_sha256: null,
  source_origin: "transcription",
  source_batch_id: null,
  source_rel_path: "bim-project-delivery-training-long-responsive-name.mp4",
  is_current: true,
  has_published_head: true,
  latest_publication_status: "done",
  publication_attempt_count: 2,
  publication_failure: null,
  media_duration_ms: 3_723_000,
  media_file_size: 84_934_656,
  has_pending_revision: true,
  reclassification_job_id: null,
  reclassification_status: null,
  created_at: 1700000000,
  updated_at: 1700000600,
};

const trashItems = [{
  ...items[4],
  source_rel_path: "公司知识库归档/制度与流程/企业知识库使用规范.md",
  archived_at: 1700000600,
  archived_by_name: "合成资料员",
  pre_archive_lifecycle_status: "published",
  retention_status: "expiring", retention_days_remaining: 4, purge_eligible_at: 1700346200,
}, {
  ...items[0], item_id: "item-trash-overdue", version_id: "version-trash-overdue",
  title: "项目交付检查清单", original_filename: "项目交付检查清单.pdf",
  archived_at: 1690000000, archived_by_name: "合成管理员",
  pre_archive_lifecycle_status: "approved", retention_status: "overdue",
  retention_days_remaining: -12, purge_eligible_at: 1697776000,
}];

const uploadTasks = [
  {
    batch_id: "batch-upload-failed", upload_mode: "files", status: "failed",
    target_category_id: "cat-company", target_path: "01 行业规范与标准 / 02 文件夹上传测试",
    total_files: 1, accepted_files: 0, skipped_files: 0, total_bytes: 42_000, total_uploaded_bytes: 0,
    created_by_name: "合成资料员", created_at: 1786927800, updated_at: 1786927800,
    error_summary: "上传连接中断，可以重试。", entries: null,
  },
  {
    batch_id: "batch-upload-partial", upload_mode: "folder", status: "partial_success",
    target_category_id: "cat-project", target_path: "04 项目资料 / 01 模型成果 / 合成长目录名称用于响应式检查",
    total_files: 3, accepted_files: 2, skipped_files: 1, total_bytes: 126_000, total_uploaded_bytes: 84_000,
    created_by_name: "合成管理员", created_at: 1786927500, updated_at: 1786927500,
    error_summary: null, entries: null,
  },
  {
    batch_id: "batch-upload-completed", upload_mode: "files", status: "completed",
    target_category_id: "cat-project", target_path: "04 项目资料 / 02 竣工交付",
    total_files: 2, accepted_files: 2, skipped_files: 0, total_bytes: 86_000, total_uploaded_bytes: 86_000,
    created_by_name: "合成资料员", created_at: 1786927200, updated_at: 1786927200,
    error_summary: null, entries: null,
  },
];

const indexedDocuments = [
  {
    document_id: "document-ready", display_path: "公司标准 / synthetic-ready.pdf",
    filename: "建筑信息模型交付标准（合成长文件名用于资料列表响应式检查）.pdf", doc_title: "建筑信息模型交付标准（合成长文件名用于响应式检查）",
    category: "公司标准", doc_type: "pdf", company: null, parent_count: 18, child_count: 54,
    preview_parent_id: "parent-ready", media_id: null, file_size: 2_048_000, status: "done", is_indexed: true,
    latest_job_id: 101, error_summary: null, uploaded_by: "合成管理员", created_at: 1700000000, updated_at: 1700000300,
  },
  {
    document_id: "document-processing", display_path: "项目资料 / synthetic-processing.docx",
    filename: "机电专业协同检查清单.docx", doc_title: "机电专业协同检查清单",
    category: "项目资料", doc_type: "docx", company: null, parent_count: 0, child_count: null,
    preview_parent_id: null, media_id: null, file_size: 384_000, status: "embedding", is_indexed: false,
    latest_job_id: 102, error_summary: null, uploaded_by: "合成资料员", created_at: 1700000100, updated_at: 1700000400,
  },
  {
    document_id: "document-failed", display_path: "客户标准 / 合成客户 / synthetic-failed.xlsx",
    filename: "项目资料归档检查表.xlsx", doc_title: "项目资料归档检查表",
    category: "客户标准", doc_type: "xlsx", company: "合成客户", parent_count: 0, child_count: null,
    preview_parent_id: null, media_id: null, file_size: 96_000, status: "failed", is_indexed: false,
    latest_job_id: 103, error_summary: "解析器暂不可用，可以重试。", uploaded_by: "合成管理员", created_at: 1700000200, updated_at: 1700000500,
  },
];

const indexJobs = [
  { id: 101, user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", filename: indexedDocuments[0].filename, category: "公司标准", doc_type: "pdf", source_path: "synthetic/ready.pdf", source_exists: true, file_size: 2_048_000, status: "done", error: null, parents: 18, children: 54, created_at: 1700000000, started_at: 1700000010, finished_at: 1700000300 },
  { id: 102, user_id: 9002, employee_id: "TEST-EDITOR", real_name: "合成资料员", filename: indexedDocuments[1].filename, category: "项目资料", doc_type: "docx", source_path: "synthetic/processing.docx", source_exists: true, file_size: 384_000, status: "embedding", error: null, parents: 0, children: 0, created_at: 1700000100, started_at: 0, finished_at: null },
  { id: 103, user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", filename: indexedDocuments[2].filename, category: "客户标准", doc_type: "xlsx", source_path: "synthetic/failed.xlsx", source_exists: true, file_size: 96_000, status: "failed", error: "解析器暂不可用", parents: 0, children: 0, created_at: 1700000200, started_at: 1700000210, finished_at: 1700000250 },
  { id: 104, user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", filename: "已移除源文件的历史资料.pptx", category: "培训资料", doc_type: "pptx", source_path: "synthetic/missing.pptx", source_exists: false, file_size: 512_000, status: "done", error: null, parents: 12, children: 36, created_at: 1699990000, started_at: 1699990010, finished_at: 1699990200 },
];

const managedIndexJobs = [{
  id: "managed-job-1", publication_id: "publication-1", version_id: "version-1",
  attempt_number: 1, status: "failed", error_code: "parser_request_failed",
  error_summary: "文档解析服务请求失败，请稍后重试。",
  failure: { code: "parser_request_failed", message: "文档解析服务请求失败。", retryable: true, recommended_action: "请稍后重试；持续失败时联系系统管理员。" },
  attempt_count: 4, created_at: 1700000000, started_at: 1700000010, finished_at: 1700000020, updated_at: 1700000020,
  title: "资料管理发布失败的合成长文件名资料", original_filename: "managed-publication-failure-with-long-name.pdf",
  doc_type: "pdf", category_id: "cat-03", category_label: "03 公司内部标准",
  category_path: "03 公司内部标准 / 01 建模标准", version_number: 4, file_size: 2_048_000,
  source_origin: "legacy", is_archived: false, is_current_head: false, is_latest_attempt: true,
  parent_count: null, preview_parent_id: null,
}];

const archivedManagedIndexJob = {
  ...managedIndexJobs[0],
  id: "managed-job-archived",
  publication_id: "publication-archived",
  version_id: "version-archived",
  status: "done",
  error_code: null,
  error_summary: null,
  failure: null,
  attempt_count: 1,
  title: "已移入回收站的合成资料",
  original_filename: "archived-managed-document.xlsx",
  doc_type: "xlsx",
  is_archived: true,
};

const mediaAssets = [
  {
    media_id: "media-failed-1", title: "机电协同培训录像", original_filename: "mep-training-recording.mp4",
    mime_type: "video/mp4", file_size: 3_456_789, transcript_origin: "generated", status: "failed",
    review_status: "not_required", publication_status: "not_published", publication_index_status: "pending",
    created_at: 1700000400, updated_at: 1700000400, error: "provider_unavailable",
  },
  {
    media_id: "media-failed-2", title: "机电协同培训录像（重复提交）", original_filename: "mep-training-recording.mp4",
    mime_type: "video/mp4", file_size: 3_456_789, transcript_origin: "generated", status: "failed",
    review_status: "not_required", publication_status: "not_published", publication_index_status: "pending",
    created_at: 1700000300, updated_at: 1700000300, error: "provider_unavailable",
  },
  {
    media_id: "media-ready", title: "项目交付培训", original_filename: "project-delivery-training.mp4",
    mime_type: "video/mp4", file_size: 8_765_432, transcript_origin: "generated", status: "transcript_ready",
    review_status: "awaiting_review", publication_status: "not_published", publication_index_status: "pending",
    created_at: 1700000200, updated_at: 1700000200, error: null,
  },
];

const transcriptionProfiles = [{
  profile_id: "funasr-sensevoice-zh-experimental-v1", display_name: "受控中文转录", description: "合成服务端 Profile",
  qualification: "experimental", admission: "enabled", availability: "available", unavailable_reason_code: null,
  requires_review: true, auto_publish: false, auto_index: false,
}];

const asrProfiles = ([
  ["natural", null, 500, 1000, "自然分段"],
  ["balanced", 30_000, 240, 750, "均衡分段"],
  ["fine", 15_000, 120, 500, "细分段"],
] as const).map(([preset, maxDuration, maxChars, mergeGap, label]) => ({
  profile_id: `whisperx-large-v3-zh-${preset}-v2`,
  display_name: `WhisperX 工程转录 ${label} v2`,
  description: `${label}合成配置，用于验证长中文名称、固定术语和响应式布局。`,
  profile_version: "2",
  application_config_hash: preset.padEnd(64, "a"),
  qualification: "qualification_approved",
  admission: preset === "balanced" ? "enabled" : "disabled",
  availability: "available",
  unavailable_reason_code: null,
  release_eligible: true,
  segmentation: {
    preset,
    max_segment_duration_ms: maxDuration,
    max_segment_chars: maxChars,
    max_merge_gap_ms: mergeGap,
  },
  terminology_rule_set: "bim-engineering-v1",
  protected_terms: ["Revit", "Navisworks", "AutoCAD", "BIM", "BIM-2026-0805", "12.5", "208", "95%"],
  decode: {
    service_profile_id: "whisperx-large-v3-zh-align-v2",
    model_name: "Whisper large-v3 + 中文对齐",
    beam_size: 10,
    temperature: 0.1,
    hotword_count: 20,
    prompt_asset_id: "asr_engineering_zh_v2",
    service_profile_config_hash: "a".repeat(64),
    qualification_policy: "whisperx-r3/1",
  },
}));

const asrReleaseRequest = {
  request_id: "11111111-1111-4111-8111-111111111111",
  profile_id: "whisperx-large-v3-zh-balanced-v2",
  profile_display_name: "WhisperX 工程转录 均衡分段 v2",
  profile_config_hash: "balanced".padEnd(64, "a"),
  status: "requested",
  request_reason: "合成培训视频发布申请",
  requested_by_name: "合成管理员",
  created_at: 1700000000,
  updated_at: 1700000000,
};

const transcriptionJobs = [{
  job_id: "media-failed-job", media_id: "media-failed-1", attempt_number: 1,
  profile_id: "funasr-sensevoice-zh-experimental-v1", status: "failed", stage: null,
  processed_ms: 0, total_ms: 0, failure_error_code: "provider_unavailable",
  error_summary: "转录服务当前暂停接收任务，请稍后重试。",
  failure: { code: "provider_unavailable", message: "转录服务当前暂停接收任务，请稍后重试。", retryable: true },
  result_version_id: null, created_at: 1700000400, started_at: 1700000401, finished_at: 1700000402, updated_at: 1700000402,
}];

const baseTranscriptVersion = {
  version_id: "11111111-1111-4111-8111-111111111111", media_id: "media-ready", source: "automatic",
  profile_id: "synthetic-profile", provider_key: "synthetic-asr", model_id: "synthetic-model", model_revision: "r1",
  markdown_storage_kind: "managed_artifact",
  review_status: "awaiting_review", reviewed_by: null, reviewed_at: null, review_note: null,
  publication_status: "not_published", published_at: null, supersedes_version_id: null,
  derived_from_version_id: null, edited_by: null,
  markdown_sha256: "a".repeat(64), created_at: 1700000200, updated_at: 1700000200, is_current: false,
};

const transcriptVersions = [
  baseTranscriptVersion,
  {
    ...baseTranscriptVersion,
    version_id: "22222222-2222-4222-8222-222222222222",
    source: "manual",
    profile_id: null,
    provider_key: null,
    model_id: null,
    model_revision: null,
    review_status: "review_approved",
    derived_from_version_id: baseTranscriptVersion.version_id,
    markdown_sha256: "b".repeat(64),
    created_at: 1700000190,
  },
  {
    ...baseTranscriptVersion,
    version_id: "33333333-3333-4333-8333-333333333333",
    review_status: "review_rejected",
    review_note: "时间轴术语需要重新核对",
    markdown_sha256: "c".repeat(64),
    created_at: 1700000180,
  },
  {
    ...baseTranscriptVersion,
    version_id: "44444444-4444-4444-8444-444444444444",
    review_status: "review_approved",
    publication_status: "published",
    published_at: 1700000175,
    markdown_sha256: "d".repeat(64),
    created_at: 1700000170,
    is_current: true,
  },
  {
    ...baseTranscriptVersion,
    version_id: "55555555-5555-4555-8555-555555555555",
    source: "manual",
    profile_id: null,
    provider_key: null,
    model_id: null,
    model_revision: null,
    markdown_storage_kind: "legacy_manual",
    review_status: "not_required",
    markdown_sha256: "e".repeat(64),
    created_at: 1700000160,
  },
];

const permissionUsers = [
  { user_id: 9001, employee_id: "TEST-ADMIN", real_name: "合成管理员", role: "admin", is_active: true, permissions: [] },
  {
    user_id: 9002,
    employee_id: "TEST-EDITOR",
    real_name: "合成资料员",
    role: "user",
    is_active: true,
    permissions: [
      "workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit",
      "item.move_draft", "item.archive_draft", "folder.request", "item.review",
      "item.move_review", "folder.review", "trash.view", "trash.restore",
    ],
  },
  { user_id: 9003, employee_id: "TEST-INACTIVE", real_name: "停用测试用户", role: "user", is_active: false, permissions: [] },
];

const permissionCatalog = {
  schema_version: 4,
  permissions: [
    { key: "workspace.view", domain: "access", domain_label: "入口与查看", label: "进入资料工作台", description: "进入资料管理工作台。", dependencies: [] },
    { key: "item.view", domain: "access", domain_label: "入口与查看", label: "查看资料", description: "查看资料列表、详情和预览。", dependencies: ["workspace.view"] },
    { key: "item.download", domain: "access", domain_label: "入口与查看", label: "下载资料", description: "下载单份资料或批量打包下载。", dependencies: ["workspace.view", "item.view"] },
    { key: "category.view", domain: "access", domain_label: "入口与查看", label: "查看分类", description: "查看资料分类树和完整路径。", dependencies: ["workspace.view"] },
    { key: "item.upload", domain: "organize", domain_label: "资料整理", label: "上传资料", description: "上传文件并创建资料草稿。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "item.submit", domain: "organize", domain_label: "资料整理", label: "提交确认", description: "将草稿或退回资料提交确认。", dependencies: ["workspace.view", "item.view"] },
    { key: "item.move_draft", domain: "organize", domain_label: "资料整理", label: "移动草稿", description: "移动草稿或退回状态的资料。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "item.archive_draft", domain: "organize", domain_label: "资料整理", label: "归档草稿", description: "将草稿或退回资料移入回收站。", dependencies: ["workspace.view", "item.view"] },
    { key: "item.review", domain: "review", domain_label: "确认流程", label: "确认与退回", description: "确认或退回待确认资料。", dependencies: ["workspace.view", "item.view"] },
    { key: "item.move_review", domain: "review", domain_label: "确认流程", label: "移动待确认资料", description: "移动待确认状态的资料。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "item.publish", domain: "publish", domain_label: "发布流程", label: "发布资料", description: "发布或重新发布已确认资料。", dependencies: ["workspace.view", "item.view"] },
    { key: "item.reclassify_published", domain: "publish", domain_label: "发布流程", label: "调整已发布资料分类", description: "调整已发布普通资料的分类，并同步正式索引和只读目录。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "item.archive_published", domain: "publish", domain_label: "发布流程", label: "下架正式资料", description: "将已确认、发布失败或已发布资料移入回收站。", dependencies: ["workspace.view", "item.view"] },
    { key: "trash.view", domain: "trash", domain_label: "回收站", label: "查看回收站", description: "查看和搜索已归档资料。", dependencies: ["workspace.view", "item.view"] },
    { key: "trash.restore", domain: "trash", domain_label: "回收站", label: "恢复资料", description: "从回收站恢复资料。", dependencies: ["workspace.view", "item.view", "trash.view"] },
    { key: "category.manage", domain: "category", domain_label: "分类与目录", label: "维护分类", description: "新增、修改、启用或停用资料分类。", dependencies: ["workspace.view", "category.view"] },
    { key: "folder.request", domain: "category", domain_label: "分类与目录", label: "申请目录", description: "提交子目录创建申请。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "folder.review", domain: "category", domain_label: "分类与目录", label: "审批目录", description: "查看、批准或退回目录申请。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "import.server", domain: "operations", domain_label: "导入与索引", label: "服务器导入", description: "执行受控的服务器批次导入。", dependencies: ["workspace.view", "item.view", "category.view"] },
    { key: "index.view", domain: "operations", domain_label: "导入与索引", label: "查看索引任务", description: "查看发布处理状态、失败原因和历史尝试。", dependencies: ["workspace.view", "item.view", "category.view"] },
  ],
};

const permissionGroups = [
  { id: "permission-group-member", group_key: "member", display_name: "普通成员", permissions: [], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-viewer", group_key: "viewer", display_name: "资料浏览者", permissions: ["workspace.view", "item.view", "item.download", "category.view"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-bim-engineer", group_key: "bim_engineer", display_name: "BIM工程师", permissions: ["workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit", "item.move_draft", "item.archive_draft", "folder.request"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-content-owner", group_key: "content_owner", display_name: "资料负责人", permissions: ["workspace.view", "item.view", "item.download", "category.view", "item.review", "item.move_review", "folder.review", "trash.view", "trash.restore"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-publisher", group_key: "publisher", display_name: "发布负责人", permissions: ["workspace.view", "item.view", "item.download", "category.view", "item.publish", "item.reclassify_published", "item.archive_published", "trash.view", "index.view"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-category-admin", group_key: "category_admin", display_name: "分类管理员", permissions: ["workspace.view", "item.view", "item.download", "category.view", "category.manage", "folder.review"], is_system: true, is_active: true, updated_at: 1700000000 },
  { id: "permission-group-system-admin", group_key: "system_admin", display_name: "系统管理员", permissions: admin.content_permissions, is_system: true, is_active: true, updated_at: 1700000000 },
];

const adminConversations = [
  { id: "conversation-1", title: "合成项目交付规范", user_id: 9002, employee_id: "TEST-EDITOR", real_name: "合成资料员", created_at: 1700000000, updated_at: 1700000400, turn_index: 2 },
  { id: "conversation-2", title: "合成索引问题排查", user_id: 9003, employee_id: "TEST-MEMBER", real_name: "合成成员", created_at: 1700000100, updated_at: 1700000300, turn_index: 1 },
];

const conversationState = {
  id: "conversation-1", title: "合成项目交付规范", user_id: 9002, created_at: 1700000000, updated_at: 1700000400, turn_index: 2,
  messages: [
    { id: 501, role: "user", content: "合成问题：交付标准有哪些？", created_at: 1700000200, user_versions: [{ id: 601, version_index: 1, content: "合成问题：交付标准有哪些？", created_at: 1700000200, is_active: false }, { id: 602, version_index: 2, content: "合成问题：交付标准有哪些？（确认版）", created_at: 1700000300, is_active: true }], answer_versions: null, sources_for_ui: null },
    { id: 502, role: "assistant", content: "合成回答：请按项目交付清单逐项核对。", created_at: 1700000400, user_versions: null, answer_versions: [{ id: 701, version_index: 1, content: "合成回答：请按项目交付清单逐项核对。", created_at: 1700000400, is_active: true, user_version_id: 601 }], sources_for_ui: [] },
  ],
};

const feedbackEntries = [
  { feedback_id: "feedback-1", ts: "2026-08-15T10:00:00Z", kind: "answer", rating: "down", note: "合成反馈：回答需要补充来源", query: "如何归档？", answer_text: "请查阅归档指引。", status: "pending", resolution: null, admin_note: null, conversation_id: "conversation-1", turn_index: 2 },
  { feedback_id: "feedback-2", ts: "2026-08-14T09:00:00Z", kind: "citation", rating: "up", note: "来源准确", status: "resolved", resolution: "no_action", admin_note: "合成已核对", conversation_id: "conversation-2", turn_index: 1 },
];

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function sse(route: Route, body: string) {
  return route.fulfill({ status: 200, contentType: "text/event-stream", headers: { "cache-control": "no-cache" }, body });
}

export async function installAdminRoutes(
  page: Page,
  scenario: AdminScenario = "normal",
  workspaceUser: WorkspaceUser = "admin",
  currentUser: () => typeof admin = () => workspaceUsers[workspaceUser],
  options: { includeChildFolder?: boolean; includeFolderRequest?: boolean } = {},
) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (!path.startsWith("/api/")) return route.continue();

    if (path === "/api/auth/me") return json(route, currentUser());
    const isIndexRead = request.method() === "GET" && path.startsWith("/api/admin/index/");
    const isTargetRead = request.method() === "GET" && (
      path.startsWith("/api/admin/content/")
      || isIndexRead
      || path.startsWith("/api/admin/media")
      || path.startsWith("/api/admin/transcription/")
      || path.startsWith("/api/admin/asr")
      || path.startsWith("/api/admin/feedback")
      || path.startsWith("/api/admin/conversations")
      || path.startsWith("/api/admin/stats")
      || path.startsWith("/api/admin/system-overview")
      || path.startsWith("/api/admin/maintenance")
    );
    if (isTargetRead && scenario === "loading") {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
    }
    if (isTargetRead && scenario === "error") {
      return json(route, { detail: "合成加载失败" }, 503);
    }
    if (request.method() === "GET" && path === "/api/admin/stats") {
      return json(route, scenario === "empty" ? {
        users_total: 0,
        users_active: 0,
        conversations_total: 0,
        conversations_7d: 0,
        messages_total: 0,
        messages_7d: 0,
      } : {
        users_total: 3,
        users_active: 2,
        conversations_total: 12,
        conversations_7d: 4,
        messages_total: 64,
        messages_7d: 18,
      });
    }
    if (request.method() === "GET" && path === "/api/admin/system-overview") {
      return json(route, {
        topology: "separate",
        checked_at: 1700000000,
        app: {
          status: "healthy",
          cpu_percent: 31.2,
          memory_used_bytes: 4 * 1024 ** 3,
          memory_total_bytes: 16 * 1024 ** 3,
          disk_used_bytes: 40 * 1024 ** 3,
          disk_total_bytes: 100 * 1024 ** 3,
          checked_at: 1700000000,
          error_code: null,
        },
        gpu: {
          status: "healthy",
          model_loaded: true,
          device_name: "NVIDIA RTX 合成卡",
          vram_used_bytes: 4 * 1024 ** 3,
          vram_total_bytes: 16 * 1024 ** 3,
          utilization_percent: 42,
          temperature_celsius: 53,
          inflight_requests: 1,
          checked_at: 1700000000,
          data_age_seconds: 0,
          stale: false,
          error_code: null,
        },
        office_processing: {
          enabled: false,
          mode: "deployment_config",
          disabled_reason: "office_processing_disabled",
          status: "disabled",
          checked_at: 1700000000,
          error_code: null,
        },
      });
    }
    if (request.method() === "GET" && path === "/api/admin/maintenance") {
      return json(route, {
        settings: {
          conversation_cleanup_enabled: true,
          conversation_retention_days: 30,
          updated_at: 1700000000,
          updated_by: 9001,
        },
        sweeper_interval_seconds: 3600,
        last_run: {
          id: 1,
          trigger_source: "automatic",
          status: "succeeded",
          retention_days: 30,
          deleted_conversations: 0,
          deleted_messages: 0,
          deleted_auth_sessions: 2,
          started_at: 1700000000,
          finished_at: 1700000002,
          error_summary: null,
        },
      });
    }
    if (request.method() === "GET" && path === "/api/admin/maintenance/cleanup-preview") {
      return json(route, { retention_days: 30, conversations: 4, messages: 12, auth_sessions: 2, oldest_conversation_at: 1690000000, newest_conversation_at: 1695000000 });
    }
    if (request.method() === "GET" && path === "/api/admin/maintenance/runs") {
      return json(route, { runs: [{ id: 1, trigger_source: "automatic", status: "succeeded", retention_days: 30, deleted_conversations: 0, deleted_messages: 0, deleted_auth_sessions: 2, started_at: 1700000000, finished_at: 1700000002, error_summary: null }] });
    }
    if (request.method() === "PATCH" && path === "/api/admin/maintenance/settings") {
      const payload = request.postDataJSON();
      return json(route, { ...payload, updated_at: 1700000100, updated_by: 9001 });
    }
    if (request.method() === "POST" && path === "/api/admin/maintenance/cleanup") {
      return json(route, { run_id: 2, retention_days: 30, deleted_conversations: 4, deleted_messages: 12, deleted_auth_sessions: 2, started_at: 1700000200, finished_at: 1700000202 });
    }
    if (request.method() === "GET" && path === "/api/admin/conversations") return json(route, { conversations: scenario === "empty" ? [] : adminConversations });
    if (request.method() === "GET" && /^\/api\/admin\/users\/\d+\/conversations$/.test(path)) return json(route, { conversations: scenario === "empty" ? [] : adminConversations.filter((conversation) => conversation.user_id === 9002) });
    if (request.method() === "GET" && /^\/api\/conversations\/[^/]+$/.test(path)) return json(route, conversationState);
    if (request.method() === "GET" && path === "/api/admin/feedback") {
      const entries = scenario === "empty" ? [] : feedbackEntries;
      return json(route, { entries, total: entries.length, page: 1, page_size: 20, counts: { pending: entries.filter((entry) => entry.status === "pending").length, in_progress: 0, resolved: entries.filter((entry) => entry.status === "resolved").length, archived: 0 } });
    }
    if (request.method() === "PATCH" && /^\/api\/admin\/feedback\/[^/]+$/.test(path)) return json(route, feedbackEntries[0]);
    if (request.method() === "POST" && path === "/api/admin/media" && scenario === "media_upload") {
      await new Promise((resolve) => setTimeout(resolve, 3_000));
      return json(route, { ...mediaAssets[2], media_id: "media-uploaded", transcription_job_id: "job-uploaded" });
    }
    if (request.method() === "GET" && path === "/api/admin/media") {
      if (scenario === "empty") return json(route, []);
      if (scenario === "media_progress") return json(route, [{ ...mediaAssets[2], status: "transcribing" }]);
      return json(route, mediaAssets);
    }
    if (request.method() === "GET" && path === "/api/admin/transcription/profiles") return json(route, transcriptionProfiles);
    if (request.method() === "GET" && path === "/api/admin/asr") {
      if (scenario === "empty") {
        return json(route, { service: { status: "healthy", queue_depth: 0, queue_limit: 8, pause_reason: null }, profiles: [], release_requests: [], audit_events: [] });
      }
      const service = scenario === "disabled"
        ? { status: "disabled", queue_depth: null, queue_limit: null, pause_reason: null }
        : { status: "healthy", queue_depth: 1, queue_limit: 8, pause_reason: null };
      const profiles = scenario === "disabled"
        ? asrProfiles.map((profile) => ({ ...profile, release_eligible: false, availability: "unavailable", unavailable_reason_code: "asr_service_disabled" }))
        : asrProfiles;
      return json(route, { service, profiles, release_requests: [], audit_events: [] });
    }
    if (request.method() === "POST" && path === "/api/admin/asr/release-requests") {
      await new Promise((resolve) => setTimeout(resolve, 800));
      const body = request.postDataJSON() as { profile_id: string; request_reason?: string | null };
      const selected = asrProfiles.find((profile) => profile.profile_id === body.profile_id) || asrProfiles[1];
      return json(route, { ...asrReleaseRequest, profile_id: selected.profile_id, profile_display_name: selected.display_name, profile_config_hash: selected.application_config_hash, request_reason: body.request_reason || null });
    }
    if (request.method() === "GET" && path === "/api/admin/transcription/jobs") {
      if (scenario === "empty" || scenario === "media_upload") return json(route, []);
      if (scenario === "media_progress") {
        return json(route, [{
          ...transcriptionJobs[0],
          job_id: "media-running-job",
          media_id: "media-ready",
          status: "running",
          stage: "transcribing",
          processed_ms: 0,
          total_ms: 4_800_000,
          failure_error_code: null,
          error_summary: null,
          failure: null,
          started_at: Math.floor(Date.now() / 1000) - 125,
          finished_at: null,
          updated_at: Math.floor(Date.now() / 1000),
        }]);
      }
      return json(route, transcriptionJobs);
    }
    if (request.method() === "GET" && path === "/api/admin/transcription/jobs/job-uploaded") {
      return json(route, {
        ...transcriptionJobs[0],
        job_id: "job-uploaded",
        media_id: "media-uploaded",
        status: "pending",
        stage: null,
        failure_error_code: null,
        error_summary: null,
        failure: null,
      });
    }
    if (request.method() === "GET" && /^\/api\/admin\/transcription\/jobs\/[^/]+$/.test(path)) return json(route, transcriptionJobs[0]);
    if (request.method() === "GET" && path === "/api/admin/transcription/media/media-ready/versions") return json(route, transcriptVersions);
    if (request.method() === "GET" && /^\/api\/admin\/transcription\/media\/[^/]+\/versions$/.test(path)) return json(route, []);
    if (request.method() === "GET" && path === "/api/admin/transcription/versions/11111111-1111-4111-8111-111111111111/markdown") {
      return json(route, { version_id: transcriptVersions[0].version_id, markdown: "# 项目交付培训\n\n说话人 1 00:00:00\n**培训开始**\n\n说话人 2 00:00:12\n- 核对模型命名\n- 核对交付目录\n", markdown_sha256: transcriptVersions[0].markdown_sha256 });
    }
    if (request.method() === "GET" && path === "/api/admin/transcription/versions/11111111-1111-4111-8111-111111111111/timeline") {
      return json(route, {
        media_id: "media-ready",
        version_id: transcriptVersions[0].version_id,
        language: "zh-CN",
        duration_ms: 30_000,
        segments: [
          { id: 0, start_ms: 0, end_ms: 12_000, text: "培训开始" },
          { id: 1, start_ms: 12_000, end_ms: null, text: "核对模型命名\n核对交付目录" },
        ],
      });
    }
    if (request.method() === "GET" && path === "/api/media/media-ready") {
      return route.fulfill({ status: 200, contentType: "video/mp4", body: "" });
    }
    if (request.method() === "GET" && path === "/api/admin/media/media-ready/preview") {
      return route.fulfill({ status: 200, contentType: "video/mp4", body: "" });
    }
    if (path === "/api/categories") return json(route, { categories: [], second_level_categories: [] });
    if (path === "/api/conversations" && request.method() === "GET") return json(route, { conversations: adminConversations.slice(0, 1) });
    if (path === "/api/conversations" && request.method() === "POST") return json(route, { ...adminConversations[0] });
    if (request.method() === "GET" && path.startsWith("/api/pdf/")) {
      return route.fulfill({ status: 404, contentType: "application/pdf", body: "" });
    }
    if (request.method() === "GET" && path === "/api/source/parent-ready/raw") {
      return route.fulfill({
        status: 200,
        contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body: "synthetic docx fixture",
      });
    }
    if (request.method() === "GET" && /^\/api\/admin\/content\/versions\/[^/]+\/file$/.test(path)) {
      return route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF synthetic fixture" });
    }
    if (request.method() === "POST" && path === "/api/admin/content/bulk-download") {
      await new Promise((resolve) => setTimeout(resolve, 300));
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        headers: { "content-disposition": "attachment; filename=managed-content.zip" },
        body: "synthetic zip fixture",
      });
    }
    if (path === "/api/admin/users") return json(route, { users: permissionUsers.map((user) => ({
      id: user.user_id, employee_id: user.employee_id, real_name: user.real_name, role: user.role,
      is_active: user.is_active, created_at: 1700000000, last_login_at: 1700000000,
      conversation_count: user.user_id === 9001 ? 3 : 0,
      content_permissions: user.role === "admin" ? admin.content_permissions : user.permissions,
    })) });

    if (path === "/api/admin/content/capabilities") {
      return json(route, { enabled: scenario !== "disabled", max_upload_bytes: 10_000_000, supported_extensions: [".pdf", ".md", ".docx", ".xlsx", ".pptx"] });
    }
    if (request.method() === "GET" && path === "/api/admin/content/categories") {
      const childFolder = { id: "cat-company-modeling", category_key: "company_modeling", parent_id: "cat-company", display_code: "01", display_name: "建模标准（长名称用于响应式检查）", sort_order: 10, level: 2, is_active: true, version: 1, created_at: 1700000000, updated_at: 1700000000, full_path: "03 公司内部标准 / 01 建模标准（长名称用于响应式检查）", item_count: 1 };
      return json(route, scenario === "empty" ? [] : options.includeChildFolder ? [...categories, childFolder] : categories);
    }
    if (request.method() === "GET" && path === "/api/admin/content/upload-tasks") {
      const status = url.searchParams.get("status");
      const query = (url.searchParams.get("query") || "").trim().toLocaleLowerCase("zh-CN");
      const rows = (scenario === "empty" ? [] : uploadTasks).filter((task) =>
        (!status || task.status === status)
        && (!query || task.target_path.toLocaleLowerCase("zh-CN").includes(query)),
      );
      return json(route, { tasks: rows, total: rows.length, status_counts: { completed: 1, partial_success: 1, failed: 1 } });
    }
    if (request.method() === "GET" && path.startsWith("/api/admin/content/upload-tasks/")) {
      const task = uploadTasks.find((candidate) => candidate.batch_id === path.split("/").at(-1));
      return task ? json(route, task) : json(route, { detail: "合成任务不存在" }, 404);
    }
    if (path === "/api/admin/content/items-page") {
      const fixtureItems = scenario === "media_library" ? [mediaLibraryItem] : items;
      const rows = scenario === "empty" ? [] : fixtureItems.map((item) => item.lifecycle_status === "publication_failed" && scenario === "publication_failure" ? { ...item, latest_publication_status: "failed", publication_attempt_count: 4, publication_failure: { code: "pdf_password_required", message: "PDF 需要密码才能解析。", retryable: false, recommended_action: "请上传已解除密码保护的 PDF。" } } : item);
      return json(route, { items: rows, total: rows.length, status_counts: rows.reduce<Record<string, number>>((counts, item) => ({ ...counts, [item.lifecycle_status]: (counts[item.lifecycle_status] || 0) + 1 }), {}) });
    }
    if (path === "/api/admin/content/trash") {
      const rows = scenario === "empty" ? [] : trashItems;
      return json(route, { items: rows, total: rows.length, status_counts: rows.length ? { published: 1, approved: 1 } : {}, retention_counts: rows.length ? { retained: 0, expiring: 1, overdue: 1 } : {} });
    }
    if (path === "/api/admin/content/bulk-restore/preflight") {
      const payload = request.postDataJSON() as { items: Array<{ item_id: string; expected_version_id: string }> };
      return json(route, { results: payload.items.map((item) => ({ ...item, version_id: item.expected_version_id, status: "ready", message: "可以恢复", target_category_path: "03 公司内部标准" })), ready: payload.items.length, blocked: 0 });
    }
    if (request.method() === "GET" && /^\/api\/admin\/content\/items\/[^/]+\/audit-events$/.test(path)) {
      return json(route, [{
        event_type: "content.archived",
        actor_name: "合成资料员",
        created_at: 1700000800,
        previous_status: "published",
        restored_status: null,
        restore_strategy: null,
        source_category_path: null,
        target_category_path: null,
        category_path: "03 公司内部标准 / 03-01 建模标准",
        archive_reason: null,
        replaced_title: null,
        replaced_filename: null,
      }]);
    }
    if (path === "/api/admin/content/folder-requests") {
      return json(route, options.includeFolderRequest ? folderRequests : []);
    }
    if (path === "/api/admin/content/index-jobs") {
      const includesArchived = url.searchParams.get("include_archived") === "true";
      const jobs = scenario === "empty" ? [] : includesArchived ? [...managedIndexJobs, archivedManagedIndexJob] : managedIndexJobs;
      return json(route, {
        jobs,
        total: jobs.length,
        status_counts: jobs.length
          ? { processing: 0, ready: includesArchived ? 1 : 0, failed: 1 }
          : {},
      });
    }
    if (path === "/api/admin/content/permissions") {
      return json(route, scenario === "empty" ? [] : permissionUsers);
    }
    if (path === "/api/admin/content/permission-catalog") {
      return json(route, permissionCatalog);
    }
    if (path === "/api/admin/content/permission-groups") {
      return json(route, permissionGroups);
    }
    if (request.method() === "GET" && path === "/api/admin/index/category-tree") {
      return json(route, { categories: [{ name: "公司标准", two_level: false, subcategories: [] }, { name: "客户标准", two_level: true, subcategories: ["合成客户"] }, { name: "项目资料", two_level: false, subcategories: [] }], second_level_categories: ["客户标准"] });
    }
    if (request.method() !== "GET" && path.startsWith("/api/admin/content/")) {
      await new Promise((resolve) => setTimeout(resolve, 800));
      if (request.method() === "POST" && /^\/api\/admin\/content\/categories\/[^/]+\/move$/.test(path)) {
        const categoryId = path.split("/").at(-2);
        const body = request.postDataJSON() as { target_parent_id?: string | null; before_category_id?: string | null; expected_version: number };
        const current = categories.find((category) => category.id === categoryId) || categories[0];
        const parent = categories.find((category) => category.id === body.target_parent_id);
        return json(route, categories.map((category) => category.id === categoryId ? {
          ...current,
          parent_id: body.target_parent_id || null,
          level: parent ? parent.level + 1 : 1,
          full_path: `${parent ? `${parent.full_path} / ` : ""}${current.display_code} ${current.display_name}`,
          version: current.version + 1,
          updated_at: 1700000600,
        } : category));
      }
      if (request.method() === "POST" && path === "/api/admin/content/categories") {
        await new Promise((resolve) => setTimeout(resolve, 1_200));
        const body = request.postDataJSON() as { parent_id?: string | null; display_code?: string; display_name?: string; sort_order?: number };
        const parent = categories.find((category) => category.id === body.parent_id);
        const created = {
          id: "cat-synthetic-created",
          category_key: "category_synthetic_created",
          parent_id: body.parent_id || null,
          display_code: body.display_code || "",
          display_name: body.display_name || "",
          sort_order: body.sort_order || 0,
          level: parent ? parent.level + 1 : 1,
          is_active: true,
          version: 1,
          created_at: 1700000600,
          updated_at: 1700000600,
          full_path: `${parent ? `${parent.full_path} / ` : ""}${body.display_code || ""} ${body.display_name || ""}`.trim(),
          item_count: 0,
        };
        return json(route, created);
      }
      if (request.method() === "PATCH" && /^\/api\/admin\/content\/categories\/[^/]+\/number$/.test(path)) {
        const categoryId = path.split("/").at(-2);
        const body = request.postDataJSON() as { target_position: number; expected_version: number };
        const current = categories.find((category) => category.id === categoryId) || categories[0];
        const siblings = categories
          .filter((category) => category.parent_id === current.parent_id)
          .sort((left, right) => Number(left.display_code) - Number(right.display_code));
        const ordered = siblings.filter((category) => category.id !== categoryId);
        ordered.splice(body.target_position - 1, 0, current);
        const positions = new Map(ordered.map((category, index) => [category.id, index + 1]));
        return json(route, categories.map((category) => {
          const position = positions.get(category.id);
          return position ? {
            ...category,
            display_code: String(position).padStart(2, "0"),
            sort_order: position * 10,
            version: category.version + 1,
            updated_at: 1700000600,
          } : category;
        }));
      }
      if (request.method() === "PATCH" && path.startsWith("/api/admin/content/categories/")) {
        const categoryId = path.split("/").at(-1);
        const current = categories.find((category) => category.id === categoryId) || categories[0];
        const body = request.postDataJSON() as Partial<typeof current>;
        return json(route, { ...current, ...body, version: current.version + 1, updated_at: 1700000600 });
      }
      if (path === "/api/admin/content/uploads") {
        return json(route, { batch_id: "synthetic-batch", entries: [{ filename: "synthetic.pdf", item_id: "new-item", version_id: "new-version", sha256: null, status: "accepted", reason: null }] });
      }
      if (path.endsWith("/bulk-review") || path.endsWith("/bulk-publish") || path.endsWith("/bulk-move") || path.endsWith("/bulk-archive")) {
        const body = request.postDataJSON() as { version_ids?: string[]; items?: Array<{ item_id: string; expected_version_id: string }> };
        const versionIds = body.version_ids || body.items?.map((item) => item.expected_version_id) || [];
        return json(route, { results: versionIds.map((version_id) => ({ version_id, status: "succeeded", message: null, index_job_id: null })), succeeded: versionIds.length, failed: 0 });
      }
      return json(route, items[0]);
    }
    if (request.method() === "DELETE" && /^\/api\/admin\/media\/[^/]+$/.test(path)) return route.fulfill({ status: 204 });
    throw new Error(`Visual fixture has no route for ${request.method()} ${path}`);
  });
}

export async function installAuthRoutes(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/auth/me") return json(route, { detail: "未登录" }, 401);
    if (path === "/api/auth/login" && request.method() === "POST") return json(route, { detail: "合成用户名或密码错误" }, 401);
    if (path === "/api/auth/register" && request.method() === "POST") return json(route, { detail: "合成用户名已存在" }, 409);
    throw new Error(`Auth fixture has no route for ${request.method()} ${path}`);
  });
}

export async function installChatRoutes(page: Page, scenario: "normal" | "error" | "video" = "normal") {
  const chatUser = { ...workspaceUsers.member, csrf_token: "synthetic-chat-csrf" };
  const videoSource = {
    parent_id: "video-parent",
    doc_title: "项目交付培训视频：移动端长标题适配验证",
    section_path: "培训 / 项目交付",
    category: "教学视频",
    score: 0.91,
    rrf_score: 0.88,
    text: "核对模型命名、交付目录与归档要求。",
    doc_type: "transcript",
    start_time: "00:00:12",
    media_id: "media-ready",
    sheet_name: null,
    cell_range: null,
    slide_number: null,
    paragraph_anchor: null,
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/auth/me") return json(route, chatUser);
    if (path === "/api/categories") return json(route, { categories: ["公司标准", "项目资料"], second_level_categories: [] });
    if (path === "/api/conversations" && request.method() === "GET") return json(route, { conversations: [] });
    if (path === "/api/conversations" && request.method() === "POST") return json(route, { id: "conversation-chat", title: "合成新对话", user_id: chatUser.id, created_at: 1700000000, updated_at: 1700000000, turn_index: 0 });
    if (path === "/api/conversations/conversation-chat" && request.method() === "GET") return json(route, { ...conversationState, id: "conversation-chat", title: "合成新对话", user_id: chatUser.id, messages: [] });
    if (path === "/api/conversations/conversation-chat/chat" && request.method() === "POST") {
      if (scenario === "error") return sse(route, `event: error\ndata: ${JSON.stringify({ message: "合成回答失败" })}\n\n`);
      const sources = scenario === "video" ? [videoSource] : [];
      return sse(route, [
        `event: prep\ndata: ${JSON.stringify({ search_query: "合成问题", rewrite_applied: false, history_chars: 0, budget: 1000, fresh_count: 1, final_count: 1, used_sources: sources, no_source_fallback: false })}\n\n`,
        `event: token\ndata: ${JSON.stringify({ text: "合成回答" })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ answer_text: "合成回答", assistant_message_id: 503, timings: {}, sources, history_chars: 0, budget: 1000 })}\n\n`,
      ].join(""));
    }
    if (scenario === "video" && path === "/api/media/media-ready/transcript") {
      return json(route, {
        media_id: "media-ready",
        segments: Array.from({ length: 24 }, (_, index) => ({
          id: index,
          start_ms: index * 12_000,
          end_ms: (index + 1) * 12_000,
          text: `第 ${index + 1} 段合成转录内容，用于验证移动端滚动区域和底部安全距离。`,
        })),
      });
    }
    if (scenario === "video" && path === "/api/media/media-ready") {
      return route.fulfill({ status: 200, contentType: "video/mp4", body: "" });
    }
    if (/^\/api\/conversations\/[^/]+$/.test(path) && request.method() === "DELETE") return route.fulfill({ status: 204 });
    throw new Error(`Chat fixture has no route for ${request.method()} ${path}`);
  });
}
