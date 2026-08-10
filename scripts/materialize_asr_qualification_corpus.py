"""Materialize the approved shared ASR qualification corpus once."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.asr_qualification_manifest import (
    SHARED_SCHEMA_VERSION,
    SOURCE_DECLARATION,
    SampleManifest,
    _assert_no_reparse_components,
    _sha256_file,
    allowed_schema_versions,
    load_manifest,
)

APPROVED_SAMPLE_SET_ID = "self-made-faster-whisper-r3"


def _source_snapshot(manifest: SampleManifest) -> tuple[tuple[str, int, int, str], ...]:
    paths = (manifest.path, *(sample.path for sample in manifest.samples))
    return tuple(
        (
            path.name,
            path.stat().st_size,
            path.stat().st_mtime_ns,
            _sha256_file(path),
        )
        for path in paths
    )


def _sample_identity(manifest: SampleManifest) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            sample.sample_id,
            sample.sha256,
            sample.size_bytes,
            sample.duration_ms,
            sample.scenario,
        )
        for sample in manifest.samples
    )


def _assert_same_corpus(source: SampleManifest, target: SampleManifest) -> None:
    if (
        target.sample_set_id != source.sample_set_id
        or target.annotation_version != source.annotation_version
        or _sample_identity(target) != _sample_identity(source)
    ):
        raise ValueError("target corpus identity does not match the approved source")


def _shared_payload(source: SampleManifest) -> dict[str, object]:
    return {
        "schema_version": SHARED_SCHEMA_VERSION,
        "sample_set_id": source.sample_set_id,
        "annotation_version": source.annotation_version,
        "source": SOURCE_DECLARATION,
        "samples": [
            {
                "id": sample.sample_id,
                "path": sample.path.relative_to(source.root).as_posix(),
                "size_bytes": sample.size_bytes,
                "sha256": sample.sha256,
                "duration_ms": sample.duration_ms,
                "scenario": sample.scenario,
                "reference_text": sample.reference_text,
                "reference_segments": [
                    {"start_ms": segment.start_ms, "text": segment.text}
                    for segment in sample.reference_segments
                ],
                "expected_terms": list(sample.expected_terms),
                "expected_codes": list(sample.expected_codes),
            }
            for sample in source.samples
        ],
    }


def materialize(
    source_root: Path,
    source_manifest: Path,
    target_root: Path,
    run_id: str,
) -> dict[str, object]:
    if not run_id.isdigit() or len(run_id) > 20:
        raise ValueError("run_id must contain 1 to 20 digits")
    source = load_manifest(
        source_manifest,
        root=source_root,
        allowed_schema_versions=allowed_schema_versions(
            "legacy", "faster-whisper"
        ),
        manifest_source="legacy",
    )
    if source.sample_set_id != APPROVED_SAMPLE_SET_ID:
        raise ValueError("source sample set is not the approved faster-whisper corpus")
    before = _source_snapshot(source)

    target = Path(os.path.abspath(target_root))
    if (
        target.name != APPROVED_SAMPLE_SET_ID
        or target.parent.name != "shared-corpus"
        or target.parent.parent.name != "qualification"
    ):
        raise ValueError("target root is outside the fixed shared corpus location")
    _assert_no_reparse_components(target.parent, "target parent")
    target_manifest = target / "manifest.json"
    if target.exists():
        existing = load_manifest(
            target_manifest,
            root=target,
            allowed_schema_versions=allowed_schema_versions(
                "neutral", "faster-whisper"
            ),
            manifest_source="neutral",
        )
        _assert_same_corpus(source, existing)
        if _source_snapshot(source) != before:
            raise ValueError("source corpus changed during validation")
        return {"status": "existing", **existing.identity()}

    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_components(target.parent, "target parent")
    staging = target.parent / f".{target.name}.staging-{run_id}"
    if staging.exists():
        raise ValueError("materialization staging directory already exists")
    staging.mkdir()

    for sample in source.samples:
        relative = sample.path.relative_to(source.root)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample.path, destination)
        if (
            destination.stat().st_size != sample.size_bytes
            or _sha256_file(destination) != sample.sha256
        ):
            raise ValueError("copied sample identity mismatch")

    staging_manifest = staging / "manifest.json"
    staging_manifest.write_text(
        json.dumps(_shared_payload(source), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    candidate = load_manifest(
        staging_manifest,
        root=staging,
        allowed_schema_versions=allowed_schema_versions(
            "neutral", "faster-whisper"
        ),
        manifest_source="neutral",
    )
    _assert_same_corpus(source, candidate)
    if _source_snapshot(source) != before:
        raise ValueError("source corpus changed during materialization")

    os.replace(staging, target)
    published = load_manifest(
        target_manifest,
        root=target,
        allowed_schema_versions=allowed_schema_versions(
            "neutral", "faster-whisper"
        ),
        manifest_source="neutral",
    )
    _assert_same_corpus(source, published)
    return {"status": "materialized", **published.identity()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        result = materialize(
            args.source_root,
            args.source_manifest,
            args.target_root,
            args.run_id,
        )
    except Exception:
        print(json.dumps({"status": "failed", "failure_code": "materialization_failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
