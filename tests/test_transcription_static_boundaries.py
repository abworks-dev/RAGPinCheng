import ast
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "transcription"
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures" / "transcription"

ALLOWED = {
    "types": set(),
    "candidate": {"types"},
    "profile": {"types"},
    "provider_protocol": {"types", "candidate", "profile"},
    "canonical": {"types", "profile"},
    "terminology": {"types"},
    "service_profiles": {"types", "terminology"},
    "normalizer": {"types", "candidate", "provider_protocol", "profile", "canonical", "terminology"},
    "pipeline": {"types", "profile", "provider_protocol", "normalizer", "canonical"},
    "formatter": {"types", "canonical"},
    "policy": {"types", "profile"},
    "persistence": {"types", "profile", "provider_protocol", "canonical"},
    "workflow": {"types", "profile", "provider_protocol", "canonical", "persistence", "policy"},
    "runtime_ports": {"types"},
    "provider_registry": {"types", "provider_protocol", "runtime_ports"},
    "asr_service_contract": {"types", "candidate", "provider_protocol"},
    "remote_provider": {
        "types", "profile", "provider_protocol", "runtime_ports",
        "provider_registry", "asr_service_contract",
    },
    "profile_catalog": {"types", "profile", "asr_service_contract"},
    "__init__": {
        "types", "candidate", "profile", "provider_protocol", "canonical",
        "normalizer", "pipeline", "formatter", "policy", "persistence", "workflow",
        "runtime_ports", "provider_registry", "asr_service_contract",
        "remote_provider", "profile_catalog",
    },
}
FORBIDDEN_IMPORT_ROOTS = {
    "funasr", "faster_whisper", "qwen_asr", "whisper", "torch", "av", "ffmpeg",
    "qdrant_client", "requests", "httpx", "aiohttp", "socket", "urllib",
    "subprocess", "sqlite3", "sqlalchemy",
}
DYNAMIC_IMPORT_CALLS = {
    "__import__", "import_module", "spec_from_file_location", "SourceFileLoader",
}
PROTECTED_SHA256 = {
    "src/chunk.py": "f22d79fe976a6da4fc4c2ba430490ad2c24205b2399e733912390e480858c542",
}


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def scoped_python_files() -> tuple[Path, ...]:
    paths = [
        *CORE.rglob("*.py"),
        *TESTS.glob("test_transcription*.py"),
        TESTS / "test_transcript_manual_regression.py",
        TESTS / "transcription_fixture_helpers.py",
    ]
    return tuple(sorted(set(paths)))


def imported_modules(path: Path) -> tuple[str, ...]:
    result: list[str] = []
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            result.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                result.append(f"src.transcription.{node.module}")
            elif node.module:
                result.append(node.module)
    return tuple(result)


def call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def local_dependencies(path: Path) -> set[str]:
    return {
        module.rsplit(".", 1)[-1]
        for module in imported_modules(path)
        if module.startswith("src.transcription.")
    }


def assert_acyclic(graph: dict[str, set[str]]) -> None:
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(node: str) -> None:
        if node in permanent:
            return
        assert node not in temporary, f"transcription import cycle at {node}"
        temporary.add(node)
        for dependency in graph[node]:
            visit(dependency)
        temporary.remove(node)
        permanent.add(node)

    for node in graph:
        visit(node)


def test_frozen_module_dag_is_acyclic_and_has_no_reverse_dependencies():
    graph: dict[str, set[str]] = {}
    for path in CORE.glob("*.py"):
        own = path.stem
        local = local_dependencies(path)
        assert local <= ALLOWED[own], (own, local - ALLOWED[own])
        graph[own] = local
    assert_acyclic(graph)


