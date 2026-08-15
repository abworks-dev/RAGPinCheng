import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _imports_under(directory: str) -> set[str]:
    imports: set[str] = set()
    for path in (ROOT / directory).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    return imports


def test_src_has_no_service_directory_dependencies():
    imports = _imports_under("src")
    assert not any(
        name == service or name.startswith(f"{service}.")
        for service in ("api", "services", "gpu_service", "asr_service", "libreoffice")
        for name in imports
    )


def test_api_does_not_import_service_implementations():
    imports = _imports_under("api")
    assert not any(
        name == service or name.startswith(f"{service}.")
        for service in ("services", "gpu_service", "asr_service", "libreoffice")
        for name in imports
    )


def test_asr_service_only_uses_shared_src_transcription_contract():
    imports = _imports_under("services/asr_service")
    forbidden = {
        "api",
        "gpu_service",
        "libreoffice",
        "services.gpu_service",
        "services.libreoffice",
    }
    assert not any(
        name == root or name.startswith(f"{root}.")
        for root in forbidden
        for name in imports
    )
    src_imports = {name for name in imports if name == "src" or name.startswith("src.")}
    assert all(
        name == "src.transcription" or name.startswith("src.transcription.")
        for name in src_imports
    )


def test_gpu_and_libreoffice_are_standalone_services():
    for service in ("gpu_service", "libreoffice"):
        imports = _imports_under(f"services/{service}")
        assert not any(
            name == root or name.startswith(f"{root}.")
            for root in ("api", "src", "asr_service", "services.asr_service")
            for name in imports
        )


def test_services_only_resolve_from_the_canonical_namespace():
    for service in ("asr_service", "gpu_service"):
        try:
            legacy = importlib.util.find_spec(f"{service}.config")
        except ModuleNotFoundError:
            legacy = None
        canonical = importlib.util.find_spec(f"services.{service}.config")
        assert not list((ROOT / service).glob("*.py"))
        assert legacy is None
        assert canonical is not None
        assert Path(canonical.origin).resolve().is_relative_to(
            ROOT / "services" / service
        )
