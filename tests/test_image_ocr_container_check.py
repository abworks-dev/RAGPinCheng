"""Harness safety contracts; actual Linux container inference runs separately in CI."""
from __future__ import annotations

import subprocess
import sys

import pytest

from scripts import check_image_ocr_container as checker


@pytest.mark.parametrize('fail_at', [None, 'run', 'exec'])
def test_synthetic_container_harness_isolated_and_always_cleaned(monkeypatch, fail_at):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['check', '--image', 'synthetic-ocr:ci'])

    def run(command, **kwargs):
        calls.append(command)
        if len(command) > 1 and command[1] == fail_at:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(checker.subprocess, 'run', run)
    if fail_at:
        with pytest.raises(subprocess.CalledProcessError):
            checker.main()
    else:
        checker.main()
    create = calls[0]
    assert create[:3] == ['docker', 'run', '--detach']
    assert create[create.index('--network') + 1] == 'none'
    assert '--read-only' in create
    assert '--publish' not in create and '-p' not in create
    assert 'ALL' == create[create.index('--cap-drop') + 1]
    mount = create[create.index('--mount') + 1]
    assert 'synthetic-chinese.png' in mount and mount.endswith('readonly')
    name = create[create.index('--name') + 1]
    assert calls[-1] == ['docker', 'rm', '--force', name]