def test_all_scoped_python_rejects_forbidden_and_dynamic_imports():
    for path in scoped_python_files():
        modules = imported_modules(path)
        roots = {module.split(".", 1)[0].lower() for module in modules}
        forbidden = FORBIDDEN_IMPORT_ROOTS
        if not path.is_relative_to(CORE):
            forbidden = forbidden - {"sqlite3", "sqlalchemy"}
        if path == CORE / "remote_provider.py":
            forbidden = forbidden - {"httpx"}
        if path.name == "test_transcription_remote_provider.py":
            forbidden = forbidden - {"httpx"}
        assert not roots & forbidden, (path, roots & forbidden)
        dynamic = {
            call_name(node)
            for node in ast.walk(tree(path))
            if isinstance(node, ast.Call) and call_name(node) in DYNAMIC_IMPORT_CALLS
        }
        assert not dynamic, (path, dynamic)
        if path.name != "test_transcript_manual_regression.py":
            assert not any(module == "src.chunk" or module.startswith("src.chunk.") for module in modules), path


def test_pipeline_is_only_provider_call_and_application_normalizer_owner():
    transcribe: list[str] = []
    normalize: list[str] = []
    for path in CORE.glob("*.py"):
        for node in ast.walk(tree(path)):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "transcribe":
                transcribe.append(path.name)
            if call_name(node) == "normalize_candidate":
                normalize.append(path.name)
    assert transcribe == ["pipeline.py"]
    assert normalize == ["pipeline.py"]

    test_provider_calls = []
    for path in scoped_python_files():
        if path.is_relative_to(CORE):
            continue
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "transcribe":
                test_provider_calls.append(path.name)
    assert not test_provider_calls


def test_core_has_no_provider_name_branch_and_canonical_has_one_constructor_owner():
    for path in CORE.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name not in {"profile.py", "types.py"}:
            assert not re.search(r"fake-(alpha|beta|gamma)", text)

    constructors: list[str] = []
    for path in CORE.glob("*.py"):
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.Call) and call_name(node) == "CanonicalTranscript":
                constructors.append(path.name)
    assert constructors == ["canonical.py"]


def test_protocol_candidate_and_profile_registry_ownership_are_frozen():
    provider = (CORE / "provider_protocol.py").read_text(encoding="utf-8")
    candidate = provider[provider.index("class ProviderCandidate"):provider.index("class ProviderFailure")]
    assert "warnings" not in candidate

    owners: dict[str, list[str]] = {"ProfileOperation": [], "ProfileRegistry": [], "resolve_profile": []}
    for path in CORE.glob("*.py"):
        for node in tree(path).body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in owners:
                owners[node.name].append(path.name)
    assert owners == {
        "ProfileOperation": ["profile.py"],
        "ProfileRegistry": ["profile.py"],
        "resolve_profile": ["profile.py"],
    }


def test_no_manual_provider_parser_copy_or_real_media_fixture():
    manual_provider_definitions = [
        path
        for path in scoped_python_files()
        for node in ast.walk(tree(path))
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == "ManualTranscriptProvider"
    ]
    assert not manual_provider_definitions
    helper = (TESTS / "transcription_fixture_helpers.py").read_text(encoding="utf-8")
    manual_test = (TESTS / "test_transcript_manual_regression.py").read_text(encoding="utf-8")
    assert "TRANSCRIPT_TURN_RE" not in helper
    assert "importorskip" not in manual_test
    assert "ASR_ENABLED" not in helper + manual_test
    assert all(path.suffix in {".json", ".md", ".sha256", ".sql"} for path in FIXTURES.rglob("*"))
    candidate_fixture = json.loads((FIXTURES / "candidate.json").read_text(encoding="utf-8"))
    assert "warnings" not in candidate_fixture


def test_protected_manual_paths_match_phase1_baseline():
    for relative_path, expected in PROTECTED_SHA256.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        content = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        assert hashlib.sha256(content).hexdigest() == expected
    assert not any((CORE / name).exists() for name in ["database.py", "worker.py", "qdrant.py", "api.py"])


def test_ci_runs_complete_phase1_suite_with_existing_dependencies():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "test-transcription-contracts:" in ci
    assert "pytest tests/test_transcription*.py tests/test_transcript_manual_regression.py" in ci
    assert 'python-version: "3.11"' in ci
    assert "funasr" not in ci.lower() and "faster-whisper" not in ci.lower()
