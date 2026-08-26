"""Forward-only application database migration runner."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .content_permission_catalog import (
    CONTENT_PERMISSION_V2_SYSTEM_CONTENT_PERMISSION_GROUPS,
    LEGACY_SYSTEM_CONTENT_PERMISSION_GROUPS,
    PRE_CATEGORY_FORCE_DELETE_SYSTEM_CONTENT_PERMISSION_GROUPS,
    PRE_RECLASSIFICATION_SYSTEM_CONTENT_PERMISSION_GROUPS,
    PRE_TRASH_LIFECYCLE_SYSTEM_CONTENT_PERMISSION_GROUPS,
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
        doc_type TEXT NOT NULL CHECK (doc_type IN ('pdf','markdown','docx','xlsx','pptx','xmind','transcript')),
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

ANSWER_POLICY_STATEMENTS = (
    "ALTER TABLE message_answer_versions ADD COLUMN policy_version TEXT",
    "ALTER TABLE message_answer_versions ADD COLUMN policy_json TEXT",
    """CREATE TABLE IF NOT EXISTS answer_policy_settings (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        answer_temperature REAL NOT NULL CHECK (answer_temperature BETWEEN 0 AND 1),
        answer_max_output_tokens INTEGER NOT NULL CHECK (answer_max_output_tokens BETWEEN 256 AND 4096),
        answer_context_chars INTEGER NOT NULL CHECK (answer_context_chars BETWEEN 2000 AND 12000),
        relevance_gate_enabled INTEGER NOT NULL DEFAULT 0 CHECK (relevance_gate_enabled IN (0,1)),
        relevance_min_score REAL NOT NULL DEFAULT 0 CHECK (relevance_min_score >= 0),
        relevance_min_rrf REAL NOT NULL DEFAULT 0 CHECK (relevance_min_rrf >= 0),
        relevance_min_margin REAL NOT NULL DEFAULT 0 CHECK (relevance_min_margin >= 0),
        policy_version TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE IF NOT EXISTS answer_policy_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_policy_json TEXT NOT NULL,
        new_policy_json TEXT NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        change_reason TEXT,
        created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_answer_policy_audit_created_desc ON answer_policy_audit(created_at DESC, id DESC)",
)

MEDIA_TRANSCRIPT_LIBRARY_STATEMENTS = (
    """INSERT INTO content_items(
           id,title,content_kind,category_id,media_id,created_by,created_at,
           updated_at,archived_at,normalized_filename
       )
       SELECT 'media-transcript-' || m.media_id,m.title,'media_transcript','cat-05',
              m.media_id,m.created_by,m.created_at,
              CASE WHEN m.updated_at > h.updated_at THEN m.updated_at ELSE h.updated_at END,
              NULL,NULL
       FROM media_transcript_heads h
       JOIN media_assets m ON m.media_id=h.media_id
       JOIN transcript_versions v ON v.id=h.current_version_id AND v.media_id=m.media_id
       WHERE m.status <> 'archived' AND v.publication_status='published'
         AND NOT EXISTS (
             SELECT 1 FROM content_items i WHERE i.media_id=m.media_id
         )""",
)

ASR_PROFILE_MANAGEMENT_STATEMENTS = (
    """CREATE TABLE asr_profile_release_requests (
        id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        profile_id TEXT NOT NULL,
        profile_config_hash TEXT NOT NULL,
        profile_snapshot_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'requested'
            CHECK (status IN ('requested','completed','rejected','cancelled')),
        request_reason TEXT,
        requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE INDEX idx_asr_profile_release_requests_created
       ON asr_profile_release_requests(created_at DESC)""",
    """CREATE TABLE asr_profile_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL CHECK (event_type IN ('release_requested')),
        release_request_id TEXT REFERENCES asr_profile_release_requests(id) ON DELETE RESTRICT,
        profile_id TEXT NOT NULL,
        profile_config_hash TEXT NOT NULL,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        event_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    """CREATE INDEX idx_asr_profile_audit_events_created
       ON asr_profile_audit_events(created_at DESC)""",
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

TRANSCRIPT_MANUAL_REVISION_STATEMENTS = (
    "ALTER TABLE transcript_versions ADD COLUMN derived_from_version_id TEXT REFERENCES transcript_versions(id) ON DELETE RESTRICT",
    "ALTER TABLE transcript_versions ADD COLUMN edited_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE transcript_versions ADD COLUMN edit_idempotency_key TEXT",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_transcript_versions_edit_idempotency
       ON transcript_versions(edit_idempotency_key)
       WHERE edit_idempotency_key IS NOT NULL""",
)

CONTENT_PERMISSION_DOWNLOAD_STATEMENTS = (
    "ALTER TABLE content_permissions RENAME TO content_permissions_v11",
    """CREATE TABLE content_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.archive_published','trash.view','trash.restore',
            'category.manage','folder.request','folder.review','import.server','index.view'
        )),
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, permission)
    )""",
    """INSERT INTO content_permissions(user_id,permission,granted_by,created_at)
       SELECT user_id,permission,granted_by,created_at FROM content_permissions_v11""",
    """INSERT INTO content_permissions(user_id,permission,granted_by,created_at)
       SELECT user_id,'item.download',MIN(granted_by),MIN(created_at)
       FROM content_permissions_v11
       WHERE permission='item.view'
       GROUP BY user_id""",
    "DROP TABLE content_permissions_v11",
    "ALTER TABLE content_permission_group_items RENAME TO content_permission_group_items_v11",
    """CREATE TABLE content_permission_group_items (
        group_id TEXT NOT NULL REFERENCES content_permission_groups(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.archive_published','trash.view','trash.restore',
            'category.manage','folder.request','folder.review','import.server','index.view'
        )),
        PRIMARY KEY(group_id, permission)
    )""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT group_id,permission FROM content_permission_group_items_v11""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT group_id,'item.download'
       FROM content_permission_group_items_v11
       WHERE permission='item.view'
       GROUP BY group_id""",
    "DROP TABLE content_permission_group_items_v11",
)

