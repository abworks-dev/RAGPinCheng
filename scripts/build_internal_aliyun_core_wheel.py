"""Build and validate the fixed internal aliyun-python-sdk-core wheel."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_internal_oss2_wheel as _builder


_builder.PACKAGE_NAME = "aliyun-python-sdk-core"
_builder.PACKAGE_VERSION = "2.16.0"
_builder.PACKAGE_REQUIREMENT = "aliyun-python-sdk-core==2.16.0"
_builder.SDIST_FILE_NAME = "aliyun-python-sdk-core-2.16.0.tar.gz"
_builder.SDIST_SHA256 = "651caad597eb39d4fad6cf85133dffe92837d53bdf62db9d8f37dab6508bb8f9"
_builder.SDIST_SIZE_BYTES = 449555
_builder.SDIST_URL = (
    "https://files.pythonhosted.org/packages/3e/09/"
    "da9f58eb38b4fdb97ba6523274fbf445ef6a06be64b433693da8307b4bec/"
    "aliyun-python-sdk-core-2.16.0.tar.gz"
)
_builder._ROOT = f"{_builder.PACKAGE_NAME}-{_builder.PACKAGE_VERSION}"


def main(argv: Sequence[str] | None = None) -> int:
    return _builder.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
