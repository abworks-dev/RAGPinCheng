from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = [
    REPO / "scripts" / "Register-CodexWorktree.ps1",
    REPO / "scripts" / "Close-CodexWorktree.ps1",
    REPO / "scripts" / "Audit-CodexWorktrees.ps1",
]


def pwsh() -> str:
    executable = shutil.which("pwsh")
    if executable is None:
        raise unittest.SkipTest("PowerShell 7 is unavailable")
    return executable


class TestWorktreeLifecycle(unittest.TestCase):
    def test_scripts_parse(self):
        for script in SCRIPTS:
            command = (
                "$e=$null;$t=$null;"
                f"[void][Management.Automation.Language.Parser]::ParseFile('{script}',[ref]$t,[ref]$e);"
                "if($e.Count){exit 1}"
            )
            result = subprocess.run([pwsh(), "-NoProfile", "-Command", command])
            self.assertEqual(result.returncode, 0, script.name)

    def test_register_whatif_is_non_mutating(self):
        target = REPO.parent / ".worktrees" / REPO.name / "lifecycle-preview-test"
        result = subprocess.run(
            [pwsh(), "-NoProfile", "-File", str(SCRIPTS[0]),
             "-RepositoryPath", str(REPO), "-WorktreePath", str(target),
             "-Branch", "codex/lifecycle-preview-test", "-Intent", "New", "-WhatIf"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(payload["status"], "preview")
        self.assertFalse(target.exists())

    def test_register_rejects_path_outside_approved_root(self):
        result = subprocess.run(
            [pwsh(), "-NoProfile", "-File", str(SCRIPTS[0]),
             "-RepositoryPath", str(REPO), "-WorktreePath", str(REPO.parents[2] / "bad"),
             "-Branch", "codex/rejected", "-Intent", "New", "-WhatIf"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside approved", result.stderr)
