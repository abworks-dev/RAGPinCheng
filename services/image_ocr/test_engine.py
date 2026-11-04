"""Run with the service's isolated dependencies, never with customer images."""
from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from services.image_ocr import app as service


def test_real_bundled_cpu_engine_recognizes_synthetic_text(tmp_path):
    image = Image.new("RGB", (1100, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((35, 40), "PROJECT ALPHA 110", fill="black", font=ImageFont.load_default(size=64))
    path = tmp_path / "synthetic.png"
    image.save(path)
    result = asyncio.run(service.run_worker(path))
    normalized = result["text"].replace(" ", "").upper()
    assert "PROJECT" in normalized
    assert "110" in normalized
    assert result["blocks"]
    assert result["engine"] == "rapidocr-onnxruntime/1.4.4"
    assert result["model_version"].startswith("bundled-sha256:")


def test_real_bundled_engine_preserves_chinese_utf8():
    # Synthetic browser canvas: black 48px text on white, no customer content.
    path = Path(__file__).with_name("synthetic-chinese.png")
    result = asyncio.run(service.run_worker(path))
    assert "消防通道" in result["text"].replace(" ", "")
    assert "110" in result["text"]


def test_real_bundled_engine_accepts_no_text(tmp_path):
    image = Image.new("RGB", (400, 180), "white")
    path = tmp_path / "synthetic-blank.png"
    image.save(path)
    result = asyncio.run(service.run_worker(path))
    assert result["text"] == ""
    assert result["blocks"] == []
