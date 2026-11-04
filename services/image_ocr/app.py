"""Private, bounded CPU image OCR. No cloud clients or runtime model downloads."""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from starlette.requests import Request as FormRequest

app = FastAPI(title="Local Image OCR", version="image-ocr/1")
MAX_BYTES = int(os.getenv("IMAGE_OCR_MAX_BYTES", "33554432"))
MAX_PIXELS = int(os.getenv("IMAGE_OCR_MAX_PIXELS", "80000000"))
TIMEOUT = int(os.getenv("IMAGE_OCR_PROCESS_TIMEOUT_SECONDS", "25"))
TOKEN = os.getenv("IMAGE_OCR_TOKEN", "")
_SEMAPHORE = asyncio.Semaphore(1)
_WORKER = Path(__file__).with_name("worker.py")


def _authorize(request: Request) -> None:
    if not TOKEN:
        raise HTTPException(503, detail="image_ocr_not_configured")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {TOKEN}".encode("utf-8")):
        raise HTTPException(401, detail="image_ocr_unauthorized")


def validate_image(data: bytes) -> None:
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, detail="image_ocr_file_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"}:
                    raise HTTPException(415, detail="image_ocr_unsupported_type")
                if image.width * image.height > MAX_PIXELS or max(image.size) > 32768:
                    raise HTTPException(413, detail="image_ocr_pixel_limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise HTTPException(415, detail="image_ocr_multiframe_unsupported")
                image.verify()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(413, detail="image_ocr_pixel_limit") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise HTTPException(422, detail="image_ocr_invalid_image") from exc


async def _read_file(request: Request) -> bytes:
    # Authenticate before parsing multipart. Bound the entire body, including file spooling.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES + 65536:
            raise HTTPException(413, detail="image_ocr_file_too_large")
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    clone = FormRequest(request.scope, receive)
    try:
        async with clone.form(max_files=1, max_fields=0, max_part_size=MAX_BYTES) as form:
            file = form.get("file")
            if file is None or not hasattr(file, "read"):
                raise HTTPException(422, detail="image_ocr_file_required")
            data = await file.read(MAX_BYTES + 1)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, detail="image_ocr_invalid_multipart") from exc
    validate_image(data)
    return data


async def run_worker(path: Path) -> dict:
    env = os.environ.copy()
    # Explicit CPU limits. The container has no network; the worker only uses bundled models.
    env.update({"OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(_WORKER), str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(504, detail="image_ocr_timeout") from exc
    if proc.returncode != 0:
        raise HTTPException(422, detail="image_ocr_engine_failed")
    try:
        result = json.loads(stdout)
        if not isinstance(result.get("text"), str) or not isinstance(result.get("blocks"), list):
            raise ValueError("invalid result")
        if len(result["text"]) > 200000 or len(result["blocks"]) > 10000:
            raise HTTPException(413, detail="image_ocr_result_limit")
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(502, detail="image_ocr_invalid_result") from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if TOKEN else "unconfigured", "api_version": "image-ocr/1"}


@app.post("/v1/ocr")
async def ocr(request: Request) -> dict:
    _authorize(request)
    try:
        await asyncio.wait_for(_SEMAPHORE.acquire(), timeout=0.1)
    except asyncio.TimeoutError as exc:
        raise HTTPException(429, detail="image_ocr_busy") from exc
    try:
        try:
            data = await asyncio.wait_for(_read_file(request), timeout=10)
        except asyncio.TimeoutError as exc:
            raise HTTPException(408, detail="image_ocr_upload_timeout") from exc
        with tempfile.TemporaryDirectory(prefix="image-ocr-") as directory:
            path = Path(directory) / "source.image"
            path.write_bytes(data)
            return await run_worker(path)
    finally:
        _SEMAPHORE.release()
