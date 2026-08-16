"""Forward-only application database migration runner."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .content_permission_catalog import (
    LEGACY_SYSTEM_CONTENT_PERMISSION_GROUPS,
    SYSTEM_CONTENT_PERMISSION_GROUPS,
)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


PHASE2_STATEMENTS = (
    """CREATE TABLE transcription_jobs (
        id TEXT PRIMARY KEY,
        media_id TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        request_idempotency_key TEXT NOT NULL UNIQUE,
        execution_identity TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        provider_key TEXT NOT NULL,
        model_id TEXT,
        model_revision TEXT,
        profile_definition_version TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        profile_snapshot_json TEXT NOT NULL,
        execution_config_json TEXT NOT NULL,
        execution_fingerprint TEXT NOT NULL,
        audio_sha256 TEXT NOT NULL,
        input_kind TEXT NOT NULL,
        input_size_bytes INTEGER NOT NULL CHECK (input_size_bytes >= 0),
        total_ms INTEGER NOT NULL CHECK (total_ms > 0),
        processed_ms INTEGER NOT NULL DEFAULT 0 CHECK (processed_ms >= 0 AND processed_ms <= total_ms),
        status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
        stage TEXT CHECK (stage IS NULL OR stage IN ('validating_input','transcribing','normalizing','formatting')),
        failure_error_code TEXT,
        failure_classification TEXT CHECK (failure_classification IS NULL OR failure_classification IN ('transient','permanent')),
        error_summary TEXT,
        checkpoint_json TEXT,
        result_version_id TEXT,
        canonical_sha256 TEXT,
        draft_markdown_rel_path TEXT,
        draft_markdown_sha256 TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        updated_at INTEGER NOT NULL,
        UNIQUE(media_id, attempt_number),
        CHECK ((model_id IS NULL AND model_revision IS NULL) OR (model_id IS NOT NULL AND model_revision IS NOT NULL))
    )""",
    """CREATE UNIQUE INDEX uq_transcription_jobs_one_active_media
       ON transcription_jobs(media_id) WHERE status IN ('pending','running')""",
    """CREATE TABLE transcript_versions (
        id TEXT PRIMARY KEY,
        media_id TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        transcription_job_id TEXT UNIQUE REFERENCES transcription_jobs(id) ON DELETE RESTRICT,
        source TEXT NOT NULL CHECK (source IN ('automatic','manual')),
        profile_id TEXT,
        provider_key TEXT,
        model_id TEXT,
        model_revision TEXT,
        config_hash TEXT,
        profile_snapshot_json TEXT,
        canonical_json TEXT,
        canonical_sha256 TEXT,
        markdown_storage_kind TEXT NOT NULL CHECK (markdown_storage_kind IN ('managed_artifact','legacy_manual')),
        markdown_rel_path TEXT NOT NULL,
        markdown_sha256 TEXT NOT NULL,
        markdown_size_bytes INTEGER NOT NULL CHECK (markdown_size_bytes >= 0),
        review_status TEXT NOT NULL CHECK (review_status IN ('not_required','awaiting_review','review_approved','review_rejected')),
        reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reviewed_at INTEGER,
        review_note TEXT,
        publication_status TEXT NOT NULL CHECK (publication_status IN ('not_published','publishing','published','publication_failed')),
        published_at INTEGER,
        supersedes_version_id TEXT REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        CHECK ((model_id IS NULL AND model_revision IS NULL) OR (model_id IS NOT NULL AND model_revision IS NOT NULL))
    )""",
    """CREATE TABLE transcript_version_artifacts (
        version_id TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        artifact_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        PRIMARY KEY(version_id, artifact_id)
    )""",
    """CREATE TABLE transcript_publication_index_jobs (
        id TEXT PRIMARY KEY,
        transcript_version_id TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        candidate_version_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        canonical_sha256 TEXT,
        markdown_sha256 TEXT NOT NULL,
        target_index_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','parsing','chunking','embedding','done','failed')),
        error_code TEXT,
        error_summary TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        updated_at INTEGER NOT NULL,
        UNIQUE(transcript_version_id, attempt_number),
        UNIQUE(target_index_id)
    )""",
    """CREATE UNIQUE INDEX uq_transcript_publication_index_one_active
       ON transcript_publication_index_jobs(transcript_version_id)
       WHERE status IN ('pending','parsing','chunking','embedding')""",
    """CREATE TABLE media_transcript_heads (
        media_id TEXT PRIMARY KEY REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        current_version_id TEXT NOT NULL UNIQUE REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        updated_at INTEGER NOT NULL
    )""",
)

ANSWER_VERSION_STATEMENTS = (
    """CREATE TABLE message_answer_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assistant_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        version_index INTEGER NOT NULL CHECK (version_index > 0),
        content TEXT NOT NULL,
        sources_json TEXT,
        final_sources_json TEXT,
        search_query TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        UNIQUE(assistant_message_id, version_index)
    )""",
    """CREATE TABLE message_answer_heads (
        assistant_message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        active_version_id INTEGER NOT NULL UNIQUE REFERENCES message_answer_versions(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE message_turn_requests (
        user_message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        categories_json TEXT
    )""",
)

FEEDBACK_WORKFLOW_STATEMENTS = (
    """CREATE TABLE feedback_workflow (
        feedback_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','in_progress','resolved','archived')),
        resolution TEXT
            CHECK (resolution IS NULL OR resolution IN (
                'knowledge_fixed','answer_improved','no_action','duplicate','other'
            )),
        admin_note TEXT,
        assignee_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        resolved_at INTEGER
    )""",
    """CREATE INDEX idx_feedback_workflow_status_updated
       ON feedback_workflow(status, updated_at DESC)""",
)

USER_QUESTION_VERSION_STATEMENTS = (
    """CREATE TABLE message_user_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        version_index INTEGER NOT NULL CHECK (version_index > 0),
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE(user_message_id, version_index)
    )""",
    """CREATE TABLE message_user_heads (
        user_message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        active_version_id INTEGER NOT NULL UNIQUE REFERENCES message_user_versions(id) ON DELETE CASCADE
    )""",
    """ALTER TABLE message_answer_versions
       ADD COLUMN user_version_id INTEGER REFERENCES message_user_versions(id) ON DELETE SET NULL""",
)

CONTENT_LIBRARY_STATEMENTS = (
    """CREATE TABLE category_nodes (
        id TEXT PRIMARY KEY,
        category_key TEXT NOT NULL UNIQUE,
        parent_id TEXT REFERENCES category_nodes(id) ON DELETE RESTRICT,
        display_code TEXT NOT NULL,
        display_name TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
    )""",
    """CREATE UNIQUE INDEX uq_category_nodes_sibling_code
       ON category_nodes(COALESCE(parent_id,''), display_code)""",
    """CREATE INDEX idx_category_nodes_parent_order
       ON category_nodes(parent_id, sort_order, display_name)""",
    """CREATE TABLE category_import_aliases (
        id TEXT PRIMARY KEY,
        parent_category_id TEXT REFERENCES category_nodes(id) ON DELETE RESTRICT,
        folder_name TEXT NOT NULL,
        target_category_id TEXT NOT NULL REFERENCES category_nodes(id) ON DELETE RESTRICT,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE UNIQUE INDEX uq_category_alias_context_name
       ON category_import_aliases(COALESCE(parent_category_id,''), folder_name)""",
    """CREATE TABLE content_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'organize','review','publish','manage_categories','import_server'
        )),
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, permission)
    )""",
    """CREATE TABLE upload_batches (
        id TEXT PRIMARY KEY,
        origin TEXT NOT NULL CHECK (origin IN ('web','server','legacy')),
        status TEXT NOT NULL CHECK (status IN (
            'staging','validating','awaiting_mapping','ready_for_review','completed','failed','closed'
        )),
        storage_rel_path TEXT,
        manifest_rel_path TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        submitted_at INTEGER,
        error_summary TEXT
    )""",
    """CREATE INDEX idx_upload_batches_status_created
       ON upload_batches(status, created_at DESC)""",
    """CREATE TABLE content_objects (
        sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        mime_type TEXT NOT NULL,
        storage_rel_path TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE content_items (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        content_kind TEXT NOT NULL CHECK (content_kind IN ('document','media_transcript')),
        category_id TEXT NOT NULL REFERENCES category_nodes(id) ON DELETE RESTRICT,
        media_id TEXT UNIQUE REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        archived_at INTEGER
    )""",
    """CREATE INDEX idx_content_items_category_updated
       ON content_items(category_id, updated_at DESC)""",
    """CREATE TABLE content_versions (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE RESTRICT,
        version_number INTEGER NOT NULL CHECK (version_number > 0),
        object_sha256 TEXT REFERENCES content_objects(sha256) ON DELETE RESTRICT,
        transcript_version_id TEXT UNIQUE REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        original_filename TEXT NOT NULL,
        doc_type TEXT NOT NULL CHECK (doc_type IN ('pdf','markdown','docx','xlsx','pptx','transcript')),
        source_origin TEXT NOT NULL CHECK (source_origin IN ('web','server','legacy','transcription')),
        source_batch_id TEXT REFERENCES upload_batches(id) ON DELETE RESTRICT,
        source_rel_path TEXT,
        lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
            'draft','awaiting_review','approved','rejected','publishing','published',
            'publication_failed','superseded'
        )),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE(item_id, version_number),
        CHECK (object_sha256 IS NOT NULL OR transcript_version_id IS NOT NULL)
    )""",
    """CREATE INDEX idx_content_versions_item_created
       ON content_versions(item_id, version_number DESC)""",
    """CREATE TABLE content_reviews (
        id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES content_versions(id) ON DELETE RESTRICT,
        decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
        reviewer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        note TEXT,
        created_at INTEGER NOT NULL
    )""",
    """CREATE INDEX idx_content_reviews_version_created
       ON content_reviews(version_id, created_at DESC)""",
    """CREATE TABLE content_publications (
        id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES content_versions(id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK (status IN ('pending','indexing','published','failed','withdrawn')),
        publisher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        published_at INTEGER,
        withdrawn_at INTEGER,
        error_code TEXT,
        error_summary TEXT
    )""",
    """CREATE UNIQUE INDEX uq_content_publication_one_active
       ON content_publications(version_id)
       WHERE status IN ('pending','indexing','published')""",
    """CREATE TABLE content_index_jobs (
        id TEXT PRIMARY KEY,
        publication_id TEXT NOT NULL REFERENCES content_publications(id) ON DELETE RESTRICT,
        version_id TEXT NOT NULL REFERENCES content_versions(id) ON DELETE RESTRICT,
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        target_index_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN (
            'pending','parsing','chunking','summarizing','embedding','done','failed'
        )),
        error_code TEXT,
        error_summary TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        updated_at INTEGER NOT NULL,
        UNIQUE(publication_id, attempt_number)
    )""",
    """CREATE UNIQUE INDEX uq_content_index_one_active
       ON content_index_jobs(publication_id)
       WHERE status IN ('pending','parsing','chunking','summarizing','embedding')""",
    """CREATE TABLE content_item_heads (
        item_id TEXT PRIMARY KEY REFERENCES content_items(id) ON DELETE RESTRICT,
        current_version_id TEXT NOT NULL UNIQUE REFERENCES content_versions(id) ON DELETE RESTRICT,
        publication_id TEXT NOT NULL UNIQUE REFERENCES content_publications(id) ON DELETE RESTRICT,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE content_audit_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        item_id TEXT REFERENCES content_items(id) ON DELETE RESTRICT,
        version_id TEXT REFERENCES content_versions(id) ON DELETE RESTRICT,
        batch_id TEXT REFERENCES upload_batches(id) ON DELETE RESTRICT,
        category_id TEXT REFERENCES category_nodes(id) ON DELETE RESTRICT,
        metadata_json TEXT,
        created_at INTEGER NOT NULL
    )""",
    """CREATE INDEX idx_content_audit_created
       ON content_audit_events(created_at DESC)""",
    """INSERT INTO category_nodes
       (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
       VALUES
       ('cat-01','industry_standards',NULL,'01','行业规范与标准',10,1,1,strftime('%s','now'),strftime('%s','now')),
       ('cat-02','client_requirements',NULL,'02','客户标准与要求',20,1,1,strftime('%s','now'),strftime('%s','now')),
       ('cat-03','company_standards',NULL,'03','公司内部标准',30,1,1,strftime('%s','now'),strftime('%s','now')),
       ('cat-04','project_materials',NULL,'04','项目资料',40,1,1,strftime('%s','now'),strftime('%s','now')),
       ('cat-05','training_materials',NULL,'05','培训资料',50,1,1,strftime('%s','now'),strftime('%s','now')),
       ('cat-06','project_experience',NULL,'06','项目经验与案例',60,1,1,strftime('%s','now'),strftime('%s','now')),
       ('cat-99','pending_confirmation',NULL,'99','待确认资料',990,1,1,strftime('%s','now'),strftime('%s','now'))""",
    """INSERT INTO category_import_aliases
       (id,parent_category_id,folder_name,target_category_id,created_at,updated_at)
       VALUES
       ('alias-design',NULL,'设计规范','cat-01',strftime('%s','now'),strftime('%s','now')),
       ('alias-client',NULL,'客户标准','cat-02',strftime('%s','now'),strftime('%s','now')),
       ('alias-company',NULL,'公司标准','cat-03',strftime('%s','now'),strftime('%s','now')),
       ('alias-teaching',NULL,'教学视频','cat-05',strftime('%s','now'),strftime('%s','now')),
       ('alias-training',NULL,'培训视频','cat-05',strftime('%s','now'),strftime('%s','now'))""",
)

CONTENT_PERMISSION_GROUP_STATEMENTS = (
    """CREATE TABLE content_permission_groups (
        id TEXT PRIMARY KEY,
        group_key TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0,1)),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE content_permission_group_items (
        group_id TEXT NOT NULL REFERENCES content_permission_groups(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'organize','review','publish','manage_categories','import_server'
        )),
        PRIMARY KEY(group_id, permission)
    )""",
    """INSERT INTO content_permission_groups
       (id,group_key,display_name,is_system,is_active,created_at,updated_at) VALUES
       ('permission-group-member','member','普通成员',1,1,strftime('%s','now'),strftime('%s','now')),
       ('permission-group-bim-engineer','bim_engineer','BIM工程师',1,1,strftime('%s','now'),strftime('%s','now')),
       ('permission-group-content-owner','content_owner','资料负责人',1,1,strftime('%s','now'),strftime('%s','now')),
       ('permission-group-system-admin','system_admin','系统管理员',1,1,strftime('%s','now'),strftime('%s','now'))""",
    """INSERT INTO content_permission_group_items(group_id,permission) VALUES
       ('permission-group-bim-engineer','organize'),
       ('permission-group-content-owner','review'),
       ('permission-group-system-admin','organize'),
       ('permission-group-system-admin','review'),
       ('permission-group-system-admin','publish'),
       ('permission-group-system-admin','manage_categories'),
       ('permission-group-system-admin','import_server')""",
)

CONTENT_FOLDER_REQUEST_STATEMENTS = (
    """CREATE TABLE content_folder_requests (
        id TEXT PRIMARY KEY,
        parent_category_id TEXT NOT NULL REFERENCES category_nodes(id) ON DELETE RESTRICT,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
        requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        review_note TEXT,
        created_category_id TEXT REFERENCES category_nodes(id) ON DELETE RESTRICT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        reviewed_at INTEGER
    )""",
    """CREATE INDEX idx_content_folder_requests_status_created
       ON content_folder_requests(status, created_at DESC)""",
    """CREATE UNIQUE INDEX uq_content_folder_requests_pending
       ON content_folder_requests(parent_category_id, display_name)
       WHERE status='pending'""",
)

SYSTEM_MAINTENANCE_STATEMENTS = (
    """CREATE TABLE maintenance_settings (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        conversation_cleanup_enabled INTEGER NOT NULL DEFAULT 1
            CHECK (conversation_cleanup_enabled IN (0,1)),
        conversation_retention_days INTEGER NOT NULL DEFAULT 30
            CHECK (conversation_retention_days BETWEEN 7 AND 3650),
        updated_at INTEGER NOT NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE maintenance_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_source TEXT NOT NULL CHECK (trigger_source IN ('automatic','manual')),
        status TEXT NOT NULL CHECK (status IN ('succeeded','failed')),
        retention_days INTEGER NOT NULL CHECK (retention_days BETWEEN 7 AND 3650),
        deleted_conversations INTEGER NOT NULL DEFAULT 0 CHECK (deleted_conversations >= 0),
        deleted_messages INTEGER NOT NULL DEFAULT 0 CHECK (deleted_messages >= 0),
        deleted_auth_sessions INTEGER NOT NULL DEFAULT 0 CHECK (deleted_auth_sessions >= 0),
        started_at INTEGER NOT NULL,
        finished_at INTEGER NOT NULL,
        error_summary TEXT
    )""",
    """CREATE INDEX idx_maintenance_runs_started_desc
       ON maintenance_runs(started_at DESC)""",
)

SYSTEM_MAINTENANCE_RETENTION_STATEMENTS = (
    "ALTER TABLE maintenance_settings RENAME TO maintenance_settings_v8",
    """CREATE TABLE maintenance_settings (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        conversation_cleanup_enabled INTEGER NOT NULL DEFAULT 1 CHECK (conversation_cleanup_enabled IN (0,1)),
        conversation_retention_days INTEGER DEFAULT 30 CHECK (conversation_retention_days IS NULL OR conversation_retention_days BETWEEN 7 AND 3650),
        updated_at INTEGER NOT NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    )""",
    """INSERT INTO maintenance_settings SELECT singleton_id, conversation_cleanup_enabled,
       conversation_retention_days, updated_at, updated_by FROM maintenance_settings_v8""",
    "DROP TABLE maintenance_settings_v8",
    "ALTER TABLE maintenance_runs RENAME TO maintenance_runs_v8",
    """CREATE TABLE maintenance_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_source TEXT NOT NULL CHECK (trigger_source IN ('automatic','manual')),
        status TEXT NOT NULL CHECK (status IN ('succeeded','failed')),
        retention_days INTEGER CHECK (retention_days IS NULL OR retention_days BETWEEN 7 AND 3650),
        deleted_conversations INTEGER NOT NULL DEFAULT 0 CHECK (deleted_conversations >= 0),
        deleted_messages INTEGER NOT NULL DEFAULT 0 CHECK (deleted_messages >= 0),
        deleted_auth_sessions INTEGER NOT NULL DEFAULT 0 CHECK (deleted_auth_sessions >= 0),
        started_at INTEGER NOT NULL, finished_at INTEGER NOT NULL, error_summary TEXT
    )""",
    """INSERT INTO maintenance_runs SELECT id, trigger_source, status, retention_days,
       deleted_conversations, deleted_messages, deleted_auth_sessions, started_at, finished_at, error_summary
       FROM maintenance_runs_v8""",
    "DROP TABLE maintenance_runs_v8",
    "CREATE INDEX idx_maintenance_runs_started_desc ON maintenance_runs(started_at DESC)",
)

CONTENT_VERSION_METADATA_STATEMENTS = (
    "ALTER TABLE content_items ADD COLUMN normalized_filename TEXT",
    "ALTER TABLE content_versions ADD COLUMN title TEXT",
    """UPDATE content_versions
       SET title=(SELECT i.title FROM content_items i WHERE i.id=content_versions.item_id)
       WHERE title IS NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_content_items_active_filename
       ON content_items(category_id, normalized_filename)
       WHERE archived_at IS NULL AND normalized_filename IS NOT NULL""",
)

CONTENT_PERMISSION_V2_STATEMENTS = (
    """CREATE TEMP TABLE content_permission_v11_map (
        old_permission TEXT NOT NULL,
        new_permission TEXT NOT NULL,
        PRIMARY KEY(old_permission, new_permission)
    )""",
    """INSERT INTO content_permission_v11_map(old_permission,new_permission) VALUES
       ('organize','workspace.view'),('organize','item.view'),('organize','category.view'),
       ('organize','item.upload'),('organize','item.submit'),('organize','item.move_draft'),
       ('organize','item.archive_draft'),('organize','folder.request'),
       ('review','workspace.view'),('review','item.view'),('review','category.view'),
       ('review','item.review'),('review','item.move_review'),('review','folder.review'),
       ('review','trash.view'),('review','trash.restore'),
       ('publish','workspace.view'),('publish','item.view'),('publish','category.view'),
       ('publish','item.publish'),('publish','item.archive_published'),('publish','trash.view'),
       ('publish','index.view'),
       ('manage_categories','workspace.view'),('manage_categories','item.view'),
       ('manage_categories','category.view'),('manage_categories','category.manage'),
       ('manage_categories','folder.review'),
       ('import_server','workspace.view'),('import_server','item.view'),
       ('import_server','category.view'),('import_server','import.server')""",
    "ALTER TABLE content_permissions RENAME TO content_permissions_v10",
    """CREATE TABLE content_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.archive_published','trash.view','trash.restore',
            'category.manage','folder.request','folder.review','import.server','index.view'
        )),
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, permission)
    )""",
    """INSERT INTO content_permissions(user_id,permission,granted_by,created_at)
       SELECT old.user_id,mapping.new_permission,MIN(old.granted_by),MIN(old.created_at)
       FROM content_permissions_v10 old
       JOIN content_permission_v11_map mapping ON mapping.old_permission=old.permission
       GROUP BY old.user_id,mapping.new_permission""",
    "DROP TABLE content_permissions_v10",
    "ALTER TABLE content_permission_group_items RENAME TO content_permission_group_items_v10",
    """CREATE TABLE content_permission_group_items (
        group_id TEXT NOT NULL REFERENCES content_permission_groups(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.archive_published','trash.view','trash.restore',
            'category.manage','folder.request','folder.review','import.server','index.view'
        )),
        PRIMARY KEY(group_id, permission)
    )""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT DISTINCT old.group_id,mapping.new_permission
       FROM content_permission_group_items_v10 old
       JOIN content_permission_v11_map mapping ON mapping.old_permission=old.permission""",
    "DROP TABLE content_permission_group_items_v10",
    """INSERT INTO content_permission_groups
       (id,group_key,display_name,is_system,is_active,created_at,updated_at) VALUES
       ('permission-group-viewer','viewer','资料浏览者',1,1,strftime('%s','now'),strftime('%s','now')),
       ('permission-group-publisher','publisher','发布负责人',1,1,strftime('%s','now'),strftime('%s','now')),
       ('permission-group-category-admin','category_admin','分类管理员',1,1,strftime('%s','now'),strftime('%s','now'))""",
    """INSERT INTO content_permission_group_items(group_id,permission) VALUES
       ('permission-group-viewer','workspace.view'),
       ('permission-group-viewer','item.view'),
       ('permission-group-viewer','category.view'),
       ('permission-group-publisher','workspace.view'),
       ('permission-group-publisher','item.view'),
       ('permission-group-publisher','category.view'),
       ('permission-group-publisher','item.publish'),
       ('permission-group-publisher','item.archive_published'),
       ('permission-group-publisher','trash.view'),
       ('permission-group-publisher','index.view'),
       ('permission-group-category-admin','workspace.view'),
       ('permission-group-category-admin','item.view'),
       ('permission-group-category-admin','category.view'),
       ('permission-group-category-admin','category.manage'),
       ('permission-group-category-admin','folder.review')""",
    "DROP TABLE content_permission_v11_map",
)

UPLOAD_TASK_STATEMENTS = (
    "ALTER TABLE upload_batches ADD COLUMN upload_mode TEXT NOT NULL DEFAULT 'files' CHECK (upload_mode IN ('files','folder'))",
    "ALTER TABLE upload_batches ADD COLUMN target_category_id TEXT REFERENCES category_nodes(id) ON DELETE SET NULL",
    "ALTER TABLE upload_batches ADD COLUMN total_files INTEGER NOT NULL DEFAULT 0 CHECK (total_files >= 0)",
    "ALTER TABLE upload_batches ADD COLUMN accepted_files INTEGER NOT NULL DEFAULT 0 CHECK (accepted_files >= 0)",
    "ALTER TABLE upload_batches ADD COLUMN skipped_files INTEGER NOT NULL DEFAULT 0 CHECK (skipped_files >= 0)",
    "ALTER TABLE upload_batches ADD COLUMN total_bytes INTEGER NOT NULL DEFAULT 0 CHECK (total_bytes >= 0)",
    "ALTER TABLE upload_batches ADD COLUMN total_uploaded_bytes INTEGER NOT NULL DEFAULT 0 CHECK (total_uploaded_bytes >= 0)",
    """CREATE TABLE upload_batch_entries (
        batch_id TEXT NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        filename TEXT NOT NULL,
        relative_path TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
        status TEXT NOT NULL CHECK (status IN ('accepted','skipped')),
        reason TEXT,
        item_id TEXT REFERENCES content_items(id) ON DELETE SET NULL,
        version_id TEXT REFERENCES content_versions(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(batch_id, sequence)
    )""",
    "CREATE INDEX idx_upload_batch_entries_batch_sequence ON upload_batch_entries(batch_id, sequence)",
)

MIGRATIONS = (
    Migration(1, "multi_engine_transcription_phase2", PHASE2_STATEMENTS),
    Migration(2, "answer_regeneration_versions", ANSWER_VERSION_STATEMENTS),
    Migration(3, "feedback_workflow", FEEDBACK_WORKFLOW_STATEMENTS),
    Migration(4, "user_question_edit_versions", USER_QUESTION_VERSION_STATEMENTS),
    Migration(5, "managed_content_library", CONTENT_LIBRARY_STATEMENTS),
    Migration(6, "content_permission_groups", CONTENT_PERMISSION_GROUP_STATEMENTS),
    Migration(7, "content_folder_requests", CONTENT_FOLDER_REQUEST_STATEMENTS),
    Migration(8, "system_maintenance", SYSTEM_MAINTENANCE_STATEMENTS),
    Migration(9, "system_maintenance_permanent_retention", SYSTEM_MAINTENANCE_RETENTION_STATEMENTS),
    Migration(10, "managed_content_version_metadata", CONTENT_VERSION_METADATA_STATEMENTS),
    Migration(11, "granular_content_permissions", CONTENT_PERMISSION_V2_STATEMENTS),
    Migration(12, "managed_upload_tasks", UPLOAD_TASK_STATEMENTS),
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version
PHASE2_TABLES = frozenset(
    {
        "transcription_jobs",
        "transcript_versions",
        "transcript_version_artifacts",
        "transcript_publication_index_jobs",
        "media_transcript_heads",
    }
)
ANSWER_VERSION_TABLES = frozenset(
    {"message_answer_versions", "message_answer_heads", "message_turn_requests"}
)
FEEDBACK_WORKFLOW_TABLES = frozenset({"feedback_workflow"})
USER_QUESTION_VERSION_TABLES = frozenset(
    {"message_user_versions", "message_user_heads"}
)
CONTENT_LIBRARY_TABLES = frozenset(
    {
        "category_nodes",
        "category_import_aliases",
        "content_permissions",
        "upload_batches",
        "content_objects",
        "content_items",
        "content_versions",
        "content_reviews",
        "content_publications",
        "content_index_jobs",
        "content_item_heads",
        "content_audit_events",
    }
)
UPLOAD_TASK_TABLES = frozenset({"upload_batch_entries"})
CONTENT_PERMISSION_GROUP_TABLES = frozenset(
    {"content_permission_groups", "content_permission_group_items"}
)
CONTENT_FOLDER_REQUEST_TABLES = frozenset({"content_folder_requests"})
SYSTEM_MAINTENANCE_TABLES = frozenset({"maintenance_settings", "maintenance_runs"})
def validate_system_content_permission_groups(
    conn: sqlite3.Connection,
    expected_groups: dict[str, tuple[str, frozenset[str]]] = SYSTEM_CONTENT_PERMISSION_GROUPS,
) -> None:
    rows = conn.execute(
        """SELECT g.group_key,g.display_name,g.is_system,g.is_active,i.permission
           FROM content_permission_groups g
           LEFT JOIN content_permission_group_items i ON i.group_id=g.id
           WHERE g.is_system=1
           ORDER BY g.group_key,i.permission"""
    ).fetchall()
    actual: dict[str, tuple[str, int, set[str]]] = {}
    for group_key, display_name, is_system, is_active, permission in rows:
        entry = actual.setdefault(group_key, (display_name, is_active, set()))
        if entry[:2] != (display_name, is_active) or is_system != 1:
            raise RuntimeError("system_permission_group_mismatch")
        if permission is not None:
            entry[2].add(permission)
    expected = {
        key: (display_name, 1, set(permissions))
        for key, (display_name, permissions) in expected_groups.items()
    }
    if actual != expected:
        raise RuntimeError("system_permission_group_mismatch")


def validate_content_version_metadata(conn: sqlite3.Connection) -> None:
    item_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_items)")}
    version_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_versions)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(content_items)")}
    if "normalized_filename" not in item_columns or "title" not in version_columns:
        raise RuntimeError("migration_schema_mismatch")
    if "uq_content_items_active_filename" not in indexes:
        raise RuntimeError("migration_schema_mismatch")
    if conn.execute("SELECT 1 FROM content_versions WHERE title IS NULL LIMIT 1").fetchone():
        raise RuntimeError("migration_schema_mismatch")


def split_sql_statements(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("incomplete_sql_statement")
    return tuple(statements)


def execute_migration_statement(conn: sqlite3.Connection, statement: str) -> None:
    match = re.fullmatch(
        r"ALTER TABLE ([A-Za-z_][A-Za-z0-9_]*) ADD COLUMN ([A-Za-z_][A-Za-z0-9_]*) .+",
        statement.strip(),
        flags=re.DOTALL,
    )
    if match:
        table_name, column_name = match.groups()
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
        if column_name in columns:
            return
    conn.execute(statement)


def read_schema_inventory(path: Path) -> tuple[frozenset[str], frozenset[str], tuple[tuple[int, str], ...]]:
    if not path.exists():
        return frozenset(), frozenset(), ()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = frozenset(
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        )
        columns = (
            frozenset(row[1] for row in conn.execute("PRAGMA table_info(index_jobs)").fetchall())
            if "index_jobs" in tables
            else frozenset()
        )
        applied = (
            tuple(conn.execute("SELECT version, name FROM app_schema_migrations ORDER BY version").fetchall())
            if "app_schema_migrations" in tables
            else ()
        )
        return tables, columns, applied
    finally:
        conn.close()


def validate_applied_migrations(applied: Iterable[tuple[int, str]]) -> None:
    rows = tuple(applied)
    expected_by_version = {item.version: item.name for item in MIGRATIONS}
    versions = [row[0] for row in rows]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError("migration_version_gap")
    for version, name in rows:
        if version not in expected_by_version:
            raise RuntimeError("unknown_future_migration")
        if expected_by_version[version] != name:
            raise RuntimeError("migration_definition_mismatch")


def has_pending_ddl(path: Path, *, base_tables: frozenset[str]) -> bool:
    tables, index_columns, applied = read_schema_inventory(path)
    validate_applied_migrations(applied)
    if any(version == 1 for version, _name in applied) and not PHASE2_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 2 for version, _name in applied) and not ANSWER_VERSION_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 3 for version, _name in applied) and not FEEDBACK_WORKFLOW_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 4 for version, _name in applied) and not USER_QUESTION_VERSION_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 5 for version, _name in applied) and not CONTENT_LIBRARY_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 6 for version, _name in applied) and not CONTENT_PERMISSION_GROUP_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 6 for version, _name in applied):
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            expected_groups = (
                SYSTEM_CONTENT_PERMISSION_GROUPS
                if any(version == 11 for version, _name in applied)
                else LEGACY_SYSTEM_CONTENT_PERMISSION_GROUPS
            )
            validate_system_content_permission_groups(conn, expected_groups)
        finally:
            conn.close()
    if any(version == 7 for version, _name in applied) and not CONTENT_FOLDER_REQUEST_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 8 for version, _name in applied) and not SYSTEM_MAINTENANCE_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 10 for version, _name in applied):
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            validate_content_version_metadata(conn)
        finally:
            conn.close()
    if any(version == 12 for version, _name in applied) and not UPLOAD_TASK_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if not base_tables.issubset(tables):
        return True
    if "index_jobs" in tables and "media_id" not in index_columns:
        return True
    applied_versions = {row[0] for row in applied}
    return any(item.version not in applied_versions for item in MIGRATIONS)


def apply_all(conn: sqlite3.Connection, *, base_schema: str, applied_at: int) -> None:
    """Apply base, legacy, and Phase 2 DDL in one explicit transaction."""
    if type(applied_at) is not int or applied_at < 0:
        raise ValueError("invalid_applied_at")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in split_sql_statements(base_schema):
            conn.execute(statement)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(index_jobs)").fetchall()}
        if "media_id" not in columns:
            conn.execute("ALTER TABLE index_jobs ADD COLUMN media_id TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_schema_migrations (
                   version INTEGER PRIMARY KEY,
                   name TEXT NOT NULL UNIQUE,
                   applied_at INTEGER NOT NULL
               )"""
        )
        rows = tuple(conn.execute("SELECT version, name FROM app_schema_migrations ORDER BY version"))
        validate_applied_migrations(rows)
        applied_versions = {row[0] for row in rows}
        for migration in MIGRATIONS:
            if migration.version in applied_versions:
                continue
            for statement in migration.statements:
                execute_migration_statement(conn, statement)
            conn.execute(
                "INSERT INTO app_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, applied_at),
            )
            applied_versions.add(migration.version)
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not PHASE2_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not ANSWER_VERSION_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not FEEDBACK_WORKFLOW_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not USER_QUESTION_VERSION_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not CONTENT_LIBRARY_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not CONTENT_PERMISSION_GROUP_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not CONTENT_FOLDER_REQUEST_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if not SYSTEM_MAINTENANCE_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 12 in applied_versions and not UPLOAD_TASK_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        validate_system_content_permission_groups(
            conn,
            SYSTEM_CONTENT_PERMISSION_GROUPS
            if 11 in applied_versions
            else LEGACY_SYSTEM_CONTENT_PERMISSION_GROUPS,
        )
        validate_content_version_metadata(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("migration_foreign_key_check_failed")
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("migration_integrity_check_failed")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
