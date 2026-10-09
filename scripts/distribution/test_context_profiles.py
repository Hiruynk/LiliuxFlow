# SPDX-License-Identifier: Apache-2.0
"""CPU tests of finite profiles, trust migration and native lane evidence."""
import asyncio
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
import model_runner
import ownership
import profile_registry
import runtime_proof
import trust
import test_runtime

ROOT = Path(__file__).resolve().parents[2]


class ContextRegistryTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((ROOT / profile_registry.REGISTRY_PATH).read_text())
        for row in self.document['profiles']:
            row['production_enabled'] = row['profile_id'] == 'ctx64k'
            row['validation_only'] = row['profile_id'] != 'ctx64k'

    def test_three_exact_profiles_are_immutable_and_retired_alias_is_unknown(self):
        registry = profile_registry.parse_registry(self.document)
        legacy = tuple(p for p in registry.profiles if p.engine_id == profile_registry.LEGACY_ENGINE)
        self.assertEqual([p.context_tokens for p in legacy], [65536, 131072, 262144])
        self.assertEqual([(p.profile_id, p.production_enabled, p.validation_only) for p in registry.profiles if p.engine_id != profile_registry.LEGACY_ENGINE],
                         [('ctx64k-mtp2', False, False), ('ctx128k-mtp2', False, False), ('ctx262k-mtp2', False, False)])
        self.assertEqual([p.profile_id for p in registry.enabled_profiles], ['ctx64k'])
        with self.assertRaises(FrozenInstanceError):
            registry.default.context_tokens = 262144
        with self.assertRaises(DistributionError):
            registry.resolve('qwen38-flash-next-q4-safe64k')
        with self.assertRaises(DistributionError):
            registry.resolve(registry.profiles[1].public_alias)
        selected = registry.resolve(registry.profiles[1].public_alias, allow_validation=True)
        detached = selected.as_dict(); detached['context_tokens'] = 1
        self.assertEqual(selected.context_tokens, 131072)

    def test_finite_schema_rejects_escalation_boolean_coercion_duplicate_and_extra_fields(self):
        mutations = [
            lambda d: d['profiles'][0].update(context_tokens=262144),
            lambda d: d['profiles'][1].update(max_sessions=4),
            lambda d: d['profiles'][0].update(minimum_ram_headroom_gib=13),
            lambda d: d['profiles'][1].update(minimum_ram_headroom_gib=13),
            lambda d: d['profiles'][2].update(minimum_ram_headroom_gib=12),
            lambda d: d['profiles'][2].update(minimum_ram_headroom_gib=True),
            lambda d: d['profiles'][1].update(cache_bytes=24 * 1024**3),
            lambda d: d['profiles'][2].update(cache_namespace='../outside'),
            lambda d: d['profiles'][0].update(production_enabled=1),
            lambda d: d['shared'].update(mtp_drafts=2),
            lambda d: d['shared'].update(qsa_route='tiled'),
            lambda d: d['profiles'][1].update(disk_cache_bytes=16 * 1024**3),
            lambda d: d['profiles'].append(d['profiles'][0]),
            lambda d: d.update(extra_args='--max-seq 1048576'),
        ]
        for mutate in mutations:
            document = json.loads(json.dumps(self.document)); mutate(document)
            with self.assertRaises(DistributionError):
                profile_registry.parse_registry(document)

    def test_runner_argv_selects_real_context_cache_and_sessions_without_default_thinking_override(self):
        registry = profile_registry.parse_registry(self.document)
        with tempfile.TemporaryDirectory() as temporary:
            trusted = {'registry': registry, 'data_root': Path(temporary).resolve(),
                       'binaries': {'lily': Path('/synthetic/lily')},
                       'config': {'model_dir': '/single/read-only/Q4', 'ports': {'guard': 18080}}}
            paths = []
            for profile in registry.profiles:
                if profile.engine_id != profile_registry.LEGACY_ENGINE:
                    with self.assertRaises(DistributionError):
                        model_runner.lily_argv(trusted, 19000, profile_id=profile.profile_id, allow_validation=True)
                    continue
                argv = model_runner.lily_argv(trusted, 19000, profile_id=profile.profile_id, allow_validation=True)
                self.assertEqual(argv[argv.index('--max-seq') + 1], str(profile.context_tokens))
                self.assertEqual(argv[argv.index('--max-sessions') + 1], str(profile.max_sessions))
                self.assertEqual(argv[argv.index('--cache-bytes') + 1], str(8 * 1024**3))
                self.assertEqual(argv[argv.index('--mtp-drafts') + 1], '0')
                self.assertNotIn('--thinking', argv)
                paths.append(argv[argv.index('--disk-cache-dir') + 1])
            self.assertEqual(len(set(paths)), 3)
            with self.assertRaises(DistributionError):
                model_runner.lily_argv(trusted, 19000, profile_id='ctx128k')
            with self.assertRaises(DistributionError):
                model_runner.lily_argv(trusted, 18080)


class ProfileRamAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.registry = profile_registry.load_registry(ROOT)

    def test_262_recommends_13_without_authorizing_a_ram_stop(self):
        profile = self.registry.by_id('ctx262k', allow_validation=True)
        self.assertEqual(model_runner.ram_headroom_status(profile, int(13.5 * 1024**3))['ram_headroom_status'], 'meets_recommended')
        low = model_runner.ram_headroom_status(profile, int(12.99 * 1024**3))
        self.assertEqual(low['ram_warning_reason'], 'RAM_BELOW_RECOMMENDED')
        self.assertEqual(low['ram_headroom_policy'], 'advisory')
        self.assertIsNone(low['ram_stop_reason'])
        self.assertEqual(profile.as_dict()['minimum_ram_headroom_gib'], 13)
        self.assertEqual(profile_registry.SHARED['minimum_ram_headroom_gib'], 15)

    def test_64_and_128_recommend_15_without_authorizing_a_ram_stop(self):
        for pid in ('ctx64k', 'ctx128k'):
            profile = self.registry.by_id(pid, allow_validation=True)
            low = model_runner.ram_headroom_status(profile, int(14.99 * 1024**3))
            self.assertEqual(low['ram_warning_reason'], 'RAM_BELOW_RECOMMENDED')
            self.assertEqual(low['recommended_ram_headroom_gib'], 15)
            self.assertIsNone(low['ram_stop_reason'])
            self.assertIsNone(model_runner.ram_headroom_status(profile, 15 * 1024**3)['ram_warning_reason'])

    def test_invalid_headroom_fails_closed(self):
        for value in (None, -1, True, 13.5):
            with self.assertRaises(DistributionError):
                model_runner.ram_headroom_status(self.registry.default, value)


class ContextTrustTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_runtime.RuntimeTests('test_complete_synthetic_trust_validates_without_live_resources')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def _new_receipt(self):
        fixture = self.fixture
        file = fixture.source / profile_registry.REGISTRY_PATH
        document = json.loads((ROOT / profile_registry.REGISTRY_PATH).read_text())
        for row in document['profiles']:
            row['production_enabled'] = row['profile_id'] == 'ctx64k'
            row['validation_only'] = row['profile_id'] != 'ctx64k'
        file.write_text(json.dumps(document))
        fixture.record['profile_registry'] = {'path': profile_registry.REGISTRY_PATH, 'sha256': trust.sha256(file)}
        for name in ('litellm_profile_policy.py', 'patch_litellm_profiles.py'):
            source_file = fixture.source / 'scripts/distribution' / name
            source_file.write_bytes((ROOT / 'scripts/distribution' / name).read_bytes())
            fixture.record['source_files'].append({'path': str(source_file.relative_to(fixture.source)), 'sha256': trust.sha256(source_file)})
        prefix = 'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/'
        module = fixture.data / prefix / '_liliuxflow_context_profiles.py'
        module.parent.mkdir(parents=True)
        module.write_bytes((ROOT / 'scripts/distribution/litellm_profile_policy.py').read_bytes())
        proxy = module.with_name('proxy_server.py')
        proxy.write_text('# LILIUXFLOW_CONTEXT_PROFILE_CATALOG_V1\n' * 3)
        fixture.record['litellm_profile_policy'] = {
            'module': {'path': str(module.relative_to(fixture.data)), 'sha256': trust.sha256(module)},
            'proxy': {'path': str(proxy.relative_to(fixture.data)), 'sha256': trust.sha256(proxy),
                      'before_sha256': '8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7'},
            'catalog_filters': 3}
        manifest = fixture.source / 'manifests/distribution/checkpoint-files.json'
        value = json.loads(manifest.read_text()); value['revision'] = profile_registry.SHARED['model_revision']
        manifest.write_text(json.dumps(value))
        trust.atomic_private_json(fixture.data / 'runtime/release-trust.json', fixture.record)
        trust.verify_checkpoint(fixture.data)

    def test_legacy_receipt_cannot_grant_long_validation_even_if_registry_file_appears(self):
        fixture = self.fixture
        (fixture.source / profile_registry.REGISTRY_PATH).write_bytes((ROOT / profile_registry.REGISTRY_PATH).read_bytes())
        trusted = trust.validate(fixture.data)
        self.assertTrue(trusted['registry'].legacy)
        with self.assertRaises(DistributionError):
            trusted['registry'].by_id('ctx128k', allow_validation=True)

    def test_new_receipt_requires_hashbound_registry_and_complete_serving_source(self):
        self._new_receipt()
        trusted = trust.validate(self.fixture.data)
        self.assertFalse(trusted['registry'].legacy)
        self.assertEqual(trusted['registry'].by_id('ctx262k', allow_validation=True).context_tokens, 262144)
        file = self.fixture.source / profile_registry.REGISTRY_PATH
        file.write_text(file.read_text() + '\n')
        with self.assertRaises(DistributionError):
            trust.validate(self.fixture.data)
        self.fixture.record['profile_registry']['sha256'] = trust.sha256(file)
        self.fixture.record['source_files'] = [r for r in self.fixture.record['source_files']
                                             if r['path'] != 'scripts/distribution/runtime_proof.py']
        trust.atomic_private_json(self.fixture.data / 'runtime/release-trust.json', self.fixture.record)
        with self.assertRaises(DistributionError):
            trust.validate(self.fixture.data)

    def test_new_registry_requires_catalog_policy_and_runtime_module_identity(self):
        self._new_receipt()
        record = self.fixture.record
        saved = record.pop('litellm_profile_policy')
        trust.atomic_private_json(self.fixture.data / 'runtime/release-trust.json', record)
        with self.assertRaises(DistributionError):
            trust.validate(self.fixture.data)
        record['litellm_profile_policy'] = saved
        path = self.fixture.data / saved['module']['path']
        path.write_text('untrusted')
        saved['module']['sha256'] = trust.sha256(path)
        trust.atomic_private_json(self.fixture.data / 'runtime/release-trust.json', record)
        with self.assertRaises(DistributionError):
            trust.validate(self.fixture.data)

    def test_validation_factory_requires_explicit_candidate_and_private_scoped_authorization(self):
        self._new_receipt()
        trusted = trust.validate(self.fixture.data)
        self.assertEqual(runtime_proof.portable_guard_kwargs(trusted)['validation_aliases'], ())
        with self.assertRaises(DistributionError):
            runtime_proof.portable_guard_kwargs(trusted, validation_profile_ids=('ctx128k',))
        record = {'schema_version': 1, 'installation_id': trusted['config']['installation_id'],
                  'registry_sha256': trusted['trust']['profile_registry']['sha256'],
                  'binary_sha256': trusted['trust']['binaries']['lily']['sha256'],
                  'profile_ids': ['ctx128k'], 'expires_at': time.time() + 600}
        file = trusted['data_root'] / 'run/context-validation.json'
        trust.atomic_private_json(file, record)
        self.assertEqual(runtime_proof.portable_guard_kwargs(trusted)['validation_aliases'], ())
        self.assertEqual(runtime_proof.portable_guard_kwargs(trusted, validation_profile_ids=('ctx128k',))['validation_aliases'],
                         (trusted['registry'].profiles[1].public_alias,))
        with self.assertRaises(DistributionError):
            runtime_proof.portable_guard_kwargs(trusted, validation_profile_ids=('ctx262k',))
        record['expires_at'] = time.time() - 1; trust.atomic_private_json(file, record)
        with self.assertRaises(DistributionError):
            runtime_proof.portable_guard_kwargs(trusted, validation_profile_ids=('ctx128k',))


