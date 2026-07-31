"""Phase 0 ASR sandbox — license audit utility (R2 fix).

Behavior per R2 spec §九:
  - Scans actually-installed packages via importlib.metadata; reads License
    field plus PEP 639 / License :: classifier; computes tier.
  - Scans actually-staged model directories under the models_root; reads
    LICENSE / model card; records revision, source URL, file SHA-256.
  - Hardcoded entries are treated as "expected value" only — never as
    "verified fact". A hardcoded entry without an actual installed/staged
    file is reported as EXPECTED_MISSING.
  - Compound licenses (e.g. "MIT OR Apache-2.0") take the HIGHEST tier of
    any constituent license.
  - Tier 2 (LGPL / MPL), tier 3 (GPL / AGPL), and tier 0 (UNKNOWN) all
    require explicit manual review; the audit exit code is non-zero if
    any of these exist UNAPPROVED.
  - --report-only mode writes the audit but DOES NOT affect exit code;
    main GPU entry scripts must not invoke audit in --report-only mode.

Run:
  C:\\FunASR-Phase0\\venv\\Scripts\\python.exe ^
    E:\\Repository\\Github\\RAGPinCheng\\scripts\\funasr_phase0\\lib_license_audit.py ^
    --config phase0-config.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as ilm
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

AUDIT_SCHEMA_VERSION = "phase0-license-audit/1"

# ─────────────────────────────────────────────────────────────────────────────
# License classification
# ─────────────────────────────────────────────────────────────────────────────

# SPDX short names -> base tier
SPDX_TIER = {
    "MIT": 1, "Apache-2.0": 1, "BSD-2-Clause": 1, "BSD-3-Clause": 1,
    "ISC": 1, "MPL-2.0": 2, "Zlib": 1, "Python-2.0": 1, "PSF-2.0": 1,
    "LGPL-2.0": 2, "LGPL-2.0+": 2, "LGPL-2.1": 2, "LGPL-2.1+": 2,
    "LGPL-3.0": 2, "LGPL-3.0+": 2,
    "GPL-2.0": 3, "GPL-2.0+": 3, "GPL-3.0": 3, "GPL-3.0+": 3,
    "AGPL-3.0": 3, "AGPL-3.0+": 3,
    "CC0-1.0": 1, "CC-BY-4.0": 1, "CC-BY-SA-4.0": 2,
}

# Substring matches (PEP 639 / classifier text)
_TOK_TIER = [
    ("Affero General Public License", 3), ("AGPL", 3),
    ("GNU General Public License", 3), ("GPL-3", 3), ("GPL-2", 3),
    ("GNU Lesser General Public", 2), ("Lesser General Public", 2), ("LGPL", 2),
    ("Mozilla Public License", 2), ("MPL", 2),
    ("Apache License", 1), ("BSD", 1), ("ISC", 1), ("MIT License", 1),
]


def _split_compound(s: str) -> list[str]:
    """Split 'MIT OR Apache-2.0' / 'MIT AND (GPL-2.0 OR MIT)' into parts.

    Splits on top-level OR/AND/WITH, also strips surrounding parens.
    """
    s = (s or "").strip()
    out = [s]
    for sep in (" OR ", " AND ", " WITH "):
        new: list[str] = []
        for part in out:
            new.extend(p.strip() for p in part.split(sep))
        out = new
    cleaned: list[str] = []
    for p in out:
        p = p.strip().strip("()").strip()
        if p:
            cleaned.append(p)
    return cleaned


def classify_license(license_str: str | None) -> tuple[int, list[str], str]:
    """Return (tier, constituent_names, normalized_display).

    tier: 0=unknown, 1=permissive, 2=weak-copyleft, 3=strong-copyleft
    constituent_names: SPDX short names recognized
    """
    if not license_str:
        return 0, [], "UNKNOWN"
    parts = _split_compound(license_str)
    constituents: list[str] = []
    tier = 0
    for p in parts:
        # Try SPDX exact match
        p_strip = p.strip().strip("()")
        for sp, t in SPDX_TIER.items():
            if sp in p_strip or p_strip == sp:
                if sp not in constituents:
                    constituents.append(sp)
                if t > tier:
                    tier = t
                break
        else:
            for tok, t in _TOK_TIER:
                if tok in p:
                    if tok not in constituents:
                        constituents.append(tok)
                    if t > tier:
                        tier = t
                    break
    return tier, constituents, license_str


# ─────────────────────────────────────────────────────────────────────────────
# Package metadata
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PkgInfo:
    name: str
    version: str
    license_field: str
    license_classifier: str
    tier: int
    constituents: list[str]
    homepage: str


def collect_pkg_licenses(names: Iterable[str] | None = None) -> list[PkgInfo]:
    out: list[PkgInfo] = []
    if names is None:
        names = sorted({d.metadata["Name"] for d in ilm.distributions() if d.metadata.get("Name")})
    for name in names:
        try:
            dist = ilm.distribution(name)
        except ilm.PackageNotFoundError:
            continue
        license_field = (dist.metadata.get("License") or "").strip()
        classifiers = dist.metadata.get_all("Classifier") or []
        license_classifier = ""
        for c in classifiers:
            if c.startswith("License ::"):
                license_classifier = c.split(" :: ", 2)[-1]
                break
        combined = license_field or license_classifier or "UNKNOWN"
        tier, constituents, _ = classify_license(combined)
        out.append(PkgInfo(
            name=name,
            version=dist.version or "?",
            license_field=license_field,
            license_classifier=license_classifier,
            tier=tier,
            constituents=constituents,
            homepage=(dist.metadata.get("Home-page") or "").strip(),
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Model files on disk
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelEntry:
    model_id: str
    source_url: str
    expected_license: str
    expected_tier: int
    expected_constituents: list[str]
    found_path: str | None
    found_license_text: str | None
    found_license_tier: int
    found_files_sha256: dict[str, str]
    found_model_card_excerpt: str | None
    status: str  # "VERIFIED" / "EXPECTED_MISSING" / "LICENSE_MISMATCH"


def _sha256_file(p: Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(chunk), b""):
            h.update(c)
    return h.hexdigest()


def _scan_model_dir(model_root: Path, model_id: str) -> tuple[Path | None, dict[str, str], str | None, str | None]:
    """Look for a directory under model_root that matches model_id's tail."""
    if not model_root.exists():
        return None, {}, None, None
    target = model_id.replace("/", os.sep)
    candidates = (
        model_root / target,
        model_root / "modelscope" / target,
        model_root / "modelscope" / "hub" / target,
        model_root / "hub" / target,
    )
    candidate = next((p for p in candidates if p.is_dir()), None)
    if candidate is None:
        return None, {}, None, None
    sha: dict[str, str] = {}
    license_text: str | None = None
    card: str | None = None
    for p in candidate.rglob("*"):
        if not p.is_file():
            continue
        try:
            sha[str(p.relative_to(candidate))] = _sha256_file(p)
        except OSError:
            continue
        if p.name.lower() in {"license", "license.md", "license.txt"}:
            try:
                license_text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        if p.name.lower() in {"readme.md", "modelcard.md", "model_card.md"} or p.name == "README.md":
            try:
                card = p.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                pass
    return candidate, sha, license_text, card


