#!/usr/bin/env python3
"""Atomically update or restore the non-secret transcription admission setting."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.transcription_admission_config import (
    restore_transcription_admission,
    set_transcription_admission,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("--env-file", required=True, type=Path)
    set_parser.add_argument("--state-file", required=True, type=Path)
    set_parser.add_argument("--value", required=True)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--env-file", required=True, type=Path)
    restore_parser.add_argument("--state-file", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "set":
        set_transcription_admission(args.env_file, args.state_file, args.value)
    else:
        restore_transcription_admission(args.env_file, args.state_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