class DiskCacheBudgetTests(unittest.TestCase):
    def test_actual_262_stop_footprint_is_allowed_inflight_but_requires_preflight_reserve(self):
        from collections import namedtuple
        Disk = namedtuple('Disk', 'total used free')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            budget = model_runner.DiskCacheBudget(profile_registry.load_registry(ROOT),
                                                  [base / str(index) for index in range(3)], base)
            with patch.object(model_runner, 'cache_footprint', return_value=(45834584100, 23750497503)), \
                 patch.object(model_runner.shutil, 'disk_usage', return_value=Disk(200*1024**3, 0, 148625563648)):
                self.assertIsNone(budget.read()['stop_reason'])
                self.assertEqual(budget.read()['budget_phase'], 'inflight')
                self.assertEqual(budget.read(preflight=True)['stop_reason'], 'ADDED_CACHE_TEMPORARY_RESERVE')
                with self.assertRaises(DistributionError): budget.preflight()

    def test_observer_counts_unfinished_files_and_refuses_symlink_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            roots = [base / str(index) for index in range(3)]
            for root in roots: root.mkdir(mode=0o700)
            (roots[0] / 'retained.bin').write_bytes(b'0123456789')
            (roots[1] / 'unfinished-prefix.bin').write_bytes(b'012345')
            (roots[2] / 'unfinished-checkpoint.bin').write_bytes(b'0123')
            self.assertEqual(model_runner.cache_footprint(roots), (20, 10))
            (roots[1] / 'unsafe-link').symlink_to(roots[0] / 'retained.bin')
            with self.assertRaises(DistributionError):
                model_runner.cache_footprint(roots)

    def test_budget_reserves_staging_before_exhaustion_and_fails_unknown_counters(self):
        registry = profile_registry.load_registry(ROOT); gib = 1024**3
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            budget = model_runner.DiskCacheBudget(registry, [base / str(index) for index in range(3)], base)
            from collections import namedtuple
            Disk = namedtuple('Disk', 'total used free')
            with patch.object(model_runner.shutil, 'disk_usage', return_value=Disk(200*gib, 0, 64*gib)):
                with patch.object(model_runner, 'cache_footprint', return_value=(52*gib, 20*gib)):
                    self.assertIsNone(budget.preflight()['stop_reason'])
                with patch.object(model_runner, 'cache_footprint', return_value=(52*gib+1, 20*gib)):
                    self.assertEqual(budget.read()['stop_reason'], 'CACHE_TEMPORARY_RESERVE')
                    self.assertEqual(budget.read(preflight=True)['stop_reason'], 'CACHE_TEMPORARY_RESERVE')
                    with self.assertRaises(DistributionError): budget.preflight()
                with patch.object(model_runner, 'cache_footprint', return_value=(40*gib, 20*gib+1)):
                    self.assertIsNone(budget.read()['stop_reason'])
                    self.assertEqual(budget.read(preflight=True)['stop_reason'], 'ADDED_CACHE_TEMPORARY_RESERVE')
                with patch.object(model_runner, 'cache_footprint', return_value=(64*gib, 32*gib)):
                    self.assertEqual(budget.read()['stop_reason'], 'ADDED_CACHE_HARD_CAP')
                with patch.object(model_runner, 'cache_footprint', return_value=(64*gib+1, 20*gib)):
                    self.assertEqual(budget.read()['stop_reason'], 'CACHE_HARD_CAP')
                with patch.object(model_runner, 'cache_footprint', return_value=(40*gib, 32*gib+1)):
                    self.assertEqual(budget.read()['stop_reason'], 'ADDED_CACHE_HARD_CAP')
                with patch.object(model_runner, 'cache_footprint', return_value=(None, 0)):
                    with self.assertRaises(DistributionError): budget.read()
            with patch.object(model_runner.shutil, 'disk_usage', return_value=Disk(200*gib, 0, 64*gib-1)), \
                 patch.object(model_runner, 'cache_footprint', return_value=(0, 0)):
                self.assertEqual(budget.read()['stop_reason'], 'DISK_FREE_FLOOR')
