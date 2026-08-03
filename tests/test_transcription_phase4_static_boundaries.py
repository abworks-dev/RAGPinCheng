import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHASE4 = (
    ROOT / "api" / "transcription_media.py",
    ROOT / "api" / "transcription_runtime.py",
    ROOT / "api" / "transcription_service.py",
    ROOT / "api" / "transcription_worker.py",
    ROOT / "api" / "routes_transcription.py",
)


def imports(path):
    result = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_phase4_has_no_engine_sdk_qdrant_or_general_index_worker_dependency():
    forbidden = {"funasr", "torch", "av", "qdrant_client", "api.indexing"}
    for path in PHASE4:
        roots = {name.split(".", 1)[0] for name in imports(path)}
        assert not roots & forbidden, path


def test_only_media_adapter_may_invoke_subprocess_and_never_uses_shell():
    for path in PHASE4:
        text = path.read_text(encoding="utf-8")
        if path.name == "transcription_media.py":
            assert "subprocess.run(" in text
            assert "shell=False" in text
        else:
            assert "subprocess" not in text


def test_application_provider_result_flows_through_pipeline_only():
    service = (ROOT / "api" / "transcription_service.py").read_text(encoding="utf-8")
    assert "execute_transcription(" in service
    assert ".transcribe(" not in service
    assert "index_single" not in service
    assert "qdrant" not in service.lower()


def test_admin_request_never_accepts_provider_execution_controls():
    schemas = (ROOT / "api" / "schemas.py").read_text(encoding="utf-8")
    request = schemas[schemas.index("class RetryTranscriptionRequest"):]
    for forbidden in ("service_url", "token", "model_id", "model_revision", "hotwords", "decoder"):
        assert forbidden not in request
