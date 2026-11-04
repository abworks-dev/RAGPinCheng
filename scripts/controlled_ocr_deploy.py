"""Private production OCR configuration transaction. Never prints configuration/secrets.

Input is an already resolved *complete private* Compose stack, never the public
OCR overlay. History verification reads only identifier/status metadata through
read-only SQLite connections; no documents, Docker runtime or external services.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import tempfile

FILES = ("state.json", "token.env", "compose.json")


def private_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def check_directory(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("ocr_state_directory_must_be_absolute_and_real")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    for name in FILES:
        item = path / name
        if item.is_symlink() or (item.exists() and not item.is_file()):
            raise ValueError("ocr_state_file_must_be_regular")
        if item.exists() and os.name != "nt" and stat.S_IMODE(item.stat().st_mode) & 0o077:
            raise ValueError("ocr_state_file_permissions_must_be_private")


def snapshot(directory: Path, backup: Path) -> None:
    check_directory(directory)
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup.chmod(0o700)
    for name in FILES:
        source = directory / name
        if source.exists():
            shutil.copyfile(source, backup / name)
            (backup / name).chmod(0o600)
    private_write(backup / "snapshot.json", json.dumps({"present": [name for name in FILES if (directory / name).exists()]}))


def restore(directory: Path, backup: Path) -> None:
    check_directory(directory)
    present = json.loads((backup / "snapshot.json").read_text(encoding="utf-8"))["present"]
    for name in FILES:
        if name in present:
            private_write(directory / name, (backup / name).read_text(encoding="utf-8"))
        else:
            (directory / name).unlink(missing_ok=True)


def prepare(base: dict, *, directory: Path, repository: Path, action: str, previous_enabled: bool) -> dict:
    check_directory(directory)
    if action not in {"PRESERVE_CURRENT", "ENABLE_LOCAL", "DISABLE_LOCAL"}:
        raise ValueError("unsupported_image_ocr_action")
    if not repository.is_absolute() or not (repository / "services/image_ocr/Dockerfile").is_file():
        raise ValueError("ocr_requires_absolute_repository_with_service_source")
    services = base.get("services", {})
    backend = services.get("backend", {})
    default_network = base.get("networks", {}).get("default", {})
    if default_network.get("name") != "ragpincheng-prod_default" or default_network.get("external") is not True:
        raise ValueError("ocr_requires_existing_private_production_network")
    # A resolved production stack must keep the private mounts/network/build context.
    mounts = {item.get("target"): item for item in backend.get("volumes", [])}
    content = mounts.get("/app/content", {})
    if content.get("source") != "/data/business/ragpincheng/content" or content.get("type") != "bind":
        raise ValueError("ocr_requires_complete_private_production_stack")
    build = backend.get("build", {})
    if not isinstance(build, dict) or not Path(build.get("context", "")).is_absolute():
        raise ValueError("ocr_requires_resolved_absolute_build_context")
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    if state is None and previous_enabled:
        raise ValueError("unmanaged_enabled_ocr_requires_explicit_reconciliation")
    if state is not None and bool(state["enabled"]) != previous_enabled:
        raise ValueError("managed_ocr_state_differs_from_running_backend")
    enabled = previous_enabled if action == "PRESERVE_CURRENT" else action == "ENABLE_LOCAL"
    token_path = directory / "token.env"
    token = ""
    if token_path.exists():
        raw = token_path.read_text(encoding="ascii").strip()
        if not raw.startswith("IMAGE_OCR_TOKEN="):
            raise ValueError("invalid_managed_ocr_token_file")
        token = raw.split("=", 1)[1]
        if len(token) < 32 or not all(ch.isalnum() or ch in "-_" for ch in token):
            raise ValueError("invalid_managed_ocr_token")
    if enabled and not token:
        if previous_enabled:
            raise ValueError("enabled_ocr_token_is_missing")
        token = secrets.token_urlsafe(48)
        private_write(token_path, "IMAGE_OCR_TOKEN=" + token + "\n")
    result = copy.deepcopy(base)
    result.pop("name", None)
    backend = result["services"]["backend"]
    environment = backend.setdefault("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("ocr_requires_resolved_environment_mapping")
    environment.update({"IMAGE_OCR_ENABLED": "true" if enabled else "false", "IMAGE_OCR_URL": "http://image-ocr:8091", "IMAGE_OCR_TOKEN": token})
    # Keep every pre-existing backend network, including the external production network.
    networks = backend.setdefault("networks", {"default": None})
    if not isinstance(networks, dict):
        raise ValueError("ocr_requires_resolved_network_mapping")
    networks["image_ocr_private"] = None
    result.setdefault("networks", {})["image_ocr_private"] = {"internal": True}
    result["services"]["image-ocr"] = {
        "build": {"context": str((repository / "services/image_ocr").resolve()), "dockerfile": "Dockerfile"},
        "image": "pincheng-image-ocr:local-v1", "restart": "unless-stopped",
        "environment": {"IMAGE_OCR_TOKEN": token, "IMAGE_OCR_MAX_BYTES": "33554432", "IMAGE_OCR_MAX_PIXELS": "80000000", "IMAGE_OCR_PROCESS_TIMEOUT_SECONDS": "25"},
        "networks": {"image_ocr_private": None}, "read_only": True,
        "tmpfs": ["/tmp:size=128m,mode=1777"], "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"], "mem_limit": "1536m", "cpus": 2, "pids_limit": 64,
        "healthcheck": {"test": ["CMD", "python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=2))['status']=='ok'"], "interval": "30s", "timeout": "5s", "retries": 3, "start_period": "30s"},
        "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
    }
    private_write(directory / "compose.json", json.dumps(result))
    private_write(state_path, json.dumps({"version": 1, "enabled": enabled}))
    return {"enabled": enabled, "token_configured": bool(token), "historical_backfill": False}


def verify_no_history_enqueue(database_path: Path, baseline_path: Path) -> None:
    """Reject new OCR jobs only for image heads already published at backup time.

    Newly uploaded drafts and newly published versions are not historical
    backfill. Keep identifier comparisons inside SQLite; never emit customer IDs.
    """
    with sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True) as database:
        database.execute("ATTACH DATABASE ? AS baseline", (baseline_path.resolve().as_uri() + "?mode=ro",))
        database.execute("PRAGMA query_only=ON")
        previous_jobs_exist = database.execute("SELECT 1 FROM baseline.sqlite_master WHERE type='table' AND name='image_ocr_jobs'").fetchone()
        exclude_existing = "AND NOT EXISTS (SELECT 1 FROM baseline.image_ocr_jobs old WHERE old.id=j.id)" if previous_jobs_exist else ""
        count = database.execute("""
            SELECT count(*) FROM main.image_ocr_jobs j
            JOIN baseline.content_versions v ON v.id=j.version_id
            JOIN baseline.content_item_heads h ON h.current_version_id=v.id AND h.item_id=v.item_id
            JOIN baseline.content_publications p ON p.id=h.publication_id AND p.version_id=v.id
            WHERE v.doc_type IN ('png','jpg','jpeg')
              AND v.lifecycle_status='published' AND p.status='published'
            """ + exclude_existing).fetchone()[0]
    if count:
        raise ValueError("unexpected_image_ocr_history_enqueue")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "prepare", "restore", "verify-history"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--action", default="PRESERVE_CURRENT")
    parser.add_argument("--previous-enabled", choices=("true", "false"), default="false")
    args = parser.parse_args()
    if args.operation == "verify-history":
        if args.database is None or args.baseline is None:
            parser.error("verify-history requires --database and --baseline")
        verify_no_history_enqueue(args.database, args.baseline)
        print("IMAGE_OCR_HISTORY status=verified historical_enqueue=false")
        return
    if args.directory is None:
        parser.error("configuration operations require --directory")
    if args.operation == "snapshot":
        snapshot(args.directory, args.backup)
    elif args.operation == "restore":
        restore(args.directory, args.backup)
    else:
        result = prepare(json.loads(args.base.read_text(encoding="utf-8")), directory=args.directory, repository=args.repository, action=args.action, previous_enabled=args.previous_enabled == "true")
        print("IMAGE_OCR_CONFIGURATION " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
