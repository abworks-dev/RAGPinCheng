"""Tests for enumeration/count retrieval intent, the wider top-k path, and the
compact title-inventory context used to answer "list/count" questions."""
from __future__ import annotations

from src.generate import _build_enumeration_context
from src.intent import is_enumeration_intent
from src.retrieve import RetrievedParent


def _parent(index: int, *, doc_type: str = "transcript") -> RetrievedParent:
    return RetrievedParent(
        parent_id=f"p-{index}",
        doc_title=f"机电管综培训（{index}）",
        category="教学视频",
        section_path="transcript" if doc_type == "transcript" else f"第 {index} 节",
        source_path=f"/media/video-{index}.mp4",
        text=f"说话人 1 00:00:0{index}\n这是视频 {index} 的正文内容，描述管路调整步骤。\n",
        score=1.0,
        matched_children=["utterance"],
        doc_type=doc_type,
        start_time=f"00:00:0{index}" if doc_type == "transcript" else None,
        media_id=f"media-{index}",
    )


# ── intent detection ─────────────────────────────────────────────────────────


def test_enumeration_intent_matches_count_words():
    assert is_enumeration_intent("机电管综培训总共有几个培训视频")
    assert is_enumeration_intent("一共有多少个培训视频")
    assert is_enumeration_intent("分别列出所有章节")
    assert is_enumeration_intent("都有哪些教学视频")
    assert is_enumeration_intent("总共几个视频")


def test_enumeration_intent_matches_standalone_rewrite():
    # A vague follow-up that the rewrite expands to an enumeration form.
    assert is_enumeration_intent("继续说", standalone_query="机电管综培训总共有几个培训视频")


def test_enumeration_intent_does_not_match_plain_fact_question():
    assert not is_enumeration_intent("管综培训里的管道避让原则是什么")
    assert not is_enumeration_intent("如何做净高检查")
    assert not is_enumeration_intent("Revit 怎么创建族")


def test_enumeration_intent_ignores_short_fill():
    assert not is_enumeration_intent("")


# ── enumeration context builder ──────────────────────────────────────────────


