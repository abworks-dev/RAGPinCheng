"""Version-bound local attachment metadata/OCR inverted index (not a RAG index)."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata

from .content_attachment_types import ATTACHMENT_TYPES

FIELDS = ('filename', 'title', 'aliases', 'tags', 'project', 'description', 'category_path', 'ocr')
TYPES_SQL = ','.join("'" + kind + "'" for kind in sorted(ATTACHMENT_TYPES))
# Active ancestry is mandatory; chat inclusion is a per-category choice.
VISIBLE_CTE = f"""WITH RECURSIVE paths AS (
 SELECT id,category_key,display_name AS category_path,chat_search_enabled FROM category_nodes
 WHERE parent_id IS NULL AND is_active=1 AND deleted_at IS NULL
 UNION ALL
 SELECT c.id,c.category_key,p.category_path || ' / ' || c.display_name,c.chat_search_enabled
 FROM category_nodes c JOIN paths p ON p.id=c.parent_id
 WHERE c.is_active=1 AND c.deleted_at IS NULL AND p.chat_search_enabled=1
), visible AS (
 SELECT v.*,i.category_id,COALESCE(v.title,i.title) AS display_title,
 p.category_key,p.category_path,o.size_bytes AS file_size,o.storage_rel_path
 FROM content_item_heads h JOIN content_versions v ON v.id=h.current_version_id AND v.item_id=h.item_id
 JOIN content_items i ON i.id=h.item_id JOIN paths p ON p.id=i.category_id
 JOIN content_objects o ON o.sha256=v.object_sha256
 WHERE i.archived_at IS NULL AND p.chat_search_enabled=1 AND v.lifecycle_status='published'
 AND v.doc_type IN ({TYPES_SQL})
 AND EXISTS(SELECT 1 FROM content_publications cp WHERE cp.id=h.publication_id AND cp.version_id=v.id AND cp.status='published')
) """


def normalize_query(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _grams(text: str) -> set[str]:
    # Index 1/2/3-grams so Chinese short words and numeric filenames need no tokenizer.
    return {text[i:i+n] for n in (1, 2, 3) for i in range(len(text)-n+1) if not text[i:i+n].isspace()}


def index_attachment_version(conn: sqlite3.Connection, version_id: str) -> bool:
    row = conn.execute(f'''SELECT v.*,COALESCE(v.title,i.title) AS display_title FROM content_versions v
        JOIN content_items i ON i.id=v.item_id WHERE v.id=? AND v.doc_type IN ({TYPES_SQL})''', (version_id,)).fetchone()
    if row is None:
        return False
    meta = conn.execute('SELECT * FROM attachment_search_metadata WHERE version_id=?', (version_id,)).fetchone()
    ocr = conn.execute('''SELECT j.id,j.text FROM image_ocr_heads h JOIN image_ocr_jobs j ON j.id=h.job_id
        WHERE h.version_id=? AND h.source_sha256=? AND j.status='succeeded' ''', (version_id, row['object_sha256'])).fetchone()
    values = dict(filename=row['original_filename'], title=row['display_title'], aliases=' '.join(json.loads(meta['aliases_json'])) if meta else '',
                  tags=' '.join(json.loads(meta['tags_json'])) if meta else '', project=meta['project'] if meta else '',
                  description=meta['description'] if meta else '', ocr=ocr['text'] if ocr else '')
    values = {key: normalize_query(value) for key, value in values.items()}
    conn.execute('DELETE FROM attachment_search_terms WHERE version_id=?', (version_id,))
    conn.executemany('INSERT INTO attachment_search_terms(term,version_id,field) VALUES (?,?,?)',
                     ((term, version_id, field) for field, text in values.items() for term in _grams(text)))
    conn.execute('''INSERT INTO attachment_search_documents(version_id,source_sha256,metadata_revision,title,filename,aliases,tags,project,description,ocr,ocr_job_id,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET
        source_sha256=excluded.source_sha256,metadata_revision=excluded.metadata_revision,title=excluded.title,
        filename=excluded.filename,aliases=excluded.aliases,tags=excluded.tags,project=excluded.project,
        description=excluded.description,ocr=excluded.ocr,ocr_job_id=excluded.ocr_job_id,updated_at=excluded.updated_at''',
        (version_id, row['object_sha256'], meta['revision'] if meta else 0, values['title'], values['filename'], values['aliases'],
         values['tags'], values['project'], values['description'], values['ocr'], ocr['id'] if ocr else None, int(time.time())))
    return True


def reconcile_attachment_index(conn: sqlite3.Connection, *, limit: int = 100) -> int:
    """Bounded metadata-only reconciliation; never enqueues historical images for OCR."""
    rows = conn.execute(f'''SELECT v.id FROM content_item_heads h JOIN content_versions v ON v.id=h.current_version_id
        LEFT JOIN attachment_search_documents d ON d.version_id=v.id
        LEFT JOIN attachment_search_metadata m ON m.version_id=v.id
        WHERE v.doc_type IN ({TYPES_SQL}) AND (d.version_id IS NULL OR d.source_sha256<>v.object_sha256
        OR d.metadata_revision<>COALESCE(m.revision,0)) LIMIT ?''',
        (max(1, min(limit, 500)),)).fetchall()
    for row in rows:
        index_attachment_version(conn, row['id'])
    return len(rows)


def _scope(categories: list[str] | None) -> tuple[str, list[str]]:
    if categories is None:
        return '', []
    keys = list(dict.fromkeys(str(key) for key in categories))[:200]
    if not keys:
        return ' AND 0', []
    return ' AND v.category_key IN (' + ','.join('?' for _ in keys) + ')', keys


def _card(conn: sqlite3.Connection, row: sqlite3.Row, *, matched_fields=None, snippet='', user=None) -> dict:
    from .content_attachments import preview_descriptor
    from .content_permissions import has_content_permission
    preview = preview_descriptor(conn, row)
    return dict(item_id=row['item_id'], version_id=row['id'], title=row['display_title'], original_filename=row['original_filename'],
                doc_type=row['doc_type'], file_size=row['file_size'], category_key=row['category_key'], category_path=row['category_path'],
                version_number=row['version_number'], matched_fields=matched_fields or [], snippet=snippet[:240],
                preview_kind=preview['preview_kind'], can_preview=preview['preview_status']=='ready',
                can_download=bool(user and has_content_permission(conn, user, 'item.download')))


def search_attachment_files(conn: sqlite3.Connection, query: str, *, categories: list[str] | None, limit: int = 5) -> list[dict]:
    from src import config
    if not getattr(config, 'ATTACHMENT_SEARCH_ENABLED', True):
        return []
    query = normalize_query(query)
    if not query or len(query) > 200 or categories == []:
        return []
    limit = max(1, min(int(limit), 20))
    scope, params = _scope(categories)
    # Each token can match within a field; grams narrow before inspecting OCR text.
    meaningful = re.sub(r'^(?:(?:请|帮我|麻烦|给我|想要|我要|查找|搜索|找一下|找|一下)\s*)+', '', query)
    meaningful = re.sub(r'(?:的)?(?:文件|附件)$', '', meaningful).strip()
    meaningful = re.sub(r'的(?=[a-z0-9])', ' ', meaningful)
    words = meaningful.split()[:12]
    if not words:
        return []
    query = ' '.join(words)
    grams = sorted(set(g for word in words for g in (_grams(word) if len(word)<3 else {word[i:i+3] for i in range(len(word)-2)})))
    if not grams:
        return []
    placeholders = ','.join('?' for _ in grams)
    conn.create_function('attachment_normalize', 1, normalize_query, deterministic=True)
    tests = ' AND '.join('(' + ' OR '.join(
        'instr(attachment_normalize(v.category_path),?)>0' if field == 'category_path'
        else 'instr(COALESCE(d.filename,attachment_normalize(v.original_filename)),?)>0' if field == 'filename'
        else 'instr(COALESCE(d.title,attachment_normalize(v.display_title)),?)>0' if field == 'title'
        else f'instr(d.{field},?)>0' for field in FIELDS) + ')' for _ in words)
    test_params = [word for word in words for _ in FIELDS]
    # Paths are live, not version metadata: renames/moves must not require reindexing.
    # Match category nodes before versions; no arbitrary candidate cap can hide later
    # mixed-term matches. Only matching directories supplement the term-index branch.
    directory_tests = ' OR '.join('instr(attachment_normalize(category_path),?)>0' for _ in words)
    # Legacy metadata fallback is filename/title only, no historical OCR scan or backfill.
    rows = conn.execute(VISIBLE_CTE + f'''SELECT v.*,d.filename AS indexed_filename,d.title AS indexed_title,
        d.aliases,d.tags,d.project,d.description,d.ocr FROM visible v
        LEFT JOIN attachment_search_documents d ON d.version_id=v.id AND d.source_sha256=v.object_sha256
          AND (d.ocr_job_id IS NULL OR EXISTS(SELECT 1 FROM image_ocr_heads oh JOIN image_ocr_jobs oj ON oj.id=oh.job_id
             WHERE oh.version_id=v.id AND oh.source_sha256=v.object_sha256 AND oh.job_id=d.ocr_job_id AND oj.status='succeeded'))
        WHERE 1=1 {scope} AND (
          ((v.id IN (SELECT version_id FROM attachment_search_terms WHERE term IN ({placeholders})
             GROUP BY version_id HAVING count(DISTINCT term)=?) OR v.category_id IN
             (SELECT id FROM paths WHERE {directory_tests})) AND {tests})
          OR (d.version_id IS NULL AND (instr(lower(v.original_filename),?)>0 OR instr(lower(v.display_title),?)>0)))
        ORDER BY CASE WHEN lower(v.original_filename)=? THEN 0 WHEN instr(lower(v.original_filename),?)>0 THEN 1
          WHEN instr(lower(v.display_title),?)>0 THEN 2 WHEN instr(d.aliases,?)>0 THEN 3
          WHEN instr(d.tags,?)>0 THEN 4 WHEN instr(d.project,?)>0 THEN 5 WHEN instr(d.description,?)>0 THEN 6
          WHEN instr(attachment_normalize(v.category_path),?)>0 THEN 7 ELSE 8 END,
          v.original_filename,v.id LIMIT ?''',
        (*params, *grams, len(grams), *words, *test_params, *([query] * 10), limit)).fetchall()
    result = []
    for row in rows:
        texts = {field: (row['original_filename'] if field=='filename' else row['display_title'] if field=='title' else row[field] or '') for field in FIELDS}
        matched = [field for field in FIELDS if any(word in normalize_query(texts[field]) for word in words)]
        field = next((f for f in ('description','ocr','tags','aliases','project','category_path') if f in matched), 'filename')
        text = texts[field]
        offset = max(0, normalize_query(text).find(words[0])-40)
        result.append(_card(conn, row, matched_fields=matched, snippet=text[offset:offset+240]))
    return result


def visible_attachment_version(conn: sqlite3.Connection, version_id: str, *, categories=None):
    scope, params = _scope(categories)
    return conn.execute(VISIBLE_CTE + 'SELECT v.* FROM visible v WHERE v.id=?' + scope, (version_id, *params)).fetchone()


def revalidate_attachment_results(conn: sqlite3.Connection, results: list[dict], *, categories=None, user=None) -> list[dict]:
    from src import config
    if not getattr(config, 'ATTACHMENT_SEARCH_ENABLED', True):
        return []
    fresh = []
    for candidate in results[:20]:
        if not isinstance(candidate, dict):
            continue
        row = visible_attachment_version(conn, str(candidate.get('version_id', '')), categories=categories)
        if row is not None:
            # Do not replay persisted snippets after metadata/OCR edits or cancellations.
            fresh.append(_card(conn, row, user=user))
    return fresh


def update_search_metadata(conn: sqlite3.Connection, version_id: str, *, description='', tags=None, project='', aliases=None) -> dict:
    if len(description)>4000 or len(project)>300 or any(len(v)>200 for v in [*(tags or []), *(aliases or [])]) or len(tags or [])>50 or len(aliases or [])>50:
        raise ValueError('attachment_metadata_too_large')
    if not conn.execute(f'SELECT 1 FROM content_versions WHERE id=? AND doc_type IN ({TYPES_SQL})', (version_id,)).fetchone():
        raise ValueError('attachment_not_found')
    conn.execute('''INSERT INTO attachment_search_metadata(version_id,description,tags_json,project,aliases_json,updated_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET description=excluded.description,tags_json=excluded.tags_json,
        project=excluded.project,aliases_json=excluded.aliases_json,revision=revision+1,updated_at=excluded.updated_at''',
        (version_id,description,json.dumps(tags or [],ensure_ascii=False),project,json.dumps(aliases or [],ensure_ascii=False),int(time.time())))
    index_attachment_version(conn, version_id)
    return dict(conn.execute('SELECT * FROM attachment_search_metadata WHERE version_id=?', (version_id,)).fetchone())
