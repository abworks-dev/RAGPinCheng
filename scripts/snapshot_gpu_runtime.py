from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path


PACKAGES = (
    ("torch", "torch"),
    ("FlagEmbedding", "FlagEmbedding"),
    ("transformers", "transformers"),
    ("tokenizers", "tokenizers"),
    ("sentence-transformers", "sentence_transformers"),
    ("accelerate", "accelerate"),
    ("safetensors", "safetensors"),
    ("huggingface-hub", "huggingface_hub"),
    ("numpy", "numpy"),
)


def package_record(distribution: str, module_name: str) -> dict[str, object]:
    record: dict[str, object] = {
        "distribution": distribution,
        "module": module_name,
    }
    try:
        record["version"] = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        record["version"] = None
    try:
        module = importlib.import_module(module_name)
        record["module_path"] = str(Path(module.__file__).resolve())
    except Exception as exc:  # Diagnostic output, not an application code path.
        record["import_error"] = f"{type(exc).__name__}: {exc}"
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    payload: dict[str, object] = {
        "schema_version": 1,
        "python": {
            "version": platform.python_version(),
            "executable": str(Path(sys.executable).resolve()),
            "prefix": str(Path(sys.prefix).resolve()),
        },
        "packages": [package_record(*package) for package in PACKAGES],
    }

    try:
        import torch

        payload["cuda"] = {
            "available": torch.cuda.is_available(),
            "torch_cuda_version": torch.version.cuda,
            "device_count": torch.cuda.device_count(),
            "device_names": [
                torch.cuda.get_device_name(index)
                for index in range(torch.cuda.device_count())
            ],
        }
    except Exception as exc:
        payload["cuda"] = {"probe_error": f"{type(exc).__name__}: {exc}"}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
