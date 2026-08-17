from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_whisperx_runtime_preflight as runtime_preflight


def test_profile_admission_accepts_pinned_common_profile_fields():
    assert runtime_preflight._profile_admission() == "disabled"


def test_profile_admission_rejects_enabled_common_profile_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = runtime_preflight.PROFILE_CATALOG_PATH.read_text(encoding="utf-8")
    modified = source.replace(
        '"admission": ProfileAdmission.disabled,',
        '"admission": ProfileAdmission.enabled,',
        1,
    )
    assert modified != source
    catalog_path = tmp_path / "profile_catalog.py"
    catalog_path.write_text(modified, encoding="utf-8")
    monkeypatch.setattr(runtime_preflight, "PROFILE_CATALOG_PATH", catalog_path)

    with pytest.raises(ValueError, match="profile_admission_not_disabled"):
        runtime_preflight._profile_admission()
