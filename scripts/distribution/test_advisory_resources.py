# SPDX-License-Identifier: Apache-2.0
"""CPU mocks exercise advisory resources without listeners, signals or a model."""
import argparse
from contextlib import ExitStack
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from common import DistributionError
import liliuxflow as cli
import model_runner
import ownership
import profile_registry
import trust

ROOT = Path(__file__).resolve().parents[2]
GIB = 1024**3


class SyntheticChild:
    """A child whose natural exit or owned termination is controlled by the test."""
    pid = 123456

    def __init__(self):
        self.returncode = None
        self.stderr = io.BytesIO(b'private-fixture-prompt\n')

    def poll(self):
        return self.returncode

    def wait(self):
        if self.returncode is None:
            raise AssertionError('test attempted to wait on an active synthetic child')
        return self.returncode


class RunnerAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='resource advisory CPU-')
        self.data = Path(self.temporary.name).resolve()
        self.run_root = self.data / 'run'
        self.lease_root = self.data / 'leases'
        self.registry = profile_registry.load_registry(ROOT)
        self.trusted = {
            'data_root': self.data, 'registry': self.registry,
            'binaries': {'lily': self.data / 'synthetic-lily'},
            'config': {'installation_id': 'synthetic-cpu-resource-policy',
                       'model_dir': '/synthetic/read-only-model', 'ports': {'guard': 18080}},
            'trust': {'binaries': {'lily': {'sha256': '1' * 64}}},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, profile_id, measurements, *, ticks=3, disk_free=None, disk_error=False):
        child = SyntheticChild()
        snapshots = []
        identity = {'pid': child.pid, 'started': 'synthetic-start', 'uid': os.getuid()}

        def capture(pid, **_kwargs):
            return identity if pid == child.pid else {'pid': pid, 'started': 'synthetic-runner'}

        def terminate(record, *, grace):
            self.assertEqual(record, identity)
            self.assertEqual(grace, 20)
            child.returncode = -15
            return True

        def advance(_seconds):
            # Every completed observer pass must leave low-RAM children active.
            self.assertIsNone(child.poll())
            snapshots.append(json.loads((self.run_root / 'model-resources.json').read_text()))
            if len(snapshots) == ticks:
                child.returncode = 0

        disk_values = [SimpleNamespace(free=100 * GIB)]
        if disk_error:
            disk_values.append(OSError('synthetic disk observation failure'))
        elif disk_free is not None:
            disk_values.append(SimpleNamespace(free=disk_free))
        else:
            disk_values.extend(SimpleNamespace(free=100 * GIB) for _ in range(ticks))

        with ExitStack() as stack:
            socket_factory = stack.enter_context(patch.object(model_runner.socket, 'socket'))
            socket_factory.return_value.__enter__.return_value.connect_ex.return_value = 61
            stack.enter_context(patch.object(model_runner.signal, 'signal'))
            stack.enter_context(patch.object(model_runner, 'capture', side_effect=capture))
            stack.enter_context(patch.object(model_runner, 'unchanged', return_value=True))
            stopped = stack.enter_context(patch.object(model_runner, 'terminate', side_effect=terminate))
            popen = stack.enter_context(patch.object(model_runner.subprocess, 'Popen', return_value=child))
            memory = stack.enter_context(patch.object(model_runner, 'memory_available', side_effect=measurements))
            stack.enter_context(patch.object(model_runner.shutil, 'disk_usage', side_effect=disk_values))
            stack.enter_context(patch.object(model_runner, 'cache_footprint', return_value=(0, 0)))
            stack.enter_context(patch.object(model_runner.time, 'sleep', side_effect=advance))
            printed = stack.enter_context(patch('builtins.print'))
            code = model_runner.execute_trusted(
                self.trusted, 19000, profile_id=profile_id, allow_validation=True,
                run_root=self.run_root, lease_root=self.lease_root)

        logs = [call.args[0] for call in printed.call_args_list]
        self.assertNotIn('private-fixture-prompt', '\n'.join(logs))
        self.assertFalse((self.lease_root / 'gpu.lease').exists())
        self.assertFalse((self.run_root / 'model.json').exists())
        state = json.loads((self.run_root / 'model-state.json').read_text())
        self.assertTrue(state['child_exited'])
        self.assertEqual(state['child'], identity)
        return SimpleNamespace(code=code, snapshots=snapshots, logs=logs,
                               stopped=stopped, popen=popen, memory=memory)

    def test_all_profiles_start_and_remain_running_below_recommended_ram(self):
        for profile in self.registry.profiles:
            with self.subTest(profile_id=profile.profile_id):
                low = (profile.minimum_ram_headroom_gib - 1) * GIB
                result = self.execute(profile.profile_id, [low] * 4)
                self.assertEqual(result.code, 0)
                result.popen.assert_called_once()
                result.stopped.assert_not_called()
                self.assertEqual(result.memory.call_count, 4)
                self.assertEqual(len(result.snapshots), 3)
                for receipt in result.snapshots:
                    self.assertEqual(receipt['ram_headroom_bytes'], low)
                    self.assertEqual(receipt['ram_headroom_status'], 'below_recommended')
                    self.assertEqual(receipt['ram_headroom_policy'], 'advisory')
                    self.assertIsNone(receipt['stop_reason'])
                self.assertEqual(len(result.logs), 1)
                self.assertTrue(result.logs[0].startswith('LILIUXFLOW_RESOURCE_WARNING '))

    def test_runtime_low_ram_then_recovery_and_recurrence_log_one_warning(self):
        result = self.execute('ctx64k', [24 * GIB, 2 * GIB, 0, 24 * GIB, 1 * GIB], ticks=4)
        self.assertEqual(result.code, 0)
        result.stopped.assert_not_called()
        self.assertEqual([r['ram_headroom_status'] for r in result.snapshots],
                         ['below_recommended', 'below_recommended', 'meets_recommended', 'below_recommended'])
        self.assertEqual(len(result.logs), 1)

    def test_low_ram_preserves_disk_stop_reason_and_owned_teardown(self):
        result = self.execute('ctx262k', [GIB, GIB], disk_free=64 * GIB - 1)
        self.assertEqual(result.code, -15)
        result.stopped.assert_called_once()
        resource = json.loads((self.run_root / 'model-resources.json').read_text())
        self.assertEqual(resource['stop_reason'], 'DISK_FREE_FLOOR')
        self.assertEqual(resource['ram_warning_reason'], 'RAM_BELOW_RECOMMENDED')
        self.assertEqual(result.logs[-1], 'LILIUXFLOW_RESOURCE_STOP reason=DISK_FREE_FLOOR')

    def test_unavailable_disk_observer_still_stops_low_ram_child(self):
        result = self.execute('ctx64k', [GIB], disk_error=True)
        self.assertEqual(result.code, -15)
        result.stopped.assert_called_once()
        self.assertEqual(result.logs[-1], 'LILIUXFLOW_RESOURCE_STOP reason=RESOURCE_OBSERVER_UNAVAILABLE')

    def test_unknown_runtime_ram_observation_still_stops_owned_child(self):
        for invalid in (None, -1, True, 13.5, DistributionError('synthetic vm_stat failure')):
            with self.subTest(invalid=invalid):
                result = self.execute('ctx64k', [24 * GIB, invalid])
                self.assertEqual(result.code, -15)
                result.stopped.assert_called_once()
                self.assertEqual(result.logs, ['LILIUXFLOW_RESOURCE_STOP reason=RESOURCE_OBSERVER_UNAVAILABLE'])

    def test_invalid_preflight_observation_refuses_without_starting_child(self):
        with patch.object(model_runner.DiskCacheBudget, 'preflight'), \
             patch.object(model_runner, 'memory_available', return_value=None), \
             patch.object(model_runner.subprocess, 'Popen') as popen:
            with self.assertRaises(DistributionError):
                model_runner.execute_trusted(self.trusted, 19000, run_root=self.run_root, lease_root=self.lease_root)
        popen.assert_not_called()
        self.assertFalse((self.lease_root / 'gpu.lease').exists())

    def test_unknown_profile_or_trust_fails_before_child_start(self):
        with patch.object(model_runner.subprocess, 'Popen') as popen:
            with self.assertRaises(DistributionError):
                model_runner.execute_trusted(self.trusted, 19000, profile_id='unknown')
            with patch.object(model_runner, 'validate', side_effect=DistributionError('synthetic invalid trust')):
                with self.assertRaises(DistributionError):
                    model_runner.run(self.data, 19000)
        popen.assert_not_called()
        invalid = replace(self.registry.default, minimum_ram_headroom_gib=1)
        with self.assertRaises(DistributionError):
            model_runner.ram_headroom_status(invalid, 0)

    def test_vm_stat_with_unknown_counters_or_page_size_is_refused(self):
        for output in ('page size of 0 bytes\nPages free: 1.\nPages inactive: 1.\nPages speculative: 1.\n',
                       'page size of 16384 bytes\nPages free: 1.\nPages inactive: 1.\n'):
            with self.subTest(output=output), \
                 patch.object(model_runner.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output)):
                with self.assertRaises(DistributionError):
                    model_runner.memory_available()