CONTENT_RECLASSIFICATION_STATEMENTS = (
    "ALTER TABLE content_permissions RENAME TO content_permissions_v18",
    """CREATE TABLE content_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.reclassify_published','item.archive_published','trash.view','trash.restore',
            'category.manage','folder.request','folder.review','import.server','index.view'
        )),
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, permission)
    )""",
    """INSERT INTO content_permissions(user_id,permission,granted_by,created_at)
       SELECT user_id,permission,granted_by,created_at FROM content_permissions_v18""",
    "DROP TABLE content_permissions_v18",
    "ALTER TABLE content_permission_group_items RENAME TO content_permission_group_items_v18",
    """CREATE TABLE content_permission_group_items (
        group_id TEXT NOT NULL REFERENCES content_permission_groups(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.reclassify_published','item.archive_published','trash.view','trash.restore',
            'category.manage','folder.request','folder.review','import.server','index.view'
        )),
        PRIMARY KEY(group_id, permission)
    )""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT group_id,permission FROM content_permission_group_items_v18""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT id,'item.reclassify_published' FROM content_permission_groups
       WHERE is_system=1 AND group_key IN ('publisher','system_admin')""",
    "DROP TABLE content_permission_group_items_v18",
    """CREATE TABLE IF NOT EXISTS content_reclassification_jobs (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE RESTRICT,
        expected_version_id TEXT NOT NULL REFERENCES content_versions(id) ON DELETE RESTRICT,
        source_category_id TEXT NOT NULL REFERENCES category_nodes(id) ON DELETE RESTRICT,
        target_category_id TEXT NOT NULL REFERENCES category_nodes(id) ON DELETE RESTRICT,
        source_category_key TEXT NOT NULL,
        source_category_label TEXT NOT NULL,
        source_category_version INTEGER NOT NULL,
        target_category_key TEXT NOT NULL,
        target_category_label TEXT NOT NULL,
        target_category_version INTEGER NOT NULL,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        retry_of_job_id TEXT REFERENCES content_reclassification_jobs(id) ON DELETE SET NULL,
        status TEXT NOT NULL CHECK (status IN (
            'pending','applying','committing','rolling_back','succeeded','failed'
        )),
        qdrant_point_count INTEGER NOT NULL DEFAULT 0 CHECK (qdrant_point_count >= 0),
        parent_count INTEGER NOT NULL DEFAULT 0 CHECK (parent_count >= 0),
        qdrant_applied INTEGER NOT NULL DEFAULT 0 CHECK (qdrant_applied IN (0,1)),
        parents_applied INTEGER NOT NULL DEFAULT 0 CHECK (parents_applied IN (0,1)),
        item_committed INTEGER NOT NULL DEFAULT 0 CHECK (item_committed IN (0,1)),
        view_activated INTEGER NOT NULL DEFAULT 0 CHECK (view_activated IN (0,1)),
        candidate_view_path TEXT,
        error_code TEXT,
        error_summary TEXT,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_content_reclassification_active_item
       ON content_reclassification_jobs(item_id)
       WHERE status IN ('pending','applying','committing','rolling_back')""",
    """CREATE INDEX IF NOT EXISTS idx_content_reclassification_item_created
       ON content_reclassification_jobs(item_id,created_at DESC)""",
)

