from __future__ import annotations

import base64
import zipfile
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/repair-pptx-previews-production.yml").read_text(
    encoding="utf-8"
)
FIXTURE = ROOT / "tests/fixtures/office/synthetic-preview-check.pptx.b64"


def test_synthetic_pptx_fixture_is_a_valid_office_package() -> None:
    encoded = "".join(FIXTURE.read_text(encoding="ascii").split())
    payload = base64.b64decode(encoded, validate=True)

    with zipfile.ZipFile(BytesIO(payload)) as package:
        names = set(package.namelist())

    assert "[Content_Types].xml" in names
    assert "ppt/presentation.xml" in names
    assert "ppt/slides/slide1.xml" in names


def test_production_workflow_has_preview_apply_and_manifest_gates() -> None:
    for contract in (
        "workflow_dispatch:",
        "options: [preview, apply]",
        "options: [CANCEL, PREVIEW_PPTX, REPAIR_PPTX]",
        "runs-on: [self-hosted, linux, ubuntu, production, app]",
        "environment: production-asr",
        "group: production-app-manual-v1",
        "git config --get remote.origin.url",
        "actions/download-artifact@v4",
        "EXPECTED_MANIFEST_SHA256",
        "production PPTX preview state changed after the approved dry-run",
        "--apply --confirm REPAIR_PPTX_PREVIEWS --version-id",
        "LIBREOFFICE_CONVERSION status=valid_pdf",
        "actions/upload-artifact@v4",
    ):
        assert contract in WORKFLOW


def test_production_workflow_keeps_repair_scope_narrow() -> None:
    for forbidden in (
        "docker compose down",
        "docker rm",
        "git reset",
        "git clean",
        "qdrant",
        "parents.sqlite",
        "app.sqlite",
        "cat .env",
    ):
        assert forbidden not in WORKFLOW
