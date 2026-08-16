from pathlib import Path


COMPOSE_FILE = Path("docker/docker-compose.yml")


def test_backend_env_file_uses_private_deployment_path_with_local_fallback():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "- ${COMPOSE_ENV_FILE:-../.env}" in compose
    assert "- ../.env" not in compose
