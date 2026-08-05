from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "asr_service"
REAL_ADAPTERS = {
    SERVICE / "engines" / "faster_whisper.py",
    SERVICE / "engines" / "funasr_sensevoice.py",
    SERVICE / "engines" / "qwen3_asr.py",
    SERVICE / "engines" / "whisperx.py",
}
FORBIDDEN_SERVICE_ROOTS = {
    "api",
    "qdrant_client",
    "sqlite3",
    "sqlalchemy",
    "gpu_service",
}
FORBIDDEN_SERVICE_MODULES = {
    "src.transcription.canonical",
    "src.transcription.normalizer",
    "src.transcription.formatter",
    "src.transcription.pipeline",
    "src.transcription.profile",
    "src.transcription.persistence",
    "src.transcription.workflow",
}


def imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result, tree


def test_service_core_has_no_application_storage_index_or_canonical_dependencies():
    for path in SERVICE.rglob("*.py"):
        if "tests" in path.parts:
            continue
        modules, _tree = imports(path)
        roots = {module.split(".", 1)[0] for module in modules}
        assert not roots & FORBIDDEN_SERVICE_ROOTS, (path, roots)
        assert not set(modules) & FORBIDDEN_SERVICE_MODULES, path


def test_real_engine_dynamic_imports_are_confined_to_exact_adapters():
    dynamic_files = []
    for path in [*SERVICE.rglob("*.py"), *ROOT.glob("tests/test_transcription*.py")]:
        _modules, tree = imports(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "import_module":
                    dynamic_files.append(path)
    assert set(dynamic_files) == REAL_ADAPTERS


def test_main_requirements_have_no_real_asr_or_gpu_packages():
    forbidden = ("funasr", "faster-whisper", "qwen-asr", "torch", "pyav")
    for name in ("requirements.txt", "requirements-prod.txt"):
        packages = [
            line.strip().lower()
            for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not any(any(item in line for item in forbidden) for line in packages)


def test_faster_whisper_dependencies_remain_separate_from_runtime_sets():
    optional = (SERVICE / "requirements-faster-whisper.txt").read_text(
        encoding="utf-8"
    )
    assert "faster-whisper==1.2.1" in optional
    assert "ctranslate2==4.8.1" in optional
    for path in (
        ROOT / "requirements.txt",
        ROOT / "requirements-prod.txt",
        SERVICE / "requirements.txt",
        SERVICE / "requirements-windows.txt",
    ):
        packages = path.read_text(encoding="utf-8").lower()
        assert "faster-whisper" not in packages
        assert "ctranslate2" not in packages


def test_qwen3_asr_dependencies_remain_separate_from_runtime_sets():
    optional = (SERVICE / "requirements-qwen3-asr.txt").read_text(
        encoding="utf-8"
    )
    assert "qwen-asr==0.0.6" in optional
    for path in (
        ROOT / "requirements.txt",
        ROOT / "requirements-prod.txt",
        SERVICE / "requirements.txt",
        SERVICE / "requirements-windows.txt",
    ):
        assert "qwen-asr" not in path.read_text(encoding="utf-8").lower()


def test_phase3_contract_tests_have_no_skip_or_xfail():
    paths = [*SERVICE.glob("tests/test_*.py"), *ROOT.glob("tests/test_transcription_*.py")]
    forbidden_calls = {"importorskip", "skip", "xfail"}
    for path in paths:
        _modules, tree = imports(path)
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not calls & forbidden_calls, (path, calls & forbidden_calls)


def test_ci_collects_phase3_without_conditional_success():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    section = ci.split("  test-asr-service-contract:", 1)[1].split(
        "  test-gpu-contract:", 1
    )[0]
    assert "asr_service/tests" in section
    assert "test_transcription_remote_provider.py" in section
    assert "pip install pytest fastapi httpx" in section
    assert "|| true" not in section
    assert "asr_service" in ci.split("Check Python syntax", 1)[1]
