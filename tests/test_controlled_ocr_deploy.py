"""Synthetic deployment state only; no production configuration or Docker access."""
import copy
import json
import os
from pathlib import Path
import stat
import sqlite3
import shutil
import subprocess

import pytest

from scripts.controlled_ocr_deploy import prepare, restore, snapshot, verify_no_history_enqueue

ROOT = Path(__file__).resolve().parents[1]


def base_stack(tmp_path):
    return {"services": {"backend": {
        "build": {"context": str(tmp_path.resolve()), "dockerfile": "docker/Dockerfile.backend"},
        "volumes": [{"type": "bind", "source": "/data/business/ragpincheng/content", "target": "/app/content"}],
        "env_file": [{"path": "/private/synthetic.env", "required": True}],
        "environment": {"ASR_ENABLED": "true", "TRANSCRIPTION_ADMITTED_PROFILE_IDS": "synthetic-profile", "GPU_SERVICE_URL": "http://synthetic-gpu"},
        "networks": {"default": None},
    }, "qdrant": {"image": "synthetic-qdrant"}, "libreoffice": {"image": "synthetic-libreoffice"}},
        "networks": {"default": {"external": True, "name": "ragpincheng-prod_default"}}}


def configure(tmp_path, action="ENABLE_LOCAL", previous=False):
    return prepare(base_stack(tmp_path), directory=tmp_path / "managed", repository=ROOT, action=action, previous_enabled=previous)


def test_enable_private_cpu_and_preserve_other_services(tmp_path, capsys):
    original = base_stack(tmp_path)
    before = copy.deepcopy(original)
    directory = tmp_path / "managed"
    result = prepare(original, directory=directory, repository=ROOT, action="ENABLE_LOCAL", previous_enabled=False)
    assert original == before
    assert result == {"enabled": True, "token_configured": True, "historical_backfill": False}
    config = json.loads((directory / "compose.json").read_text())
    backend = config["services"]["backend"]
    assert backend["env_file"] == before["services"]["backend"]["env_file"]
    for key in ("ASR_ENABLED", "TRANSCRIPTION_ADMITTED_PROFILE_IDS", "GPU_SERVICE_URL"):
        assert backend["environment"][key] == before["services"]["backend"]["environment"][key]
    for name in ("qdrant", "libreoffice"):
        assert config["services"][name] == before["services"][name]
    service = config["services"]["image-ocr"]
    assert service["networks"] == {"image_ocr_private": None}
    assert config["networks"]["image_ocr_private"] == {"internal": True}
    assert config["networks"]["default"] == before["networks"]["default"]
    assert service["cap_drop"] == ["ALL"] and service["read_only"] is True
    assert not any(key in service for key in ("ports", "volumes", "devices", "gpus", "deploy", "network_mode"))
    assert Path(service["build"]["context"]).is_absolute()
    assert backend["environment"]["IMAGE_OCR_TOKEN"] == service["environment"]["IMAGE_OCR_TOKEN"]
    assert len(service["environment"]["IMAGE_OCR_TOKEN"]) >= 48
    assert capsys.readouterr().out == ""
    if os.name != "nt":
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        for name in ("state.json", "token.env", "compose.json"):
            assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600


def test_future_release_preserves_enablement_and_token_without_flapping(tmp_path):
    configure(tmp_path)
    token = (tmp_path / "managed/token.env").read_bytes()
    assert configure(tmp_path, "PRESERVE_CURRENT", True)["enabled"] is True
    assert (tmp_path / "managed/token.env").read_bytes() == token
    assert configure(tmp_path, "DISABLE_LOCAL", True)["enabled"] is False
    assert configure(tmp_path, "PRESERVE_CURRENT", False)["enabled"] is False
    assert configure(tmp_path, "ENABLE_LOCAL", False)["enabled"] is True
    assert (tmp_path / "managed/token.env").read_bytes() == token


def test_first_preserve_does_not_generate_token(tmp_path):
    assert configure(tmp_path, "PRESERVE_CURRENT")["enabled"] is False
    assert not (tmp_path / "managed/token.env").exists()


