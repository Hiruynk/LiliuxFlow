# SPDX-License-Identifier: Apache-2.0
"""Locked official SDK with MockTransport: no listener or external model payload."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
from model_catalog import HUB_VERSION, MODEL_ID, OptionalModel, select_model
import model_installer as installer
import trust

os.environ.update({'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'HF_HUB_DISABLE_XET': '1',
                   'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_PROGRESS_BARS': '1',
                   'HF_HUB_VERBOSITY': 'error'})
SDK_AVAILABLE = importlib.util.find_spec('huggingface_hub') is not None
if SDK_AVAILABLE:
    import httpx
    import huggingface_hub as hub
    from huggingface_hub import constants
    from huggingface_hub._local_folder import get_local_download_paths

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(SDK_AVAILABLE, 'run with the independent services/model-installer locked environment')
class OfficialSDKTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(hub.__version__, HUB_VERSION)
        self.temporary = tempfile.TemporaryDirectory(prefix='model SDK CPU Unicode 模型-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.output = self.base / 'new external 模型 space'
        self.payloads = {'config.json': b'{"fixture":true}', 'LICENSE': b'original fixture license',
                         'model-00001-of-00002.safetensors': b'0123456789abcdef',
                         'model-00002-of-00002.safetensors': b'fedcba9876543210'}
        self.entry = select_model(ROOT, MODEL_ID).entry
        self.inventory = {'files': [{'path': name, 'size_bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                                    for name, value in self.payloads.items()]}
        self.manifest = self.base / 'small-fixture-inventory.json';self.manifest.write_text(json.dumps(self.inventory))
        self.model = OptionalModel(MODEL_ID, self.entry, self.inventory, self.manifest, ())
        self.requests = [];self.api_error = None;self.wrong_revision = False;self.missing = None
        self.corrupt = None;self.interrupt = None;self.interrupted = False;self.received = 0
        constants.HF_HUB_DISABLE_XET = True
        constants.HF_HUB_DISABLE_IMPLICIT_TOKEN = True
        installer._sdk()  # Checks the actual version and suppresses private SDK exception details.
        hub.set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(self.transport), follow_redirects=True))
        self.addCleanup(lambda: hub.set_client_factory(lambda: httpx.Client()))

    def transport(self, request):
        self.requests.append({'method': request.method, 'path': request.url.path,
                              'range': request.headers.get('range'), 'authorization': request.headers.get('authorization')})
        self.assertEqual(request.url.host, 'huggingface.co')
        if request.method == 'GET' and request.url.path.startswith('/api/models/'):
            if self.api_error is not None:
                code, name = self.api_error
                return httpx.Response(code, headers={'X-Error-Code': name}, json={'error': 'fixture denial, secret-never-log'})
            names = [name for name in self.payloads if name != self.missing]
            return httpx.Response(200, json={'id': self.entry['repository'],
                'sha': 'b' * 40 if self.wrong_revision else self.entry['revision'], 'siblings': [{'rfilename': name} for name in names]})
        name = request.url.path.split('/resolve/')[-1].split('/', 1)[-1]
        if name not in self.payloads:raise AssertionError('unexpected SDK path')
        payload = self.payloads[name]
        headers = {'X-Repo-Commit': 'b' * 40 if self.wrong_revision else self.entry['revision'],
                   'ETag': hashlib.sha256(payload).hexdigest(), 'Content-Length': str(len(payload))}
        if request.method == 'HEAD':return httpx.Response(200, headers=headers)
        self.assertEqual(request.method, 'GET')
        if name == self.corrupt:payload = b'X' * len(payload)
        offset = 0
        if request.headers.get('range'):
            value = request.headers['range'];self.assertTrue(value.startswith('bytes='));offset = int(value[6:-1])
        if name == self.interrupt and not self.interrupted:
            self.interrupted = True
            owner = self
            class InterruptedStream(httpx.SyncByteStream):
                def __iter__(self):
                    owner.received += 4
                    yield payload[:4]
                    raise KeyboardInterrupt()
            return httpx.Response(200, headers=headers, stream=InterruptedStream())
        content = payload[offset:];self.received += len(content)
        if offset:
            headers.update({'Content-Length': str(len(content)), 'Content-Range': f'bytes {offset}-{len(payload)-1}/{len(payload)}'})
        return httpx.Response(206 if offset else 200, headers=headers, content=content)

    def call(self, *, dry_run=False, execute=False, token=False):
        with patch.object(installer, 'select_model', return_value=self.model), patch.object(installer.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)):
            return installer.install_model(ROOT, MODEL_ID, self.output, execute=execute, dry_run=dry_run,
                                            snapshot=hub.snapshot_download, token=token)

    def test_actual_sdk_dry_run_has_metadata_requests_only_and_bytes_zero(self):
        result = self.call(dry_run=True)
        self.assertEqual(result['state'], 'model_download_plan');self.assertEqual(result['remaining_download_bytes'], self.model.total_bytes)
        self.assertEqual(result['downloaded_bytes'], 0);self.assertEqual(self.received, 0);self.assertFalse(self.output.exists())
        self.assertTrue(all(row['method'] == 'HEAD' or row['path'].startswith('/api/models/') for row in self.requests))
        self.assertTrue(all(row['authorization'] is None for row in self.requests))

    def test_fresh_home_missing_models_parent_refuses_before_sdk_or_lock(self):
        home = self.base / 'fresh home'
        home.mkdir(mode=0o700)
        self.output = home / 'Models' / 'Qwen3.8-Flash-Next-lily-q4'
        with patch.object(installer, '_sdk', side_effect=AssertionError('missing parent must not initialize SDK')) as sdk:
            with self.assertRaisesRegex(DistributionError, 'existing local parent directory'):
                self.call(dry_run=True)
            sdk.assert_not_called()
        self.assertEqual(self.requests, [])
        self.assertEqual(list(home.iterdir()), [])

    def test_documented_parent_only_preparation_reaches_locked_sdk_mock_metadata(self):
        home = self.base / 'fresh home'
        home.mkdir(mode=0o700)
        self.output = home / 'Models' / 'Qwen3.8-Flash-Next-lily-q4'
        subprocess.run(['/bin/sh', '-c', 'mkdir -p "$(dirname "$MODEL_DIR")"'],
                       env={**os.environ, 'MODEL_DIR': str(self.output)}, check=True)
        self.assertTrue(self.output.parent.is_dir())
        self.assertFalse(self.output.exists())
        result = self.call(dry_run=True)
        self.assertEqual(result['state'], 'model_download_plan')
        self.assertTrue(self.requests)
        self.assertEqual(self.received, 0)
        self.assertEqual(result['downloaded_bytes'], 0)
        self.assertFalse(self.output.exists())
        staging = self.output.with_name('.' + self.output.name + '.liliuxflow-partial')
        self.assertFalse(any(staging.glob('*.safetensors')))

    def test_actual_sdk_download_verification_and_exclusive_atomic_publish_have_one_copy(self):
        result = self.call(execute=True)
        self.assertEqual(result['state'], 'model_installed');self.assertTrue(result['payload_verified']);self.assertFalse(result['model_loaded'])
        self.assertFalse(self.output.with_name('.' + self.output.name + '.liliuxflow-partial').exists())
        for name, payload in self.payloads.items():self.assertEqual((self.output / name).read_bytes(), payload)
        self.assertEqual(self.received, self.model.total_bytes)
        count = len(self.requests);second = self.call(execute=True)
        self.assertEqual(second['state'], 'already_installed');self.assertEqual(len(self.requests), count)
        receipt = json.loads((self.output / installer.COMPLETE_MARKER).read_text())
        self.assertEqual(len(receipt['files']), len(self.payloads))

    def test_actual_sdk_interruption_preserves_incomplete_and_range_resumes(self):
        self.interrupt = 'model-00001-of-00002.safetensors'
        with patch.object(constants, 'DOWNLOAD_CHUNK_SIZE', 4):
            with self.assertRaises(KeyboardInterrupt):self.call(execute=True)
        staging = self.output.with_name('.' + self.output.name + '.liliuxflow-partial')
        item = next(row for row in self.inventory['files'] if row['path'] == self.interrupt)
        partial = get_local_download_paths(staging, self.interrupt).incomplete_path(item['sha256'])
        self.assertEqual(partial.read_bytes(), self.payloads[self.interrupt][:4]);self.assertFalse(self.output.exists())
        self.assertFalse((staging / installer.COMPLETE_MARKER).exists())
        preview = self.call(dry_run=True);self.assertEqual(preview['resumable_bytes'], 4)
        self.requests.clear();self.call(execute=True)
        requests = [row for row in self.requests if row['method'] == 'GET' and row['path'].endswith('/' + self.interrupt)]
        self.assertEqual(len(requests), 1);self.assertEqual(requests[0]['range'], 'bytes=4-')
        self.assertEqual((self.output / self.interrupt).read_bytes(), self.payloads[self.interrupt])
        self.assertEqual(self.received, self.model.total_bytes)

    def test_wrong_revision_missing_shard_and_checksum_never_publish_or_fallback(self):
        self.wrong_revision = True
        with self.assertRaises(DistributionError):self.call(execute=True)
        self.assertFalse(self.output.exists());self.assertEqual(self.received, 0)
        self.wrong_revision = False;self.missing = 'model-00002-of-00002.safetensors'
        with self.assertRaises(DistributionError):self.call(execute=True)
        self.assertFalse(self.output.exists());self.assertEqual(self.received, 0)
        self.missing = None;self.corrupt = 'model-00002-of-00002.safetensors'
        with self.assertRaises(DistributionError):self.call(execute=True)
        self.assertFalse(self.output.exists());self.assertFalse(any('/main/' in row['path'] for row in self.requests))

    def test_403_404_wrong_revision_error_is_sanitized_and_downloads_nothing(self):
        for code, name in ((403, 'GatedRepo'), (404, 'RepoNotFound'), (404, 'RevisionNotFound')):
            self.api_error = (code, name)
            with self.assertRaises(DistributionError) as raised:self.call(execute=True, token='synthetic-token-never-log')
            self.assertNotIn('synthetic-token-never-log', str(raised.exception));self.assertNotIn('secret-never-log', str(raised.exception))
            self.assertFalse(self.output.exists());self.assertEqual(self.received, 0)

    def test_timeout_disk_shortfall_unrelated_destination_and_busy_lock_leave_existing_files(self):
        with patch.object(installer, 'select_model', return_value=self.model):
            with patch.object(installer.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
                with self.assertRaises(DistributionError):installer.install_model(ROOT, MODEL_ID, self.output, execute=True, snapshot=hub.snapshot_download)
        self.assertEqual(self.received, 0)
        def timeout(**kwargs):raise httpx.ReadTimeout('do-not-log token=synthetic-token')
        with patch.object(installer, 'select_model', return_value=self.model):
            with self.assertRaises(DistributionError) as error:installer.install_model(ROOT, MODEL_ID, self.output, execute=True, snapshot=timeout)
            self.assertNotIn('synthetic-token', str(error.exception))
            with installer.download_lock(self.output):
                with self.assertRaises(DistributionError):installer.install_model(ROOT, MODEL_ID, self.output, execute=True, snapshot=hub.snapshot_download)
            self.output.mkdir(mode=0o700);(self.output / 'user-file').write_bytes(b'preserve')
            with self.assertRaises((DistributionError, OSError)):installer.install_model(ROOT, MODEL_ID, self.output, execute=True, snapshot=hub.snapshot_download)
            self.assertEqual((self.output / 'user-file').read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main(verbosity=2)