class DoctorAdvisoryTests(unittest.TestCase):
    def doctor(self, host, *, disk_free=100 * GIB, trust_error=False, guard_paused=None, controlplane_only=False):
        with tempfile.TemporaryDirectory(prefix='doctor advisory CPU-') as temporary:
            data = Path(temporary).resolve()
            (data / 'run').mkdir(mode=0o700)
            (data / 'run/agent.json').write_text(json.dumps({'agent': {'pid': 12345}}))
            config = {'schema_version': 1, 'runtime_state': 'BUILT', 'model_dir': '/synthetic/model',
                      'checkpoint': {'config_sha256': 'a', 'index_sha256': 'b'},
                      'ports': dict(cli.DEFAULT_PORTS)}
            with ExitStack() as stack:
                stack.enter_context(patch.object(cli, 'load_install', return_value=config))
                stack.enter_context(patch.object(cli, 'host_metadata', return_value=host))
                stack.enter_context(patch.object(cli.shutil, 'disk_usage', return_value=SimpleNamespace(free=disk_free)))
                stack.enter_context(patch.object(cli.shutil, 'which', return_value='/synthetic/tool'))
                stack.enter_context(patch.object(cli, 'checkpoint_metadata', return_value=config['checkpoint']))
                stack.enter_context(patch.object(cli, 'listening', side_effect=lambda port: guard_paused is not None and port == config['ports']['guard']))
                stack.enter_context(patch.object(ownership, 'unchanged', return_value=True))
                validate = stack.enter_context(patch.object(trust, 'validate', return_value={'config': config},
                    side_effect=DistributionError('synthetic invalid trust') if trust_error else None))
                admission = stack.enter_context(patch.object(agent, 'guard_admission_status',
                    return_value={'admission_paused': guard_paused, 'pause_reason': 'resource_release_unverified' if guard_paused else None}))
                output = stack.enter_context(patch.object(cli, 'emit'))
                code = cli.doctor(argparse.Namespace(data_root=data, controlplane_only=controlplane_only))
            return code, output.call_args.args[0], validate, admission

    def test_chip_and_installed_ram_mismatch_only_warn_and_trust_is_still_checked(self):
        for chip, ram in (('Apple M4 Max', 128 * GIB), ('Apple M5 Max', 64 * GIB),
                          ('Apple M4 Max', 64 * GIB), (None, None)):
            for controlplane_only in (False, True):
                with self.subTest(chip=chip, ram=ram, controlplane_only=controlplane_only):
                    code, result, validate, _ = self.doctor(
                        {'os': 'Darwin', 'machine': 'arm64', 'chip': chip, 'ram_bytes': ram},
                        controlplane_only=controlplane_only)
                    self.assertEqual(code, 0)
                    self.assertEqual(result['state'], 'controlplane_ready' if controlplane_only else 'ready')
                    self.assertEqual(result['serving_ready'], not controlplane_only)
                    self.assertEqual(result['problems'], [])
                    self.assertEqual(len(result['warnings']), 1)
                    self.assertEqual(result['hardware_baseline_policy'], 'advisory')
                    self.assertFalse(result['hardware_baseline_matches'])
                    validate.assert_called_once()

    def test_matching_baseline_has_no_warning(self):
        code, result, _, _ = self.doctor({'os': 'Darwin', 'machine': 'arm64', 'chip': 'Apple M5 Max', 'ram_bytes': 128 * GIB})
        self.assertEqual(code, 0)
        self.assertEqual(result['warnings'], [])
        self.assertTrue(result['hardware_baseline_matches'])

    def test_native_architecture_disk_and_invalid_trust_remain_blocking(self):
        baseline = {'os': 'Darwin', 'machine': 'arm64', 'chip': 'Apple M4 Max', 'ram_bytes': 64 * GIB}
        for host, kwargs, expected in (
                ({**baseline, 'os': 'Linux'}, {}, 'Darwin arm64'),
                ({**baseline, 'machine': 'x86_64'}, {}, 'Darwin arm64'),
                (baseline, {'disk_free': 64 * GIB - 1}, 'free disk'),
                (baseline, {'trust_error': True}, 'release trust')):
            with self.subTest(host=host, kwargs=kwargs):
                code, result, _, _ = self.doctor(host, **kwargs)
                self.assertEqual(code, 2)
                self.assertEqual(result['state'], 'blocked')
                self.assertFalse(result['serving_ready'])
                self.assertTrue(any(expected in problem for problem in result['problems']))

    def test_hardware_warning_does_not_skip_paused_owned_guard_classification(self):
        code, result, _, admission = self.doctor(
            {'os': 'Darwin', 'machine': 'arm64', 'chip': 'Apple M4 Max', 'ram_bytes': 64 * GIB}, guard_paused=True)
        admission.assert_called_once()
        self.assertEqual(code, 2)
        self.assertEqual(result['state'], 'DEGRADED')
        self.assertFalse(result['serving_ready'])
        self.assertTrue(result['guard_admission_paused'])
        self.assertEqual(result['problems'], [])
        self.assertEqual(len(result['warnings']), 1)


if __name__ == '__main__':
    unittest.main()
