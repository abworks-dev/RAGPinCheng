"""Phase 0 ASR sandbox — annotation validator (entry point 08).

Per R2 spec §十四:
  - No editing of Python source; reads from --input draft JSONL,
    writes to --out validated JSONL.
  - Each sample must include: id, audio (relative path), audio_sha256,
    source_url or self_made note, license, internal_recording_consent_id
    (or empty for non-internal), scenario, reference_text, reference_segments,
    annotator, reviewer, annotation_version.
  - license_evidence is optional but produces an advisory when absent.
  - Validates:
      * file inside approved testdata_root
      * time non-negative
      * segments strictly increasing
      * no overlap
      * end <= audio duration
      * text non-empty
      * segment concat == reference_text (after CER-normalize)
      * id unique
      * scenario in the preregistered set
      * source and license non-empty
      * short samples: NO default 5s tolerance (must match exactly)
  - Audio + annotation NEVER written to Git (this script does not touch
    the repo; outputs to testdata_root, not into the repo).

NEVER calls any ASR / model. NEVER touches services.gpu_service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import wave
from pathlib import Path

ALLOWED_SCENARIOS = frozenset({
    "clear_single_speaker",
    "multi_speaker",
    "background_noise",
    "fast_speech",
    "background_music",
    "long_silence",
    "bim_terms",
    "noise_with_bim",
    "synthetic_long",
})

PUNCT_CATS = {"P", "Z"}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Hard limits on draft annotation input — defensive defaults for a local
# operator tool, not a security boundary.
MAX_INPUT_BYTES = 8 * 1024 * 1024   # 8 MiB
MAX_INPUT_LINES = 5_000
MAX_LINE_BYTES = 64 * 1024         # 64 KiB per line


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    return "".join(c for c in s if unicodedata.category(c)[0] not in PUNCT_CATS)


def _atomic_json_dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _validate_one(s: dict, audio_path: Path, audio_sha: str, testdata_root: Path,
                  tolerance_s: float) -> tuple[list[str], list[str]]:
    """Return (blocking_issues, advisory_issues).

    Advisory issues are recorded in the report but do NOT fail validation.
    """
    issues: list[str] = []
    advisory: list[str] = []
    sid = s.get("id", "?")
    for k in ("id", "audio", "audio_sha256", "license",
              "scenario", "reference_text", "reference_segments",
              "annotator", "reviewer", "annotation_version",
              "internal_recording_consent_id"):
        if k not in s:
            issues.append(f"{sid}: missing field '{k}'")
    # license_evidence is OPTIONAL advisory; absence is recorded in the
    # report but does NOT block validation.  Consumers may still insist on
    # it by checking the report's "advisory" field.
    if "license_evidence" not in s or not str(s.get("license_evidence", "")).strip():
        advisory.append(f"{sid}: missing license_evidence (URL/record id/note recommended)")
    if issues:
        return issues, advisory
    if not _SAFE_ID_RE.fullmatch(str(s["id"])):
        issues.append(f"{sid}: id is unsafe for checkpoint filenames")
    # audio path inside testdata_root
    try:
        audio_path.resolve().relative_to(testdata_root.resolve())
    except ValueError:
        issues.append(f"{sid}: audio {audio_path} is NOT inside testdata_root {testdata_root}")
    # audio exists
    if not audio_path.exists():
        issues.append(f"{sid}: audio file not found: {audio_path}")
    # sha256 match
    if not audio_path.exists():
        return issues, advisory
    h = hashlib.sha256()
    with audio_path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    actual_sha = h.hexdigest()
    if actual_sha != audio_sha:
        issues.append(f"{sid}: audio_sha256 mismatch: declared={audio_sha[:16]} actual={actual_sha[:16]}")
    # scenario
    if s["scenario"] not in ALLOWED_SCENARIOS:
        issues.append(f"{sid}: scenario {s['scenario']!r} not in {sorted(ALLOWED_SCENARIOS)}")
    # Exactly one auditable source form: public URL or a self-made note.
    source_url = str(s.get("source_url", "")).strip()
    self_made = str(s.get("self_made", "")).strip()
    if bool(source_url) == bool(self_made):
        issues.append(f"{sid}: provide exactly one of source_url or self_made")
    if not str(s["license"]).strip():
        issues.append(f"{sid}: license is empty")
    consent = str(s.get("internal_recording_consent_id", "")).strip()
    if source_url and consent:
        issues.append(f"{sid}: public source must not carry internal recording consent id")
    if self_made and not consent and s.get("is_internal_recording") is True:
        issues.append(f"{sid}: internal recording requires internal_recording_consent_id")
    # segments
    segs = s.get("reference_segments") or []
    if not isinstance(segs, list) or not segs:
        issues.append(f"{sid}: reference_segments empty")
        return issues, advisory
    last_end = 0
    for i, sg in enumerate(segs):
        for k in ("start_ms", "end_ms", "text"):
            if k not in sg:
                issues.append(f"{sid}: segment[{i}] missing {k}")
        s_ms = sg.get("start_ms", 0)
        e_ms = sg.get("end_ms", 0)
        if s_ms < 0:
            issues.append(f"{sid}: segment[{i}] start_ms negative")
        if e_ms < s_ms:
            issues.append(f"{sid}: segment[{i}] end_ms < start_ms")
        if not str(sg.get("text", "")).strip():
            issues.append(f"{sid}: segment[{i}] text is empty")
        if s_ms < last_end:
            issues.append(f"{sid}: segment[{i}] not strictly increasing (start {s_ms} < prev end {last_end})")
        last_end = max(last_end, e_ms)
    # duration check
    try:
        with wave.open(str(audio_path), "rb") as wf:
            audio_dur = wf.getnframes() / wf.getframerate()
    except Exception as e:  # noqa: BLE001
        issues.append(f"{sid}: unreadable audio ({e})")
        audio_dur = 0.0
    last_end_s = last_end / 1000.0
    if last_end_s - audio_dur > tolerance_s:
        issues.append(
            f"{sid}: last segment ends at {last_end_s:.2f}s, audio is {audio_dur:.2f}s "
            f"(tolerance {tolerance_s}s)"
        )
    # text concat vs reference
    joined = "".join(_norm(sg.get("text", "")) for sg in segs)
    ref = _norm(s["reference_text"])
    if joined != ref:
        issues.append(
            f"{sid}: segment texts != reference_text after CER-normalize "
            f"(joined_len {len(joined)} vs ref_len {len(ref)})"
        )
    return issues, advisory


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--input", required=True, help="draft annotations JSONL")
    p.add_argument("--out", required=True, help="validated annotations JSONL")
    args = p.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config, gate_for_cpu_entry, ConfigGateError
    cfg = load_config(args.config)
    try:
        gate_for_cpu_entry(cfg, command_name="08_annotate")
    except ConfigGateError as e:
        print(f"!! gate rejected: {e}")
        return 1

    testdata_root = Path(cfg.testdata_root).resolve()
    in_p = Path(args.input).resolve()
    out_p = Path(args.out).resolve()
    for name, path in (("input", in_p), ("output", out_p)):
        try:
            path.relative_to(testdata_root)
        except ValueError:
            print(f"!! {name} {path} is NOT inside testdata_root {testdata_root}")
            return 1
    if not in_p.exists():
        print(f"!! input not found: {in_p}")
        return 1
    # Defensive input size limits (local operator tool, not a security boundary).
    in_size = in_p.stat().st_size
    if in_size > MAX_INPUT_BYTES:
        print(f"!! input file too large: {in_size} > {MAX_INPUT_BYTES} bytes")
        return 1
    out_p.parent.mkdir(parents=True, exist_ok=True)

    items: list[dict] = []
    with in_p.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if i > MAX_INPUT_LINES:
                print(f"!! too many lines (> {MAX_INPUT_LINES}); rejected at line {i}")
                return 1
            line_bytes = len(line.encode("utf-8"))
            if line_bytes > MAX_LINE_BYTES:
                print(f"!! line {i} too long: {line_bytes} > {MAX_LINE_BYTES} bytes")
                return 1
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"!! input line {i} is invalid JSON: {e}")
                return 1
            if not isinstance(item, dict):
                print(f"!! input line {i} must be a JSON object")
                return 1
            items.append(item)

    all_issues: list[str] = []
    all_advisory: list[str] = []
    seen_ids: set[str] = set()
    validated: list[dict] = []
    for it in items:
        sid = it.get("id", "?")
        if sid in seen_ids:
            all_issues.append(f"{sid}: duplicate id")
        seen_ids.add(sid)
        audio = (in_p.parent / it.get("audio", "")).resolve()
        audio_sha = it.get("audio_sha256", "")
        issues, advisory = _validate_one(it, audio, audio_sha, testdata_root,
                                        cfg.short_sample_tolerance_s)
        all_issues.extend(issues)
        all_advisory.extend(advisory)
        if not issues:
            validated.append(it)

    if all_issues:
        print(f"!! {len(all_issues)} blocking validation issue(s):")
        for issue in all_issues:
            print(f"   {issue}")
        if all_advisory:
            print(f"   + {len(all_advisory)} advisory note(s); see report")
        # write the report anyway
        report = {
            "schema_version": "phase0-annotate-report/2",
            "run_id": cfg.run_id,
            "config_sha256": cfg.config_sha256,
            "n_input": len(items),
            "n_validated": len(validated),
            "issues": all_issues,
            "advisory": all_advisory,
        }
        rej = out_p.with_suffix(out_p.suffix + ".rejected.json")
        _atomic_json_dump(rej, report)
        print(f">> wrote {rej}")
        return 1

    # Even on success, record advisory items so consumers can demand a
    # second-pass review.
    if all_advisory:
        print(f">> {len(all_advisory)} advisory note(s) (see report):")
        for note in all_advisory:
            print(f"   ~ {note}")

    tmp = out_p.with_suffix(out_p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for it in validated:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, out_p)
    report = {
        "schema_version": "phase0-annotate-report/2",
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "n_input": len(items),
        "n_validated": len(validated),
        "issues": [],
        "advisory": all_advisory,
    }
    report_path = out_p.with_suffix(out_p.suffix + ".report.json")
    _atomic_json_dump(report_path, report)
    print(f">> wrote {out_p}  ({len(validated)} validated)")
    if all_advisory:
        print(f">> wrote {report_path}  (advisory: {len(all_advisory)})")


if __name__ == "__main__":
    sys.exit(main())
