"""Resolve directory aliases before binding or comparing execution roots."""

import os
from pathlib import Path


class DirectoryError(ValueError):
    pass


def canonical_directory(value: str) -> str:
    path = Path(value)
    if not value.strip() or not path.is_absolute():
        raise DirectoryError("请选择已存在的绝对目录")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise DirectoryError("项目路径必须是目录")
        return os.path.normcase(str(resolved))
    except (OSError, RuntimeError, ValueError) as error:
        raise DirectoryError("项目目录不存在或无法访问") from error


def directories_overlap(left: str, right: str) -> bool:
    try:
        return os.path.commonpath((left, right)) in (left, right)
    except ValueError:  # Different Windows drives cannot overlap.
        return False
