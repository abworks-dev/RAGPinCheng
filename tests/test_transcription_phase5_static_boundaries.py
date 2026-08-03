from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


def test_candidate_index_entry_never_calls_destructive_index_paths():
    node = _function_node(ROOT / "src" / "indexing_pipeline.py", "index_transcript_candidate")
    calls = {
        call.func.id if isinstance(call.func, ast.Name) else call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute))
    }
    assert not calls & {"_purge_existing", "index_single", "reset_index", "delete_source"}
    assert {"chunk_document", "store_parents", "index_children"} <= calls


def test_every_phase5_retrieval_prefetch_has_a_visibility_bearing_filter():
    node = _function_node(ROOT / "src" / "retrieve.py", "_recall_scored")
    prefetch_calls = [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "Prefetch"
    ]
    assert len(prefetch_calls) == 3
    for call in prefetch_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "filter" in keywords
        assert not (isinstance(keywords["filter"], ast.Constant) and keywords["filter"].value is None)


def test_phase5_api_dtos_do_not_expose_server_paths_tokens_or_free_config():
    source = (ROOT / "api" / "schemas.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "TranscriptVersionDTO",
        "TranscriptMarkdownPreviewDTO",
        "TranscriptPublicationJobDTO",
        "PublishTranscriptVersionResponse",
    }
    forbidden = {"path", "absolute_path", "artifact_path", "token", "provider_config", "execution_config", "canonical"}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in names:
            fields = {item.target.id for item in node.body if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
            assert not fields & forbidden, (node.name, fields & forbidden)


def test_phase5_tests_do_not_import_real_asr_media_or_gpu_packages():
    forbidden = {"funasr", "faster_whisper", "torch", "av", "ffmpeg"}
    test_paths = [
        ROOT / "tests" / name
        for name in (
            "test_transcription_phase5_application_e2e.py",
            "test_transcription_publication_index_adapter.py",
            "test_transcription_retrieval_visibility.py",
            "test_transcription_retrieval_integration.py",
            "test_transcription_phase5_api.py",
            "test_transcription_phase5_worker.py",
            "test_transcription_index_metadata.py",
            "test_transcription_phase5_static_boundaries.py",
        )
    ]
    source_paths = [
        ROOT / "api" / "transcription_publication.py",
        ROOT / "src" / "transcription" / "retrieval_visibility.py",
    ]
    for path in test_paths + source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert not roots & forbidden, (path, roots & forbidden)

