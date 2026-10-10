# SPDX-License-Identifier: Apache-2.0
"""CPU launch/ownership failure tests; no model process is started."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
import mtp2_shared_runner as runner
from mtp2_shared_runtime import SharedInstallation, SharedEngine


class SharedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name).resolve()
        self.data.chmod(0o700)
        self.binary = self.data / 'runtime/bin/lily-mtp2-shared'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'SYNTHETIC_NONEXECUTABLE_CONTENT')
        self.binary.chmod(0o700)
        self.model = self.data / 'synthetic-model'
        self.model.mkdir()
        engine = SharedEngine('a' * 64, hashlib.sha256(self.binary.read_bytes()).hexdigest(), (), 237)
        self.install = SharedInstallation(engine, self.binary, self.model, self.data,
                                         ('ctx64k-mtp2',), (4000, 8001, 8080, 8081, 15432))
        self.trusted = {'config': {'installation_id': 'synthetic-install'},
                        'secrets': {'MANAGER_BACKEND_TOKEN': 'SYNTHETIC_NOT_A_KEY_' + 'x' * 32},
                        'registry': SimpleNamespace(profiles=[])}
        self.lease = self.data / 'leases/gpu.lease'
        self.who = {'pid': 432101, 'ppid': os.getpid(), 'uid': os.getuid(),
                    'started': 'SYNTHETIC', 'command_sha256': 'b' * 64}

    def patches(self, proc):
        budget = Mock()
        budget.read.return_value = {'stop_reason': None}
        patches = [patch.object(runner, 'global_gpu_lease', return_value=self.lease),
                   patch.object(runner, 'DiskCacheBudget', return_value=budget),
                   patch.object(runner, 'memory_available', return_value=24 * 1024**3),
                   patch.object(runner, 'capture', return_value=self.who),
                   patch.object(runner.socket.socket, 'connect_ex', return_value=111),
                   patch.object(runner.subprocess, 'Popen', return_value=proc),
                   patch.object(runner.threading, 'Thread', return_value=Mock())]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return budget

    def test_unfinalized_recipe_refuses_before_trust_or_launch(self):
        with patch.object(runner, 'RELEASE_EXPECTATION', None), patch.object(runner, 'validate') as validate, patch.object(runner.subprocess, 'Popen') as launch:
            with self.assertRaises(DistributionError):
                runner.run(self.data, 5810)
            validate.assert_not_called()
            launch.assert_not_called()

    def test_launch_has_control_fd_without_secret_in_args_or_env(self):
        tmp = self.data / 'tmp'
        with runner.authenticated_launch(self.install, 5810, b'x' * 32, tmp) as (argv, fds):
            self.assertEqual(len(fds), 1)
            self.assertEqual(argv[-2:], ['--liliuxflow-mtp2-control-key-fd', str(fds[0])])
            self.assertIn('--liliuxflow-mtp2-control', argv)
            self.assertNotIn(('x' * 32), json.dumps(argv))
            self.assertEqual(os.fstat(fds[0]).st_nlink, 0)
        with self.assertRaises(OSError):
            os.fstat(fds[0])

    def test_success_removes_only_exact_lease_and_record(self):
        proc = Mock(pid=432101)
        proc.poll.return_value = 0
        proc.wait.return_value = 0
        self.patches(proc)
        self.assertEqual(runner.execute_trusted(self.trusted, self.install, 5810), 0)
        self.assertFalse(self.lease.exists())
        self.assertFalse((self.data / 'run/mtp2-shared-model.json').exists())
        args = runner.subprocess.Popen.call_args
        self.assertEqual(args.kwargs['pass_fds'].__len__(), 1)
        self.assertNotIn(self.trusted['secrets']['MANAGER_BACKEND_TOKEN'], json.dumps(args.args))
        self.assertEqual(set(args.kwargs['env']),
                         {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LILY_QSA_ROUTE', 'LILY_QSA_SCORES'})

    def test_existing_global_lease_preserved(self):
        proc = Mock()
        self.patches(proc)
        self.lease.mkdir(parents=True, mode=0o700)
        marker = self.lease / 'foreign'
        marker.write_text('synthetic unrelated owner')
        with self.assertRaises(DistributionError):
            runner.execute_trusted(self.trusted, self.install, 5810)
        runner.subprocess.Popen.assert_not_called()
        self.assertTrue(marker.exists())

    def test_capture_runner_failure_does_not_leak_empty_lease(self):
        proc = Mock()
        self.patches(proc)
        runner.capture.side_effect = DistributionError('synthetic identity unavailable')
        with self.assertRaises(DistributionError):
            runner.execute_trusted(self.trusted, self.install, 5810)
        self.assertFalse(self.lease.exists())
        runner.subprocess.Popen.assert_not_called()

    def test_popen_failure_releases_own_unloaded_lease(self):
        proc = Mock()
        self.patches(proc)
        runner.subprocess.Popen.side_effect = OSError('synthetic launch fail')
        with self.assertRaises(OSError):
            runner.execute_trusted(self.trusted, self.install, 5810)
        self.assertFalse(self.lease.exists())
        self.assertEqual(list((self.data / 'run/tmp').iterdir()), [])

    def test_nonempty_shared_disk_namespace_refuses_before_lease(self):
        proc = Mock()
        self.patches(proc)
        cache = self.data / 'cache' / runner.CACHE_NAMESPACE
        cache.mkdir(parents=True, mode=0o700)
        (cache / 'foreign').write_text('preserve')
        with self.assertRaises(DistributionError):
            runner.execute_trusted(self.trusted, self.install, 5810)
        runner.subprocess.Popen.assert_not_called()
        self.assertFalse(self.lease.exists())
        self.assertTrue((cache / 'foreign').exists())

    def test_unverified_live_child_keeps_lease_and_never_signals(self):
        proc = Mock(pid=432101)
        proc.poll.return_value = None
        self.patches(proc)
        runner.capture.side_effect = [self.who, DistributionError('synthetic child mismatch')]
        with patch.object(runner, 'pending_owned_identity', side_effect=DistributionError('unknown child')):
            with patch.object(runner, 'terminate') as terminate:
                with self.assertRaises(DistributionError):
                    runner.execute_trusted(self.trusted, self.install, 5810)
                terminate.assert_not_called()
        self.assertTrue((self.lease / 'lease.json').exists())

    def test_changed_lease_record_after_exit_preserved(self):
        proc = Mock(pid=432101)
        proc.poll.return_value = 0
        def wait():
            file = self.lease / 'lease.json'
            record = json.loads(file.read_text())
            record['startup_id'] = '0' * 32
            file.write_text(json.dumps(record))
            return 0
        proc.wait.side_effect = wait
        self.patches(proc)
        self.assertEqual(runner.execute_trusted(self.trusted, self.install, 5810), 0)
        self.assertTrue((self.lease / 'lease.json').exists())
        self.assertEqual(json.loads((self.lease / 'lease.json').read_text())['startup_id'], '0' * 32)


if __name__ == '__main__':
    unittest.main()
