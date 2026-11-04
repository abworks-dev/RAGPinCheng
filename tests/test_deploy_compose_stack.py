"""Execute deploy-app with inert host commands; validate captured stacks without a daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def shell_path(path):
    value = Path(path).resolve().as_posix()
    if os.name == "nt":
        return "/" + value[0].lower() + value[2:]
    return value


def native_path(value):
    if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == "/":
        return value[1].upper() + ":" + value[2:]
    return value


@pytest.mark.parametrize("decoupled", [True, False])
def test_every_deploy_compose_call_uses_selected_stack(tmp_path, decoupled):
    bash = shutil.which("bash")
    docker = shutil.which("docker")
    if not bash or not docker:
        pytest.skip("Bash and Docker Compose CLI required (no daemon)")
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    base = repo / "docker/docker-compose.yml"
    override = tmp_path / "complete.json"
    overlay = repo / "docker/compose.source-decoupled.yml"
    env_file = tmp_path / "synthetic.env"
    env_file.write_text("SYNTHETIC_STACK_MARKER=selected\n", encoding="utf-8")
    base_stack = {"services": {
        "backend": {"image": "synthetic-backend", "volumes": ["/synthetic/legacy:/app/docs"]},
        "libreoffice": {"image": "synthetic-libreoffice"},
        "qdrant": {"image": "synthetic-qdrant"},
    }}
    base.write_text(json.dumps(base_stack), encoding="utf-8")
    selected = json.loads(json.dumps(base_stack)) if decoupled else {"services": {"backend": {}}}
    selected["services"]["backend"]["environment"] = {"MARKER": "${SYNTHETIC_STACK_MARKER:?required}"}
    if decoupled:
        selected["services"]["backend"].pop("volumes")
        selected["services"]["backend"]["tmpfs"] = ["/app/docs:ro,size=1048576,mode=0555"]
    override.write_text(json.dumps(selected), encoding="utf-8")
    overlay.write_text((ROOT / "docker/compose.source-decoupled.yml").read_text(encoding="utf-8"), encoding="utf-8")
    log = tmp_path / "argv.log"
    harness = tmp_path / "capture.sh"
    harness.write_text(r'''set -euo pipefail
# All external effects are inert; only temporary backup directories are created.
docker() {
    printf '%s\t' "$@" >> "$ARGV_LOG"
    printf '\n' >> "$ARGV_LOG"
    case "$*" in
        *"ps -q libreoffice"*) echo synthetic-container ;;
        inspect*) echo LIBREOFFICE_MAX_FILE_MB=100 ;;
        *"exec -T libreoffice"*) echo 100 ;;
    esac
    return 0
}
git() { echo "$DEPLOY_COMMIT_SHA"; }
curl() { echo '{"status":"ok","model_loaded":true,"api_version":"1","embedding_dimension":1024}'; }
python3() {
    case "$*" in
        *"get('api_version'"*) echo 1 ;;
        *"get('embedding_dimension'"*) echo 1024 ;;
    esac
    return 0
}
source "$DEPLOY_SCRIPT"
''', encoding="utf-8", newline="\n")
    environment = os.environ.copy()
    environment.update({
        "REPO_PATH": shell_path(repo), "BACKUP_DIR": shell_path(tmp_path / "backups"),
        "DATA_PATH": shell_path(tmp_path / "data"), "COMPOSE_OVERRIDE": shell_path(override),
        "COMPOSE_ENV_FILE": shell_path(env_file), "DEPLOY_COMMIT_SHA": "a" * 40,
        "SOURCE_DECOUPLING_COMPLETE": str(decoupled).lower(),
        "SOURCE_DECOUPLED_OVERRIDE_SANITIZED": "true", "SCHEMA_MIGRATION_ACTION": "APPLY_PENDING",
        "GPU_SERVICE_URL": "http://synthetic.invalid", "ARGV_LOG": shell_path(log),
        "DEPLOY_SCRIPT": shell_path(ROOT / "scripts/deploy-app.sh"),
        "CONTENT_HOST_PATH": "/synthetic/content", "MEDIA_HOST_PATH": "/synthetic/media",
    })
    result = subprocess.run([bash, shell_path(harness)], cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [line.rstrip("\t").split("\t") for line in log.read_text().splitlines()]
    compose_calls = [call[1:] for call in calls if call[0] == "compose"]
    expected_files = [shell_path(override), shell_path(overlay)] if decoupled else [shell_path(base), shell_path(override)]
    commands = []
    for call in compose_calls:
        # Every call must share project, ordered files, and explicit environment.
        prefix = ["-p", "ragpincheng-prod"]
        for path in expected_files:
            prefix += ["-f", path]
        prefix += ["--env-file", shell_path(env_file)]
        assert call[:len(prefix)] == prefix, call
        commands.append(call[len(prefix):])
    assert sum(command == ["config", "--quiet"] for command in commands) == 2
    for required in (["build", "backend"], ["stop", "backend"],
                     ["up", "-d", "--no-deps", "--force-recreate", "backend"],
                     ["up", "-d", "--no-deps", "--force-recreate", "libreoffice"],
                     ["ps", "-q", "libreoffice"]):
        assert required in commands
    assert any(command[:2] == ["run", "--rm"] for command in commands)
    assert sum(command[0] == "exec" for command in commands) == 3
    assert any(call[:3] == ["build", "-t", "pincheng-libreoffice:latest"] for call in calls)
    # Real CLI resolves exactly the captured stack; no build/up/daemon operations.
    prefix = compose_calls[0][:-2]
    native_prefix = [native_path(value) for value in prefix]
    config = subprocess.run([docker, "compose", *native_prefix, "config", "--no-env-resolution", "--format", "json"],
                            cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
    assert config.returncode == 0, config.stderr
    backend = json.loads(config.stdout)["services"]["backend"]
    assert backend["environment"]["MARKER"] == "selected"
    docs_mounts = [mount for mount in backend.get("volumes", []) if mount["target"] == "/app/docs"]
    assert bool(docs_mounts) is not decoupled
    if decoupled:
        assert any(item.startswith("/app/docs:ro,") for item in backend["tmpfs"])
    assert "libreoffice" in json.loads(config.stdout)["services"]
