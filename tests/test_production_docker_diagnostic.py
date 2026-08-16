from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "diagnose-production-docker.yml").read_text(encoding="utf-8")


def test_docker_diagnostic_is_read_only_and_captures_failures():
    assert "args = @('version')" in WORKFLOW
    assert "args = @('info')" in WORKFLOW
    assert "args = @('system', 'df')" in WORKFLOW
    assert "destructive_operations_executed=$false" in WORKFLOW
    assert "stderr" in WORKFLOW
    assert "Start-Process" in WORKFLOW
    assert "RedirectStandardError" in WORKFLOW
    for forbidden in ("docker system prune", "docker image rm", "docker volume rm", "docker compose down"):
        assert forbidden not in WORKFLOW
