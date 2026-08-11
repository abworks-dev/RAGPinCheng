from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


LEGACY_CATEGORY_HINTS = {
    "设计规范": "industry_standards",
    "客户标准": "client_requirements",
    "公司标准": "company_standards",
    "教学视频": "training_materials",
    "培训视频": "training_materials",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path, kind: str) -> list[dict[str, object]]:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError(f"invalid_{kind}_root")
    rows: list[dict[str, object]] = []
    for path in sorted(resolved.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        rel = path.relative_to(resolved)
        if path.is_symlink():
            rows.append({"kind": kind, "relative_path": rel.as_posix(), "status": "symlink_rejected"})
            continue
        first = rel.parts[0] if rel.parts else ""
        media_id = rel.parts[0] if kind == "media" and rel.parts else None
        rows.append(
            {
                "kind": kind,
                "relative_path": rel.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "category_hint": LEGACY_CATEGORY_HINTS.get(first, "pending_confirmation") if kind == "docs" else "training_materials",
                "media_id_hint": media_id,
                "status": "inventoried",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only inventory for legacy docs and media")
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output already exists")
    rows = inventory(args.docs_root, "docs") + inventory(args.media_root, "media")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"schema_version": 1, "entries": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"inventoried {len(rows)} files into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
