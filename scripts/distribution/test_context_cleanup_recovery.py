# SPDX-License-Identifier: Apache-2.0
"""CPU-only exact cleanup recovery tests, with synthetic files and transports."""


import asyncio


import copy


import json


import os


from pathlib import Path


import sys


import tempfile


import time


from types import SimpleNamespace


import unittest


from unittest.mock import patch


import httpx


ROOT = Path(__file__).resolve().parents[2]


sys.path.insert(0, str(ROOT / 'scripts/distribution'))


sys.path.insert(0, str(ROOT / 'services/compat/src'))


from profile_registry import load_registry


from runtime_proof import RunnerResourceProbe


from trust import atomic_private_json


from compat_api.llama_guard import create_app, _ProfileTicket


class LeaseRecoveryTests(unittest.TestCase):
    def test_only_exact_gone_owner_lease_reaped_and_native_counter_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); run = root / 'run'; leases = root / 'leases'
            run.mkdir(mode=0o700); leases.mkdir(mode=0o700)
            registry = load_registry(ROOT); profile = registry.by_id('ctx262k', allow_validation=True)
            ident = {'pid': 99001, 'ppid': 99000, 'pgid': 99000, 'uid': os.getuid(),
                     'started': 'synthetic-start', 'command_sha256': 'a' * 64}
            record = {'schema_version': 1, 'installation_id': 'CPU-install', 'runner': {**ident, 'pid': 99000},
                'child': ident, 'profile_id': profile.profile_id, 'public_alias': profile.public_alias,
                'context_tokens': profile.context_tokens, 'backend_port': 19001, 'binary_sha256': '1' * 64,
                'startup_id': 'CPU-startup', 'argv_sha256': '2' * 64}
            state = {**record, 'active_lane_ids': ['req-1791210000-3'], 'acquired_sequence': 3,
                     'released_sequence': 2, 'child_exited': False, 'proof_valid': False}
            atomic_private_json(run / 'model-state.json', state)
            original = (run / 'model-state.json').read_bytes()
            trusted = {'registry': registry, 'data_root': root, 'config': {'installation_id': 'CPU-install'},
                       'trust': {'binaries': {'lily': {'sha256': '1' * 64}}}}
            probe = RunnerResourceProbe(trusted, lease_root=leases)
            lease = leases / 'gpu.lease'; lease.mkdir(mode=0o700)
            changed = {**record, 'startup_id': 'different'}; atomic_private_json(lease / 'lease.json', changed)
            with patch('runtime_proof.unchanged', return_value=False), patch.object(probe, '_port_free', return_value=True):
                self.assertFalse(probe._probe(profile, 'recover_closed')); self.assertTrue(lease.exists())
                atomic_private_json(lease / 'lease.json', record)
                with patch('runtime_proof.unchanged', return_value=True):
                    self.assertFalse(probe._probe(profile, 'recover_closed')); self.assertTrue(lease.exists())
                self.assertTrue(probe._probe(profile, 'recover_closed')); self.assertFalse(lease.exists())
                self.assertTrue(probe._probe(profile, 'unloaded'))
            self.assertEqual((run / 'model-state.json').read_bytes(), original)


class GuardRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []; self.models = []; self.proven = True
        async def transport(_request): return httpx.Response(200, json={'running': self.models})
        async def proof(_profile, phase): self.calls.append(phase); return self.proven
        self.registry = load_registry(ROOT)
        self.app = create_app('http://127.0.0.1:18081', 'synthetic-control', backend_token='synthetic-backend',
                             registry=self.registry, resource_probe=proof, transport=httpx.MockTransport(transport))
        self.life = self.app.router.lifespan_context(self.app); await self.life.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://guard.test')
        self.guard = self.app.state.lifecycle_guard
        future = asyncio.get_running_loop().create_future(); future.set_result(True)
        self.guard._ticket = _ProfileTicket(self.registry.default, future, time.monotonic() + 30,
                                           forwarded=True, stage='cleanup')
        self.guard._active_inferences = 1; self.guard._admission_paused = True
        self.guard._pause_reason = 'resource_release_unverified'

    async def asyncTearDown(self):
        await self.client.aclose(); await self.life.__aexit__(None, None, None)

    async def recover(self, token='synthetic-control'):
        return await self.client.post('/__recovery/maintenance/recover', headers={'X-Recovery-Control-Token': token})

    async def test_authenticated_cleanup_recovery_resets_only_after_proof(self):
        self.assertEqual((await self.recover('wrong')).status_code, 403); self.assertEqual(self.calls, [])
        response = await self.recover(); self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, ['recover_closed', 'unloaded'])
        self.assertTrue(response.json()['recovered']); self.assertFalse(response.json()['inference_completed'])
        self.assertFalse(response.json()['native_lane_counters_modified'])
        self.assertIsNone(self.guard._ticket); self.assertFalse(self.guard._admission_paused)

    async def test_active_stage_running_manager_or_missing_exit_proof_refuses_recovery(self):
        self.guard._ticket.stage = 'active'
        self.assertEqual((await self.recover()).status_code, 409); self.assertEqual(self.calls, [])
        self.guard._ticket.stage = 'cleanup'; self.models = [{'model': self.registry.default.public_alias}]
        self.assertEqual((await self.recover()).status_code, 409); self.assertEqual(self.calls, [])
        self.models = []; self.proven = False
        self.assertEqual((await self.recover()).status_code, 409)
        self.assertTrue(self.guard._admission_paused); self.assertEqual(self.guard._active_inferences, 1)

    async def test_pending_work_refuses_recovery_and_busy_lifecycle_stays409(self):
        self.guard._pending.append(self.guard._ticket)
        self.assertEqual((await self.recover()).status_code, 409)
        self.assertEqual((await self.client.post('/api/models/unload')).status_code, 409)


    async def test_unavailable_resource_proof_returns503_without_reset(self):
        async def unavailable(_profile, _phase): raise OSError('synthetic proof unavailable')
        self.guard.resource_probe = unavailable
        response = await self.recover()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(self.guard._admission_paused)
        self.assertEqual(self.guard._active_inferences, 1)
        self.assertIsNotNone(self.guard._ticket)

    async def test_concurrent_control_recovery_does_not_duplicate_exit_proof(self):
        entered = asyncio.Event(); release = asyncio.Event()
        async def proof(_profile, phase):
            self.calls.append(phase)
            if phase == 'recover_closed':
                entered.set(); await release.wait()
            return True
        self.guard.resource_probe = proof
        first = asyncio.create_task(self.recover())
        await entered.wait()
        second = await self.recover()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.calls, ['recover_closed'])
        release.set()
        self.assertEqual((await first).status_code, 200)
        self.assertEqual(self.calls, ['recover_closed', 'unloaded'])


if __name__ == '__main__': unittest.main()