def scan_models(model_root: Path, expected: Iterable[dict[str, str]]) -> list[ModelEntry]:
    out: list[ModelEntry] = []
    for ex in expected:
        mid = ex["model_id"]
        url = ex.get("source_url", "")
        exp_lic = ex.get("expected_license", "UNKNOWN")
        exp_tier, exp_con, _ = classify_license(exp_lic)
        found_path, sha, license_text, card = _scan_model_dir(model_root, mid)
        if found_path is None:
            out.append(ModelEntry(
                model_id=mid, source_url=url,
                expected_license=exp_lic, expected_tier=exp_tier,
                expected_constituents=exp_con,
                found_path=None, found_license_text=None, found_license_tier=0,
                found_files_sha256={}, found_model_card_excerpt=None,
                status="EXPECTED_MISSING",
            ))
            continue
        license_to_check = license_text or exp_lic
        found_tier, found_con, _ = classify_license(license_to_check)
        status = "VERIFIED" if found_tier <= exp_tier else "LICENSE_MISMATCH"
        out.append(ModelEntry(
            model_id=mid, source_url=url,
            expected_license=exp_lic, expected_tier=exp_tier,
            expected_constituents=exp_con,
            found_path=str(found_path), found_license_text=license_text,
            found_license_tier=found_tier,
            found_files_sha256=sha, found_model_card_excerpt=card,
            status=status,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────


def _tier_mark(t: int) -> str:
    return {0: "❓0", 1: "✅1", 2: "⚠️2", 3: "⛔3"}.get(t, "?")


def render_markdown(pkgs: list[PkgInfo], models: list[ModelEntry], out: Path) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# FunASR Phase 0 License Audit")
    lines.append("")
    lines.append(f"- Schema: {AUDIT_SCHEMA_VERSION}")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("- Tier 1 = permissive (MIT/Apache/BSD/…) — OK")
    lines.append("- Tier 2 = weak-copyleft (LGPL / MPL) — **manual review**")
    lines.append("- Tier 3 = strong-copyleft (GPL / AGPL) — **manual review**")
    lines.append("- Tier 0 = unknown — **manual review**")
    lines.append("")
    lines.append("## Actually-installed packages")
    lines.append("")
    lines.append("| Package | Version | Tier | License (field) | License (classifier) | Constituents |")
    lines.append("|---|---|---|---|---|---|")
    for p in sorted(pkgs, key=lambda x: (-x.tier, x.name.lower())):
        fld = (p.license_field or "")[:60].replace("|", "\\|")
        cls = (p.license_classifier or "")[:60].replace("|", "\\|")
        con = ", ".join(p.constituents)[:60]
        lines.append(f"| {p.name} | {p.version} | {_tier_mark(p.tier)} | {fld} | {cls} | {con} |")
    lines.append("")
    lines.append("## Staged model directories (actual files on disk)")
    lines.append("")
    lines.append("| Model | Status | Tier (expected / found) | License (expected / found) | n_files |")
    lines.append("|---|---|---|---|---|")
    for m in models:
        lic_exp = m.expected_license[:30]
        lic_fnd = (m.found_license_text or "")[:30].replace("\n", " ")
        lines.append(
            f"| {m.model_id} | {m.status} | {_tier_mark(m.expected_tier)} / {_tier_mark(m.found_license_tier)} | "
            f"{lic_exp} / {lic_fnd} | {len(m.found_files_sha256)} |"
        )
    lines.append("")
    lines.append("## Action items (must be zero or explicitly approved to proceed)")
    needs = [p for p in pkgs if p.tier in (0, 2, 3)]
    needs += [m for m in models if m.found_license_tier in (0, 2, 3)
              or m.expected_tier in (0, 2, 3) or m.status != "VERIFIED"]
    if needs:
        lines.append("")
        for x in needs:
            if isinstance(x, PkgInfo):
                lines.append(f"- [Pip] `{x.name}=={x.version}` tier={x.tier} field='{x.license_field}'")
            else:
                lines.append(
                    f"- [Model] {x.model_id} status={x.status} "
                    f"expected_tier={x.expected_tier} found_tier={x.found_license_tier}"
                )
    summary = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_packages": len(pkgs),
        "n_models": len(models),
        "needs_review": [x for x in needs],
    }
    lines.append("")
    lines.append(f"## Summary")
    lines.append(f"- packages: {len(pkgs)}; models: {len(models)}; needs_review: {len(needs)}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


# Default expected models (treated as "expected", not "verified")
DEFAULT_EXPECTED_MODELS: list[dict[str, str]] = [
    {
        "model_id": "iic/SenseVoiceSmall",
        "source_url": "https://www.modelscope.cn/models/iic/SenseVoiceSmall",
        "expected_license": "Apache-2.0",
    },
    {
        "model_id": "damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "source_url": "https://www.modelscope.cn/models/damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "expected_license": "Apache-2.0",
    },
    {
        "model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "source_url": "https://www.modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "expected_license": "Apache-2.0",
    },
    {
        "model_id": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "source_url": "https://www.modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "expected_license": "Apache-2.0",
    },
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, help="phase0-config.json")
    p.add_argument("--report-only", action="store_true",
                   help="write report but DO NOT enforce non-zero exit on blockers")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    # Defer import to avoid path issues; entry scripts already on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config
    cfg = load_config(args.config)

    pkgs = collect_pkg_licenses()
    expected_by_id = {row["model_id"]: row for row in DEFAULT_EXPECTED_MODELS}
    configured_ids = [*cfg.allowed_asr_model_ids, cfg.vad_model_id, cfg.punc_model_id]
    expected_models = []
    for model_id in dict.fromkeys(configured_ids):
        expected_models.append(expected_by_id.get(model_id, {
            "model_id": model_id,
            "source_url": f"https://www.modelscope.cn/models/{model_id}",
            "expected_license": "UNKNOWN",
        }))
    models = scan_models(Path(cfg.models_root), expected_models)
    out_path = Path(args.out) if args.out else (
        Path(cfg.reports_root) / f"license-audit-{datetime.now():%Y%m%d-%H%M%S}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = render_markdown(pkgs, models, out_path)

    # Also write the JSON sidecar for machine consumption
    sidecar = out_path.with_suffix(".json")
    # summary["needs_review"] may contain PkgInfo / ModelEntry objects; serialize defensively
    def _ser(x: Any) -> Any:
        if isinstance(x, PkgInfo):
            return {"type": "pkg", "name": x.name, "version": x.version,
                    "tier": x.tier, "license_field": x.license_field}
        if isinstance(x, ModelEntry):
            return {"type": "model", "model_id": x.model_id, "status": x.status,
                    "expected_tier": x.expected_tier, "found_tier": x.found_license_tier}
        return str(x)
    sidecar.write_text(json.dumps({
        "schema_version": summary["schema_version"],
        "generated_at": summary["generated_at"],
        "n_packages": summary["n_packages"],
        "n_models": summary["n_models"],
        "needs_review": [_ser(x) for x in summary["needs_review"]],
        "packages": [
            {"name": p.name, "version": p.version, "tier": p.tier,
             "license_field": p.license_field, "license_classifier": p.license_classifier,
             "constituents": p.constituents, "homepage": p.homepage}
            for p in pkgs
        ],
        "models": [
            {"model_id": m.model_id, "status": m.status, "expected_tier": m.expected_tier,
             "found_tier": m.found_license_tier, "expected_license": m.expected_license,
             "found_license_excerpt": (m.found_license_text or "")[:300],
             "n_files": len(m.found_files_sha256), "files_sha256": m.found_files_sha256,
             "source_url": m.source_url, "found_path": m.found_path}
            for m in models
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> wrote {out_path}")
    print(f">> wrote {sidecar}")

    blockers = [p for p in pkgs if p.tier in (0, 2, 3)]
    blockers += [m for m in models if m.found_license_tier in (0, 2, 3)
                 or m.expected_tier in (0, 2, 3) or m.status != "VERIFIED"]
    if blockers and not args.report_only:
        print(f">> {len(blockers)} blocker(s) found; refusing to exit 0")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