def test_restore_removes_new_token_and_restores_existing_state(tmp_path):
    directory = tmp_path / "managed"
    backup = tmp_path / "initial-backup"
    snapshot(directory, backup)
    configure(tmp_path)
    restore(directory, backup)
    assert list(directory.iterdir()) == []
    configure(tmp_path)
    saved = {path.name: path.read_bytes() for path in directory.iterdir()}
    backup2 = tmp_path / "enabled-backup"
    snapshot(directory, backup2)
    configure(tmp_path, "DISABLE_LOCAL", True)
    restore(directory, backup2)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == saved


def test_reject_unmanaged_enabled_and_drift(tmp_path):
    with pytest.raises(ValueError, match="unmanaged_enabled"):
        configure(tmp_path, previous=True)
    configure(tmp_path)
    with pytest.raises(ValueError, match="differs_from_running"):
        configure(tmp_path, previous=False)


def test_public_overlay_not_accepted_as_private_stack(tmp_path):
    with pytest.raises(ValueError, match="complete_private"):
        prepare({"services": {"backend": {}}, "networks": base_stack(tmp_path)["networks"]}, directory=tmp_path / "managed", repository=ROOT, action="ENABLE_LOCAL", previous_enabled=False)
    base = base_stack(tmp_path)
    base["services"]["backend"]["build"]["context"] = "../relative"
    with pytest.raises(ValueError, match="absolute_build"):
        prepare(base, directory=tmp_path / "managed", repository=ROOT, action="ENABLE_LOCAL", previous_enabled=False)


def test_workflow_uses_existing_transaction_and_no_history_enqueue():
    text = (ROOT / ".github/workflows/deploy-production-app-manual.yml").read_text(encoding="utf-8")
    assert "image_ocr_action:" in text
    assert "--no-env-resolution --format json" in text
    assert "umask 077" in text
    assert 'snapshot --directory "${OCR_STATE_DIR}"' in text
    assert 'restore --directory "${OCR_STATE_DIR}"' in text
    assert 'docker tag "${OCR_ROLLBACK_TAG}" pincheng-image-ocr:local-v1' in text
    assert 'verify-history --database "${APP_DATABASE}"' in text
    assert '--baseline "${BACKUP_PATH}/app.sqlite"' in text
    assert "ocr-job-count.before" not in text
    assert 'CURRENT_COMPOSE_FILES=(-f "${OCR_STATE_DIR}/compose.json")' in text
    assert 'CURRENT_COMPOSE_FILES=("${COMPOSE_FILES[@]}")' in text
    assert '-f "${BACKUP_PATH}/rollback-compose.json" --env-file' in text
    assert "python -m scripts.verify_local_image_ocr" in text
    assert '"image_ocr_jobs": ("pending", "running")' in text
    assert "down -v" not in text
    assert "enqueue_image_ocr(" not in text
    assert '"${COMPOSE[@]}" up -d --no-deps --force-recreate image-ocr' in text


def history_databases(tmp_path, *, previous_jobs=True):
    baseline, live = tmp_path / 'before.sqlite', tmp_path / 'live.sqlite'
    with sqlite3.connect(baseline) as db:
        db.executescript('''
            CREATE TABLE content_versions(id TEXT PRIMARY KEY,item_id TEXT,doc_type TEXT,lifecycle_status TEXT);
            CREATE TABLE content_item_heads(item_id TEXT,current_version_id TEXT,publication_id TEXT);
            CREATE TABLE content_publications(id TEXT,version_id TEXT,status TEXT);
            INSERT INTO content_versions VALUES('old-image','old-item','png','published');
            INSERT INTO content_versions VALUES('draft-image','draft-item','jpg','draft');
            INSERT INTO content_versions VALUES('old-pdf','pdf-item','pdf','published');
            INSERT INTO content_item_heads VALUES('old-item','old-image','old-publication');
            INSERT INTO content_item_heads VALUES('pdf-item','old-pdf','pdf-publication');
            INSERT INTO content_publications VALUES('old-publication','old-image','published');
            INSERT INTO content_publications VALUES('pdf-publication','old-pdf','published');
        ''')
        if previous_jobs:
            db.executescript("CREATE TABLE image_ocr_jobs(id TEXT PRIMARY KEY,version_id TEXT,status TEXT); INSERT INTO image_ocr_jobs VALUES('existing-job','old-image','done');")
        with sqlite3.connect(live) as target:
            db.backup(target)
    if not previous_jobs:
        with sqlite3.connect(live) as db:
            db.execute('CREATE TABLE image_ocr_jobs(id TEXT PRIMARY KEY,version_id TEXT,status TEXT)')
    return baseline, live


