"""Fail-closed license evidence audit for the FunASR Phase 0 sandbox.

The audit distinguishes declared package/model licenses from bundled notices.
Tier 0/2/3 artifacts require an exact, external approval bound to the current
evidence digest.  Expected model licenses are comparison hints only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as ilm
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_SCHEMA_VERSION = "phase0-license-audit/2"
APPROVAL_SCHEMA_VERSION = "phase0-license-approvals/1"

SPDX_TIER = {
    "MIT": 1, "Apache-2.0": 1, "BSD-2-Clause": 1, "BSD-3-Clause": 1,
    "ISC": 1, "Zlib": 1, "Python-2.0": 1, "PSF-2.0": 1,
    "MPL-2.0": 2, "LGPL-2.0": 2, "LGPL-2.0+": 2,
    "LGPL-2.0-only": 2, "LGPL-2.0-or-later": 2, "LGPL-2.1": 2,
    "LGPL-2.1+": 2, "LGPL-2.1-only": 2, "LGPL-2.1-or-later": 2,
    "LGPL-3.0": 2, "LGPL-3.0+": 2, "LGPL-3.0-only": 2,
    "LGPL-3.0-or-later": 2, "GPL-2.0": 3, "GPL-2.0+": 3,
    "GPL-2.0-only": 3, "GPL-2.0-or-later": 3, "GPL-3.0": 3,
    "GPL-3.0+": 3, "GPL-3.0-only": 3, "GPL-3.0-or-later": 3,
    "AGPL-3.0": 3, "AGPL-3.0+": 3, "AGPL-3.0-only": 3,
    "AGPL-3.0-or-later": 3, "CC0-1.0": 1, "CC-BY-4.0": 1,
    "CC-BY-SA-4.0": 2,
}

_ALIASES = {
    "apache license 2.0": "Apache-2.0", "apache software license": "Apache-2.0",
    "apache 2.0 license": "Apache-2.0", "apache-2.0 license": "Apache-2.0",
    "http://www.apache.org/licenses/license-2.0": "Apache-2.0",
    "https://www.apache.org/licenses/license-2.0": "Apache-2.0",
    "mit license": "MIT", "the mit license": "MIT", "bsd license": "BSD-3-Clause",
    "bsd-3-clause license": "BSD-3-Clause", "bsd 3-clause license": "BSD-3-Clause",
    "mozilla public license 2.0": "MPL-2.0",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_sha(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8"))


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _license_tokens(value: str) -> list[str]:
    value = re.sub(r"[()]", " ", value.strip())
    return [part.strip() for part in re.split(r"\s+(?:AND|OR|WITH)\s+", value,
                                               flags=re.IGNORECASE) if part.strip()]


def _normalize_token(token: str) -> str | None:
    exact = token.strip().rstrip("./").strip()
    if exact in SPDX_TIER:
        return exact
    alias = _ALIASES.get(exact.casefold())
    if alias:
        return alias
    # Classifier tails include a family prefix, e.g. OSI Approved :: MIT License.
    for phrase, normalized in _ALIASES.items():
        if exact.casefold().endswith(phrase):
            return normalized
    return None


def classify_license(value: str | None) -> tuple[int, list[str], str]:
    """Classify a declaration, never an arbitrary full license/NOTICE body."""
    if not value or not value.strip():
        return 0, [], "UNKNOWN"
    normalized: list[str] = []
    for token in _license_tokens(value):
        item = _normalize_token(token)
        if item and item not in normalized:
            normalized.append(item)
    if not normalized:
        return 0, [], value.strip()
    return max(SPDX_TIER[item] for item in normalized), normalized, " AND ".join(normalized)


def _short_declaration(value: str) -> bool:
    return bool(value) and len(value) <= 256 and "\n" not in value and "\r" not in value


@dataclass
class PkgInfo:
    name: str
    version: str
    license_expression: str
    license_field: str
    license_classifiers: list[str]
    selected_source: str
    selected_license: str
    tier: int
    constituents: list[str]
    homepage: str
    notice_sha256: str | None
    evidence_sha256: str
    approved: bool = False
    approval_reason: str | None = None


def _package_info(dist: Any) -> PkgInfo:
    metadata = dist.metadata
    expression = (metadata.get("License-Expression") or "").strip()
    field = (metadata.get("License") or "").strip()
    classifiers = [c for c in (metadata.get_all("Classifier") or [])
                   if c.startswith("License ::")]
    selected_source, selected = "none", "UNKNOWN"
    candidates: list[tuple[str, str]] = []
    if expression:
        candidates.append(("license-expression", expression))
    candidates.extend(("classifier", c) for c in classifiers)
    if _short_declaration(field):
        candidates.append(("license-field", field))
    for source, candidate in candidates:
        tier, constituents, normalized = classify_license(candidate)
        if tier:
            selected_source, selected = source, normalized
            break
    else:
        tier, constituents = 0, []
    notice_sha = _sha256_bytes(field.encode("utf-8")) if field and not _short_declaration(field) else None
    evidence = {
        "type": "package", "name": metadata.get("Name") or dist.name,
        "version": dist.version or "?", "selected_source": selected_source,
        "selected_license": selected, "notice_sha256": notice_sha,
    }
    return PkgInfo(
        name=str(evidence["name"]), version=str(evidence["version"]),
        license_expression=expression,
        license_field=(field if _short_declaration(field) else
                       ("[bundled notice recorded by SHA-256]" if field else "")),
        license_classifiers=classifiers, selected_source=selected_source,
        selected_license=selected, tier=tier, constituents=constituents,
        homepage=(metadata.get("Home-page") or "").strip(),
        notice_sha256=notice_sha, evidence_sha256=_canonical_json_sha(evidence),
    )


def collect_pkg_licenses(names: Iterable[str] | None = None) -> list[PkgInfo]:
    if names is None:
        names = sorted({d.metadata["Name"] for d in ilm.distributions()
                        if d.metadata.get("Name")})
    result: list[PkgInfo] = []
    for name in names:
        try:
            result.append(_package_info(ilm.distribution(name)))
        except ilm.PackageNotFoundError:
            continue
    return result


@dataclass
class ModelEntry:
    model_id: str
    revision: str
    source_url: str
    expected_license: str
    expected_tier: int
    found_path: str | None
    declared_license: str | None
    declaration_source: str | None
    found_license_tier: int
    found_files_sha256: dict[str, str]
    evidence_sha256: str
    status: str
    approved: bool = False
    approval_reason: str | None = None


def _model_card_license(text: str) -> str | None:
    head = text[:8192]
    match = re.search(r"(?im)^\s*license\s*:\s*['\"]?([^\r\n'\"]+)", head)
    return match.group(1).strip() if match else None


def _license_text_declaration(text: str) -> str | None:
    low = text[:4096].casefold()
    patterns = [
        ("apache license", "Apache-2.0"), ("mit license", "MIT"),
        ("mozilla public license", "MPL-2.0"),
        ("gnu lesser general public license", "LGPL-3.0-or-later"),
        ("gnu affero general public license", "AGPL-3.0-or-later"),
        ("gnu general public license", "GPL-3.0-or-later"),
        ("bsd 3-clause", "BSD-3-Clause"),
    ]
    return next((value for phrase, value in patterns if phrase in low), None)


def _find_model_dir(root: Path, model_id: str) -> Path | None:
    target = model_id.replace("/", os.sep)
    return next((p for p in (root / target, root / "modelscope" / target,
                             root / "modelscope" / "hub" / target,
                             root / "hub" / target) if p.is_dir()), None)


def scan_models(model_root: Path, expected: Iterable[dict[str, str]]) -> list[ModelEntry]:
    result: list[ModelEntry] = []
    for spec in expected:
        model_id, revision = spec["model_id"], spec["revision"]
        expected_license = spec.get("expected_license", "UNKNOWN")
        expected_tier = classify_license(expected_license)[0]
        model_dir = _find_model_dir(model_root, model_id)
        hashes: dict[str, str] = {}
        declared: str | None = None
        source: str | None = None
        if model_dir:
            for path in sorted(model_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(model_dir).as_posix()
                try:
                    hashes[rel] = _sha256_file(path)
                    if path.name.casefold() in {"license", "license.md", "license.txt"}:
                        text = path.read_text(encoding="utf-8", errors="replace")
                        declaration = _license_text_declaration(text)
                        if declaration and not declared:
                            declared, source = declaration, rel
                    elif path.name.casefold() in {"readme.md", "modelcard.md", "model_card.md"}:
                        text = path.read_text(encoding="utf-8", errors="replace")
                        declaration = _model_card_license(text)
                        if declaration and not declared:
                            declared, source = declaration, f"{rel}:frontmatter"
                except OSError:
                    continue
        found_tier = classify_license(declared)[0]
        if not model_dir:
            status = "EXPECTED_MISSING"
        elif not hashes:
            status = "NO_ARTIFACT_EVIDENCE"
        elif not declared:
            status = "NO_LICENSE_EVIDENCE"
        elif found_tier == 0:
            status = "UNRECOGNIZED_LICENSE"
        elif expected_tier and found_tier != expected_tier:
            status = "LICENSE_MISMATCH"
        else:
            status = "VERIFIED"
        evidence = {
            "type": "model", "model_id": model_id, "revision": revision,
            "declared_license": declared, "declaration_source": source,
            "files_sha256": hashes,
        }
        result.append(ModelEntry(
            model_id=model_id, revision=revision,
            source_url=spec.get("source_url", ""), expected_license=expected_license,
            expected_tier=expected_tier, found_path=str(model_dir) if model_dir else None,
            declared_license=declared, declaration_source=source,
            found_license_tier=found_tier, found_files_sha256=hashes,
            evidence_sha256=_canonical_json_sha(evidence), status=status,
        ))
    return result


def _load_approvals(path: Path | None, config_sha256: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if raw.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise ValueError("unsupported license approval schema")
    if raw.get("config_sha256") != config_sha256:
        raise ValueError("license approval config_sha256 is stale or mismatched")
    approvals: dict[tuple[str, str, str], dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for item in raw.get("approvals", []):
        if not isinstance(item, Mapping):
            raise ValueError("approval entries must be objects")
        required = {"artifact_type", "name", "version_or_revision", "evidence_sha256",
                    "approved_by", "approved_at", "reason"}
        if not required.issubset(item) or any(not str(item[k]).strip() for k in required):
            raise ValueError("approval entry has missing/empty required fields")
        if "*" in (item["name"], item["version_or_revision"], item["evidence_sha256"]):
            raise ValueError("wildcards are forbidden in license approvals")
        approved_at = datetime.fromisoformat(str(item["approved_at"]).replace("Z", "+00:00"))
        if approved_at.tzinfo is None:
            raise ValueError("approved_at must include a UTC offset")
        expires = item.get("expires_at")
        if expires:
            expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("expires_at must include a UTC offset")
            if expiry <= now:
                continue
        key = (str(item["artifact_type"]), str(item["name"]),
               str(item["version_or_revision"]))
        if key in approvals:
            raise ValueError(f"duplicate approval for {key}")
        approvals[key] = dict(item)
    return approvals


def _apply_approvals(pkgs: list[PkgInfo], models: list[ModelEntry], approvals: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    for pkg in pkgs:
        item = approvals.get(("package", pkg.name, pkg.version))
        if item and item["evidence_sha256"] == pkg.evidence_sha256:
            pkg.approved, pkg.approval_reason = True, str(item["reason"])
    for model in models:
        item = approvals.get(("model", model.model_id, model.revision))
        if item and item["evidence_sha256"] == model.evidence_sha256:
            model.approved, model.approval_reason = True, str(item["reason"])


def _package_blocked(pkg: PkgInfo) -> bool:
    return pkg.tier in (0, 2, 3) and not pkg.approved


def _model_blocked(model: ModelEntry) -> bool:
    return (model.status != "VERIFIED" or model.found_license_tier in (0, 2, 3)) and not model.approved


def _markdown_escape(value: Any, limit: int = 80) -> str:
    return str(value or "")[:limit].replace("|", "\\|").replace("\n", " ")


def render_markdown(pkgs: list[PkgInfo], models: list[ModelEntry], out: Path,
                    config_sha256: str, approval_path: Path | None) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    blockers: list[PkgInfo | ModelEntry] = [p for p in pkgs if _package_blocked(p)]
    blockers += [m for m in models if _model_blocked(m)]
    lines = [
        "# FunASR Phase 0 License Audit", "", f"- Schema: {AUDIT_SCHEMA_VERSION}",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Config SHA-256: `{config_sha256}`",
        f"- Approval file: `{approval_path if approval_path else 'not supplied'}`", "",
        "Tier 0/2/3 and unverified model evidence are blocked unless an exact external approval matches.",
        "Bundled notices are recorded separately and never replace authoritative package declarations.",
        "", "## Installed packages", "",
        "| Package | Version | Tier | Selected declaration | Source | Notice SHA-256 | Approved |",
        "|---|---|---:|---|---|---|---|",
    ]
    for pkg in sorted(pkgs, key=lambda item: (-item.tier, item.name.casefold())):
        lines.append(f"| {pkg.name} | {pkg.version} | {pkg.tier} | {_markdown_escape(pkg.selected_license)} | "
                     f"{pkg.selected_source} | {_markdown_escape(pkg.notice_sha256)} | {pkg.approved} |")
    lines += ["", "## Staged models", "",
              "| Model | Revision | Status | Tier | Declaration | Evidence source | Files | Approved |",
              "|---|---|---|---:|---|---|---:|---|"]
    for model in models:
        lines.append(f"| {model.model_id} | {model.revision} | {model.status} | "
                     f"{model.found_license_tier} | {_markdown_escape(model.declared_license)} | "
                     f"{_markdown_escape(model.declaration_source)} | {len(model.found_files_sha256)} | {model.approved} |")
    lines += ["", "## Blockers", ""]
    if blockers:
        for item in blockers:
            if isinstance(item, PkgInfo):
                lines.append(f"- Package `{item.name}=={item.version}` tier={item.tier}; evidence `{item.evidence_sha256}`")
            else:
                lines.append(f"- Model `{item.model_id}@{item.revision}` status={item.status}; evidence `{item.evidence_sha256}`")
    else:
        lines.append("- None")
    lines += ["", "## Summary", "",
              f"- packages: {len(pkgs)}; models: {len(models)}; blockers: {len(blockers)}"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"schema_version": AUDIT_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config_sha256": config_sha256, "blockers": blockers}


DEFAULT_EXPECTED_MODELS = {
    "iic/SenseVoiceSmall": ("https://www.modelscope.cn/models/iic/SenseVoiceSmall", "Apache-2.0"),
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch": (
        "https://www.modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "Apache-2.0"),
    "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch": (
        "https://www.modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch", "Apache-2.0"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--approvals", help="external exact-match approval JSON")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config
    cfg = load_config(args.config)
    revisions = dict(zip(cfg.allowed_asr_model_ids, cfg.allowed_asr_revisions))
    revisions[cfg.vad_model_id] = cfg.vad_model_revision
    revisions[cfg.punc_model_id] = cfg.punc_model_revision
    specs = []
    for model_id, revision in revisions.items():
        source, expected = DEFAULT_EXPECTED_MODELS.get(
            model_id, (f"https://www.modelscope.cn/models/{model_id}", "UNKNOWN"))
        specs.append({"model_id": model_id, "revision": revision,
                      "source_url": source, "expected_license": expected})
    pkgs = collect_pkg_licenses()
    models = scan_models(Path(cfg.models_root), specs)
    reports_root = Path(cfg.reports_root).resolve()
    approval_path = (Path(args.approvals).resolve() if args.approvals else
                     reports_root / "license-approvals.json")
    try:
        approval_path.relative_to(reports_root)
    except ValueError:
        print(">> approval file must be inside reports_root", file=sys.stderr)
        return 3
    try:
        approvals = _load_approvals(approval_path if approval_path.exists() else None,
                                    cfg.config_sha256)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f">> invalid approval file: {exc}", file=sys.stderr)
        return 3
    _apply_approvals(pkgs, models, approvals)
    out = Path(args.out) if args.out else Path(cfg.reports_root) / f"license-audit-{datetime.now():%Y%m%d-%H%M%S}.md"
    summary = render_markdown(pkgs, models, out, cfg.config_sha256,
                              approval_path if approval_path.exists() else None)
    sidecar = out.with_suffix(".json")
    sidecar.write_text(json.dumps({
        "schema_version": summary["schema_version"], "generated_at": summary["generated_at"],
        "config_sha256": cfg.config_sha256,
        "approval_file": str(approval_path) if approval_path.exists() else None,
        "packages": [asdict(item) for item in pkgs],
        "models": [asdict(item) for item in models],
        "blockers": [({"type": "package", "name": x.name, "version": x.version,
                        "evidence_sha256": x.evidence_sha256} if isinstance(x, PkgInfo) else
                       {"type": "model", "name": x.model_id, "revision": x.revision,
                        "evidence_sha256": x.evidence_sha256}) for x in summary["blockers"]],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> wrote {out}")
    print(f">> wrote {sidecar}")
    if summary["blockers"] and not args.report_only:
        print(f">> {len(summary['blockers'])} blocker(s); refusing GPU execution")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
