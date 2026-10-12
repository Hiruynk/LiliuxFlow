# SPDX-License-Identifier: Apache-2.0
"""Ephemeral inherited control-key descriptor; no environment or disk pathname.

The existing installation backend secret is domain-separated by the reviewed
native transport. This helper neither starts a process nor grants runtime trust.
The returned descriptor is read-only, owned mode0600, regular, unlinked, and
positioned at zero. The launcher passes only its numeric descriptor via pass_fds.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import tempfile

from common import DistributionError, private_directory
from mtp2_shared_transport import derive_native_key


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode), info.st_size)


@contextmanager
def inherited_control_key(backend_secret, directory):
    directory = private_directory(Path(directory))
    key = bytearray(derive_native_key(backend_secret).hex().encode('ascii'))
    writer = reader = None
    path = None
    try:
        writer, name = tempfile.mkstemp(prefix='.mtp2-control-', dir=directory)
        path = Path(name)
        os.fchmod(writer, 0o600)
        written = 0
        while written < len(key):
            n = os.write(writer, memoryview(key)[written:])
            if n <= 0:
                raise DistributionError('shared MTP2 control descriptor write failed')
            written += n
        original = os.fstat(writer)
        reader = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = os.fstat(reader)
        if (_identity(original) != _identity(opened) or opened.st_uid != os.getuid()
                or not stat.S_ISREG(opened.st_mode) or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_size != 64 or reader < 3
                or fcntl.fcntl(reader, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
            raise DistributionError('shared MTP2 control descriptor identity differs')
        os.unlink(path)
        path = None
        os.close(writer)
        writer = None
        yield reader
    finally:
        for index in range(len(key)):
            key[index] = 0
        try:
            if path is not None:
                # Only unlink the file this helper created; preserve replacements.
                try:
                    current = path.lstat()
                    owned = os.fstat(writer) if writer is not None else None
                    if owned is not None and _identity(current) == _identity(owned):
                        path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            try:
                if reader is not None:
                    os.close(reader)
            finally:
                if writer is not None:
                    os.close(writer)
