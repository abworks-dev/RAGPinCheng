CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL UNIQUE,
    real_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    last_login_at INTEGER
);

CREATE TABLE index_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    category TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    stats_json TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);

CREATE TABLE media_assets (
    media_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    storage_rel_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    sha256 TEXT,
    transcript_source_path TEXT,
    transcript_origin TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    error TEXT
);
