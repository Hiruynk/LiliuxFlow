# SPDX-License-Identifier: Apache-2.0
"""Offline catalog, no-model lifecycle and explicit local attach CPU counterexamples."""
import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
import build_native
from common import DistributionError
import liliuxflow as cli
import litellm_profile_policy as policy
import model_catalog as catalog
import model_installer as installer
import runtime_proof
import trust
import test_context_profiles

ROOT = Path(__file__).resolve().parents[2]


class CatalogTests(unittest.TestCase):
    def test_global_version_is_offline_without_an_installation(self):
        with patch.object(installer, '_sdk', side_effect=AssertionError('version must not initialize SDK')), patch.object(cli, 'load_install', side_effect=AssertionError('version must not read installation')), patch.object(sys, 'argv', ['lf', '--version']), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as result:cli.main()
        self.assertEqual(result.exception.code, 0);self.assertEqual(output.getvalue().strip(), 'LiliuxFlow 0.1.0')
    def test_canonical_inventory_is_referenced_once_with_three_profiles_and_no_default_download(self):
        model = catalog.select_model(ROOT, catalog.MODEL_ID)
        self.assertEqual(model.total_bytes, 105_518_409_249)
        self.assertEqual(len(model.inventory['files']), 67)
        self.assertEqual([p.context_tokens for p in model.profiles], [65536, 131072, 262144])
        self.assertFalse(model.entry['install_by_default'])
        self.assertFalse(model.entry['redistribute_payload'])
        self.assertNotIn('files', model.entry)

    def test_offline_list_and_info_work_without_install_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            nonexistent = Path(folder) / 'not-created'
            for command in ('list', 'info'):
                args = [sys.executable, str(ROOT / 'scripts/distribution/liliuxflow.py'), '--data-root', str(nonexistent), 'models', command]
                if command == 'info':args.append(catalog.MODEL_ID)
                result = subprocess.run(args, text=True, capture_output=True, env={**os.environ, 'HF_HUB_OFFLINE': '1'})
                self.assertEqual(result.returncode, 0, result.stdout)
                value = json.loads(result.stdout)
                self.assertFalse(value['network_operation'])
                self.assertFalse(nonexistent.exists())
            with patch.object(cli, 'load_install', side_effect=AssertionError('offline catalog must not read installation')), patch.object(cli.subprocess, 'run', side_effect=AssertionError('offline catalog must not fetch SDK')):
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.models(argparse.Namespace(model_command='list', data_root=nonexistent))

    def test_catalog_rejects_main_format_auto_install_and_inconsistent_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            for value in ('manifests/distribution/native-sources.json', catalog.INVENTORY_PATH, 'profiles/distribution/context-registry.json'):
                target = source / value; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / value, target)
            path = source / 'manifests/distribution/native-sources.json'; original = json.loads(path.read_text())
            for field, value in (('revision', 'main'), ('format', 'other'), ('install_by_default', True), ('redistribute_payload', True), ('kind', 'github-source')):
                document = copy.deepcopy(original);document['optional_models'][catalog.MODEL_ID][field] = value;path.write_text(json.dumps(document))
                with self.assertRaises(DistributionError):catalog.load_catalog(source)
            path.write_text(json.dumps(original))
            inventory = source / catalog.INVENTORY_PATH; document = json.loads(inventory.read_text());document['files'][0]['path'] = '../escape';inventory.write_text(json.dumps(document))
            with self.assertRaises(DistributionError):catalog.load_catalog(source)
        with self.assertRaises(DistributionError):catalog.select_model(ROOT, 'arbitrary/model')

    def test_install_without_execution_choice_exits_before_helper_or_payload(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/distribution/liliuxflow.py'), 'models', 'install', catalog.MODEL_ID, '--output', '/not-created'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--dry-run', result.stderr)
        self.assertIn('--execute', result.stderr)


class NoModelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_context_profiles.ContextTrustTests()
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.fixture._new_receipt()
        self.base = self.fixture.fixture
        self.data = self.base.data
        # Canonical metadata is public source; no real checkpoint file is needed.
        extras = ['scripts/distribution/model_catalog.py', 'scripts/distribution/model_installer.py',
                  'services/model-installer/pyproject.toml', 'services/model-installer/uv.lock',
                  'manifests/distribution/native-sources.json', catalog.INVENTORY_PATH]
        for value in extras:
            file = self.base.source / value;file.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(ROOT / value, file)
            self.base.record['source_files'] = [r for r in self.base.record['source_files'] if r['path'] != value]
            self.base.record['source_files'].append({'path': value, 'sha256': trust.sha256(file)})
        self.base.record['optional_model_support'] = {'schema_version': 1}
        self.base.config.update({'schema_version': 2, 'model_dir': None, 'model_id': None,
                                 'model_state': 'not_configured', 'checkpoint': None, 'caller_key_created': False})
        trust.atomic_private_json(self.data / 'install.json', self.base.config)
        trust.atomic_private_json(self.data / 'runtime/release-trust.json', self.base.record)
        (self.data / 'runtime/checkpoint-verified.json').unlink()

    def test_no_checkpoint_receipt_and_runtime_credentials_source_and_binary_still_validate(self):
        before = (self.data / 'secrets/bootstrap.json').read_bytes()
        trusted = trust.validate(self.data, require_checkpoint='available')
        self.assertFalse(trusted['model_configured']);self.assertIsNone(trusted['checkpoint']);self.assertEqual(trusted['profiles'], ())
        self.assertFalse((self.data / 'runtime/checkpoint-verified.json').exists())
        self.assertFalse((self.data / 'runtime/checkpoint-metadata.json').exists())
        self.assertEqual(before, (self.data / 'secrets/bootstrap.json').read_bytes())
        for name in ('lily',):
            (self.data / 'runtime/bin' / name).write_text('tampered')
            with self.assertRaises(DistributionError):trust.validate(self.data, require_checkpoint='available')

    def test_direct_model_entry_and_fake_absence_schema_refuse(self):
        with self.assertRaises(DistributionError):trust.validate(self.data)
        with self.assertRaises(DistributionError):trust.verify_checkpoint(self.data)
        with self.assertRaises(DistributionError):trust.verify_metadata(self.data)
        self.base.config['model_dir'] = str(self.base.model)
        trust.atomic_private_json(self.data / 'install.json', self.base.config)
        with self.assertRaises(DistributionError):trust.validate(self.data, require_checkpoint='available')

    def test_no_model_configs_and_catalog_do_not_grant_any_route_or_validation(self):
        trusted = trust.validate(self.data, require_checkpoint='available')
        manager, lp = agent.configs(trusted)
        self.assertEqual(json.loads(manager.read_text())['models'], {})
        self.assertEqual(json.loads(lp.read_text())['model_list'], [])
        access = policy.parse_policy(json.loads((self.data / 'run/context-profile-policy.json').read_text()))
        for alias in (policy.DEFAULT_MODEL, *policy.LONG_PROFILE_MODELS, 'liliuxflow:model-not-configured'):
            self.assertFalse(policy.explicit_profile_access(alias, ['*'], policy=access))
        with self.assertRaises(DistributionError):agent.validation_routes(trusted, ('ctx128k',))
        hooks = runtime_proof.portable_guard_kwargs(trusted)
        self.assertEqual(hooks['registry'].enabled_profiles, ())
        self.assertFalse((self.data / 'run/model.json').exists())

    def test_start_plan_and_stop_do_not_load_model_or_create_gpu_lease(self):
        with patch.object(installer, '_sdk', side_effect=AssertionError('no implicit SDK/network')), patch.object(agent.subprocess, 'run', side_effect=AssertionError('no host lifecycle for CPU plan')):
            plan = agent.start(self.data, dry_run=True)
            stopped = agent.stop(self.data)
        self.assertEqual(plan['model_load'], 'not_configured');self.assertFalse(plan['inference_ready'])
        self.assertEqual(stopped['state'], 'already_stopped')
        self.assertFalse((self.data / 'run/model.json').exists())

    def test_initialized_database_and_keys_preserved_without_a_checkpoint(self):
        with patch.object(agent, 'postgres_tools', return_value=Path('/fixture-tools')), patch.object(agent, 'pg_start', side_effect=AssertionError('existing DB must not restart')):
            result = agent.initialize(self.data)
        self.assertEqual(result['state'], 'already_initialized')
        caller = self.data / 'secrets/caller.json';trust.atomic_private_json(caller, {'api_key': 'sk-fixture-no-reset'})
        with patch.object(agent, 'http_json', side_effect=AssertionError('existing caller must not change')):
            agent.make_caller(trust.validate(self.data, require_checkpoint='available'))
        self.assertEqual(json.loads(caller.read_text())['api_key'], 'sk-fixture-no-reset')

    def test_fresh_initialization_uses_owned_database_tools_without_model_receipts(self):
        self.base.config['database_initialized'] = False
        trust.atomic_private_json(self.data / 'install.json', self.base.config)
        def fixture_run(argv, **kwargs):
            if str(argv[0]).endswith('/psql') and 'SELECT count(*)' in kwargs.get('input_text', ''):return '0'
            return ''
        with patch.object(agent, 'preflight_ports'), patch.object(agent, 'postgres_tools', return_value=Path('/fixture-tools')), patch.object(agent, 'private_run', side_effect=fixture_run) as commands, patch.object(agent, 'pg_start', return_value={'fixture': True}), patch.object(agent, 'pg_stop'):
            result = agent.initialize(self.data)
        self.assertEqual(result['state'], 'database_initialized');self.assertFalse(result['model_loaded'])
        self.assertTrue(cli.load_install(self.data)['database_initialized'])
        self.assertTrue(any(str(call.args[0][0]).endswith('/initdb') for call in commands.call_args_list))
        self.assertFalse((self.data / 'runtime/checkpoint-verified.json').exists())
        self.assertFalse((self.data / 'run/model.json').exists())

    def test_authenticated_compat_requests_are_not_found_and_never_forward_inference(self):
        sys.path.insert(0, str(ROOT / 'services/compat/src'))
        from compat_api.app import Settings, create_app
        from fastapi.testclient import TestClient
        import httpx
        calls = []
        def transport(request):
            calls.append(request)
            self.assertEqual(request.method, 'GET');self.assertEqual(request.url.path, '/v1/models')
            return httpx.Response(200, json={'data': []})
        settings = Settings(litellm_base_url='http://127.0.0.1:14000', public_alias=policy.DEFAULT_MODEL,
                            runtime_model_id='Qwen3.8-Flash-Next', context_limit_tokens=65536,
                            checkpoint_revision=None, checkpoint_manifest_sha256=None, checkpoint_file_count=0,
                            model_configured=False)
        app = create_app(settings, transport=httpx.MockTransport(transport))
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/chat', json={'model': policy.DEFAULT_MODEL, 'messages': []}).status_code, 401)
            headers = {'Authorization': 'Bearer sk-synthetic-caller'}
            result = client.post('/api/chat', headers=headers, json={'model': policy.DEFAULT_MODEL, 'messages': []})
            self.assertEqual(result.status_code, 404)
            capability = client.get('/api/capabilities', headers=headers).json()
            self.assertFalse(capability['model_configured']);self.assertFalse(capability['inference_ready']);self.assertEqual(capability['models'], [])
            identity = client.get('/api/model-blob-identity', params={'model': policy.DEFAULT_MODEL}, headers=headers).json()
            self.assertFalse(identity['checkpoint_manifest_present'])
        self.assertEqual(len(calls), 3)

    def test_guard_disabled_registry_rejects_generation_before_resource_probe(self):
        sys.path.insert(0, str(ROOT / 'services/compat/src'))
        from compat_api.llama_guard import create_app
        from fastapi.testclient import TestClient
        import httpx
        trusted = trust.validate(self.data, require_checkpoint='available')
        hooks = runtime_proof.portable_guard_kwargs(trusted)
        async def proof(*args):raise AssertionError('no-model guard must not acquire or inspect a model resource')
        hooks['resource_probe'] = proof
        def transport(request):
            raise AssertionError('no-model generation must not reach the manager')
        app = create_app(manager_url='http://127.0.0.1:18081', backend_token='synthetic-backend',
                         transport=httpx.MockTransport(transport), **hooks)
        with TestClient(app) as client:
            result = client.post('/v1/chat/completions', headers={'Authorization': 'Bearer synthetic-backend'},
                                 json={'model': policy.DEFAULT_MODEL, 'messages': []})
            self.assertEqual(result.status_code, 503)
        self.assertFalse((self.data / 'run/model.json').exists())

    def test_new_controlplane_caller_has_explicit_unavailable_acl_and_keeps_authentication(self):
        calls = []
        def fake_http(url, **kwargs):
            calls.append((url, kwargs))
            return 200, {'key': 'sk-synthetic-only-key-for-no-model'}
        with patch.object(agent, 'http_json', side_effect=fake_http):
            agent.make_caller(trust.validate(self.data, require_checkpoint='available'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]['payload']['models'], ['liliuxflow:model-not-configured'])
        self.assertNotEqual(calls[1][1]['payload']['models'], [])

    def test_doctor_management_readiness_is_separate_from_inference(self):
        with patch.object(installer, '_sdk', side_effect=AssertionError('no implicit SDK/network')), patch.object(cli, 'host_metadata', return_value={'os': 'Darwin', 'machine': 'arm64', 'ram_bytes': 64 * 1024**3, 'chip': 'Apple M4 Max'}), patch.object(cli, 'listening', return_value=False), patch.object(cli.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)), patch.object(cli, 'checkpoint_metadata', side_effect=AssertionError('no checkpoint metadata read')), patch.object(cli, 'emit') as emit:
            self.assertEqual(cli.doctor(argparse.Namespace(data_root=self.data, controlplane_only=False)), 0)
        value = emit.call_args.args[0]
        self.assertEqual(value['state'], 'controlplane_ready');self.assertTrue(value['controlplane_ready']);self.assertFalse(value['inference_ready']);self.assertFalse(value['payload_verified']);self.assertIsNone(value['model'])

    def test_native_source_view_includes_helper_locks_and_model_license_without_weights(self):
        copied = self.data / 'runtime/new-source-fixture'
        view, records = build_native.install_source_view(ROOT, self.data, 'cpu-fixture', destination_name=copied.name)
        for value in ('scripts/distribution/model_catalog.py', 'scripts/distribution/model_installer.py', 'services/model-installer/uv.lock', 'services/model-installer/pyproject.toml', 'docs/productization/MODEL_LICENSE.txt'):
            self.assertEqual((view / value).read_bytes(), (ROOT / value).read_bytes())
        self.assertFalse(any(file.suffix == '.safetensors' for file in view.rglob('*')))

    def test_runtime_source_with_optional_support_refuses_missing_helper_lock(self):
        source = self.base.source
        license_file = source / 'docs/productization/MODEL_LICENSE.txt'
        license_file.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(ROOT / 'docs/productization/MODEL_LICENSE.txt', license_file)
        (source / 'services/model-installer/uv.lock').unlink()
        with self.assertRaises(DistributionError):build_native.install_source_view(source, self.data, 'cpu-missing-lock', destination_name='incomplete-source')
        self.assertFalse((self.data / 'runtime/incomplete-source').exists())


class SetupAttachTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='optional Unicode 模型 space-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve();self.data = self.base / 'data 安裝'
        args = argparse.Namespace(data_root=self.data, model_dir=None, without_model=True, port=None)
        with contextlib.redirect_stdout(io.StringIO()):cli.setup(args)
        self.config = cli.load_install(self.data)

    def test_setup_repeat_and_legacy_read_do_not_migrate_identity_keys_database_or_model(self):
        before = (self.data / 'install.json').read_bytes();keys = (self.data / 'secrets/bootstrap.json').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):cli.setup(argparse.Namespace(data_root=self.data, model_dir=None, without_model=True, port=None))
        self.assertEqual(before, (self.data / 'install.json').read_bytes());self.assertEqual(keys, (self.data / 'secrets/bootstrap.json').read_bytes())
        self.assertIsNone(self.config['model_dir']);self.assertIsNone(self.config['checkpoint'])
        legacy = copy.deepcopy(self.config);legacy.update(schema_version=1, model_dir=str(self.base / 'existing-external'), checkpoint={'existing': True})
        for name in ('model_state', 'model_id'):legacy.pop(name)
        trust.atomic_private_json(self.data / 'install.json', legacy);raw = (self.data / 'install.json').read_bytes()
        self.assertEqual(cli.load_install(self.data), legacy);self.assertEqual(raw, (self.data / 'install.json').read_bytes())

    def fixture_model(self):
        directory = self.base / 'external 模型';directory.mkdir()
        config = {'model_type': 'qwen4_exp', 'lily': {'format': 'qwen4_exp-affine-v1', 'quantization': {'default': {'bits': 4, 'mode': 'affine', 'group_size': 64}}}}
        (directory / 'config.json').write_text(json.dumps(config))
        for name in ('tokenizer.json', 'tokenizer_config.json', 'generation_config.json'):(directory / name).write_text('{}')
        for name in ('chat_template.jinja', 'LICENSE'):(directory / name).write_text('synthetic CPU fixture')
        (directory / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'fixture': 'shard.safetensors'}}))
        (directory / 'shard.safetensors').write_bytes(b'fixture!')
        inventory = {'files': [{'path': p.name, 'size_bytes': p.stat().st_size, 'sha256': trust.sha256(p)} for p in directory.iterdir()]}
        manifest = self.base / 'fixture-inventory.json';manifest.write_text(json.dumps(inventory))
        entry = dict(catalog.select_model(ROOT, catalog.MODEL_ID).entry)
        return directory, catalog.OptionalModel(catalog.MODEL_ID, entry, inventory, manifest, ())

    def test_attach_offline_verifies_then_preserves_identity_keys_database_and_path(self):
        directory, model = self.fixture_model();keys = (self.data / 'secrets/bootstrap.json').read_bytes()
        with patch.object(installer, 'select_model', return_value=model), patch.object(installer, '_launchd_loaded', return_value=False) as host:
            result = installer.attach_model(self.data, catalog.MODEL_ID, directory)
        config = cli.load_install(self.data)
        self.assertEqual(config['installation_id'], self.config['installation_id']);self.assertEqual(config['database_initialized'], self.config['database_initialized'])
        self.assertEqual(keys, (self.data / 'secrets/bootstrap.json').read_bytes())
        self.assertEqual(config['model_dir'], str(directory));self.assertTrue(result['payload_verified']);self.assertFalse(result['network_operation']);self.assertFalse(result['service_restarted'])
        self.assertTrue(json.loads((self.data / 'runtime/checkpoint-verified.json').read_text())['payload_verified'])
        self.assertFalse(json.loads((self.data / 'runtime/checkpoint-metadata.json').read_text())['payload_verified'])
        self.assertEqual(host.call_count, 1)

    def test_attach_checksum_fail_or_running_marker_never_changes_install_or_keys(self):
        directory, model = self.fixture_model();before = (self.data / 'install.json').read_bytes()
        (directory / 'shard.safetensors').write_bytes(b'corrupt!')
        with patch.object(installer, 'select_model', return_value=model), patch.object(installer, '_launchd_loaded', return_value=False):
            with self.assertRaises(DistributionError):installer.attach_model(self.data, catalog.MODEL_ID, directory)
            (self.data / 'run/stack.plist').write_text('active-fixture')
            with self.assertRaises(DistributionError):installer.attach_model(self.data, catalog.MODEL_ID, directory)
        self.assertEqual(before, (self.data / 'install.json').read_bytes());self.assertFalse((self.data / 'runtime/checkpoint-verified.json').exists())

    def test_model_paths_source_private_tree_symlink_and_unrelated_existing_destination_refuse(self):
        link = self.base / 'linked';link.symlink_to(self.data, target_is_directory=True)
        for output in (ROOT / 'model', self.data / 'model', link / 'model'):
            with self.assertRaises(DistributionError):installer.model_destination(output, ROOT, self.data)
        with installer.download_lock(self.base / 'fixture-target'):
            with self.assertRaises(DistributionError):
                with installer.download_lock(self.base / 'fixture-target'):pass
        staging = self.base / 'stage';staging.mkdir(mode=0o700);target = self.base / 'existing';target.mkdir()
        with self.assertRaises(DistributionError):installer._publish(staging, target)
        self.assertTrue(staging.exists());self.assertTrue(target.exists())

    def test_readonly_attach_under_another_git_checkout_does_not_inherit_download_restriction(self):
        (self.base / '.git').mkdir()
        source = self.base / 'isolated-product-source';source.mkdir()
        directory, _model = self.fixture_model()
        config = cli.load_install(self.data);config['source_root'] = str(source)
        trust.atomic_private_json(self.data / 'install.json', config)
        self.assertEqual(installer.attached_model_directory(directory, source, self.data), directory)
        with self.assertRaises(DistributionError):installer.model_destination(self.base / 'new-download', source, self.data)
        with self.assertRaises(DistributionError):installer.attached_model_directory(source, source, self.data)

    def test_complete_bytes_in_owned_partial_directory_cannot_be_attached_or_set_up(self):
        directory, model = self.fixture_model()
        (directory / installer.STAGING_MARKER).write_text('{}')
        with self.assertRaises(DistributionError):cli.checkpoint_metadata(directory)
        with patch.object(installer, 'select_model', return_value=model):
            with self.assertRaises(DistributionError):installer.attach_model(self.data, catalog.MODEL_ID, directory)


if __name__ == '__main__':
    unittest.main(verbosity=2)
