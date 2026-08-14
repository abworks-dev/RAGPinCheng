"""Compatibility namespace for the legacy GPU service module path."""

from pathlib import Path


__path__ = [str(Path(__file__).resolve().parents[1] / "services" / "gpu_service")]
