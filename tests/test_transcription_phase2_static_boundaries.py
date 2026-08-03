import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "transcription"
ADAPTERS = (
    ROOT / "api" / "db.py",
    ROOT / "api" / "db_backup.py",
    ROOT / "api" / "db_migrations.py",
    ROOT / "api" / "transcription_artifacts.py",
    ROOT / "api" / "transcription_store.py",
)
PROTECTED = {
    "api/routes_media.py": "bba0b53767961c90e06e948cfecb659e33a395db1538a9f4f2acd4800087eb14",
    "api/indexing.py": "9abe3f09f26b36344a76f63e51813de28956e86a145e49fc79a131bc5b1c8fa1",
    "src/chunk.py": "a8102823c17e781b9d082b1914404e2fd60369558191acf2083fa21681dd72fd",
    "src/index.py": "3be3ed33255a7c476405775c3787a3bc106e4c98de29b318177f43aa2934c0a1",
    "src/indexing_pipeline.py": "d920b05975ee0bf55d0c7f99d448c2e311714228a461fce8d3a8a8499c401c50",
    "src/retrieve.py": "7004843ef0b979f6c223904feccdd3b8cb14c6b3ff542ebe378752e7655e22d2",
}
FORBIDDEN = {
    "funasr", "faster_whisper", "whisper", "torch", "av", "ffmpeg",
    "qdrant_client", "requests", "httpx", "aiohttp", "socket", "urllib", "subprocess",
}


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(item.name.split(".", 1)[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module.split(".", 1)[0])
    return result


def normalized_hash(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_phase1_core_and_phase2_workflow_have_no_adapter_dependencies():
    for path in CORE.glob("*.py"):
        modules = imports(path)
        assert "api" not in modules, path
        assert "sqlite3" not in modules, path
        assert "qdrant_client" not in modules, path


def test_phase2_code_has_no_real_engine_network_qdrant_or_subprocess_imports():
    paths = [*CORE.glob("*.py"), *ADAPTERS]
    for path in paths:
        forbidden = FORBIDDEN - ({"httpx"} if path.name == "remote_provider.py" else set())
        assert not imports(path) & forbidden, path
        text = path.read_text(encoding="utf-8").lower()
        assert "manualtranscriptprovider" not in text
        assert "__import__(" not in text
        assert "import_module(" not in text


def test_publication_adapter_does_not_reuse_general_index_queue():
    store = (ROOT / "api" / "transcription_store.py").read_text(encoding="utf-8")
    workflow = (CORE / "workflow.py").read_text(encoding="utf-8")
    assert "api.indexing" not in store + workflow
    assert "from index_jobs" not in store.lower()
    assert "into index_jobs" not in store.lower()
    assert "transcript_publication_index_jobs" in store


def test_protected_manual_and_qdrant_paths_match_approved_baseline():
    for relative, expected in PROTECTED.items():
        assert normalized_hash(ROOT / relative) == expected, relative


def test_phase2_tests_use_temp_paths_not_application_database():
    paths = sorted((ROOT / "tests").glob("test_transcription_phase2*.py")) + [
        ROOT / "tests" / "test_transcription_db_migrations.py",
        ROOT / "tests" / "test_transcription_artifacts.py",
        ROOT / "tests" / "test_transcription_store.py",
        ROOT / "tests" / "test_transcription_workflow_persistence.py",
        ROOT / "tests" / "test_transcription_publication_transaction.py",
        ROOT / "tests" / "test_transcription_recovery.py",
    ]
    disk_tests = {
        "test_transcription_db_migrations.py",
        "test_transcription_artifacts.py",
        "test_transcription_store.py",
        "test_transcription_workflow_persistence.py",
        "test_transcription_publication_transaction.py",
        "test_transcription_recovery.py",
        "test_transcription_phase2_manual_regression.py",
    }
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.name != "test_transcription_phase2_static_boundaries.py":
            assert "APP_DB_PATH" not in text, path
            assert "data/app.sqlite" not in text.replace("\\", "/"), path
        if path.name in disk_tests:
            assert "tmp_path" in text, path


def test_existing_ci_glob_collects_phase2_tests_without_new_dependencies():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest tests/test_transcription*.py tests/test_transcript_manual_regression.py" in ci
    requirements = (
        (ROOT / "requirements.txt").read_text(encoding="utf-8")
        + (ROOT / "requirements-prod.txt").read_text(encoding="utf-8")
    ).lower()
    assert "funasr" not in requirements and "faster-whisper" not in requirements
