"""One request per process: terminate the process to bound native OCR runtime."""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path


def recognize(path: Path) -> dict:
    # Import and engine diagnostics must never corrupt the JSON IPC response.
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import rapidocr_onnxruntime
        from PIL import Image, ImageOps
        from rapidocr_onnxruntime import RapidOCR

        model_dir = Path(rapidocr_onnxruntime.__file__).parent / "models"
        models = sorted(model_dir.glob("*.onnx"))
        if len(models) < 3:
            raise RuntimeError("bundled_ocr_models_missing")
        model_hash = hashlib.sha256()
        for model in models:
            model_hash.update(model.name.encode("utf-8"))
            model_hash.update(model.read_bytes())
        with Image.open(path) as source:
            source = ImageOps.exif_transpose(source)
            width, height = source.size
            source = source.convert("RGB")
            source.thumbnail((4096, 4096))
            scale_x, scale_y = width / source.width, height / source.height
            pixels = np.asarray(source)[:, :, ::-1].copy()  # OpenCV uses BGR.
        engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        recognized, _elapsed = engine(pixels)
    blocks = []
    total_chars = 0
    for box, text, confidence in recognized or []:
        text = str(text).strip()
        if not text:
            continue
        total_chars += len(text) + 1
        if len(blocks) >= 10000 or total_chars > 200000:
            raise RuntimeError("ocr_result_limit")
        blocks.append({
            "text": text,
            "confidence": float(confidence),
            "box": [[float(x) * scale_x, float(y) * scale_y] for x, y in box],
        })
    return {
        "text": "\n".join(block["text"] for block in blocks),
        "blocks": blocks,
        "engine": "rapidocr-onnxruntime/1.4.4",
        "model_version": f"bundled-sha256:{model_hash.hexdigest()}",
    }


if __name__ == "__main__":
    result = recognize(Path(sys.argv[1]))
    # IPC encoding is explicit; Windows' console encoding must not corrupt Chinese text.
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
