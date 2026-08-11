"""Local-only ASR development lab with explicit filesystem and evidence scope."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from scripts.asr_model_download import (
    AsrModelDownloadError,
    HF_ORIGIN_IP_ENV,
    _safe_snapshot_path,
    curl_snapshot_download,
    hugging_face_origin_override,
)


LAB_SCHEMA_VERSION = "asr-local-lab/1"
REPORT_SCHEMA_VERSION = "asr-local-development-report/2"
MARKER_NAME = ".ragpincheng-asr-lab.json"
ENGINE_NAMES = ("qwen3-asr", "whisperx")
ENGINE_PORTS = {"qwen3-asr": 18310, "whisperx": 18320}
FORBIDDEN_PORTS = (8100, 8200)
LAB_DIRECTORIES = (
    "envs/qwen3-asr",
    "envs/whisperx",
    "envs/lab-tools",
    "wheel-cache/qwen3-asr",
    "wheel-cache/whisperx",
    "models/qwen3-asr",
    "models/whisperx",
    "corpus",
    "tools",
    "caches/pip",
    "caches/huggingface",
    "caches/torch",
    "caches/torch-extensions",
    "caches/cuda",
    "caches/nltk",
    "caches/temp",
    "caches/pycache",
    "runs",
)
FOCUS_SCENARIOS = {
    "qwen3-asr": (
        "standard-codes",
        "noisy-bim-zh",
        "mixed-zh-en",
        "negative-control",
    ),
    "whisperx": (
        "standard-codes",
        "noisy-bim-zh",
        "negative-control",
    ),
}


LabConfigurationError = AsrModelDownloadError


def _canonical(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _logical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    reparse_flag = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _assert_no_reparse_components(path: Path, label: str) -> None:
    logical = _logical_absolute(path)
    current = Path(logical.anchor)
    for part in logical.parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            break
        if _is_reparse_point(current):
            raise LabConfigurationError(f"{label} contains a reparse point: {current}")


def _is_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def ensure_unprivileged() -> None:
    if _is_elevated():
        raise LabConfigurationError("local ASR lab must run without elevation")


def validate_lab_root(lab_root: Path, source_root: Path) -> tuple[Path, Path]:
    logical_lab = _logical_absolute(lab_root)
    _assert_no_reparse_components(logical_lab, "lab root")
    lab = _canonical(logical_lab)
    source = _canonical(source_root)
    if lab == Path(lab.anchor):
        raise LabConfigurationError("lab root must not be a drive root")
    home = _canonical(Path.home())
    if lab in {home, source}:
        raise LabConfigurationError("lab root must not be the home or source root")
    if _is_within(lab, source) or _is_within(source, lab):
        raise LabConfigurationError("lab and source roots must be disjoint")
    return lab, source


def require_inside_lab(path: Path, lab_root: Path, label: str) -> Path:
    logical = _logical_absolute(path)
    logical_lab = _logical_absolute(lab_root)
    if not _is_within(logical, logical_lab):
        raise LabConfigurationError(f"{label} must remain inside the lab root")
    _assert_no_reparse_components(logical, label)
    resolved = _canonical(logical)
    lab = _canonical(logical_lab)
    if not _is_within(resolved, lab):
        raise LabConfigurationError(f"{label} must remain inside the lab root")
    return resolved


def validate_managed_tree(lab_root: Path) -> None:
    for relative in LAB_DIRECTORIES:
        path = lab_root / relative
        if not path.is_dir():
            raise LabConfigurationError(
                f"managed lab directory is missing or unsafe: {relative}"
            )
        _assert_no_reparse_components(path, f"managed lab directory {relative}")


def _marker_path(lab_root: Path) -> Path:
    return lab_root / MARKER_NAME


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def initialize_lab(lab_root: Path, source_root: Path) -> dict[str, object]:
    ensure_unprivileged()
    lab, source = validate_lab_root(lab_root, source_root)
    marker = _marker_path(lab)
    if marker.exists():
        load_marker(lab, source)
    else:
        if lab.exists() and any(lab.iterdir()):
            raise LabConfigurationError(
                "refusing to adopt a non-empty directory without a lab marker"
            )
        lab.mkdir(parents=True, exist_ok=True)
        _write_json(
            marker,
            {
                "schema_version": LAB_SCHEMA_VERSION,
                "source_root": str(source),
            },
        )
    for relative in LAB_DIRECTORIES:
        (lab / relative).mkdir(parents=True, exist_ok=True)
    validate_managed_tree(lab)
    return {
        "schema_version": LAB_SCHEMA_VERSION,
        "status": "initialized",
        "lab_root": str(lab),
        "source_root": str(source),
    }


def load_marker(lab_root: Path, source_root: Path) -> dict[str, object]:
    lab, source = validate_lab_root(lab_root, source_root)
    marker = _marker_path(lab)
    if not marker.is_file() or marker.is_symlink():
        raise LabConfigurationError("local ASR lab marker is missing")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload != {
        "schema_version": LAB_SCHEMA_VERSION,
        "source_root": str(source),
    }:
        raise LabConfigurationError("local ASR lab marker does not match source root")
    validate_managed_tree(lab)
    return payload


def process_environment(lab_root: Path) -> dict[str, str]:
    lab = _canonical(lab_root)
    return {
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(lab / "caches/pycache"),
        "PIP_CACHE_DIR": str(lab / "caches/pip"),
        "PIP_CONFIG_FILE": "NUL" if os.name == "nt" else "/dev/null",
        "HF_HOME": str(lab / "caches/huggingface"),
        "HF_HUB_CACHE": str(lab / "caches/huggingface/hub"),
        "TORCH_HOME": str(lab / "caches/torch"),
        "TORCH_EXTENSIONS_DIR": str(lab / "caches/torch-extensions"),
        "CUDA_CACHE_PATH": str(lab / "caches/cuda"),
        "NLTK_DATA": str(lab / "caches/nltk"),
        "TEMP": str(lab / "caches/temp"),
        "TMP": str(lab / "caches/temp"),
    }


def _port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.2)
        return client.connect_ex(("127.0.0.1", port)) == 0


def _gpu_identity() -> dict[str, object]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total,memory.free",
        "--format=csv,noheader,nounits",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {"available": False}
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        return {"available": False, "reason": "single-gpu-required"}
    values = [item.strip() for item in rows[0].split(",")]
    if len(values) != 4:
        return {"available": False, "reason": "invalid-nvidia-smi-output"}
    return {
        "available": True,
        "name": values[0],
        "driver_version": values[1],
        "memory_total_mib": int(values[2]),
        "memory_free_mib": int(values[3]),
    }


def _venv_python(lab_root: Path, engine: str) -> Path:
    executable = "python.exe" if os.name == "nt" else "python"
    scripts = "Scripts" if os.name == "nt" else "bin"
    return lab_root / "envs" / engine / scripts / executable


def _model_status(source_root: Path, lab_root: Path, engine: str) -> dict[str, object]:
    sys.path.insert(0, str(source_root))
    try:
        from asr_service.model_cache import (
            validate_qwen3_aligner_cache,
            validate_qwen3_asr_cache,
            validate_whisperx_align_cache,
            validate_whisperx_cache,
        )
        if engine == "qwen3-asr":
            root = lab_root / "models/qwen3-asr"
            asr = validate_qwen3_asr_cache(
                root,
                root
                / "Qwen3-ASR-0.6B"
                / "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
                / "model-manifest.json",
            )
            align = validate_qwen3_aligner_cache(
                root,
                root
                / "Qwen3-ForcedAligner-0.6B"
                / "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
                / "model-manifest.json",
            )
        else:
            from scripts.run_whisperx_cuda_smoke import (
                ALIGN_RELATIVE_PATH,
                ASR_RELATIVE_PATH,
            )
            root = lab_root / "models/whisperx"
            asr = validate_whisperx_cache(
                root, root / ASR_RELATIVE_PATH / "model-manifest.json"
            )
            align = validate_whisperx_align_cache(
                root, root / ALIGN_RELATIVE_PATH / "model-manifest.json"
            )
        return {
            "available": bool(asr.available and align.available),
            "asr_reason": asr.reason_code,
            "aligner_reason": align.reason_code,
        }
    finally:
        if sys.path and sys.path[0] == str(source_root):
            sys.path.pop(0)


def doctor(lab_root: Path, source_root: Path) -> dict[str, object]:
    lab, source = validate_lab_root(lab_root, source_root)
    load_marker(lab, source)
    usage = shutil.disk_usage(lab)
    engines = {}
    for engine in ENGINE_NAMES:
        engines[engine] = {
            "venv_available": _venv_python(lab, engine).is_file(),
            "models": _model_status(source, lab, engine),
            "port": ENGINE_PORTS[engine],
            "port_listening": _port_is_listening(ENGINE_PORTS[engine]),
        }
    return {
        "schema_version": "asr-local-lab-doctor/1",
        "status": "blocked" if _is_elevated() else "complete",
        "elevated": _is_elevated(),
        "disk_free_gib": round(usage.free / 1024**3, 2),
        "gpu": _gpu_identity(),
        "forbidden_ports": {
            str(port): _port_is_listening(port) for port in FORBIDDEN_PORTS
        },
        "engines": engines,
    }


def _first_samples_for_scenarios(samples: Iterable[object], scenarios: Iterable[str]):
    result = []
    for scenario in scenarios:
        match = next(item for item in samples if item.scenario == scenario)
        result.append(match)
    return tuple(result)


def select_development_samples(manifest, engine: str, mode: str):
    if mode == "full":
        return manifest
    if mode == "smoke":
        samples = _first_samples_for_scenarios(manifest.samples, ("clear-zh",))
    elif mode == "focus":
        samples = _first_samples_for_scenarios(
            manifest.samples, FOCUS_SCENARIOS[engine]
        )
    else:
        raise LabConfigurationError("unsupported local evaluation mode")
    return replace(manifest, samples=samples)


def _development_report(
    *, engine: str, mode: str, candidate_ids: list[str], result: dict[str, object]
) -> dict[str, object]:
    gate_status = result.get("status", "unknown")
    if gate_status not in {"pass", "fail"}:
        raise LabConfigurationError("local evaluation produced an invalid gate status")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "complete",
        "scope": "local-development",
        "qualification_eligible": False,
        "engine": engine,
        "mode": mode,
        "candidate_ids": candidate_ids,
        "gate_status": gate_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }


def _evaluation_exit_code(command: str, result: dict[str, object]) -> int:
    if command.startswith("evaluate-"):
        return 0 if result.get("gate_status") == "pass" else 2
    return 0 if result.get("status") in {"initialized", "complete"} else 2


def _whisperx_local_gate_status(
    mode: str, reports: dict[str, dict[str, object]]
) -> tuple[str, str]:
    target = "baseline" if mode == "smoke" else "full-decode"
    status = reports[target].get("status")
    if status not in {"pass", "fail"}:
        raise LabConfigurationError("WhisperX target candidate has invalid status")
    return target, str(status)


def evaluate_qwen(
    *,
    manifest_path: Path,
    qualification_root: Path,
    mode: str,
    candidate_id: str,
    base_url: str,
    token: str,
    timeout_ms: int,
) -> dict[str, object]:
    from scripts import run_qwen3_asr_qualification as qualification

    manifest = qualification.load_manifest(
        manifest_path, root=qualification_root, manifest_source="legacy"
    )
    selected = select_development_samples(manifest, "qwen3-asr", mode)
    result = qualification.run_qualification(
        selected,
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        candidate_id=candidate_id,
        repetitions=2 if mode == "full" else 1,
        verify_tls=False,
    )
    return _development_report(
        engine="qwen3-asr",
        mode=mode,
        candidate_ids=[candidate_id],
        result=result,
    )


def _whisperx_configs(candidate_ids: tuple[str, ...]):
    from asr_service.engine_protocol import (
        WHISPERX_FULL_DECODE_SERVICE_CONFIG,
        WHISPERX_HOTWORDS_SERVICE_CONFIG,
        WHISPERX_SERVICE_CONFIG,
    )
    values = {
        "baseline": WHISPERX_SERVICE_CONFIG,
        "hotwords": WHISPERX_HOTWORDS_SERVICE_CONFIG,
        "full-decode": WHISPERX_FULL_DECODE_SERVICE_CONFIG,
    }
    return tuple((candidate, values[candidate]) for candidate in candidate_ids)


def evaluate_whisperx(
    *,
    manifest_path: Path,
    qualification_root: Path,
    model_root: Path,
    nltk_root: Path,
    mode: str,
    timeout_ms: int,
) -> dict[str, object]:
    os.environ.update(
        {
            "NLTK_DATA": str(nltk_root),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    import ctranslate2
    import torch
    from scripts import run_whisperx_qualification as qualification

    if not torch.cuda.is_available():
        raise RuntimeError("torch CUDA unavailable")
    if ctranslate2.get_cuda_device_count() != 1:
        raise RuntimeError("unexpected CTranslate2 CUDA device count")
    if "float16" not in ctranslate2.get_supported_compute_types("cuda"):
        raise RuntimeError("CTranslate2 FP16 unavailable")
    manifest = qualification.load_manifest(
        manifest_path, root=qualification_root, manifest_source="legacy"
    )
    selected = select_development_samples(manifest, "whisperx", mode)
    qualification._ENGINE = qualification._build_engine(model_root)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    if mode == "full":
        result = qualification.run_candidate_matrix(selected, timeout_ms=timeout_ms)
        candidates = ["baseline", "hotwords", "full-decode"]
    else:
        candidate_ids = (
            ("baseline",) if mode == "smoke" else ("baseline", "full-decode")
        )
        reports = {}
        for candidate_id, config in _whisperx_configs(candidate_ids):
            report = qualification.run_qualification(
                selected,
                timeout_ms=timeout_ms,
                service_config=config,
                repetitions=1,
            )
            report["candidate_id"] = candidate_id
            reports[candidate_id] = report
        target_candidate, gate_status = _whisperx_local_gate_status(mode, reports)
        result = {
            "schema_version": "whisperx-local-candidate-matrix/1",
            "status": gate_status,
            "target_candidate": target_candidate,
            "sample_count": len(selected.samples),
            "candidates": reports,
        }
        candidates = list(candidate_ids)
    result.update(
        {
            "torch_version": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "gpu_name": torch.cuda.get_device_name(0),
            "peak_gpu_memory_mib": round(
                torch.cuda.max_memory_allocated() / (1024 * 1024), 2
            ),
        }
    )
    return _development_report(
        engine="whisperx", mode=mode, candidate_ids=candidates, result=result
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "doctor"):
        command = commands.add_parser(name)
        command.add_argument("--lab-root", required=True, type=Path)
        command.add_argument("--source-root", required=True, type=Path)
        command.add_argument("--report", type=Path)
    qwen = commands.add_parser("evaluate-qwen")
    qwen.add_argument("--lab-root", required=True, type=Path)
    qwen.add_argument("--source-root", required=True, type=Path)
    qwen.add_argument("--manifest", required=True, type=Path)
    qwen.add_argument("--qualification-root", required=True, type=Path)
    qwen.add_argument("--mode", choices=("smoke", "focus", "full"), required=True)
    qwen.add_argument(
        "--candidate-id",
        choices=("forced-chinese-baseline", "auto-zh-en"),
        required=True,
    )
    qwen.add_argument("--base-url", required=True)
    qwen.add_argument("--token", required=True)
    qwen.add_argument("--timeout-ms", type=int, default=600_000)
    qwen.add_argument("--report", required=True, type=Path)
    whisperx = commands.add_parser("evaluate-whisperx")
    whisperx.add_argument("--lab-root", required=True, type=Path)
    whisperx.add_argument("--source-root", required=True, type=Path)
    whisperx.add_argument("--manifest", required=True, type=Path)
    whisperx.add_argument("--qualification-root", required=True, type=Path)
    whisperx.add_argument("--model-root", required=True, type=Path)
    whisperx.add_argument("--nltk-root", required=True, type=Path)
    whisperx.add_argument(
        "--mode", choices=("smoke", "focus", "full"), required=True
    )
    whisperx.add_argument("--timeout-ms", type=int, default=600_000)
    whisperx.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    lab, source = validate_lab_root(args.lab_root, args.source_root)
    if args.report is not None:
        args.report = require_inside_lab(args.report, lab, "report")
    if args.command == "init":
        result = initialize_lab(lab, source)
    elif args.command == "doctor":
        result = doctor(lab, source)
    elif args.command == "evaluate-qwen":
        ensure_unprivileged()
        load_marker(lab, source)
        manifest_path = require_inside_lab(args.manifest, lab, "manifest")
        qualification_root = require_inside_lab(
            args.qualification_root, lab, "qualification root"
        )
        if args.base_url != f"http://127.0.0.1:{ENGINE_PORTS['qwen3-asr']}":
            raise LabConfigurationError("Qwen local service URL is not allowlisted")
        result = evaluate_qwen(
            manifest_path=manifest_path,
            qualification_root=qualification_root,
            mode=args.mode,
            candidate_id=args.candidate_id,
            base_url=args.base_url,
            token=args.token,
            timeout_ms=args.timeout_ms,
        )
    else:
        ensure_unprivileged()
        load_marker(lab, source)
        result = evaluate_whisperx(
            manifest_path=require_inside_lab(args.manifest, lab, "manifest"),
            qualification_root=require_inside_lab(
                args.qualification_root, lab, "qualification root"
            ),
            model_root=require_inside_lab(args.model_root, lab, "model root"),
            nltk_root=require_inside_lab(args.nltk_root, lab, "NLTK root"),
            mode=args.mode,
            timeout_ms=args.timeout_ms,
        )
    if args.report is not None:
        _write_json(args.report, result)
    output = {"status": result["status"]}
    if "gate_status" in result:
        output["gate_status"] = result["gate_status"]
    print(json.dumps(output, sort_keys=True))
    return _evaluation_exit_code(args.command, result)


if __name__ == "__main__":
    raise SystemExit(main())
