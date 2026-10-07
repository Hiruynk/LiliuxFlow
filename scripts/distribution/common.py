"""Small, portable filesystem primitives. No production paths or credentials."""
from __future__ import annotations
import json
import os
from pathlib import Path, PurePosixPath
import stat

class DistributionError(ValueError):
    pass

def no_symlinks(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for item in [absolute, *absolute.parents]:
        if item.is_symlink():
            raise DistributionError('path traverses a symlink')
    return absolute

def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or '\\' in value or '\x00' in value or path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise DistributionError('unsafe relative path')
    return path

def read_object(path: Path) -> dict:
    no_symlinks(path)
    if path.stat().st_size > 16 * 1024**2:
        raise DistributionError('JSON file exceeds size cap')
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict):
        raise DistributionError('expected JSON object')
    return result

def private_directory(path: Path) -> Path:
    path = no_symlinks(path)
    if path.exists():
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DistributionError('data directory must be owned and mode 0700')
    else:
        missing = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
    return path

def write_new(path: Path, data: bytes, mode: int = 0o600):
    no_symlinks(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(data)

def write_json_new(path: Path, value: dict):
    write_new(path, (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode())