@pytest.mark.parametrize('previous_jobs', [True, False])
def test_history_guard_allows_new_uploads_and_newly_published_drafts(tmp_path, previous_jobs, capsys):
    baseline, live = history_databases(tmp_path, previous_jobs=previous_jobs)
    with sqlite3.connect(live) as db:
        db.executescript('''
            INSERT INTO content_versions VALUES('new-image','new-item','png','published');
            INSERT INTO content_item_heads VALUES('new-item','new-image','new-publication');
            INSERT INTO content_publications VALUES('new-publication','new-image','published');
            UPDATE content_versions SET lifecycle_status='published' WHERE id='draft-image';
            INSERT INTO content_item_heads VALUES('draft-item','draft-image','draft-publication');
            INSERT INTO content_publications VALUES('draft-publication','draft-image','published');
            INSERT INTO image_ocr_jobs VALUES('new-upload-job','new-image','pending');
            INSERT INTO image_ocr_jobs VALUES('new-publication-job','draft-image','pending');
            INSERT INTO image_ocr_jobs VALUES('nonimage-job','old-pdf','done');
        ''')
    before = baseline.read_bytes(), live.read_bytes()
    verify_no_history_enqueue(live, baseline)
    assert (baseline.read_bytes(), live.read_bytes()) == before
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('previous_jobs', [True, False])
def test_history_guard_rejects_new_jobs_for_preexisting_published_image_head(tmp_path, previous_jobs, capsys):
    baseline, live = history_databases(tmp_path, previous_jobs=previous_jobs)
    verify_no_history_enqueue(live, baseline)
    with sqlite3.connect(live) as db:
        db.execute("INSERT INTO image_ocr_jobs VALUES('unexpected-historical-job','old-image','pending')")
    before = baseline.read_bytes(), live.read_bytes()
    with pytest.raises(ValueError, match='unexpected_image_ocr_history_enqueue'):
        verify_no_history_enqueue(live, baseline)
    assert (baseline.read_bytes(), live.read_bytes()) == before
    assert capsys.readouterr().out == ''


def test_complete_prior_stack_preserves_source_decoupled_mounts_without_base_merge(tmp_path):
    # This is also a Docker-Compose-free contract test: the persisted full stack
    # itself has no retired bind. CI/local Compose checks below do not use a daemon.
    base = base_stack(tmp_path)
    base['services']['backend']['tmpfs'] = ['/app/docs:ro,size=1048576,mode=0555']
    directory, backup = tmp_path / 'managed', tmp_path / 'backup'
    prepare(base, directory=directory, repository=ROOT, action='ENABLE_LOCAL', previous_enabled=False)
    snapshot(directory, backup)
    prior = json.loads((backup / 'compose.json').read_text())
    assert all(mount['target'] != '/app/docs' for mount in prior['services']['backend']['volumes'])
    assert prior['services']['backend']['tmpfs'] == ['/app/docs:ro,size=1048576,mode=0555']
    if shutil.which('docker'):
        command = ['docker', 'compose', '-p', 'synthetic-rollback-test', '-f', str(backup / 'compose.json'), 'config', '--no-env-resolution', '--format', 'json']
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        resolved = json.loads(completed.stdout)
        backend = resolved['services']['backend']
        assert all(mount['target'] != '/app/docs' for mount in backend['volumes'])
        assert any(item.startswith('/app/docs:ro,') for item in backend['tmpfs'])
        assert resolved['networks']['default']['external'] is True
