"""Static filesystem-boundary checks for the FunASR test harness."""
from __future__ import annotations

import re
from pathlib import Path


TESTS = Path(__file__).resolve().parent
FUNASR_TESTS = tuple(sorted(TESTS.glob("test_funasr_phase0_*.py")))


def test_funasr_tests_do_not_use_persistent_drive_output_roots():
    forbidden = re.compile(
        r"""(?ix)
        (?:["']E:\\)
        |
        (?:["']C:\\FunASR-Phase0)
        """
    )
    violations: list[str] = []
    for path in FUNASR_TESTS:
        if path == Path(__file__).resolve():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.search(line):
                violations.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not violations, "persistent drive paths in FunASR tests:\n" + "\n".join(violations)


def test_funasr_tests_do_not_use_unmanaged_mkdtemp():
    violations: list[str] = []
    for path in FUNASR_TESTS:
        if path == Path(__file__).resolve():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "tempfile.mkdtemp(" in line:
                violations.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not violations, "unmanaged mkdtemp in FunASR tests:\n" + "\n".join(violations)
