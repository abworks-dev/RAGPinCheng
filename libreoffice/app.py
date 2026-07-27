"""LibreOffice conversion service.

Provides two endpoints:
- POST /v1/recalculate: recalculate XLSX formulas → return XLSX with cached values
- POST /v1/convert: convert Office file to PDF
- GET /health: health check

Runs as a standalone container, accessed via HTTP by the backend.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("libreoffice")

app = FastAPI(title="LibreOffice Conversion Service", version="1")

# Limits
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
CONVERSION_TIMEOUT = 120  # seconds
MAX_CONCURRENT = 2

# Semaphore to limit concurrent LibreOffice processes
import asyncio
_concurrency_sem = asyncio.Semaphore(MAX_CONCURRENT)

# Supported extensions
_SUPPORTED_INPUT = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}
_RECALC_EXTS = {".xlsx", ".xls"}


async def _run_libreoffice(args: list[str], timeout: int = CONVERSION_TIMEOUT) -> None:
    """Run a LibreOffice command with timeout and resource limits."""
    env = os.environ.copy()
    env["HOME"] = "/tmp/libreoffice-home"

    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"LibreOffice timed out after {timeout}s")

    if proc.returncode != 0:
        err_text = (stderr or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LibreOffice exit code {proc.returncode}: {err_text}")


@app.get("/health")
async def health():
    """Health check — verify LibreOffice is installed and responsive."""
    try:
        result = subprocess.run(
            ["libreoffice", "--headless", "--version"],
            capture_output=True, text=True, timeout=15,
            env={"HOME": "/tmp/libreoffice-home"},
        )
        version = result.stdout.strip() or result.stderr.strip() or "unknown"
        return {"status": "ok", "version": version}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LibreOffice unavailable: {exc}")


@app.post("/v1/recalculate")
async def recalculate(file: UploadFile):
    """Recalculate XLSX formulas and return a file with cached values.

    LibreOffice opens the file, recalculates all formulas, and saves it.
    The returned XLSX has cached values that openpyxl can read with
    ``data_only=True``.
    """
    ext = Path(file.filename or "input.xlsx").suffix.lower()
    if ext not in _RECALC_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    work_dir = Path(f"/data/input/{uuid.uuid4().hex}")
    output_dir = Path(f"/data/output/{uuid.uuid4().hex}")
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = work_dir / (file.filename or "input.xlsx")

    try:
        input_path.write_bytes(content)

        async with _concurrency_sem:
            await _run_libreoffice([
                "libreoffice", "--headless", "--norestore",
                "--calc",  # Force Calc mode for formula recalculation
                f"--outdir", str(output_dir),
                str(input_path),
            ])

        # Find the output file
        out_files = list(output_dir.iterdir())
        if not out_files:
            raise HTTPException(status_code=500, detail="LibreOffice produced no output")

        output_path = out_files[0]
        return FileResponse(
            path=str(output_path),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=file.filename or "output.xlsx",
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("recalculation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        # Clean up temp files
        for p in [input_path, output_dir, work_dir]:
            if p.exists():
                import shutil
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)


@app.post("/v1/convert")
async def convert(file: UploadFile, target_format: str = "pdf"):
    """Convert an Office file to PDF.

    Supports: .docx, .pptx, .xlsx → .pdf
    """
    ext = Path(file.filename or "input").suffix.lower()
    if ext not in _SUPPORTED_INPUT:
        raise HTTPException(status_code=400, detail=f"Unsupported input format: {ext}")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    work_dir = Path(f"/data/input/{uuid.uuid4().hex}")
    output_dir = Path(f"/data/output/{uuid.uuid4().hex}")
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = work_dir / (file.filename or f"input{ext}")

    try:
        input_path.write_bytes(content)

        async with _concurrency_sem:
            await _run_libreoffice([
                "libreoffice", "--headless", "--norestore",
                "--convert-to", target_format,
                "--outdir", str(output_dir),
                str(input_path),
            ])

        out_files = list(output_dir.iterdir())
        if not out_files:
            raise HTTPException(status_code=500, detail="Conversion produced no output")

        output_path = out_files[0]
        media_type = "application/pdf" if target_format == "pdf" else "application/octet-stream"
        return FileResponse(
            path=str(output_path),
            media_type=media_type,
            filename=output_path.name,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("conversion failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        for p in [input_path, output_dir, work_dir]:
            if p.exists():
                import shutil
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8101)