def test_enumeration_context_lists_every_title_as_compact_source():
    parents = [_parent(i) for i in range(1, 9)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    assert len(used) == 8
    assert context.count("<source") == 8
    for i in range(1, 9):
        assert f'doc="机电管综培训（{i}）"' in context
        assert f'index="{i}"' in context


def test_enumeration_context_indexes_match_used_order():
    parents = [_parent(i) for i in range(1, 6)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    for i, p in enumerate(used, start=1):
        assert f'index="{i}"' in context
        assert f'doc="{p.doc_title}"' in context


def test_enumeration_context_every_source_has_real_snippet():
    parents = [_parent(i) for i in range(1, 6)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    assert "这是视频 1 的正文内容" in context
    assert "这是视频 2 的正文内容" in context
    # Every source carries real content — no empty placeholder.
    assert "（条目）" not in context
    assert "（内容为空）" not in context
    # All six 视频 snippets appear (compact for the tail entries).
    for i in range(1, 6):
        assert f"视频 {i} 的正文内容" in context


def test_enumeration_context_respects_budget_truncation():
    parents = [_parent(i) for i in range(1, 10)]
    context, used = _build_enumeration_context(parents, budget=400)
    # Small budget still admits multiple compact titles + a couple passages.
    assert 1 <= len(used) <= 9
    assert len(context) <= 400


def test_enumeration_context_non_transcript_uses_section():
    parents = [_parent(1, doc_type="pdf")]
    context, _ = _build_enumeration_context(parents, budget=10_000)
    assert 'type="transcript"' not in context
    assert 'section="第 1 节"' in context


def test_compact_snippet_strips_speaker_line_and_truncates():
    from src.generate import _compact_snippet

    text = "说话人 1 00:00:00\n这是视频的实际内容。"
    assert _compact_snippet(text) == "这是视频的实际内容。"
    long = "说话人 1 00:00:00\n" + "长内容" * 60
    snippet = _compact_snippet(long, limit=40)
    assert len(snippet) <= 40
    assert snippet.endswith("…")
    assert _compact_snippet("") == "（内容为空）"


# ── session _fresh_retrieve top_k routing ────────────────────────────────────


def test_fresh_retrieve_uses_enumeration_top_k(monkeypatch):
    from src import session as session_mod
    from src.config import ENUMERATION_TOP_K, FINAL_TOP_K

    calls: list[dict] = []

    def fake_retrieve(query, top_k=FINAL_TOP_K, categories=None, **kwargs):
        calls.append({"query": query, "top_k": top_k, "categories": categories, "doc_types": kwargs.get("doc_types")})
        return []

    monkeypatch.setattr(session_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(session_mod, "QUERY_DECOMPOSE_ENABLED", False)

    s = session_mod.ChatSession()
    s._fresh_retrieve("机电管综培训总共有几个培训视频", categories=None, enumeration=True)
    assert calls == [{"query": "机电管综培训总共有几个培训视频", "top_k": ENUMERATION_TOP_K, "categories": None, "doc_types": ["transcript"]}]


def test_fresh_retrieve_uses_default_top_k_for_plain_question(monkeypatch):
    from src import session as session_mod
    from src.config import FINAL_TOP_K

    calls: list[dict] = []

    def fake_retrieve(query, top_k=FINAL_TOP_K, categories=None):
        calls.append({"query": query, "top_k": top_k})
        return []

    monkeypatch.setattr(session_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(session_mod, "QUERY_DECOMPOSE_ENABLED", False)

    s = session_mod.ChatSession()
    s._fresh_retrieve("管道避让原则是什么", categories=None, enumeration=False)
    assert calls[0]["top_k"] == FINAL_TOP_K


# ── series-token extraction ─────────────────────────────────────────────────


def test_extract_series_token_produces_usable_key():
    from src.intent import extract_series_token

    assert extract_series_token("机电管综培训总共有几个培训视频") == "机电管综培训"
    assert extract_series_token("净高检查共有几个部分") == "净高检查"
    token = extract_series_token("暖通机房建模培训分别有哪些视频")
    assert "暖通机房建模培训" in token
    assert "哪些" not in token and "培训视频" not in token


# ── title recall + merge ────────────────────────────────────────────────────


def _tv_parent(index: int, media_id: str = None) -> RetrievedParent:
    return RetrievedParent(
        parent_id=f"p-{index}",
        doc_title=f"机电管综培训（{index}）",
        category="教学视频",
        section_path="transcript",
        source_path=f"/media/v{index}.mp4",
        text=f"说话人 1 00:00:0{index}\n视频 {index} 正文\n",
        score=1.0,
        matched_children=[],
        doc_type="transcript",
        start_time=f"00:00:0{index}",
        media_id=media_id or f"media-{index}",
        transcript_version_id=f"00000000-0000-4{index:1d}00-8000-00000000000{index}",
    )


def test_merge_enumeration_recall_dedups_and_puts_title_hits_first():
    from src.session import _merge_enumeration_recall

    title_hits = [_tv_parent(1), _tv_parent(2)]
    # semantic includes p-2 (dup) plus p-3
    semantic = [
        RetrievedParent(**{**_tv_parent(2).__dict__, "score": 0.9}),
        _tv_parent(3),
    ]
    merged = _merge_enumeration_recall(title_hits, semantic)
    pids = [p.parent_id for p in merged]
    assert pids == ["p-1", "p-2", "p-3"]
    assert len({p.parent_id for p in merged}) == 3


def test_retrieve_enumeration_titles_filters_unpublished(monkeypatch, tmp_path):
    import sqlite3

    from src import retrieve as retrieve_mod
    from src.transcription_retrieval_visibility import PublishedTranscriptSnapshot

    db = tmp_path / "parents.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE parents (parent_id TEXT PRIMARY KEY, doc_title TEXT, category TEXT,
           section_path TEXT, source_path TEXT, text TEXT, doc_type TEXT, start_time TEXT,
           company TEXT, media_id TEXT, transcript_version_id TEXT, publication_target_id TEXT,
           content_item_id TEXT, content_version_id TEXT, category_key TEXT)"""
    )
    # Two published (admitted) series videos + one unpublished.
    admitted = set()
    rows = []
    for i in (1, 2, 3):
        version_id = f"00000000-0000-4{i}00-8000-00000000000{i}"
        admitted.add(version_id)
        rows.append((
            f"p-{i}", f"机电管综培训（{i}）", "教学视频", "transcript", f"/m{i}", f"v{i} text", "transcript",
            "00:00:00", None, f"media-{i}", version_id, None, None, None, None
        ))
    # Make video 3 unpublished by not admitting its version id.
    admitted.remove(rows[2][10])
    for r in rows:
        conn.execute(
            "INSERT INTO parents (parent_id,doc_title,category,section_path,source_path,text,doc_type,start_time,company,media_id,transcript_version_id,publication_target_id,content_item_id,content_version_id,category_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            r,
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(retrieve_mod, "PARENTS_DB", db)
    visibility = type("V", (), {"snapshot": lambda self: PublishedTranscriptSnapshot(frozenset(admitted))})()
    result = retrieve_mod.retrieve_enumeration_titles("机电管综培训总共有几个", "机电管综培训", visibility=visibility)
    titles = [p.doc_title for p in result]
    assert "机电管综培训（1）" in titles
    assert "机电管综培训（2）" in titles
    assert "机电管综培训（3）" not in titles  # unpublished excluded


def test_finalize_keeps_all_candidates_for_enumeration():
    from src.generate import finalize_answer_sources_with_diagnostics

    parents = [_parent(i) for i in range(1, 6)]
    # The model only bracketed sources [1] and [3].
    text = "机电管综培训有《培训1》[1]和《培训3》[3]。"
    finalized = finalize_answer_sources_with_diagnostics(text, parents, keep_all=True)
    # keep_all surfaces every candidate, deduped by parent_id.
    assert [p.parent_id for p in finalized.sources] == [p.parent_id for p in parents]
    assert len(finalized.sources) == 5


def test_finalize_without_keep_all_trims_to_cited():
    from src.generate import finalize_answer_sources_with_diagnostics

    parents = [_parent(i) for i in range(1, 6)]
    text = "机电管综培训有《培训1》[1]和《培训3》[3]。"
    finalized = finalize_answer_sources_with_diagnostics(text, parents)
    # Default behavior: only cited sources are retained, renumbered.
    assert [p.doc_title for p in finalized.sources] == ["机电管综培训（1）", "机电管综培训（3）"]