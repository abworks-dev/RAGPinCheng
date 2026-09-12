"""Publish a generated TTS sample into the shared ASR qualification corpus manifest.

The shared corpus lives on the production ASR node (never in this repository) and
is immutable per annotation version. This tool performs one reviewed, mechanical
edit: it replaces or appends a single sample entry built from the WAV produced by
``scripts/New-AsrQualificationSample.ps1`` and its timing/reference JSON, then
writes a new manifest with a bumped annotation version.

It refuses anything the corpus contract would reject: more than 60s of audio, an
unknown scenario, missing reference segments, duplicate sample ids, or a target
that would drop the fixed eight-sample count.

Usage:

    python scripts/build_asr_qualification_corpus_manifest.py \
        --source-manifest <corpus>/manifest.json \
        --target-manifest <build>/manifest.json \
        --sample-root <build> \
        --wav <build>/natural-narration-zh.wav \
        --timing <build>/natural-narration-zh.timing.json \
        --sample-id natural-narration-zh \
        --scenario standard-codes \
        --replace-sample-id standard-codes \
        --expected-terms "建筑信息模型,构件碰撞,净高分析,钢结构,焊缝,螺栓,梯梁,梯柱" \
        --expected-codes "GB 50011-2010,GB 50016-2014"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

MAX_DURATION_MS = 60_000
EXPECTED_SAMPLE_COUNT = 8
SCENARIOS = {
    "clear-zh",
    "bim-terms",
    "standard-codes",
    "noisy-bim-zh",
    "mixed-zh-en",
    "negative-control",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_duration_ms(path: Path) -> int:
    import wave

    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getnchannels() != 1 or handle.getframerate() != 16000:
            raise SystemExit(f"{path.name}: expected 16 kHz mono 16-bit PCM")
        return int(round(handle.getnframes() * 1000 / handle.getframerate()))


def build_entry(
    *,
    sample_root: Path,
    wav: Path,
    timing: Path,
    sample_id: str,
    scenario: str,
    expected_terms: list[str],
    expected_codes: list[str],
) -> dict[str, object]:
    if scenario not in SCENARIOS:
        raise SystemExit(f"unknown scenario: {scenario}")
    recorded = json.loads(timing.read_text(encoding="utf-8"))
    sentinel = recorded.get("schema_version")
    if sentinel != "asr-qualification-tts-sample/1":
        raise SystemExit(f"unexpected timing schema: {sentinel}")
    duration_ms = _wav_duration_ms(wav)
    if duration_ms <= 0 or duration_ms > MAX_DURATION_MS:
        raise SystemExit(f"sample duration {duration_ms} ms is outside the corpus contract")
    if int(recorded.get("duration_ms", 0)) != duration_ms:
        raise SystemExit("timing record duration does not match the WAV")
    reference_segments = []
    for sentence in recorded["sentences"]:
        start_ms = int(sentence["start_ms"])
        if start_ms < 0 or start_ms >= duration_ms:
            raise SystemExit("reference segment timestamp outside the sample")
        reference_segments.append(
            {"start_ms": start_ms, "text": str(sentence["reference_text"]).strip()}
        )
    if not reference_segments:
        raise SystemExit("positive samples require reference segments")
    return {
        "id": sample_id,
        "path": wav.relative_to(sample_root).as_posix(),
        "size_bytes": wav.stat().st_size,
        "sha256": _sha256(wav),
        "duration_ms": duration_ms,
        "scenario": scenario,
        "reference_text": str(recorded["reference_text"]).strip(),
        "reference_segments": reference_segments,
        "expected_terms": expected_terms,
        "expected_codes": expected_codes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--sample-root", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--replace-sample-id", default="")
    parser.add_argument("--expected-terms", default="")
    parser.add_argument("--expected-codes", default="")
    parser.add_argument("--annotation-version", default="")
    parser.add_argument("--copy-wav", action="store_true", help="copy the WAV next to the target manifest")
    args = parser.parse_args()

    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    samples = list(source["samples"])
    entry = build_entry(
        sample_root=args.sample_root,
        wav=args.wav,
        timing=args.timing,
        sample_id=args.sample_id,
        scenario=args.scenario,
        expected_terms=[item for item in args.expected_terms.split(",") if item],
        expected_codes=[item for item in args.expected_codes.split(",") if item],
    )
    if args.copy_wav:
        destination = args.sample_root / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() != args.wav.resolve():
            shutil.copyfile(args.wav, destination)

    replaced = False
    updated: list[dict[str, object]] = []
    for item in samples:
        if args.replace_sample_id and item["id"] == args.replace_sample_id:
            updated.append(entry)
            replaced = True
            continue
        if item["id"] == entry["id"]:
            raise SystemExit(f"sample id already exists: {entry['id']}")
        updated.append(item)
    if args.replace_sample_id and not replaced:
        raise SystemExit(f"sample to replace was not found: {args.replace_sample_id}")
    if not args.replace_sample_id:
        updated.append(entry)
    if len(updated) != EXPECTED_SAMPLE_COUNT:
        raise SystemExit(
            f"corpus must keep exactly {EXPECTED_SAMPLE_COUNT} samples, got {len(updated)}"
        )

    ids = [item["id"] for item in updated]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate sample ids in the resulting corpus")
    # The corpus contract requires sample ids to be sorted and unique.
    updated.sort(key=lambda item: item["id"])

    manifest = {
        **source,
        "annotation_version": args.annotation_version or str(source["annotation_version"]),
        "samples": updated,
    }
    args.target_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.target_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": "written",
                "target": str(args.target_manifest),
                "annotation_version": manifest["annotation_version"],
                "sample_count": len(updated),
                "sample_id": entry["id"],
                "duration_ms": entry["duration_ms"],
                "sha256": entry["sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
