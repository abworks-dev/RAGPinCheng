from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


SOURCE_RUN_ID = "31348714759"
SOURCE_COMMIT_SHA = "56f20a08d59cdfd6d93022dd2a284e6c7519fc0b"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINES = 10_000

SSL_SIGNALS = {
    "certificate_verify_failed": re.compile(
        r"certificate verify failed|unable to get local issuer|self[- ]signed certificate|unable to verify",
        re.IGNORECASE,
    ),
    "hostname_mismatch": re.compile(
        r"hostname .*doesn.t match|certificate.*hostname mismatch", re.IGNORECASE
    ),
    "proxy_tunnel_failure": re.compile(
        r"proxyerror|tunnel connection failed|cannot connect to proxy", re.IGNORECASE
    ),
    "tls_protocol_failure": re.compile(
        r"wrong version number|tlsv1 alert protocol version|unsupported protocol|protocol version",
        re.IGNORECASE,
    ),
    "tls_connection_closed": re.compile(
        r"unexpected eof|eof occurred|connection reset", re.IGNORECASE
    ),
    "ssl_error": re.compile(r"sslerror|ssl error", re.IGNORECASE),
}

ENDPOINT_FAMILIES = {
    "xet": re.compile(r"xethub|cas-bridge|cas-server", re.IGNORECASE),
    "huggingface": re.compile(r"huggingface\.co|hf\.co", re.IGNORECASE),
}


class EvidenceError(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _read_fixed_file(path: Path, *, root: Path) -> str:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    allowed_parents = {
        (resolved_root / "logs").resolve(strict=True),
        (resolved_root / "evidence").resolve(strict=True),
        (resolved_root / "reports").resolve(strict=True),
    }
    if resolved.parent not in allowed_parents:
        raise EvidenceError("source file escaped fixed run root")
    if path.is_symlink() or _is_reparse_point(path) or not path.is_file():
        raise EvidenceError("source file must be a regular non-reparse file")
    size_bytes = path.stat().st_size
    if size_bytes <= 0 or size_bytes > MAX_FILE_BYTES:
        raise EvidenceError("source file size is outside the fixed boundary")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EvidenceError("source file must be UTF-8") from exc
    lines = text.splitlines()
    if not lines or len(lines) > MAX_LINES:
        raise EvidenceError("source line count is outside the fixed boundary")
    return text


def _load_json(path: Path, *, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_fixed_file(path, root=root))
    except json.JSONDecodeError as exc:
        raise EvidenceError("source JSON is invalid") from exc
    if not isinstance(value, dict):
        raise EvidenceError("source JSON must be an object")
    return value


def extract_evidence(*, source_root: Path) -> dict[str, object]:
    verdict = _load_json(
        source_root / "reports" / "qualification-verdict.json", root=source_root
    )
    diagnostic = _load_json(
        source_root / "evidence" / "model-preparation-diagnostic.json",
        root=source_root,
    )
    expected_verdict = {
        "schema_version": "qwen3-asr-r3-verdict/1",
        "status": "fail",
        "failure_code": "model_preparation_failed",
        "commit_sha": SOURCE_COMMIT_SHA,
        "run_id": SOURCE_RUN_ID,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    expected_diagnostic = {
        "schema_version": "qwen3-asr-model-preparation-failure/1",
        "status": "fail",
        "stage": "model_preparation",
        "kind": "snapshot_download_failed",
        "model": "asr",
        "exception_type": "SSLError",
        "failure_origin": "native_exit",
        "native_exit_code": 1,
        "log_present": True,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    for key, expected in expected_verdict.items():
        if verdict.get(key) != expected:
            raise EvidenceError(f"source verdict mismatch: {key}")
    for key, expected in expected_diagnostic.items():
        if diagnostic.get(key) != expected:
            raise EvidenceError(f"source diagnostic mismatch: {key}")

    log = _read_fixed_file(
        source_root / "logs" / "model-preparation.log", root=source_root
    )
    ssl_signals = sorted(
        name for name, pattern in SSL_SIGNALS.items() if pattern.search(log)
    )
    endpoint_families = sorted(
        name for name, pattern in ENDPOINT_FAMILIES.items() if pattern.search(log)
    )
    specific_signals = [name for name in ssl_signals if name != "ssl_error"]
    return {
        "schema_version": "qwen3-asr-model-ssl-evidence/1",
        "status": "evidence_complete" if specific_signals else "evidence_incomplete",
        "source_run_id": SOURCE_RUN_ID,
        "source_commit_sha": SOURCE_COMMIT_SHA,
        "failure_kind": "snapshot_download_failed",
        "model": "asr",
        "exception_type": "SSLError",
        "ssl_signals": ssl_signals,
        "endpoint_families": endpoint_families,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    qualification_root = os.environ.get("PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT")
    if not qualification_root:
        raise EvidenceError("qualification root is required")
    root = Path(qualification_root)
    if not root.is_absolute():
        raise EvidenceError("qualification root must be absolute")
    source_root = root / "runs" / SOURCE_RUN_ID
    result = extract_evidence(source_root=source_root)
    output_parent = args.output.parent.resolve(strict=True)
    output = (output_parent / args.output.name).resolve()
    if output.parent != output_parent:
        raise EvidenceError("output escaped fixed parent")
    output.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
