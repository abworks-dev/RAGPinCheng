from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def write_stage(path: Path, name: str) -> None:
    line = f"GPU_RERANKER_STAGE stage={name} at={datetime.now(timezone.utc).isoformat()}"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
    print(line, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load the GPU embedding and reranker models for a bounded diagnostic."
    )
    parser.add_argument("--stage-file", required=True, type=Path)
    parser.add_argument(
        "--reranker-precision",
        required=True,
        choices=("fp16", "fp32"),
    )
    args = parser.parse_args()
    stage_file = args.stage_file

    write_stage(stage_file, "python_entry")
    write_stage(stage_file, "torch_import_start")
    import torch

    write_stage(stage_file, "torch_import_complete")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in the diagnostic task")

    write_stage(stage_file, "config_import_start")
    from FlagEmbedding import BGEM3FlagModel, FlagReranker
    from services.gpu_service.config import EMBED_MODEL, RERANKER_MODEL

    write_stage(stage_file, "config_import_complete")
    print(f"GPU_RERANKER_DEVICE name={torch.cuda.get_device_name(0)}", flush=True)

    write_stage(stage_file, "embed_start")
    embed_model = BGEM3FlagModel(EMBED_MODEL, devices="cuda", use_fp16=True)
    torch.cuda.synchronize()
    write_stage(stage_file, "embed_complete")

    write_stage(stage_file, "embed_inference_start")
    embed_output = embed_model.encode(
        ["GPU runtime qualification"],
        return_dense=True,
        return_sparse=True,
    )
    if embed_output["dense_vecs"].shape != (1, 1024):
        raise RuntimeError("Embedding qualification returned an unexpected shape")
    torch.cuda.synchronize()
    write_stage(stage_file, "embed_inference_complete")

    write_stage(stage_file, "reranker_start")
    reranker = FlagReranker(
        RERANKER_MODEL,
        devices="cuda",
        use_fp16=args.reranker_precision == "fp16",
    )
    torch.cuda.synchronize()
    write_stage(stage_file, "reranker_complete")

    write_stage(stage_file, "reranker_inference_start")
    scores = reranker.compute_score(
        [["qualification", "candidate passage"]],
        normalize=True,
    )
    if len(scores) != 1:
        raise RuntimeError("Reranker qualification returned an unexpected score count")
    torch.cuda.synchronize()
    write_stage(stage_file, "reranker_inference_complete")

    del reranker
    del embed_model
    torch.cuda.empty_cache()
    write_stage(stage_file, "complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
