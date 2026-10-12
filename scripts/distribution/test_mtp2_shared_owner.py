# SPDX-License-Identifier: Apache-2.0
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
from mtp2_shared_runtime import SharedInstallation, SharedEngine, runtime_identity_fields
import mtp2_shared_owner as owner


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.lease = self.root / 'lease'
        self.lease.mkdir(mode=0o700)
        (self.root / 'run').mkdir()
        engine = SharedEngine('a' * 64, 'b' * 64, (), 238)
        self.install = SharedInstallation(engine, self.root / 'binary', self.root / 'model',
                                         self.root, ('ctx64k-mtp2',), (4000, 8080))
        self.trusted = {'config': {'installation_id': 'synthetic-install'}}
        self.record = {'schema_version': 1, 'installation_id': 'synthetic-install',
                       'profile_id': 'mtp2-shared', 'public_alias': 'liliuxflow-internal-mtp2-shared',
                       'context_tokens': 262144, 'binary_sha256': 'b' * 64,
                       'backend_port': 5810, 'startup_id': 'c' * 32,
                       'runner': {'pid': 123, 'ppid': 99, 'uid': os.getuid(), 'started': 'ONE',
                                  'pgid': 123, 'command_sha256': 'd' * 64},
                       'child': {'pid': 124, 'ppid': 123, 'uid': os.getuid(), 'started': 'TWO',
                                 'pgid': 123, 'command_sha256': 'e' * 64},
                       **runtime_identity_fields(engine)}
        self.save()
        p = patch.object(owner, 'global_gpu_lease', return_value=self.lease)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(owner, 'inspect', side_effect=lambda pid, **kw: copy.deepcopy(
            self.record['runner' if pid == 123 else 'child']))
        p.start(); self.addCleanup(p.stop)
        self.probe = owner.Mtp2SharedOwnerProbe(self.trusted, self.install)

    def save(self, *, model=None):
        for path, record in ((self.lease / 'lease.json', self.record),
                             (self.root / 'run/mtp2-shared-model.json', model or self.record)):
            path.write_text(json.dumps(record))
            path.chmod(0o600)

    def test_same_owner_binding_true_and_foreign_actor_false(self):
        binding = self.probe.current_owner()
        self.assertEqual(binding.port, 5810)
        self.assertTrue(self.probe.matches(binding))
        self.assertFalse(owner.Mtp2SharedOwnerProbe(self.trusted, self.install).matches(binding))

    def test_record_change_invalidates_binding(self):
        binding = self.probe.current_owner()
        self.record['startup_id'] = 'f' * 32
        self.save()
        self.assertFalse(self.probe.matches(binding))

    def test_native_identity_change_rejects(self):
        with patch.object(owner, 'inspect', return_value=None):
            with self.assertRaises(DistributionError):
                self.probe.current_owner()

    def test_mismatched_lease_and_model_reject(self):
        altered = copy.deepcopy(self.record); altered['context_tokens'] = 65536
        self.save(model=altered)
        with self.assertRaises(DistributionError):
            self.probe.current_owner()

    def test_reused_pid_wrong_uid_wrong_context_control_port_reject(self):
        original = copy.deepcopy(self.record)
        for change in ('uid', 'context', 'port', 'bool'):
            self.record = copy.deepcopy(original)
            if change == 'uid': self.record['child']['uid'] = os.getuid() + 1
            if change == 'context': self.record['context_tokens'] = 65536
            if change == 'port': self.record['backend_port'] = 4000
            if change == 'bool': self.record['schema_version'] = True
            self.save()
            with self.subTest(change=change), self.assertRaises(DistributionError):
                self.probe.current_owner()

    def test_directory_and_record_permissions_reject(self):
        self.lease.chmod(0o755)
        with self.assertRaises(DistributionError): self.probe.current_owner()
        self.lease.chmod(0o700)
        (self.lease / 'lease.json').chmod(0o644)
        with self.assertRaises(DistributionError): self.probe.current_owner()

    def test_exit_requires_gone_child_record_lease_port_and_actor(self):
        binding = self.probe.current_owner()
        self.assertFalse(self.probe.exited(binding))
        (self.root / 'run/mtp2-shared-model.json').unlink()
        (self.lease / 'lease.json').unlink(); self.lease.rmdir()
        with patch.object(owner, 'inspect', return_value=None), patch.object(owner.socket.socket, 'connect_ex', return_value=111):
            self.assertTrue(self.probe.exited(binding))
            with patch.object(owner.socket.socket, 'connect_ex', return_value=0):
                self.assertFalse(self.probe.exited(binding))
            self.lease.mkdir(mode=0o700)
            self.assertFalse(self.probe.exited(binding))
        other = owner.Mtp2SharedOwnerProbe(self.trusted, self.install)
        self.assertFalse(other.exited(binding))


if __name__ == '__main__':
    unittest.main()