class LaneEvidenceTests(unittest.TestCase):
    def test_qsa_and_native_context_diagnostics_are_fixed_label_whitelists(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = model_runner.LaneState(Path(temporary).resolve() / 'state.json', {'context_tokens': 131072})
            self.assertTrue(state.consume('LILY_QSA_ROUTE_EFFECTIVE requested=split route=split'))
            self.assertTrue(state.consume('LILY_QSA_DISPATCH_METADATA sparse_prefill_rows=256 route=split split_dispatch_count=1 tiled_dispatch_count=0'))
            self.assertTrue(state.consume('memory: 47.1 GB allocated, 103.1 GB recommended working set, 8.6 GB session cache budget (28416 B/token of context; a full 131072-token request needs 3.7 GB)'))
            self.assertEqual(state.record['native_context_tokens'], 131072)
            self.assertEqual(state.record['qsa_dispatch_metadata']['tiled_dispatch_count'], 0)
            self.assertTrue(state.record['proof_valid'])
            self.assertFalse(state.consume('LILY_QSA_ROUTE_EFFECTIVE requested=private-prompt route=split'))
            state.consume('LILY_QSA_ROUTE_EFFECTIVE requested=tile route=tile')
            self.assertFalse(state.record['proof_valid'])

    def test_only_matching_lane_pair_proves_release_and_unknown_stderr_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'state.json'
            state = model_runner.LaneState(path, {'owner': 'synthetic'})
            self.assertFalse(state.consume('private prompt text'))
            state.consume('[lily-meta] request_id=req-100-1 phase=lane_acquired queue_wait_ms=1')
            self.assertEqual(json.loads(path.read_text())['active_lane_ids'], ['req-100-1'])
            state.consume('[lily-meta] request_id=req-100-1 phase=prefill_progress completed_chunks=1 prefilled_tokens=1024')
            self.assertEqual(json.loads(path.read_text())['last_prefill_progress'],
                             {'request_id': 'req-100-1', 'completed_chunks': 1, 'prefilled_tokens': 1024})
            state.consume('[lily-meta] request_id=req-100-1 phase=lane_released cancelled=false lane_ms=1')
            value = json.loads(path.read_text())
            self.assertTrue(value['proof_valid']); self.assertEqual(value['released_sequence'], 1)
            self.assertNotIn('private prompt', path.read_text())
            state.consume('[lily-meta] request_id=req-100-2 phase=lane_released cancelled=false lane_ms=1')
            self.assertFalse(json.loads(path.read_text())['proof_valid'])

    def test_prefill_progress_is_strict_owner_lane_data_and_resets_on_new_acquire(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = model_runner.LaneState(Path(temporary).resolve() / 'state.json', {})
            state.consume('[lily-meta] request_id=req-100-1 phase=lane_acquired queue_wait_ms=0')
            self.assertFalse(state.consume('[lily-meta] request_id=req-100-1 phase=prefill_progress completed_chunks=-1 prefilled_tokens=1024'))
            state.consume('[lily-meta] request_id=req-100-1 phase=prefill_progress completed_chunks=1 prefilled_tokens=1024')
            state.consume('[lily-meta] request_id=req-100-1 phase=lane_released cancelled=true lane_ms=1')
            state.consume('[lily-meta] request_id=req-100-2 phase=lane_acquired queue_wait_ms=0')
            self.assertIsNone(state.record['last_prefill_progress'])
            state.consume('[lily-meta] request_id=req-100-1 phase=prefill_progress completed_chunks=1 prefilled_tokens=1024')
            self.assertFalse(state.record['proof_valid'])

    def test_drain_is_bounded_and_mirrors_only_known_lane_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = model_runner.LaneState(Path(temporary).resolve() / 'state.json', {})
            pipe = io.BytesIO(b'private text\n' + b'X' * 20000 + b'\n'
                              + b'[lily-meta] request_id=req-101-1 phase=lane_acquired queue_wait_ms=0\n'
                              + b'[lily-meta] request_id=req-101-1 phase=lane_released cancelled=false lane_ms=0\n')
            with patch('builtins.print') as printed:
                model_runner._drain_metadata(pipe, state)
            self.assertEqual(printed.call_count, 2)
            self.assertEqual(state.record['active_lane_ids'], [])
            self.assertTrue(state.record['stderr_closed'])

    def test_three_real_cpu_mock_children_use_exact_identity_and_owner_bound_release_sequences(self):
        registry = profile_registry.load_registry(ROOT)
        self.assertEqual([p.profile_id for p in registry.enabled_profiles], ['ctx64k', 'ctx128k', 'ctx262k'])
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve(); run = data / 'run'; lease_root = data / 'leases'
            run.mkdir(mode=0o700); lease_root.mkdir(mode=0o700)
            trusted = {'registry': registry, 'data_root': data, 'config': {'installation_id': 'cpu-mock-install'},
                       'trust': {'binaries': {'lily': {'sha256': '1' * 64}}}}
            probe = runtime_proof.RunnerResourceProbe(trusted, lease_root=lease_root)
            for index, profile in enumerate(registry.enabled_profiles):
                # An owned CPU runner creates its own small child. No Q4/Metal.
                process = subprocess.Popen([sys.executable, '-u', '-c',
                    'import subprocess,sys,time,signal\n'
                    'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
                    'print(child.pid,flush=True)\n'
                    'def stop(*args):\n child.terminate();child.wait();sys.exit(0)\n'
                    'signal.signal(signal.SIGTERM,stop)\n'
                    'time.sleep(60)\n'], stdout=subprocess.PIPE, text=True)
                child_pid = int(process.stdout.readline())
                runner = ownership.capture(process.pid, parent=os.getpid())
                child = ownership.capture(child_pid, parent=process.pid)
                record = {'schema_version': 1, 'installation_id': 'cpu-mock-install',
                          'runner': runner, 'child': child, 'profile_id': profile.profile_id,
                          'context_tokens': profile.context_tokens, 'backend_port': 49100 + index,
                          'binary_sha256': '1' * 64, 'startup_id': 'synthetic-' + str(index)}
                lease = lease_root / 'gpu.lease'; lease.mkdir(mode=0o700)
                trust.atomic_private_json(lease / 'lease.json', record)
                state = model_runner.LaneState(run / 'model-state.json', record)
                state.consume('memory: 1.0 GB allocated, 100.0 GB recommended working set, 8.6 GB session cache budget '
                              + f'(28416 B/token of context; a full {profile.context_tokens}-token request needs 1.0 GB)')
                try:
                    self.assertTrue(probe._probe(profile, 'before_forward'))
                    self.assertFalse(probe._probe(profile, 'generation_complete'))
                    state.consume('[lily-meta] request_id=req-200-1 phase=lane_acquired queue_wait_ms=0')
                    self.assertFalse(probe._probe(profile, 'generation_complete'))
                    state.consume('[lily-meta] request_id=req-200-1 phase=lane_released cancelled=false lane_ms=0')
                    self.assertTrue(probe._probe(profile, 'generation_complete'))
                    self.assertTrue(probe._probe(profile, 'before_unload'))
                    self.assertFalse(probe._probe(profile, 'unloaded'))
                    self.assertTrue(ownership.terminate(runner, grace=3)); process.wait(timeout=3)
                    state.closed(exited=True)
                    (lease / 'lease.json').unlink(); lease.rmdir()
                    self.assertTrue(probe._probe(profile, 'unloaded'))
                finally:
                    if process.poll() is None:
                        ownership.terminate(runner, grace=3); process.wait(timeout=3)
                    process.stdout.close()
                    if lease.exists():
                        (lease / 'lease.json').unlink(); lease.rmdir()


if __name__ == '__main__':
    unittest.main()
