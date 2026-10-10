# SPDX-License-Identifier: Apache-2.0
"""Isolated synthetic CPU checks. No build, model, process, lease or service work."""
from dataclasses import FrozenInstanceError, replace
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/distribution'))
from common import DistributionError
import mtp2_shared_runtime as shared
import native_recipe
import optin_engine
import profile_registry
import trust


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RecipeAndInstallationTests(unittest.TestCase):
    def test_bounded_candidate_is_explicit_and_preserves_older_recipes(self):
        lock = json.loads((ROOT / 'manifests/distribution/native-sources.json').read_text())
        self.assertIs(shared.candidate_expectation(), shared.FROZEN_SOURCE_EXPECTATION)
        self.assertIs(shared.candidate_expectation(True), shared.BOUNDED_OUTPUT_EXPECTATION)
        for expected in (shared.FROZEN_SOURCE_EXPECTATION, shared.CPU_COPY_EXPECTATION,
                         shared.BOUNDED_OUTPUT_EXPECTATION):
            self.assertEqual(shared.select_shared_recipe(lock, expected)[0], expected.pin())
        self.assertEqual(native_recipe.select_lily_recipe(self.lock)[0], self.lock['lily'])
        self.assertFalse(self.lock[shared.BOUNDED_OUTPUT_EXPECTATION.pin_key]['default'])

    def test_bounded_candidate_requires_complete_prerequisite_chain(self):
        expected = shared.BOUNDED_OUTPUT_EXPECTATION
        for change in ({'performance_patch_sha256': None}, {'service_patch_sha256': None},
                       {'output_patch_sha256': '0' * 64}, {'output_patch_sha256': True}):
            with self.subTest(change=change), self.assertRaises(DistributionError):
                replace(expected, **change)
        bad = copy.deepcopy(self.lock)
        bad[expected.pin_key]['patches'] = bad[expected.pin_key]['patches'][:-1]
        with self.assertRaises(DistributionError):
            shared.select_shared_recipe(bad, expected)
        self.assertEqual(shared.select_shared_recipe(bad, shared.CPU_COPY_EXPECTATION)[0],
                         shared.CPU_COPY_EXPECTATION.pin())

    def test_bounded_serving_closure_requires_queue_patch_and_inventory(self):
        import mtp2_shared_recipe as recipe
        records = recipe.serving_source_records(ROOT, performance_candidate=True)
        actual = {row['path']: row['sha256'] for row in records}
        self.assertEqual(actual[shared.OUTPUT_PATCH_PATH], shared.OUTPUT_PATCH_SHA256)
        self.assertEqual(actual[shared.OUTPUT_BASELINE_PATH],
                         shared.BOUNDED_OUTPUT_EXPECTATION.baseline_sha256)
        baseline = shared._baseline(ROOT, shared.BOUNDED_OUTPUT_EXPECTATION)
        self.assertIn('src/serve/output_queue.rs', baseline)
        self.assertEqual(len(baseline), 241)

    def test_optional_service_patch_is_finite_and_code_bound(self):
        expected = shared.RecipeExpectation('1' * 64, '2' * 64, '3' * 64, '4' * 64, 238,
                                            service_patch_sha256='5' * 64)
        self.assertEqual(expected.patches[-1], {'path': shared.SERVICE_PATCH_PATH,
                                               'sha256': '5' * 64, 'unidiff_zero': False})
        self.assertEqual(len(expected.patches), len(shared.BASE_PATCH_PATHS) + 3)
        for bad in (True, '5' * 63, 'F' * 64, '/arbitrary/patch'):
            with self.subTest(bad=bad), self.assertRaises(DistributionError):
                shared.RecipeExpectation('1' * 64, '2' * 64, '3' * 64, '4' * 64, 238,
                                         service_patch_sha256=bad)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='liliuxflow-shared-cpu-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.package, self.data, self.native = (self.root / name for name in ('package', 'data', 'native'))
        for path in (self.package, self.data, self.native):
            path.mkdir(mode=0o700)
        self.lock = json.loads((ROOT / 'manifests/distribution/native-sources.json').read_text())
        self.registry = profile_registry.parse_registry(
            json.loads((ROOT / profile_registry.REGISTRY_PATH).read_text()),
            enabled_optin_profiles=list(shared.PROFILE_IDS))
        self.before = b'fn before() {}\n'
        self.middle = b'fn middle() {}\n'
        self.after = b'fn after() {}\n'
        for path, before, after in zip(shared.SHARED_PATCH_PATHS,
                                      (self.before, self.middle), (self.middle, self.after)):
            self.write(self.package, path, b'--- a/main.rs\n+++ b/main.rs\n@@ -1 +1 @@\n-' + before + b'+' + after)
        self.write(self.native, 'main.rs', self.after)
        self.write(self.native, '.gitignore', b'target\n')
        rows = [{'path': 'main.rs', 'sha256': digest(self.after)},
                {'path': '.gitignore', 'sha256': digest(b'target\n')}]
        baseline = {'schema_version': 1, 'source_commit': optin_engine.LATEST_COMMIT, 'files': rows}
        self.write(self.package, shared.BASELINE_PATH, json.dumps(baseline).encode())
        inventory = digest(json.dumps(dict((r['path'], r['sha256']) for r in rows),
                                      sort_keys=True, separators=(',', ':')).encode())
        self.expected = shared.RecipeExpectation(
            *(trust.sha256(self.package / p) for p in shared.SHARED_PATCH_PATHS),
            trust.sha256(self.package / shared.BASELINE_PATH), inventory, 2)
        self.lock['lily_mtp2_shared'] = self.expected.pin()
        for name in shared.BOUNDARY_SOURCES - {'manifests/distribution/native-sources.json'}:
            self.write(self.package, name, b'# Synthetic CPU boundary fixture; not shipped\n')
        for row in self.lock['lily_opt64']['patches']:
            destination = self.package / row['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / row['path'], destination)
        self.write(self.package, 'manifests/distribution/native-sources.json', json.dumps(self.lock).encode())
        self.write(self.data, shared.BINARY_PATH, b'SYNTHETIC_CPU_BINARY_NOT_EXECUTED\n')
        (self.data / shared.BINARY_PATH).chmod(0o700)
        self.model = self.root / 'Q4 空間 checkpoint'
        self.model.mkdir()
        paths = (shared.BOUNDARY_SOURCES | {shared.BASELINE_PATH}
                 | {row['path'] for row in self.expected.patches})
        source_rows = [{'path': p, 'sha256': trust.sha256(self.package / p)} for p in sorted(paths)]
        self.record = {'engine_id': shared.ENGINE_ID, 'source_commit': optin_engine.LATEST_COMMIT,
                       'archive_sha256': shared.ARCHIVE_SHA256,
                       'patch_sha256': [r['sha256'] for r in self.expected.patches],
                       'source_inventory_sha256': self.expected.source_inventory_sha256,
                       'source_file_count': 2, 'default': False,
                       'binary_sha256': trust.sha256(self.data / shared.BINARY_PATH)}
        self.trusted = {'trust': {'source_files': source_rows, 'mtp2_shared_runtime': {
            'schema_version': 1, 'engine': self.record,
            'binary': {'path': shared.BINARY_PATH, 'sha256': self.record['binary_sha256']},
            'enabled_profiles': list(shared.PROFILE_IDS), 'runtime_policy': shared.runtime_policy()}},
            'registry': self.registry, 'source_root': self.package, 'data_root': self.data,
            'model_configured': True, 'checkpoint': {'payload_verified': True,
            'revision': profile_registry.SHARED['model_revision']},
            'config': {'model_dir': str(self.model), 'ports': {'guard': 18080, 'manager': 18081}}}

    @staticmethod
    def write(base, path, data):
        file = base / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def test_no_extension_preserves_old_paths_without_expectation(self):
        old = copy.deepcopy(self.trusted)
        del old['trust']['mtp2_shared_runtime']
        before = copy.deepcopy(old)
        self.assertIsNone(shared.validate_shared_extension(old))
        self.assertEqual(old, before)

    def test_no_receipt_or_lock_can_supply_the_code_side_expectation(self):
        for bad in (None, self.expected.pin(), self.record, {'approval': True}):
            with self.subTest(bad=type(bad)), self.assertRaises(DistributionError):
                shared.validate_shared_extension(self.trusted, bad)

    def test_expectation_is_immutable_and_rejects_boolean_count(self):
        with self.assertRaises(FrozenInstanceError):
            self.expected.source_file_count = 3
        for bad in ('a' * 63, 'A' * 64, None):
            with self.subTest(bad=bad), self.assertRaises(DistributionError):
                shared.RecipeExpectation(bad, 'b' * 64, 'c' * 64, 'd' * 64, 2)
        with self.assertRaises(DistributionError):
            shared.RecipeExpectation('a' * 64, 'b' * 64, 'c' * 64, 'd' * 64, True)

    def test_finite_selector_does_not_change_original_lineages(self):
        before = copy.deepcopy(self.lock)
        self.assertEqual(native_recipe.select_lily_recipe(self.lock)[0], self.lock['lily'])
        self.assertEqual(native_recipe.select_lily_recipe(self.lock, optin_engine.OPT64)[0], self.lock['lily_opt64'])
        self.assertEqual(shared.select_shared_recipe(self.lock, self.expected)[0], self.expected.pin())
        self.assertNotEqual(shared.ENGINE_ID, optin_engine.OPT64)
        self.assertEqual(self.lock, before)

    def test_selector_rejects_changed_base_recipe(self):
        for key, value in (('commit', 'a' * 40), ('archive_sha256', 'a' * 64)):
            invalid = copy.deepcopy(self.lock)
            invalid['lily_opt64'][key] = value
            with self.subTest(key=key), self.assertRaises(DistributionError):
                shared.select_shared_recipe(invalid, self.expected)

    def test_selector_rejects_source_and_policy_injections(self):
        changes = ({'commit': 'a' * 40}, {'source_inventory_sha256': 'a' * 64},
                   {'default': True}, {'default': 0}, {'extra_args': ['--kv-cache', 'q8']},
                   {'baseline': '../outside.json'}, {'source_file_count': True})
        for change in changes:
            invalid = copy.deepcopy(self.lock)
            invalid['lily_mtp2_shared'].update(change)
            with self.subTest(change=change), self.assertRaises(DistributionError):
                shared.select_shared_recipe(invalid, self.expected)
        invalid = copy.deepcopy(self.lock)
        invalid['lily_mtp2_shared']['patches'][4]['unidiff_zero'] = 0
        with self.assertRaises(DistributionError):
            shared.select_shared_recipe(invalid, self.expected)

    def test_complete_source_inventory_is_verified(self):
        self.assertEqual(set(shared.verify_source_inventory(self.package, self.native, self.expected)),
                         {'main.rs', '.gitignore'})

    def test_source_inventory_rejects_extra_missing_or_changed_files(self):
        self.write(self.native, 'unlisted.rs', b'not reviewed\n')
        with self.assertRaises(DistributionError):
            shared.verify_source_inventory(self.package, self.native, self.expected)
        (self.native / 'unlisted.rs').unlink()
        (self.native / 'main.rs').unlink()
        with self.assertRaises(DistributionError):
            shared.verify_source_inventory(self.package, self.native, self.expected)
        self.write(self.native, 'main.rs', b'changed\n')
        with self.assertRaises(DistributionError):
            shared.verify_source_inventory(self.package, self.native, self.expected)

    def test_source_inventory_rejects_links_and_duplicate_records(self):
        link = self.native / 'linked.rs'
        link.symlink_to(self.native / 'main.rs')
        with self.assertRaises(DistributionError):
            shared.verify_source_inventory(self.package, self.native, self.expected)
        link.unlink()
        baseline = json.loads((self.package / shared.BASELINE_PATH).read_text())
        baseline['files'][1] = baseline['files'][0]
        self.write(self.package, shared.BASELINE_PATH, json.dumps(baseline).encode())
        changed = shared.RecipeExpectation(self.expected.p1_patch_sha256, self.expected.p2_patch_sha256,
            trust.sha256(self.package / shared.BASELINE_PATH), self.expected.source_inventory_sha256, 2)
        with self.assertRaises(DistributionError):
            shared.verify_source_inventory(self.package, self.native, changed)

    def test_cpu_replay_cannot_claim_a_build_or_runtime_acceptance(self):
        # Mock only the separately tested accepted OPT64 replay. Exercise the
        # two new hash-bound patches, actual git apply, and complete postimage.
        baseline = json.loads((self.package / shared.BASELINE_PATH).read_text())
        touched = set()
        for path in shared.BASE_PATCH_PATHS:
            touched.update(shared.patch_paths(self.package / path, 1))
        for path in sorted(touched):
            data = b'// Synthetic accepted-base replay output, not native source\n'
            self.write(self.native, path, data)
            baseline['files'].append({'path': path, 'sha256': digest(data)})
        self.write(self.package, shared.BASELINE_PATH, json.dumps(baseline).encode())
        inventory = digest(json.dumps(dict((r['path'], r['sha256']) for r in baseline['files']),
                                      sort_keys=True, separators=(',', ':')).encode())
        self.expected = shared.RecipeExpectation(self.expected.p1_patch_sha256, self.expected.p2_patch_sha256,
            trust.sha256(self.package / shared.BASELINE_PATH), inventory, len(baseline['files']))
        self.lock['lily_mtp2_shared'] = self.expected.pin()
        self.write(self.native, 'main.rs', self.before)
        with patch.object(shared, 'apply_lily_source_patches', return_value={'replay': 'PASS'}) as base:
            result = shared.replay_shared_source(self.package, self.native, self.lock, self.expected)
        base.assert_called_once_with(self.package, self.native, self.lock, optin_engine.OPT64)
        self.assertEqual(result['replay'], 'PASS')
        self.assertFalse(result['native_build'])
        self.assertFalse(result['runtime_acceptance'])
        self.assertEqual((self.native / 'main.rs').read_bytes(), self.after)

    def test_patch_mutation_is_rejected_before_source_mutation(self):
        self.write(self.native, 'main.rs', self.before)
        self.write(self.package, shared.SHARED_PATCH_PATHS[0], b'not a reviewed patch\n')
        with patch.object(shared, 'apply_lily_source_patches') as base, self.assertRaises(DistributionError):
            shared.replay_shared_source(self.package, self.native, self.lock, self.expected)
        base.assert_not_called()
        self.assertEqual((self.native / 'main.rs').read_bytes(), self.before)

    def test_generated_trust_rejects_missing_wrong_and_arbitrary_bindings(self):
        self.assertEqual(shared.from_generated_trust(self.record, self.expected).binary_sha256,
                         self.record['binary_sha256'])
        changes = ({'engine_id': optin_engine.OPT64}, {'binary_sha256': 'not-hash'},
                   {'source_inventory_sha256': 'b' * 64}, {'source_file_count': True},
                   {'patch_sha256': list(optin_engine.PATCHES)}, {'default': True},
                   {'extra_args': ['--mtp-drafts', '0']})
        for change in changes:
            value = {**self.record, **change}
            with self.subTest(change=change), self.assertRaises(DistributionError):
                shared.from_generated_trust(value, self.expected)
        value = {k: v for k, v in self.record.items() if k != 'binary_sha256'}
        with self.assertRaises(DistributionError):
            shared.from_generated_trust(value, self.expected)

    def test_additive_installation_and_argv_have_fixed_policy_and_preserve_inputs(self):
        before = copy.deepcopy(self.trusted)
        installation = shared.validate_shared_extension(self.trusted, self.expected)
        argv = shared.shared_argv(installation, 19111)
        expected_flags = {'--max-seq': '262144', '--mtp-drafts': '2', '--max-batch': '2',
                          '--kv-cache': 'bf16', '--cache-bytes': str(24 * 1024**3),
                          '--max-sessions': '2', '--disk-cache-bytes': '0', '--queue': '4',
                          '--thinking': 'true', '--thinking-budget': 'off', '--thinking-nudges': 'false',
                          '--tool-call-ends-thinking': 'false', '--ngram-preload': 'true', '--pin-weights': 'off'}
        for key, value in expected_flags.items():
            self.assertEqual(argv[argv.index(key) + 1], value)
        self.assertIn('--liliuxflow-mtp2-shared-context', argv)
        self.assertEqual(argv[argv.index('--model') + 1], str(self.model))
        self.assertEqual(argv[argv.index('--bind') + 1], '127.0.0.1:19111')
        self.assertNotIn('--max-images', argv)  # Absent in this pinned latest CLI.
        self.assertEqual(self.trusted, before)
        self.assertEqual(self.registry.default.profile_id, 'ctx64k')
        self.assertEqual([p.as_dict()['max_batch'] for p in self.registry.profiles if p.engine_id == optin_engine.OPT64], [1, 1, 1])

    def test_installation_requires_future_transport_and_runner_source_binding(self):
        for missing in ('scripts/distribution/mtp2_shared_transport.py', 'scripts/distribution/mtp2_shared_runner.py'):
            value = copy.deepcopy(self.trusted)
            value['trust']['source_files'] = [r for r in value['trust']['source_files'] if r['path'] != missing]
            with self.subTest(missing=missing), self.assertRaises(DistributionError):
                shared.validate_shared_extension(value, self.expected)

    def test_installation_rejects_policy_paths_profiles_and_no_checkpoint(self):
        changes = ({'enabled_profiles': []}, {'enabled_profiles': ['ctx64k']},
                   {'enabled_profiles': ['ctx64k-mtp2', 'ctx64k-mtp2']},
                   {'enabled_profiles': ['ctx262k-mtp2', 'ctx64k-mtp2']},
                   {'binary': {'path': '../lily', 'sha256': self.record['binary_sha256']}},
                   {'binary': {'path': shared.BINARY_PATH, 'sha256': 'a' * 64}})
        for change in changes:
            value = copy.deepcopy(self.trusted)
            value['trust']['mtp2_shared_runtime'].update(change)
            with self.subTest(change=change), self.assertRaises(DistributionError):
                shared.validate_shared_extension(value, self.expected)
        for configured, receipt in ((False, self.trusted['checkpoint']), (True, None),
                                    (True, {'payload_verified': False}),
                                    (True, {'payload_verified': True, 'revision': 'wrong'})):
            value = {**self.trusted, 'model_configured': configured, 'checkpoint': receipt}
            with self.subTest(configured=configured, receipt=receipt), self.assertRaises(DistributionError):
                shared.validate_shared_extension(value, self.expected)
        value = copy.deepcopy(self.trusted)
        value['trust']['mtp2_shared_runtime']['runtime_policy']['max_batch'] = True
        with self.assertRaises(DistributionError):
            shared.validate_shared_extension(value, self.expected)

    def test_duplicate_or_changed_boundary_sources_and_binary_are_rejected(self):
        value = copy.deepcopy(self.trusted)
        value['trust']['source_files'].append(value['trust']['source_files'][0])
        with self.assertRaises(DistributionError):
            shared.validate_shared_extension(value, self.expected)
        self.write(self.package, 'scripts/distribution/mtp2_shared_runtime.py', b'changed\n')
        with self.assertRaises(DistributionError):
            shared.validate_shared_extension(self.trusted, self.expected)

    def test_launch_rehashes_binary_and_rejects_control_port_or_linked_cache(self):
        installation = shared.validate_shared_extension(self.trusted, self.expected)
        for port in (True, 0, 65536, 18080, '19111'):
            with self.subTest(port=port), self.assertRaises(DistributionError):
                shared.shared_argv(installation, port)
        cache = self.data / 'cache'
        cache.symlink_to(self.model, target_is_directory=True)
        with self.assertRaises(DistributionError):
            shared.shared_argv(installation, 19111)
        cache.unlink()
        self.write(self.data, shared.BINARY_PATH, b'changed binary\n')
        with self.assertRaises(DistributionError):
            shared.shared_argv(installation, 19111)

    def test_environment_and_physical_lease_keep_the_existing_closed_contract(self):
        base = {'HOME': str(self.root), 'PATH': '/usr/bin:/bin', 'TMPDIR': str(self.root), 'LANG': 'en_US.UTF-8'}
        self.assertEqual(shared.shared_env(base), {**base, 'LILY_QSA_ROUTE': 'split', 'LILY_QSA_SCORES': 'scalar'})
        with self.assertRaises(ValueError):
            shared.shared_env({**base, 'SECRET_KEY': 'synthetic-not-secret'})
        self.assertEqual(shared.global_gpu_lease(), Path.home() / 'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease')
        engine = shared.from_generated_trust(self.record, self.expected)
        fields = shared.runtime_identity_fields(engine)
        self.assertEqual(fields['resident_context_tokens'], 262144)
        self.assertNotIn('profile_id', fields)
        self.assertNotIn('public_alias', fields)


class NativeEvidenceTests(unittest.TestCase):
    EPOCH = 'e' * 64

    def ready(self):
        evidence = shared.NativeEvidence()
        for line in ('LILY_ENGINE_EFFECTIVE context_tokens=262144 kv_cache=bf16 mtp_drafts=2 max_batch=2',
                     'LILY_QSA_ROUTE_EFFECTIVE requested=split route=split', self.resident()):
            self.assertTrue(evidence.consume(line))
        return evidence

    def resident(self, **changes):
        values = {'generation': 1, 'load_count': 1, 'epoch': self.EPOCH,
                  'buffers': 140, 'bytes': 12000000, 'paged': 98000000, **changes}
        return ('LILY_MTP2_RESIDENT protocol=1 resident_context_tokens=262144 kv_cache=bf16 mtp_drafts=2 max_batch=2 '
                'weight_load_generation={generation} weight_load_count={load_count} weight_allocation_id={epoch} '
                'weight_buffer_count={buffers} weight_allocation_bytes={bytes} paged_weight_bytes={paged}').format(**values)

    def policy(self, request, profile='ctx64k-mtp2', **changes):
        values = {'id': request, 'profile': profile, 'context': shared.CONTEXTS[profile], 'prompt': 4096,
                  'output': 65536 if profile != 'ctx64k-mtp2' else 1024,
                  'epoch': self.EPOCH, 'generation': 1, 'load_count': 1, 'ns': 100, **changes}
        return ('[lily-meta] request_id={id} phase=mtp2_policy protocol=1 clock=mach_absolute_time_ns admitted_ns={ns} '
                'profile_id={profile} context_tokens={context} prompt_tokens={prompt} output_tokens={output} '
                'resident_context_tokens=262144 weight_load_generation={generation} weight_load_count={load_count} '
                'weight_allocation_id={epoch} mtp_drafts=2').format(**values)

    def acquire(self, evidence, request, profile='ctx64k-mtp2'):
        for line in (f'[lily-meta] request_id={request} phase=queue_enter',
                     f'[lily-meta] request_id={request} phase=lane_acquired queue_wait_ms=0',
                     self.policy(request, profile)):
            self.assertTrue(evidence.consume(line))

    def release(self, evidence, request, cancelled='false', **changes):
        self.assertTrue(evidence.consume(f'[lily-meta] request_id={request} phase=lane_released cancelled={cancelled} lane_ms=20'))
        epoch, ns = changes.get('epoch', self.EPOCH), changes.get('ns', 200)
        return evidence.consume(f'[lily-meta] request_id={request} phase=mtp2_released clock=mach_absolute_time_ns released_ns={ns} resident_epoch={epoch} cancelled={cancelled}')

    def test_peer_release_is_correlated_and_out_of_order_is_safe_for_evidence(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        self.acquire(evidence, 'req-100-2', 'ctx128k-mtp2')
        self.assertTrue(self.release(evidence, 'req-100-2'))
        self.assertEqual(evidence.snapshot()['active_request_ids'], ['req-100-1'])
        self.assertTrue(evidence.release_observed('req-100-2'))
        self.assertFalse(evidence.release_observed('req-100-1'))
        self.assertTrue(self.release(evidence, 'req-100-1', cancelled='true'))
        self.assertEqual(evidence.snapshot()['peak_active'], 2)
        self.assertEqual(evidence.snapshot()['released_sequence'], 2)

    def test_same_alias_is_two_independent_requests(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        self.acquire(evidence, 'req-100-2')
        self.assertEqual(evidence.active, {'req-100-1', 'req-100-2'})
        self.assertTrue(self.release(evidence, 'req-100-1'))
        self.assertFalse(evidence.release_observed('req-100-2'))

    def test_stderr_cannot_authorize_live_release_even_when_evidence_is_complete(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        self.assertTrue(self.release(evidence, 'req-100-1'))
        self.assertTrue(evidence.release_observed('req-100-1'))
        self.assertFalse(evidence.snapshot()['live_release_authorized'])
        with self.assertRaisesRegex(DistributionError, 'transport is not integrated'):
            evidence.require_live_release('req-100-1')

    def test_eof_preserves_active_and_invalidates_proof(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        evidence.channel_closed()
        self.assertEqual(evidence.snapshot()['active_request_ids'], ['req-100-1'])
        self.assertFalse(evidence.proof_valid)
        self.assertFalse(evidence.release_observed('req-100-1'))

    def test_allocation_counts_bytes_and_generation_must_be_actual_positive_values(self):
        for changes in ({'generation': 0}, {'load_count': 0}, {'generation': 1, 'load_count': 2},
                        {'generation': 2**64}, {'buffers': 0}, {'bytes': 0}, {'bytes': 2**64},
                        {'epoch': 'not-salted-id'}, {'paged': 2**64}):
            evidence = shared.NativeEvidence()
            with self.subTest(changes=changes):
                self.assertFalse(evidence.consume(self.resident(**changes)))
                self.assertFalse(evidence.proof_valid)

    def test_same_pid_reload_or_allocation_change_invalidates_no_reload_evidence(self):
        for changes in ({'generation': 2, 'load_count': 2}, {'epoch': 'f' * 64},
                        {'bytes': 12000001}, {'buffers': 141}, {'paged': 98000001}):
            evidence = self.ready()
            with self.subTest(changes=changes):
                self.assertFalse(evidence.consume(self.resident(**changes)))
                self.assertFalse(evidence.proof_valid)

    def test_wrong_engine_route_and_critical_unknown_fail_closed(self):
        for line in ('LILY_ENGINE_EFFECTIVE context_tokens=65536 kv_cache=bf16 mtp_drafts=2 max_batch=2',
                     'LILY_ENGINE_EFFECTIVE context_tokens=262144 kv_cache=q8 mtp_drafts=2 max_batch=2',
                     'LILY_ENGINE_EFFECTIVE context_tokens=262144 kv_cache=bf16 mtp_drafts=0 max_batch=2',
                     'LILY_ENGINE_EFFECTIVE context_tokens=262144 kv_cache=bf16 mtp_drafts=2 max_batch=1',
                     'LILY_QSA_ROUTE_EFFECTIVE requested=split route=query',
                     '[lily-meta] request_id=req-100-1 phase=unexpected',
                     'LILY_MTP2_RESIDENT forged=true'):
            evidence = shared.NativeEvidence()
            with self.subTest(line=line):
                self.assertFalse(evidence.consume(line))
                self.assertFalse(evidence.proof_valid)

    def test_bad_context_ceiling_output_or_residency_never_releases(self):
        for changes in ({'context': 262144}, {'prompt': 0}, {'prompt': 65536}, {'output': 0},
                        {'output': 65537}, {'prompt': 65530, 'output': 10},
                        {'epoch': 'f' * 64}, {'generation': 2}, {'load_count': 2}, {'ns': 0}):
            evidence = self.ready()
            evidence.consume('[lily-meta] request_id=req-100-1 phase=queue_enter')
            evidence.consume('[lily-meta] request_id=req-100-1 phase=lane_acquired queue_wait_ms=0')
            with self.subTest(changes=changes):
                self.assertFalse(evidence.consume(self.policy('req-100-1', **changes)))
                self.assertFalse(evidence.release_observed('req-100-1'))
                self.assertEqual(evidence.active, {'req-100-1'})

    def test_replayed_policy_acquire_or_release_does_not_complete_peer(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        self.acquire(evidence, 'req-100-2')
        self.assertTrue(self.release(evidence, 'req-100-1'))
        self.assertFalse(evidence.consume('[lily-meta] request_id=req-100-1 phase=lane_released cancelled=false lane_ms=20'))
        self.assertEqual(evidence.active, {'req-100-2'})
        self.assertFalse(evidence.release_observed('req-100-2'))
        for line in (self.policy('req-100-1'), '[lily-meta] request_id=req-100-1 phase=lane_acquired queue_wait_ms=0'):
            evidence = self.ready()
            self.acquire(evidence, 'req-100-1')
            self.assertFalse(evidence.consume(line))

    def test_release_needs_matching_epoch_order_timestamp_and_cancellation(self):
        for line in (f'[lily-meta] request_id=req-100-1 phase=mtp2_released clock=mach_absolute_time_ns released_ns=200 resident_epoch={self.EPOCH} cancelled=false',
                     '[lily-meta] request_id=req-100-2 phase=lane_released cancelled=false lane_ms=20'):
            evidence = self.ready()
            self.acquire(evidence, 'req-100-1')
            self.assertFalse(evidence.consume(line))
            self.assertEqual(evidence.active, {'req-100-1'})
        for changes in ({'epoch': 'f' * 64}, {'ns': 99}, {'ns': 2**64}):
            evidence = self.ready()
            self.acquire(evidence, 'req-100-1')
            self.assertFalse(self.release(evidence, 'req-100-1', **changes))
            self.assertEqual(evidence.active, {'req-100-1'})
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        evidence.consume('[lily-meta] request_id=req-100-1 phase=lane_released cancelled=true lane_ms=20')
        self.assertFalse(evidence.consume(f'[lily-meta] request_id=req-100-1 phase=mtp2_released clock=mach_absolute_time_ns released_ns=200 resident_epoch={self.EPOCH} cancelled=false'))

    def test_active_pending_and_replay_inventory_are_bounded(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        self.acquire(evidence, 'req-100-2')
        self.assertTrue(evidence.consume('[lily-meta] request_id=req-100-3 phase=queue_enter'))
        self.assertFalse(evidence.consume('[lily-meta] request_id=req-100-3 phase=lane_acquired queue_wait_ms=0'))
        self.assertEqual(evidence.active, {'req-100-1', 'req-100-2'})
        evidence = self.ready()
        for index in range(4):
            self.assertTrue(evidence.consume(f'[lily-meta] request_id=req-100-{index} phase=queue_enter'))
        self.assertFalse(evidence.consume('[lily-meta] request_id=req-100-9 phase=queue_enter'))
        for bound in (True, 0, 5, 4097):
            with self.subTest(bound=bound), self.assertRaises(DistributionError):
                shared.NativeEvidence(request_limit=bound)

    def test_progress_is_private_bounded_and_request_local(self):
        evidence = self.ready()
        self.acquire(evidence, 'req-100-1')
        self.acquire(evidence, 'req-100-2')
        self.assertTrue(evidence.consume('[lily-meta] request_id=req-100-2 phase=prefill_progress completed_chunks=1 prefilled_tokens=4096'))
        self.assertIsNone(evidence.requests['req-100-1']['progress'])
        self.assertFalse(evidence.consume('[lily-meta] request_id=req-100-2 phase=prefill_progress completed_chunks=2 prefilled_tokens=4097'))

    def test_queue_terminal_requires_the_complete_fixed_grammar(self):
        for suffix in ('queue_rejected status=503 queue_slot_released=true',
                       'queue_cancelled queue_wait_ms=20 queue_slot_released=true',
                       'cancelled_after_engine_ready queue_wait_ms=20 model_load_aborted=false'):
            evidence = self.ready()
            self.assertTrue(evidence.consume('[lily-meta] request_id=req-100-1 phase=queue_enter'))
            self.assertTrue(evidence.consume('[lily-meta] request_id=req-100-1 phase=' + suffix))
            self.assertEqual(evidence.pending, set())
            self.assertFalse(evidence.release_observed('req-100-1'))
        evidence = self.ready()
        evidence.consume('[lily-meta] request_id=req-100-1 phase=queue_enter')
        self.assertFalse(evidence.consume('[lily-meta] request_id=req-100-1 phase=queue_rejected status=503'))

    def test_arbitrary_raw_text_is_not_retained_and_snapshots_are_detached(self):
        evidence = self.ready()
        self.assertFalse(evidence.consume('synthetic prompt-bearing stderr must not persist'))
        self.assertTrue(evidence.proof_valid)
        self.assertNotIn('synthetic prompt-bearing', json.dumps(evidence.snapshot()))
        snapshot = evidence.snapshot()
        snapshot['weight_residency']['epoch'] = 'f' * 64
        snapshot['native_engine_effective']['mtp_drafts'] = 0
        self.assertEqual(evidence.resident['epoch'], self.EPOCH)
        self.assertEqual(evidence.engine['mtp_drafts'], 2)
        self.assertFalse(evidence.consume('LILY_ENGINE_EFFECTIVE\nforged=true'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
