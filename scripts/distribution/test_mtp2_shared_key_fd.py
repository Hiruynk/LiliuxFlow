# SPDX-License-Identifier: Apache-2.0
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
from mtp2_shared_key_fd import inherited_control_key
from mtp2_shared_transport import derive_native_key


class KeyDescriptorTests(unittest.TestCase):
    SECRET = b'synthetic-backend-only-' + b'x' * 32

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)

    def test_native_contract_and_closed_after_context(self):
        with inherited_control_key(self.SECRET, self.root) as fd:
            info = os.fstat(fd)
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual((info.st_uid, stat.S_IMODE(info.st_mode), info.st_size, info.st_nlink),
                             (os.getuid(), 0o600, 64, 0))
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            self.assertFalse(os.get_inheritable(fd))
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(os.read(fd, 65), derive_native_key(self.SECRET).hex().encode())
            with self.assertRaises(OSError):
                os.write(fd, b'x')
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_real_child_explicit_pass_fds_only(self):
        with inherited_control_key(self.SECRET, self.root) as fd:
            script = 'import os,sys; b=os.read(int(sys.argv[1]),65); assert len(b)==64; assert os.fstat(int(sys.argv[1])).st_nlink==0'
            result = subprocess.run([sys.executable, '-c', script, str(fd)], pass_fds=(fd,),
                                    env={'PATH': '/usr/bin:/bin'}, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b'')

    def test_exception_closes_and_leaves_no_secret_file(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            with inherited_control_key(self.SECRET, self.root) as fd:
                raise RuntimeError('synthetic')
        self.assertEqual(list(self.root.iterdir()), [])
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_write_failure_cleans_own_file(self):
        with patch('mtp2_shared_key_fd.os.write', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                with inherited_control_key(self.SECRET, self.root):
                    self.fail('write failure yielded a descriptor')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrong_permission_and_symlink_directory_reject(self):
        self.root.chmod(0o755)
        with self.assertRaises(DistributionError):
            with inherited_control_key(self.SECRET, self.root):
                self.fail('unsafe directory accepted')
        self.root.chmod(0o700)
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(DistributionError):
            with inherited_control_key(self.SECRET, link):
                self.fail('symlink accepted')

    def test_bad_secret_no_file(self):
        for secret in ('', None, b'x' * 8):
            with self.subTest(secret_type=type(secret).__name__), self.assertRaises(DistributionError):
                with inherited_control_key(secret, self.root):
                    self.fail('bad secret accepted')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_replaced_temporary_file_preserved_on_failure(self):
        real_open = os.open
        replacement = []
        def changed(path, flags, *args, **kwargs):
            if flags & os.O_NOFOLLOW and flags & os.O_ACCMODE == os.O_RDONLY:
                p = Path(path)
                p.unlink()
                p.write_bytes(b'UNRELATED_REPLACEMENT_KEEP')
                p.chmod(0o600)
                replacement.append(p)
            return real_open(path, flags, *args, **kwargs)
        with patch('mtp2_shared_key_fd.os.open', side_effect=changed):
            with self.assertRaises(DistributionError):
                with inherited_control_key(self.SECRET, self.root):
                    self.fail('changed inode accepted')
        self.assertEqual(len(replacement), 1)
        self.assertEqual(replacement[0].read_bytes(), b'UNRELATED_REPLACEMENT_KEEP')


if __name__ == '__main__':
    unittest.main()