MEDIA_LIBRARY_VIDEO_ACTIONS_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS media_metadata_revisions (
        id TEXT PRIMARY KEY,
        media_id TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        transcript_version_id TEXT NOT NULL UNIQUE REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        base_version_id TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        proposed_title TEXT NOT NULL,
        proposed_original_filename TEXT NOT NULL,
        requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        request_idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('pending','rejected','failed','activated')),
        created_at INTEGER NOT NULL,
        activated_at INTEGER,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_media_metadata_revisions_active
       ON media_metadata_revisions(media_id) WHERE status='pending'""",
    """CREATE TABLE IF NOT EXISTS media_replacements (
        id TEXT PRIMARY KEY,
        source_media_id TEXT NOT NULL REFERENCES media_assets(media_id) ON DELETE RESTRICT,
        candidate_media_id TEXT NOT NULL UNIQUE REFERENCES media_assets(media_id) ON DELETE CASCADE,
        source_catalog_item_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE RESTRICT,
        source_head_version_id TEXT NOT NULL REFERENCES transcript_versions(id) ON DELETE RESTRICT,
        profile_id TEXT NOT NULL,
        request_idempotency_key TEXT NOT NULL UNIQUE,
        requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','failed','activated','cancelled')),
        error_code TEXT,
        created_at INTEGER NOT NULL,
        activated_at INTEGER,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_media_replacements_active_source
       ON media_replacements(source_media_id) WHERE status='pending'""",
)

CONTENT_TRASH_LIFECYCLE_STATEMENTS = (
    "ALTER TABLE content_permissions RENAME TO content_permissions_v20",
    """CREATE TABLE content_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.reclassify_published','item.archive_published','trash.view','trash.restore',
            'trash.purge','trash.policy_manage','category.manage','folder.request','folder.review',
            'import.server','index.view'
        )),
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, permission)
    )""",
    """INSERT INTO content_permissions(user_id,permission,granted_by,created_at)
       SELECT user_id,permission,granted_by,created_at FROM content_permissions_v20""",
    "DROP TABLE content_permissions_v20",
    "ALTER TABLE content_permission_group_items RENAME TO content_permission_group_items_v20",
    """CREATE TABLE content_permission_group_items (
        group_id TEXT NOT NULL REFERENCES content_permission_groups(id) ON DELETE CASCADE,
        permission TEXT NOT NULL CHECK (permission IN (
            'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
            'item.move_draft','item.archive_draft','item.review','item.move_review',
            'item.publish','item.reclassify_published','item.archive_published','trash.view','trash.restore',
            'trash.purge','trash.policy_manage','category.manage','folder.request','folder.review',
            'import.server','index.view'
        )),
        PRIMARY KEY(group_id, permission)
    )""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT group_id,permission FROM content_permission_group_items_v20""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT id,'trash.purge' FROM content_permission_groups
       WHERE is_system=1 AND group_key='system_admin'""",
    """INSERT INTO content_permission_group_items(group_id,permission)
       SELECT id,'trash.policy_manage' FROM content_permission_groups
       WHERE is_system=1 AND group_key='system_admin'""",
    "DROP TABLE content_permission_group_items_v20",
    """CREATE TABLE IF NOT EXISTS content_trash_settings (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
        cleanup_enabled INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_enabled IN (0,1)),
        retention_days INTEGER NOT NULL DEFAULT 90 CHECK (retention_days BETWEEN 1 AND 3650),
        warning_days INTEGER NOT NULL DEFAULT 7 CHECK (warning_days BETWEEN 0 AND 365),
        batch_limit INTEGER NOT NULL DEFAULT 20 CHECK (batch_limit BETWEEN 1 AND 20),
        lease_owner TEXT,
        lease_expires_at INTEGER,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at INTEGER NOT NULL
    )""",
    """INSERT OR IGNORE INTO content_trash_settings(
        singleton_id,cleanup_enabled,retention_days,warning_days,batch_limit,updated_at
    ) VALUES (1,0,90,7,20,0)""",
    """CREATE TABLE IF NOT EXISTS content_trash_purge_runs (
        id TEXT PRIMARY KEY,
        trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual','automatic')),
        policy_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
        candidate_count INTEGER NOT NULL DEFAULT 0,
        succeeded_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        error_summary TEXT,
        created_at INTEGER NOT NULL,
        finished_at INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS content_trash_purge_items (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES content_trash_purge_runs(id) ON DELETE RESTRICT,
        item_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        title TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        category_path TEXT NOT NULL,
        object_sha256 TEXT,
        status TEXT NOT NULL CHECK (status IN ('planned','succeeded','failed','blocked')),
        reason TEXT,
        qdrant_points_deleted INTEGER NOT NULL DEFAULT 0,
        parents_deleted INTEGER NOT NULL DEFAULT 0,
        object_deleted INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        finished_at INTEGER,
        UNIQUE(run_id,item_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_content_trash_purge_runs_created
       ON content_trash_purge_runs(created_at DESC)""",
)

USAGE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS external_service_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        operation TEXT NOT NULL,
        success INTEGER NOT NULL CHECK (success IN (0, 1)),
        request_count INTEGER NOT NULL DEFAULT 1,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        item_count INTEGER NOT NULL DEFAULT 0,
        input_bytes INTEGER NOT NULL DEFAULT 0,
        latency_ms INTEGER,
        created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_external_service_usage_created ON external_service_usage(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_external_service_usage_provider ON external_service_usage(provider, operation, created_at)",
)

TRANSCRIPTION_SCHEME_STATEMENTS = (
    """ALTER TABLE transcription_jobs ADD COLUMN scheme_id TEXT""",
    """ALTER TABLE transcription_jobs ADD COLUMN scheme_snapshot_json TEXT""",
    """ALTER TABLE transcript_versions ADD COLUMN scheme_id TEXT""",
    """ALTER TABLE transcript_versions ADD COLUMN scheme_snapshot_json TEXT""",
    """CREATE TABLE IF NOT EXISTS transcription_bases (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        revision TEXT NOT NULL,
        service_profile_id TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        qualification TEXT NOT NULL,
        admission TEXT NOT NULL,
        availability TEXT NOT NULL,
        capabilities_json TEXT NOT NULL,
        defaults_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS transcription_schemes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        base_id TEXT NOT NULL REFERENCES transcription_bases(id) ON DELETE RESTRICT,
        config_json TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
        system_preset INTEGER NOT NULL DEFAULT 0 CHECK (system_preset IN (0,1)),
        sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_transcription_schemes_order ON transcription_schemes(archived,enabled,sort_order,id)""",
    """CREATE TABLE IF NOT EXISTS transcription_scheme_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scheme_id TEXT NOT NULL REFERENCES transcription_schemes(id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        event_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    """INSERT OR IGNORE INTO transcription_bases(id,provider,model,revision,service_profile_id,config_hash,qualification,admission,availability,capabilities_json,defaults_json,created_at) VALUES
      ('sensevoice-v1','funasr','SenseVoiceSmall','managed-v1','funasr-sensevoice-small-v1','managed-base-sensevoice-v1','qualification_approved','enabled','runtime','{"segmentation":true,"decode_presets":true}','{"segmentation_preset":"natural"}',strftime('%s','now')),
      ('faster-whisper-v1','faster-whisper','large-v3-turbo','0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf','faster-whisper-large-v3-turbo-v1','managed-base-faster-v1','qualification_approved','enabled','runtime','{"segmentation":true,"decode_presets":true}','{"segmentation_preset":"natural"}',strftime('%s','now')),
      ('whisperx-v2','whisperx','large-v3-zh-align','full-decode-v2','whisperx-large-v3-zh-align-v2','managed-base-whisperx-v2','qualification_approved','enabled','runtime','{"segmentation":true,"decode_presets":true}','{"segmentation_preset":"balanced"}',strftime('%s','now')),
      ('qwen3-asr-v1','qwen3-asr','Qwen3-ASR-0.6B','5eb144179a02acc5e5ba31e748d22b0cf3e303b0','qwen3-asr-06b-aligner-v1','managed-base-qwen3-v1','experimental','disabled','disabled','{"segmentation":false,"decode_presets":false}','{"segmentation_preset":"natural"}',strftime('%s','now'))""",
    """INSERT OR IGNORE INTO transcription_schemes(id,name,description,base_id,config_json,config_hash,enabled,archived,system_preset,sort_order,version,created_at,updated_at) VALUES
      ('funasr-sensevoice-zh-experimental-v1','SenseVoice 快速中文','SenseVoice 中文快速转录','sensevoice-v1','{"decode_preset":"service-default-v1","max_chars":500,"max_duration_ms":null,"merge_gap_ms":1000,"preprocessing_preset":"standard-audio-v1","prompt_asset":"asr_engineering_zh_v2","segmentation_preset":"natural","terminology_profile":"bim-engineering-v1","vad_preset":"service-default-v1"}','cab80220f6aad3ad4ecf937115a9c2289d7f3fe8d22f95ed0cb140bd91453e58',1,0,1,0,1,strftime('%s','now'),strftime('%s','now')),
      ('faster-whisper-zh-experimental-v1','faster-whisper 工程术语','固定工程术语与服务端解码预设','faster-whisper-v1','{"decode_preset":"service-default-v1","max_chars":500,"max_duration_ms":null,"merge_gap_ms":1000,"preprocessing_preset":"standard-audio-v1","prompt_asset":"asr_engineering_zh_v1","segmentation_preset":"natural","terminology_profile":"bim-engineering-v1","vad_preset":"service-default-v1"}','95bc0d6219805563287db268e08a2f1d051e20ada8c29991fb3261f138e4d98a',1,0,1,1,1,strftime('%s','now'),strftime('%s','now')),
      ('whisperx-large-v3-zh-natural-v2','WhisperX 自然分段','WhisperX v2 自然分段','whisperx-v2','{"decode_preset":"service-default-v1","max_chars":500,"max_duration_ms":null,"merge_gap_ms":1000,"preprocessing_preset":"standard-audio-v1","prompt_asset":"asr_engineering_zh_v2","segmentation_preset":"natural","terminology_profile":"bim-engineering-v1","vad_preset":"service-default-v1"}','cab80220f6aad3ad4ecf937115a9c2289d7f3fe8d22f95ed0cb140bd91453e58',1,0,1,2,1,strftime('%s','now'),strftime('%s','now')),
      ('whisperx-large-v3-zh-balanced-v2','WhisperX 均衡分段','WhisperX v2 均衡分段','whisperx-v2','{"decode_preset":"service-default-v1","max_chars":500,"max_duration_ms":30000,"merge_gap_ms":750,"preprocessing_preset":"standard-audio-v1","prompt_asset":"asr_engineering_zh_v2","segmentation_preset":"balanced","terminology_profile":"bim-engineering-v1","vad_preset":"service-default-v1"}','95a70a87376bd304459ecb766f438a57919e7223932cdc7c5675026191cb96a8',1,0,1,3,1,strftime('%s','now'),strftime('%s','now')),
      ('whisperx-large-v3-zh-fine-v2','WhisperX 精细分段','WhisperX v2 精细分段','whisperx-v2','{"decode_preset":"service-default-v1","max_chars":240,"max_duration_ms":15000,"merge_gap_ms":500,"preprocessing_preset":"standard-audio-v1","prompt_asset":"asr_engineering_zh_v2","segmentation_preset":"fine","terminology_profile":"bim-engineering-v1","vad_preset":"service-default-v1"}','82d57ba229516849be26b7068182f67154119606736f2774afc22472401d2cf2',1,0,1,4,1,strftime('%s','now'),strftime('%s','now'))""",
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
    Migration(12, "transcript_manual_revisions", TRANSCRIPT_MANUAL_REVISION_STATEMENTS),
    Migration(13, "content_download_permission", CONTENT_PERMISSION_DOWNLOAD_STATEMENTS),
    Migration(14, "managed_upload_tasks", UPLOAD_TASK_STATEMENTS),
    Migration(15, "answer_policy_settings_and_snapshots", ANSWER_POLICY_STATEMENTS),
    Migration(16, "media_transcript_library_catalog", MEDIA_TRANSCRIPT_LIBRARY_STATEMENTS),
    Migration(17, "asr_profile_management", ASR_PROFILE_MANAGEMENT_STATEMENTS),
    Migration(18, "published_content_reclassification", CONTENT_RECLASSIFICATION_STATEMENTS),
    Migration(19, "media_library_video_actions", MEDIA_LIBRARY_VIDEO_ACTIONS_STATEMENTS),
    Migration(20, "content_trash_lifecycle", CONTENT_TRASH_LIFECYCLE_STATEMENTS),
    Migration(21, "external_service_usage", USAGE_STATEMENTS),
    Migration(
        22,
        "category_chat_scope_settings",
        (
            "ALTER TABLE category_nodes ADD COLUMN chat_search_enabled INTEGER NOT NULL DEFAULT 1 CHECK (chat_search_enabled IN (0,1))",
            "ALTER TABLE category_nodes ADD COLUMN chat_filter_selectable INTEGER NOT NULL DEFAULT 1 CHECK (chat_filter_selectable IN (0,1))",
            "UPDATE category_nodes SET chat_search_enabled=is_active, chat_filter_selectable=is_active",
        ),
    ),
    Migration(23, "transcription_scheme_management", TRANSCRIPTION_SCHEME_STATEMENTS),
    Migration(
        24,
        "category_logical_deletion",
        (
            "ALTER TABLE category_nodes ADD COLUMN deleted_at INTEGER",
            "ALTER TABLE category_nodes ADD COLUMN deleted_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
            "DROP INDEX uq_category_nodes_sibling_code",
            """CREATE UNIQUE INDEX uq_category_nodes_sibling_code
               ON category_nodes(COALESCE(parent_id,''), display_code)
               WHERE deleted_at IS NULL""",
            "CREATE INDEX IF NOT EXISTS idx_category_nodes_deleted_at ON category_nodes(deleted_at)",
        ),
    ),
    Migration(
        25,
        "category_force_delete",
        (
            "ALTER TABLE content_permissions RENAME TO content_permissions_v24",
            """CREATE TABLE content_permissions (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                permission TEXT NOT NULL CHECK (permission IN (
                    'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
                    'item.move_draft','item.archive_draft','item.review','item.move_review',
                    'item.publish','item.reclassify_published','item.archive_published','trash.view','trash.restore',
                    'trash.purge','trash.policy_manage','category.manage','category.force_delete','folder.request',
                    'folder.review','import.server','index.view'
                )),
                granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, permission)
            )""",
            """INSERT INTO content_permissions(user_id,permission,granted_by,created_at)
               SELECT user_id,permission,granted_by,created_at FROM content_permissions_v24""",
            "DROP TABLE content_permissions_v24",
            "ALTER TABLE content_permission_group_items RENAME TO content_permission_group_items_v24",
            """CREATE TABLE content_permission_group_items (
                group_id TEXT NOT NULL REFERENCES content_permission_groups(id) ON DELETE CASCADE,
                permission TEXT NOT NULL CHECK (permission IN (
                    'workspace.view','item.view','item.download','category.view','item.upload','item.submit',
                    'item.move_draft','item.archive_draft','item.review','item.move_review',
                    'item.publish','item.reclassify_published','item.archive_published','trash.view','trash.restore',
                    'trash.purge','trash.policy_manage','category.manage','category.force_delete','folder.request',
                    'folder.review','import.server','index.view'
                )),
                PRIMARY KEY(group_id, permission)
            )""",
            """INSERT INTO content_permission_group_items(group_id,permission)
               SELECT group_id,permission FROM content_permission_group_items_v24""",
            """INSERT OR IGNORE INTO content_permission_group_items(group_id,permission)
               SELECT id,'category.force_delete' FROM content_permission_groups
               WHERE is_system=1 AND group_key='system_admin'""",
            "DROP TABLE content_permission_group_items_v24",
            """CREATE TABLE IF NOT EXISTS category_force_delete_runs (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL,
                category_path TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
                folder_count INTEGER NOT NULL DEFAULT 0,
                item_count INTEGER NOT NULL DEFAULT 0,
                upload_batch_count INTEGER NOT NULL DEFAULT 0,
                index_job_count INTEGER NOT NULL DEFAULT 0,
                qdrant_point_count INTEGER NOT NULL DEFAULT 0,
                object_count INTEGER NOT NULL DEFAULT 0,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                error_summary TEXT,
                created_at INTEGER NOT NULL,
                finished_at INTEGER
            )""",
            "CREATE INDEX IF NOT EXISTS idx_category_force_delete_runs_created ON category_force_delete_runs(created_at DESC)",
        ),
    ),
    Migration(
        26,
        "media_upload_directory_and_conflicts",
        (
            "ALTER TABLE media_assets ADD COLUMN target_category_id TEXT REFERENCES category_nodes(id) ON DELETE RESTRICT",
            "ALTER TABLE media_assets ADD COLUMN normalized_title TEXT",
            "ALTER TABLE media_assets ADD COLUMN normalized_original_filename TEXT",
            "CREATE INDEX IF NOT EXISTS idx_media_assets_target_category ON media_assets(target_category_id)",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_media_assets_active_category_title
               ON media_assets(target_category_id,normalized_title)
               WHERE status<>'archived' AND target_category_id IS NOT NULL AND normalized_title IS NOT NULL""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_media_assets_active_category_filename
               ON media_assets(target_category_id,normalized_original_filename)
               WHERE status<>'archived' AND target_category_id IS NOT NULL AND normalized_original_filename IS NOT NULL""",
        ),
    ),
    Migration(
        27,
        "managed_upload_limit_settings",
        (
            "ALTER TABLE maintenance_settings ADD COLUMN upload_max_file_mb INTEGER NOT NULL DEFAULT 2000 CHECK (upload_max_file_mb BETWEEN 1 AND 10240)",
            "ALTER TABLE maintenance_settings ADD COLUMN upload_max_batch_files INTEGER NOT NULL DEFAULT 5000 CHECK (upload_max_batch_files BETWEEN 1 AND 10000)",
            "ALTER TABLE maintenance_settings ADD COLUMN upload_max_batch_mb INTEGER NOT NULL DEFAULT 10240 CHECK (upload_max_batch_mb BETWEEN 1 AND 102400)",
        ),
    ),
    Migration(
        28,
        "external_media_sources",
        (
            """CREATE TABLE IF NOT EXISTS external_media_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                root_alias TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                target_category_id TEXT NOT NULL REFERENCES category_nodes(id) ON DELETE RESTRICT,
                default_scheme_id TEXT NOT NULL REFERENCES transcription_schemes(id) ON DELETE RESTRICT,
                auto_enqueue INTEGER NOT NULL DEFAULT 0 CHECK (auto_enqueue IN (0,1)),
                scan_interval_seconds INTEGER NOT NULL DEFAULT 900 CHECK (scan_interval_seconds >= 60),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
                status TEXT NOT NULL DEFAULT 'never_scanned'
                    CHECK (status IN ('never_scanned','scanning','available','unavailable','scan_failed')),
                total_files INTEGER NOT NULL DEFAULT 0 CHECK (total_files >= 0),
                available_files INTEGER NOT NULL DEFAULT 0 CHECK (available_files >= 0),
                missing_files INTEGER NOT NULL DEFAULT 0 CHECK (missing_files >= 0),
                last_scan_at INTEGER,
                last_successful_scan_at INTEGER,
                last_error_code TEXT,
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                UNIQUE(root_alias, relative_path)
            )""",
            """CREATE TABLE IF NOT EXISTS external_media_entries (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES external_media_sources(id) ON DELETE CASCADE,
                media_id TEXT UNIQUE REFERENCES media_assets(media_id) ON DELETE RESTRICT,
                relative_path TEXT NOT NULL,
                parent_relative_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL CHECK (file_size >= 0),
                modified_ns INTEGER NOT NULL CHECK (modified_ns >= 0),
                fingerprint TEXT NOT NULL,
                availability TEXT NOT NULL CHECK (availability IN ('available','missing','superseded')),
                discovered_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                missing_since INTEGER,
                updated_at INTEGER NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_external_media_entries_identity
               ON external_media_entries(source_id,relative_path,fingerprint)""",
            """CREATE INDEX IF NOT EXISTS idx_external_media_entries_source_parent
               ON external_media_entries(source_id,parent_relative_path,filename)""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_external_media_entries_current_path
               ON external_media_entries(source_id,relative_path) WHERE availability='available'""",
            """CREATE INDEX IF NOT EXISTS idx_external_media_entries_media
               ON external_media_entries(media_id)""",
            """CREATE TABLE IF NOT EXISTS external_media_scan_runs (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES external_media_sources(id) ON DELETE CASCADE,
                trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual','scheduled')),
                status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed')),
                discovered_count INTEGER NOT NULL DEFAULT 0 CHECK (discovered_count >= 0),
                added_count INTEGER NOT NULL DEFAULT 0 CHECK (added_count >= 0),
                changed_count INTEGER NOT NULL DEFAULT 0 CHECK (changed_count >= 0),
                missing_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
                enqueued_count INTEGER NOT NULL DEFAULT 0 CHECK (enqueued_count >= 0),
                error_code TEXT,
                started_at INTEGER NOT NULL,
                finished_at INTEGER
            )""",
            "CREATE INDEX IF NOT EXISTS idx_external_media_scan_runs_source_started ON external_media_scan_runs(source_id,started_at DESC)",
            "ALTER TABLE media_assets ADD COLUMN storage_kind TEXT NOT NULL DEFAULT 'managed' CHECK (storage_kind IN ('managed','external'))",
        ),
    ),
    Migration(29, "xmind_managed_content", ("RELAX_CONTENT_VERSION_DOC_TYPE_XMIND",)),
    Migration(
        30,
        "managed_content_bulk_operations",
        (
            """CREATE TABLE IF NOT EXISTS content_bulk_operations (
                id TEXT PRIMARY KEY,
                operation TEXT NOT NULL CHECK (operation IN (
                    'move','submit','approve','reject','publish','download','delete','force_delete'
                )),
                status TEXT NOT NULL CHECK (status IN (
                    'awaiting_confirmation','queued','running','packaging','ready',
                    'succeeded','partial','failed','cancelled','expired'
                )),
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                target_category_id TEXT REFERENCES category_nodes(id) ON DELETE RESTRICT,
                note TEXT,
                source_json TEXT NOT NULL,
                confirmation_phrase TEXT,
                total_files INTEGER NOT NULL DEFAULT 0 CHECK (total_files >= 0),
                selected_files INTEGER NOT NULL DEFAULT 0 CHECK (selected_files >= 0),
                completed_files INTEGER NOT NULL DEFAULT 0 CHECK (completed_files >= 0),
                failed_files INTEGER NOT NULL DEFAULT 0 CHECK (failed_files >= 0),
                total_folders INTEGER NOT NULL DEFAULT 0 CHECK (total_folders >= 0),
                total_bytes INTEGER NOT NULL DEFAULT 0 CHECK (total_bytes >= 0),
                processed_bytes INTEGER NOT NULL DEFAULT 0 CHECK (processed_bytes >= 0),
                archive_filename TEXT,
                error_summary TEXT,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                expires_at INTEGER,
                updated_at INTEGER NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS content_bulk_operation_categories (
                run_id TEXT NOT NULL REFERENCES content_bulk_operations(id) ON DELETE CASCADE,
                category_id TEXT NOT NULL,
                parent_id TEXT,
                full_path TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                version INTEGER NOT NULL,
                root_category_id TEXT NOT NULL,
                is_root INTEGER NOT NULL DEFAULT 0 CHECK (is_root IN (0,1)),
                eligible INTEGER NOT NULL DEFAULT 1 CHECK (eligible IN (0,1)),
                selected INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0,1)),
                reason TEXT,
                result_status TEXT NOT NULL DEFAULT 'pending' CHECK (result_status IN (
                    'pending','succeeded','failed','skipped'
                )),
                result_message TEXT,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY(run_id,category_id)
            )""",
            """CREATE TABLE IF NOT EXISTS content_bulk_operation_items (
                run_id TEXT NOT NULL REFERENCES content_bulk_operations(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                category_id TEXT NOT NULL,
                category_path TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                title TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                content_kind TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL,
                object_sha256 TEXT,
                storage_rel_path TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
                scope_source TEXT NOT NULL CHECK (scope_source IN ('category','direct')),
                root_category_id TEXT,
                eligible INTEGER NOT NULL DEFAULT 0 CHECK (eligible IN (0,1)),
                selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0,1)),
                reason TEXT,
                result_status TEXT NOT NULL DEFAULT 'pending' CHECK (result_status IN (
                    'pending','succeeded','failed','skipped'
                )),
                result_message TEXT,
                index_job_id TEXT,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY(run_id,item_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_content_bulk_operations_actor_created ON content_bulk_operations(actor_user_id,created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_content_bulk_operations_status ON content_bulk_operations(status,updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_content_bulk_items_run_sort ON content_bulk_operation_items(run_id,sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_content_bulk_categories_run_sort ON content_bulk_operation_categories(run_id,sort_order)",
        ),
    ),
    Migration(31, "legacy_office_managed_content", ("RELAX_CONTENT_VERSION_DOC_TYPE_LEGACY_OFFICE",)),
    Migration(
        32,
        "unified_upload_transcription_entries",
        (
            "ALTER TABLE upload_batch_entries ADD COLUMN entry_kind TEXT NOT NULL DEFAULT 'document' CHECK (entry_kind IN ('document','video'))",
            "ALTER TABLE upload_batch_entries ADD COLUMN media_id TEXT REFERENCES media_assets(media_id) ON DELETE SET NULL",
            "ALTER TABLE upload_batch_entries ADD COLUMN transcription_job_id TEXT REFERENCES transcription_jobs(id) ON DELETE SET NULL",
            "ALTER TABLE upload_batch_entries ADD COLUMN failure_code TEXT",
            "CREATE INDEX IF NOT EXISTS idx_upload_batch_entries_media ON upload_batch_entries(media_id)",
            "CREATE INDEX IF NOT EXISTS idx_upload_batch_entries_transcription_job ON upload_batch_entries(transcription_job_id)",
        ),
    ),
    Migration(
        33,
        "remove_content_review_permissions",
        (
            "-- Historical review permissions remain readable for legacy API compatibility; they are absent from the active catalog",
        ),
    ),
    Migration(34, "unbounded_managed_category_depth", ("RELAX_CATEGORY_NODE_LEVEL_CHECK",)),
    Migration(
        35,
        "shared_folder_category_metadata",
        (
            "ALTER TABLE category_nodes ADD COLUMN category_kind TEXT NOT NULL DEFAULT 'folder' CHECK (category_kind IN ('folder','shared_folder'))",
            "ALTER TABLE category_nodes ADD COLUMN external_source_id TEXT REFERENCES external_media_sources(id) ON DELETE RESTRICT",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_category_nodes_external_source ON category_nodes(external_source_id) WHERE external_source_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_category_nodes_kind ON category_nodes(category_kind)",
        ),
    ),
    Migration(36, "document_pending_publication_status", ("REPLACE_DOCUMENT_REVIEW_STATUSES",)),
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
CONTENT_BULK_OPERATION_TABLES = frozenset(
    {
        "content_bulk_operations",
        "content_bulk_operation_categories",
        "content_bulk_operation_items",
    }
)
UPLOAD_TASK_TABLES = frozenset({"upload_batch_entries"})
CONTENT_PERMISSION_GROUP_TABLES = frozenset(
    {"content_permission_groups", "content_permission_group_items"}
)
CONTENT_FOLDER_REQUEST_TABLES = frozenset({"content_folder_requests"})
SYSTEM_MAINTENANCE_TABLES = frozenset({"maintenance_settings", "maintenance_runs"})
ANSWER_POLICY_TABLES = frozenset({"answer_policy_settings", "answer_policy_audit"})
ASR_PROFILE_MANAGEMENT_TABLES = frozenset(
    {"asr_profile_release_requests", "asr_profile_audit_events"}
)
CONTENT_RECLASSIFICATION_TABLES = frozenset({"content_reclassification_jobs"})
MEDIA_LIBRARY_VIDEO_ACTIONS_TABLES = frozenset(
    {"media_metadata_revisions", "media_replacements"}
)
CONTENT_TRASH_LIFECYCLE_TABLES = frozenset(
    {"content_trash_settings", "content_trash_purge_runs", "content_trash_purge_items"}
)
TRANSCRIPTION_SCHEME_TABLES = frozenset({"transcription_bases", "transcription_schemes", "transcription_scheme_audit_events"})
CATEGORY_FORCE_DELETE_TABLES = frozenset({"category_force_delete_runs"})
EXTERNAL_MEDIA_SOURCE_TABLES = frozenset(
    {"external_media_sources", "external_media_entries", "external_media_scan_runs"}
)


def validate_category_node_level_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='category_nodes'"
    ).fetchone()
    if (
        row is None
        or not row[0]
        or "level INTEGER NOT NULL CHECK (level >= 1)" not in row[0]
        or "CHECK (level BETWEEN 1 AND 4)" in row[0]
    ):
        raise RuntimeError("migration_schema_mismatch")


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
    legacy_permissions = {"item.submit", "item.review", "item.move_review"}
    actual = {
        key: (display_name, active, permissions - legacy_permissions)
        for key, (display_name, active, permissions) in actual.items()
    }
    expected = {
        key: (display_name, 1, set(permissions))
        for key, (display_name, permissions) in expected_groups.items()
    }
    expected = {
        key: (display_name, active, permissions - legacy_permissions)
        for key, (display_name, active, permissions) in expected.items()
    }
    if actual != expected:
        raise RuntimeError("system_permission_group_mismatch")


def validate_transcript_manual_revision_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(transcript_versions)")}
    if not {"derived_from_version_id", "edited_by", "edit_idempotency_key"}.issubset(columns):
        raise RuntimeError("transcript_manual_revision_schema_mismatch")
    index = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_transcript_versions_edit_idempotency'"
    ).fetchone()
    if index is None or "WHERE edit_idempotency_key IS NOT NULL" not in str(index[0]):
        raise RuntimeError("transcript_manual_revision_schema_mismatch")


def validate_content_version_metadata(
    conn: sqlite3.Connection, *, require_xmind: bool = True
) -> None:
    item_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_items)")}
    version_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_versions)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(content_items)")}
    if "normalized_filename" not in item_columns or "title" not in version_columns:
        raise RuntimeError("migration_schema_mismatch")
    if "uq_content_items_active_filename" not in indexes:
        raise RuntimeError("migration_schema_mismatch")
    if conn.execute("SELECT 1 FROM content_versions WHERE title IS NULL LIMIT 1").fetchone():
        raise RuntimeError("migration_schema_mismatch")
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_versions'"
    ).fetchone()
    if schema is None or (require_xmind and "'xmind'" not in str(schema[0])):
        raise RuntimeError("migration_schema_mismatch")


def validate_answer_policy_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(message_answer_versions)")}
    if not {"policy_version", "policy_json"}.issubset(columns):
        raise RuntimeError("migration_schema_mismatch")
    if not ANSWER_POLICY_TABLES.issubset(
        {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ):
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
    if statement == "REPLACE_DOCUMENT_REVIEW_STATUSES":
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_versions'"
        ).fetchone()
        if row is None or not row[0]:
            raise RuntimeError("migration_schema_mismatch")
        old = "'draft','awaiting_review','approved','rejected','publishing','published',\n            'publication_failed','superseded'"
        transitional = "'draft','awaiting_review','approved','rejected','pending_publication','publishing','published',\n            'publication_failed','superseded'"
        final = "'pending_publication','publishing','published',\n            'publication_failed','superseded'"
        sql = str(row[0])
        if final in sql and not any(value in sql for value in ("'draft'", "'awaiting_review'", "'approved'", "'rejected'")):
            return
        if old not in sql:
            raise RuntimeError("migration_schema_mismatch")
        conn.execute("PRAGMA writable_schema=ON")
        try:
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='content_versions'",
                (sql.replace(old, transitional),),
            )
        finally:
            conn.execute("PRAGMA writable_schema=RESET")
        schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        conn.execute(f"PRAGMA schema_version={schema_version + 1}")
        conn.execute(
            """UPDATE content_versions SET lifecycle_status='pending_publication'
               WHERE lifecycle_status IN ('draft','awaiting_review','approved','rejected')"""
        )
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_versions'"
        ).fetchone()
        conn.execute("PRAGMA writable_schema=ON")
        try:
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='content_versions'",
                (str(row[0]).replace(transitional, final),),
            )
        finally:
            conn.execute("PRAGMA writable_schema=RESET")
        conn.execute(f"PRAGMA schema_version={schema_version + 2}")
        return
    if statement == "RELAX_CATEGORY_NODE_LEVEL_CHECK":
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='category_nodes'"
        ).fetchone()
        if row is None or not row[0]:
            raise RuntimeError("migration_schema_mismatch")
        old = "level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4)"
        new = "level INTEGER NOT NULL CHECK (level >= 1)"
        if new in row[0] and "CHECK (level BETWEEN 1 AND 4)" not in row[0]:
            return
        if old not in row[0]:
            raise RuntimeError("migration_schema_mismatch")
        conn.execute("PRAGMA writable_schema=ON")
        try:
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='category_nodes'",
                (row[0].replace(old, new),),
            )
        finally:
            conn.execute("PRAGMA writable_schema=RESET")
        return
    if statement == "RELAX_CONTENT_VERSION_DOC_TYPE_LEGACY_OFFICE":
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_versions'"
        ).fetchone()
        if row is None or not row[0]:
            raise RuntimeError("migration_schema_mismatch")
        old = "'pdf','markdown','docx','xlsx','pptx','xmind','transcript'"
        new = "'pdf','markdown','doc','docx','xls','xlsx','ppt','pptx','xmind','transcript'"
        if new in row[0]:
            return
        if old not in row[0]:
            # Migration 31 may be applied directly to a database that has
            # not yet received the xmind relaxation from migration 29.
            old = "'pdf','markdown','docx','xlsx','pptx','transcript'"
            new = "'pdf','markdown','doc','docx','xls','xlsx','ppt','pptx','xmind','transcript'"
            if new in row[0]:
                return
            if old not in row[0]:
                raise RuntimeError("migration_schema_mismatch")
        conn.execute("PRAGMA writable_schema=ON")
        try:
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='content_versions'",
                (row[0].replace(old, new),),
            )
        finally:
            conn.execute("PRAGMA writable_schema=RESET")
        return
    if statement == "RELAX_CONTENT_VERSION_DOC_TYPE_XMIND":
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_versions'"
        ).fetchone()
        if row is None or not row[0]:
            raise RuntimeError("migration_schema_mismatch")
        old = "'pdf','markdown','docx','xlsx','pptx','transcript'"
        new = "'pdf','markdown','docx','xlsx','pptx','xmind','transcript'"
        if new in row[0] or "'pdf','markdown','doc','docx','xls','xlsx','ppt','pptx','xmind','transcript'" in row[0]:
            return
        if old not in row[0]:
            raise RuntimeError("migration_schema_mismatch")
        conn.execute("PRAGMA writable_schema=ON")
        try:
            conn.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='content_versions'",
                (row[0].replace(old, new),),
            )
        finally:
            conn.execute("PRAGMA writable_schema=RESET")
        return
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
                if any(version == 25 for version, _name in applied)
                else PRE_CATEGORY_FORCE_DELETE_SYSTEM_CONTENT_PERMISSION_GROUPS
                if any(version == 20 for version, _name in applied)
                else PRE_TRASH_LIFECYCLE_SYSTEM_CONTENT_PERMISSION_GROUPS
                if any(version == 18 for version, _name in applied)
                else PRE_RECLASSIFICATION_SYSTEM_CONTENT_PERMISSION_GROUPS
                if any(version == 13 for version, _name in applied)
                else CONTENT_PERMISSION_V2_SYSTEM_CONTENT_PERMISSION_GROUPS
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
    if any(version == 15 for version, _name in applied):
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            validate_answer_policy_schema(conn)
        finally:
            conn.close()
    if any(version == 17 for version, _name in applied) and not ASR_PROFILE_MANAGEMENT_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 18 for version, _name in applied) and not CONTENT_RECLASSIFICATION_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 19 for version, _name in applied) and not MEDIA_LIBRARY_VIDEO_ACTIONS_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 20 for version, _name in applied) and not CONTENT_TRASH_LIFECYCLE_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 28 for version, _name in applied) and not EXTERNAL_MEDIA_SOURCE_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 30 for version, _name in applied) and not CONTENT_BULK_OPERATION_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 34 for version, _name in applied):
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            validate_category_node_level_schema(conn)
        finally:
            conn.close()
    if any(version == 10 for version, _name in applied):
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            validate_content_version_metadata(
                conn,
                require_xmind=any(version == 29 for version, _name in applied),
            )
        finally:
            conn.close()
    if any(version == 14 for version, _name in applied) and not UPLOAD_TASK_TABLES.issubset(tables):
        raise RuntimeError("migration_schema_mismatch")
    if any(version == 12 for version, _name in applied):
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            validate_transcript_manual_revision_schema(conn)
        finally:
            conn.close()
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
        if 14 in applied_versions and not UPLOAD_TASK_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 15 in applied_versions:
            validate_answer_policy_schema(conn)
        if 17 in applied_versions and not ASR_PROFILE_MANAGEMENT_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 18 in applied_versions and not CONTENT_RECLASSIFICATION_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 19 in applied_versions and not MEDIA_LIBRARY_VIDEO_ACTIONS_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 20 in applied_versions and not CONTENT_TRASH_LIFECYCLE_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 30 in applied_versions and not CONTENT_BULK_OPERATION_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 34 in applied_versions:
            validate_category_node_level_schema(conn)
        if 25 in applied_versions and not CATEGORY_FORCE_DELETE_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 28 in applied_versions and not EXTERNAL_MEDIA_SOURCE_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        if 21 in applied_versions and not TRANSCRIPTION_SCHEME_TABLES.issubset(tables):
            raise RuntimeError("migration_schema_mismatch")
        validate_system_content_permission_groups(
            conn,
            SYSTEM_CONTENT_PERMISSION_GROUPS
            if 25 in applied_versions
            else PRE_CATEGORY_FORCE_DELETE_SYSTEM_CONTENT_PERMISSION_GROUPS
            if 20 in applied_versions
            else PRE_TRASH_LIFECYCLE_SYSTEM_CONTENT_PERMISSION_GROUPS
            if 18 in applied_versions
            else PRE_RECLASSIFICATION_SYSTEM_CONTENT_PERMISSION_GROUPS
            if 13 in applied_versions
            else CONTENT_PERMISSION_V2_SYSTEM_CONTENT_PERMISSION_GROUPS
            if 11 in applied_versions
            else LEGACY_SYSTEM_CONTENT_PERMISSION_GROUPS,
        )
        validate_content_version_metadata(conn)
        applied_after = {
            row[0] for row in conn.execute("SELECT version FROM app_schema_migrations")
        }
        if 12 in applied_after:
            validate_transcript_manual_revision_schema(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("migration_foreign_key_check_failed")
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("migration_integrity_check_failed")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
