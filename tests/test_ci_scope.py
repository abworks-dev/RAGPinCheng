from pathlib import Path

from scripts.ci_scope import classify
from scripts.ci_test_groups import GROUPS


def test_docs_are_not_full_scope():
    result = classify(["docs/README.md"])
    assert result["full"] is False
    assert not any(result[name] for name in result if name != "full")


def test_frontend_visual_changes_select_visual_scope():
    result = classify(["frontend/src/pages/admin/AdminPage.tsx"])
    assert result["frontend"] is True
    assert result["visual"] is False
    result = classify(["frontend/tests/visual/admin-golden.spec.ts"])
    assert result["frontend"] is True and result["visual"] is True


def test_ci_changes_are_conservative():
    result = classify([".github/workflows/ci.yml"])
    assert result["ci"] is True and result["full"] is True


def test_unknown_changes_fail_open():
    result = classify(["new-unknown-file.txt"])
    assert result["full"] is True


def test_python_ci_groups_are_disjoint_and_exist():
    claimed: dict[str, str] = {}
    for group, paths in GROUPS.items():
        for path in paths:
            assert Path(path).exists(), f"{group} references missing path {path}"
            assert path not in claimed, f"{path} belongs to {claimed.get(path)} and {group}"
            claimed[path] = group
