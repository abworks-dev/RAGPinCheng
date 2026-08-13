from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any


SUPPORTED_DOCUMENT_TYPES = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}
ALLOWED_CATEGORY_KEYS = {
    "industry_standards",
    "client_requirements",
    "company_standards",
    "project_materials",
    "training_materials",
    "project_experience",
    "pending_confirmation",
}
ALLOWED_HANDLING = {"document", "transcript"}
CSV_FIELDS = (
    "kind",
    "relative_path",
    "size_bytes",
    "sha256",
    "document_type",
    "category_key",
    "mapping_prefix",
    "disposition",
    "reason",
    "duplicate_group",
    "related_sha256_paths",
)


class PlanningError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MappingRule:
    kind: str
    prefix: str
    parts: tuple[str, ...]
    category_key: str
    handling: str


def _relative_parts(value: object, field: str, *, allow_dot: bool = False) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PlanningError(f"invalid_{field}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PlanningError(f"invalid_{field}")
    if value == ".":
        if allow_dot:
            return ()
        raise PlanningError(f"invalid_{field}")
    return path.parts


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanningError(f"cannot_read_json:{path.name}") from exc
    if not isinstance(payload, dict):
        raise PlanningError(f"invalid_json_object:{path.name}")
    return payload


def parse_mapping(payload: dict[str, Any]) -> list[MappingRule]:
    if payload.get("schema_version") != 1 or not isinstance(payload.get("mappings"), list):
        raise PlanningError("invalid_mapping_schema")
    rules: list[MappingRule] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for raw in payload["mappings"]:
        if not isinstance(raw, dict):
            raise PlanningError("invalid_mapping_entry")
        kind = raw.get("kind")
        if kind != "docs":
            raise PlanningError("mapping_kind_must_be_docs")
        parts = _relative_parts(raw.get("legacy_prefix"), "mapping_prefix", allow_dot=True)
        category_key = raw.get("category_key")
        handling = raw.get("handling")
        if category_key not in ALLOWED_CATEGORY_KEYS:
            raise PlanningError("invalid_category_key")
        if handling not in ALLOWED_HANDLING:
            raise PlanningError("invalid_mapping_handling")
        key = (kind, parts)
        if key in seen:
            raise PlanningError("duplicate_mapping_prefix")
        seen.add(key)
        rules.append(MappingRule(kind, "." if not parts else "/".join(parts), parts, category_key, handling))
    return sorted(rules, key=lambda rule: (rule.kind, -len(rule.parts), rule.prefix))


def _matching_rule(kind: str, path_parts: tuple[str, ...], rules: list[MappingRule]) -> MappingRule | None:
    directory_parts = path_parts[:-1]
    for rule in rules:
        if rule.kind == kind and directory_parts[: len(rule.parts)] == rule.parts:
            return rule
    return None


def _reason_and_disposition(
    entry: dict[str, Any], rule: MappingRule | None, *, max_bytes: int
) -> tuple[str, str, str | None]:
    if entry.get("status") == "symlink_rejected":
        return "symlink_rejected", "symbolic_link", None
    if entry["kind"] == "media":
        return "preserve_legacy_media", "legacy_media_pipeline", None
    filename = PurePosixPath(entry["relative_path"]).name.lower()
    suffix = PurePosixPath(filename).suffix
    document_type = SUPPORTED_DOCUMENT_TYPES.get(suffix)
    if document_type is None:
        return "unsupported", "unsupported_type", None
    if filename.endswith((".preview.pdf", ".preview.xlsx")):
        return "derived_artifact", "generated_preview", document_type
    if entry["size_bytes"] > max_bytes:
        return "unsupported", "content_too_large", document_type
    if rule is None:
        return "pending_mapping", "explicit_mapping_required", document_type
    directory_depth = len(PurePosixPath(entry["relative_path"]).parts) - 1
    if directory_depth > 4:
        return "unsupported", "directory_depth_exceeds_four", document_type
    if rule.handling == "transcript":
        if document_type != "markdown":
            return "unsupported", "transcript_must_be_markdown", document_type
        return "review_transcript_link", "manual_media_transcript_link_required", document_type
    return "import_document", "explicit_mapping_confirmed", document_type


def build_plan(inventory: dict[str, Any], mapping: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    if max_bytes < 0:
        raise PlanningError("invalid_max_bytes")
    if inventory.get("schema_version") != 1 or not isinstance(inventory.get("entries"), list):
        raise PlanningError("invalid_inventory_schema")
    rules = parse_mapping(mapping)
    normalized: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()
    for raw in inventory["entries"]:
        if not isinstance(raw, dict) or raw.get("kind") not in {"docs", "media"}:
            raise PlanningError("invalid_inventory_entry")
        kind = raw["kind"]
        parts = _relative_parts(raw.get("relative_path"), "inventory_path")
        relative_path = "/".join(parts)
        path_key = (kind, relative_path)
        if path_key in seen_paths:
            raise PlanningError("duplicate_inventory_path")
        seen_paths.add(path_key)
        status = raw.get("status")
        if status not in {"inventoried", "symlink_rejected"}:
            raise PlanningError("invalid_inventory_status")
        if status == "inventoried":
            if not isinstance(raw.get("size_bytes"), int) or raw["size_bytes"] < 0:
                raise PlanningError("invalid_size_bytes")
            sha256 = raw.get("sha256")
            if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
                raise PlanningError("invalid_sha256")
        else:
            sha256 = None
        normalized.append({"kind": kind, "relative_path": relative_path, "parts": parts, "status": status,
                           "size_bytes": raw.get("size_bytes"), "sha256": sha256})

    normalized.sort(key=lambda row: (row["kind"], row["relative_path"]))
    planned: list[dict[str, Any]] = []
    for entry in normalized:
        rule = _matching_rule(entry["kind"], entry["parts"], rules)
        disposition, reason, document_type = _reason_and_disposition(entry, rule, max_bytes=max_bytes)
        planned.append({
            "kind": entry["kind"], "relative_path": entry["relative_path"],
            "size_bytes": entry["size_bytes"], "sha256": entry["sha256"],
            "document_type": document_type,
            "category_key": rule.category_key if rule else None,
            "mapping_prefix": rule.prefix if rule else None,
            "disposition": disposition, "reason": reason,
            "duplicate_group": None, "related_sha256_paths": [],
        })

    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in planned:
        if entry["sha256"]:
            by_sha[entry["sha256"]].append(entry)
    duplicate_groups = 0
    cross_scope_links = 0
    for sha256, entries in sorted(by_sha.items()):
        scopes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            path_parts = PurePosixPath(entry["relative_path"]).parts
            if entry["kind"] == "media":
                scope = path_parts[0]
            elif entry["mapping_prefix"] is not None:
                scope = entry["mapping_prefix"]
            else:
                scope = "/".join(path_parts[:-1]) or "."
            scopes[(entry["kind"], scope)].append(entry)
        for scope_entries in scopes.values():
            if len(scope_entries) > 1:
                duplicate_groups += 1
                group_id = f"duplicate-{duplicate_groups:04d}"
                for entry in scope_entries:
                    entry["duplicate_group"] = group_id
        if len(scopes) > 1:
            cross_scope_links += 1
            for entry in entries:
                entry["related_sha256_paths"] = sorted(
                    f'{other["kind"]}:{other["relative_path"]}' for other in entries if other is not entry
                )

    def counts(field: str) -> dict[str, int]:
        return dict(sorted(Counter(str(entry[field]) for entry in planned if entry[field] is not None).items()))

    inventoried = [entry for entry in planned if entry["size_bytes"] is not None]
    summary = {
        "file_count": len(planned),
        "total_bytes": sum(entry["size_bytes"] for entry in inventoried),
        "by_kind": counts("kind"),
        "by_document_type": counts("document_type"),
        "by_disposition": counts("disposition"),
        "by_category_key": counts("category_key"),
        "unmapped_count": sum(entry["disposition"] == "pending_mapping" for entry in planned),
        "exception_count": sum(entry["disposition"] in {"unsupported", "symlink_rejected"} for entry in planned),
        "same_scope_duplicate_groups": duplicate_groups,
        "cross_scope_sha256_links": cross_scope_links,
    }
    return {"schema_version": 1, "max_bytes": max_bytes, "summary": summary, "entries": planned}


def render_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_csv(plan: dict[str, Any]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for entry in plan["entries"]:
        row = dict(entry)
        row["related_sha256_paths"] = "|".join(row["related_sha256_paths"])
        writer.writerow({field: "" if row[field] is None else row[field] for field in CSV_FIELDS})
    return output.getvalue()


def validate_outputs(paths: list[Path], *, overwrite: bool, inputs: set[Path]) -> None:
    resolved = [path.resolve(strict=False) for path in paths]
    if len(set(resolved)) != len(resolved):
        raise PlanningError("output_paths_must_be_distinct")
    if any(path in inputs for path in resolved):
        raise PlanningError("output_must_not_replace_input")
    existing = [path.name for path in paths if path.exists()]
    if existing and not overwrite:
        raise PlanningError(f"output_already_exists:{','.join(existing)}")


def write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an offline deterministic legacy content migration plan")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--max-bytes", type=int, default=200 * 1024 * 1024)
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files only")
    args = parser.parse_args()
    inputs = {args.inventory.resolve(strict=False), args.mapping.resolve(strict=False)}
    outputs = [args.output_json, *([args.output_csv] if args.output_csv else [])]
    try:
        plan = build_plan(load_json(args.inventory), load_json(args.mapping), max_bytes=args.max_bytes)
        validate_outputs(outputs, overwrite=args.overwrite, inputs=inputs)
        write_output(args.output_json, render_json(plan))
        if args.output_csv:
            write_output(args.output_csv, render_csv(plan))
    except PlanningError as exc:
        parser.error(str(exc))
    print(f"planned {plan['summary']['file_count']} files into {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
