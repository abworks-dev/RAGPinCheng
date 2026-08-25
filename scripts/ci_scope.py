#!/usr/bin/env python3
"""Classify changed paths so required CI jobs can skip unrelated work safely."""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath


SCOPES = (
    "collaboration", "python", "frontend", "visual", "transcription",
    "providers", "asr", "gpu", "compose", "migration_config", "ci",
)


def classify(paths: list[str]) -> dict[str, bool]:
    normalized = [p.replace("\\", "/").removeprefix("./") for p in paths if p.strip()]
    if not normalized:
        return {scope: True for scope in SCOPES} | {"full": True}

    result = {scope: False for scope in SCOPES}
    all_known = True
    for raw in normalized:
        path = PurePosixPath(raw)
        name = path.name
        known = raw.startswith((".github/", ".claude/", "docs/", "frontend/", "api/", "src/", "scripts/", "services/", "gpu_service/", "asr_service/", "tests/", "docker/")) or name in {
            ".dockerignore", ".env.example", ".gitignore", "AGENTS.md", "CLAUDE.md", "README.md", "TODO.md",
            "package.json", "package-lock.json", "requirements.txt", "requirements-prod.txt", "requirements-ci.txt",
        }
        all_known = all_known and known
        if raw.startswith((".github/", ".claude/")) or name in {"AGENTS.md", "CLAUDE.md"}:
            result["collaboration"] = True
        if raw.startswith(("api/", "src/", "scripts/", "services/", "gpu_service/", "asr_service/", "tests/")) or name in {"requirements.txt", "requirements-prod.txt", "requirements-ci.txt"}:
            result["python"] = True
        if raw.startswith("frontend/") or name in {"package.json", "package-lock.json"}:
            result["frontend"] = True
        if raw.startswith("frontend/tests/visual/") or raw.startswith("frontend/tests/visual-baseline/") or name == "playwright.config.ts":
            result["visual"] = True
        if raw.startswith("tests/test_transcription") or raw.startswith("tests/test_transcript") or raw.startswith("services/asr_service/"):
            result["transcription"] = True
        if name == "test_providers.py" or "provider" in name:
            result["providers"] = True
        if "asr" in raw or "whisper" in raw or "qwen" in raw or "faster_whisper" in raw:
            result["asr"] = True
        if raw.startswith("services/gpu_service/") or "gpu" in raw:
            result["gpu"] = True
        if raw.startswith(("docker/", ".dockerignore")) or name.startswith("docker-compose"):
            result["compose"] = True
        if name == ".env.example" or "migration" in name:
            result["migration_config"] = True
        if raw.startswith(".github/workflows/") or raw.startswith("scripts/tests/") or raw == "scripts/ci_scope.py":
            result["ci"] = True

    result["full"] = result["ci"] or not all_known
    return result


def main() -> None:
    github_output = "--github-output" in sys.argv
    paths = [arg for arg in sys.argv[1:] if arg != "--github-output"]
    result = classify(paths)
    if github_output:
        for name, selected in result.items():
            print(f"{name}={str(selected).lower()}")
    else:
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
