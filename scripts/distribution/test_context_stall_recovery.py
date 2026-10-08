# SPDX-License-Identifier: Apache-2.0
"""CPU regressions for exact closed-owner recovery before context admission.

All processes, ports, transports and owner records are synthetic. These tests
do not load a model, change a deployment, or establish native 262K readiness.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/distribution'))
sys.path.insert(0, str(ROOT / 'services/compat/src'))
from profile_registry import ProfileRegistry, load_registry
from common import DistributionError
from runtime_proof import RunnerResourceProbe, portable_guard_kwargs
from trust import atomic_private_json
from compat_api.llama_guard import _ProfileTicket, create_app


class ClosedOwnerProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.run = self.root / 'run'
        self.leases = self.root / 'leases'
        self.run.mkdir(mode=0o700)
        self.leases.mkdir(mode=0o700)
        self.registry = load_registry(ROOT)
        self.previous = self.registry.by_id('ctx64k', allow_validation=True)
        self.target = self.registry.by_id('ctx262k', allow_validation=True)
        runner = {'pid': 99000, 'ppid': 98000, 'pgid': 99000, 'uid': os.getuid(),
                  'started': 'synthetic-runner-start', 'command_sha256': 'a' * 64}
        child = {**runner, 'pid': 99001, 'ppid': 99000,
                 'started': 'synthetic-child-start', 'command_sha256': 'b' * 64}
        self.record = {'schema_version': 1, 'installation_id': 'CPU-installation',
            'runner': runner, 'child': child, 'profile_id': self.previous.profile_id,
            'public_alias': self.previous.public_alias, 'context_tokens': self.previous.context_tokens,
            'backend_port': 19001, 'binary_sha256': '1' * 64, 'argv_sha256': '2' * 64,
            'startup_id': 'CPU-previous-startup'}
        self.state = {**self.record, 'active_lane_ids': ['req-1-3'], 'acquired_sequence': 3,
                      'released_sequence': 2, 'proof_valid': False,
                      'child_exited': False, 'stderr_closed': False}
        atomic_private_json(self.run / 'model-state.json', self.state)
        self.state_bytes = (self.run / 'model-state.json').read_bytes()
        self.trusted = {'registry': self.registry, 'data_root': self.root,
            'config': {'installation_id': 'CPU-installation'},
            'trust': {'binaries': {'lily': {'sha256': '1' * 64}}}}
        self.probe = RunnerResourceProbe(self.trusted, lease_root=self.leases)
        self.lease = self.leases / 'gpu.lease'
        self.write_lease(self.record)
        self.identity_patch = patch('runtime_proof.unchanged', return_value=False)
        self.port_patch = patch.object(self.probe, '_port_free', return_value=True)
        self.identity_patch.start()
        self.port_patch.start()

    def tearDown(self):
        self.port_patch.stop()
        self.identity_patch.stop()
        self.temporary.cleanup()

    def write_lease(self, record):
        self.lease.mkdir(mode=0o700, exist_ok=True)
        atomic_private_json(self.lease / 'lease.json', record)

    def assert_counters_unchanged(self):
        self.assertEqual((self.run / 'model-state.json').read_bytes(), self.state_bytes)

    def test_dead_previous_profile_lease_recovered_before_requested_profile_load(self):
        self.assertTrue(self.probe._probe(self.target, 'before_forward'))
        self.assertFalse(self.lease.exists())
        self.assertIsNone(self.probe.baseline)
        self.assert_counters_unchanged()

    def test_cleanup_and_privileged_recovery_use_trusted_record_profile(self):
        for phase in ('before_unload', 'recover_closed', 'unloaded'):
            with self.subTest(phase=phase):
                self.write_lease(self.record)
                self.probe.exit_snapshot = None
                self.assertTrue(self.probe._probe(self.target, phase))
                self.assertFalse(self.lease.exists())
                self.assertTrue(self.probe._probe(self.target, 'unloaded'))
                self.assert_counters_unchanged()

    def test_live_runner_live_child_or_reused_backend_port_never_reaped(self):
        for live_pid in (self.record['runner']['pid'], self.record['child']['pid']):
            with self.subTest(live_pid=live_pid), patch('runtime_proof.unchanged',
                    side_effect=lambda identity: identity['pid'] == live_pid):
                self.assertFalse(self.probe._probe(self.target, 'before_forward'))
                self.assertFalse(self.probe._probe(self.target, 'recover_closed'))
                self.assertTrue(self.lease.exists())
        with patch.object(self.probe, '_port_free', return_value=False):
            self.assertFalse(self.probe._probe(self.target, 'before_forward'))
            self.assertFalse(self.probe._probe(self.target, 'recover_closed'))
            self.assertTrue(self.lease.exists())
        self.assert_counters_unchanged()

    def test_foreign_or_changed_lease_identity_is_preserved(self):
        for key, value in (('installation_id', 'foreign-installation'), ('startup_id', 'new-startup'),
                           ('argv_sha256', '3' * 64), ('public_alias', self.target.public_alias),
                           ('child', None)):
            with self.subTest(key=key):
                changed = {**self.record, key: value}
                self.write_lease(changed)
                original = (self.lease / 'lease.json').read_bytes()
                for phase in ('before_forward', 'before_unload', 'recover_closed', 'unloaded'):
                    self.assertFalse(self.probe._probe(self.target, phase))
                self.assertEqual((self.lease / 'lease.json').read_bytes(), original)
        self.assert_counters_unchanged()

    def test_unknown_profile_and_private_directory_changes_refuse_recovery(self):
        unknown = {**self.state, 'profile_id': 'foreign-profile'}
        atomic_private_json(self.run / 'model-state.json', unknown)
        self.write_lease({**self.record, 'profile_id': 'foreign-profile'})
        self.assertFalse(self.probe._probe(self.target, 'recover_closed'))
        self.assertTrue(self.lease.exists())
        atomic_private_json(self.run / 'model-state.json', self.state)
        self.write_lease(self.record)
        (self.lease / 'unrecognized-entry').write_text('synthetic')
        self.assertFalse(self.probe._probe(self.target, 'recover_closed'))
        self.assertTrue(self.lease.exists())

    def test_new_global_lease_after_old_reap_prevents_forward_or_unloaded_proof(self):
        original_gone = self.probe._gone
        foreign = {**self.record, 'installation_id': 'foreign-installation', 'startup_id': 'new-startup'}
        def check_gone(record):
            if not self.lease.exists():
                self.write_lease(foreign)
            return original_gone(record)
        with patch.object(self.probe, '_gone', side_effect=check_gone):
            self.assertFalse(self.probe._probe(self.target, 'before_forward'))
        self.assertFalse(self.probe._probe(self.target, 'unloaded'))
        self.assertEqual(json.loads((self.lease / 'lease.json').read_text()), foreign)
        self.assert_counters_unchanged()

    def test_no_model_factory_disables_all_profiles_and_validation_authorization(self):
        trusted = {**self.trusted, 'model_configured': False}
        with patch('runtime_proof.Path.home', return_value=self.root):
            kwargs = portable_guard_kwargs(trusted)
        self.assertEqual(kwargs['validation_aliases'], ())
        self.assertTrue(all(not profile.production_enabled and not profile.validation_only
                            for profile in kwargs['registry'].profiles))
        with self.assertRaises(DistributionError):
            portable_guard_kwargs(trusted, validation_profile_ids=('ctx262k',))


class SyntheticBody(httpx.AsyncByteStream):
    def __init__(self, manager):
        self.manager = manager

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"CPU fixture"}}]}\n\n'
        self.manager.released = True
        yield b'data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    async def aclose(self):
        pass


class RecoveryManager:
    def __init__(self):
        self.current = None
        self.trace = []
        self.forwarded = []
        self.active_forwarded = 0
        self.maximum_forwarded = 0
        self.released = True
        self.recovery_started = asyncio.Event()
        self.recovery_gate = None
        self.exit_proven = True
        self.unloaded_proven = True
        self.after_recovery = None
        self.load_fails = False

    async def handler(self, request):
        path = request.url.path
        self.trace.append(path)
        if path == '/running':
            return httpx.Response(200, json={'running': [] if self.current is None else
                [{'model': self.current, 'state': 'ready'}]})
        if path == '/api/models/unload':
            self.current = None
            self.active_forwarded = 0
            return httpx.Response(200, json={'unloaded': True})
        if path.startswith('/upstream/') and path.endswith('/health'):
            if self.active_forwarded:
                raise AssertionError('load overlapped the preceding native lane')
            if self.load_fails:
                return httpx.Response(503, json={'error': 'synthetic load failure'})
            self.current = path.split('/upstream/', 1)[1].removesuffix('/health')
            return httpx.Response(200, json={'status': 'ok', 'state': 'ready'})
        if path == '/v1/chat/completions':
            alias = json.loads(request.content)['model']
            if self.active_forwarded or alias != self.current:
                raise AssertionError('generation overlapped or forwarded the wrong profile')
            self.active_forwarded = 1
            self.maximum_forwarded = max(self.maximum_forwarded, self.active_forwarded)
            self.released = False
            self.forwarded.append(alias)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SyntheticBody(self))
        return httpx.Response(200, json={'ok': True})

    async def proof(self, _profile, phase):
        self.trace.append(phase)
        if phase == 'recover_closed':
            self.recovery_started.set()
            if self.recovery_gate is not None:
                await self.recovery_gate.wait()
            if self.after_recovery is not None:
                self.after_recovery()
            return self.exit_proven
        if phase == 'before_forward':
            return not self.active_forwarded
        if phase == 'generation_complete':
            if self.released:
                self.active_forwarded = 0
            return self.released
        if phase == 'before_unload':
            return True
        if phase == 'unloaded':
            return self.unloaded_proven
        return False


class CallerCleanupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        registry = load_registry(ROOT)
        self.registry = ProfileRegistry(tuple(replace(profile, production_enabled=True) for profile in registry.profiles))
        self.manager = RecoveryManager()
        self.app = create_app('http://127.0.0.1:18081', 'synthetic-control',
            backend_token='synthetic-backend', registry=self.registry,
            resource_probe=self.manager.proof, transport=httpx.MockTransport(self.manager.handler))
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
            base_url='http://guard.test', headers={'Authorization': 'Bearer synthetic-backend'})
        self.guard = self.app.state.lifecycle_guard
        self.guard.cleanup_seconds = .2
        granted = asyncio.get_running_loop().create_future()
        granted.set_result(True)
        held = self.registry.by_id('ctx262k')
        self.guard._ticket = _ProfileTicket(held, granted, asyncio.get_running_loop().time() + 30,
                                          cleanup_profile=held, stage='cleanup')
        self.guard._active_inferences = 1
        self.guard._admission_paused = True
        self.guard._pause_reason = 'resource_release_unverified'
        self.tasks = []

    async def asyncTearDown(self):
        if self.manager.recovery_gate is not None:
            self.manager.recovery_gate.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        recovery = self.guard._cleanup_recovery_task
        if recovery is not None:
            await asyncio.wait_for(asyncio.shield(recovery), 1)
        await self.client.aclose()
        await self.life.__aexit__(None, None, None)

    def request(self, index):
        task = asyncio.create_task(self.client.post('/v1/chat/completions', json={
            'model': self.registry.profiles[index].public_alias,
            'messages': [{'role': 'user', 'content': 'synthetic fixture'}], 'stream': True}))
        self.tasks.append(task)
        return task

    async def wait_pending(self, count):
        for _ in range(100):
            if (await self.guard.status())['pending_inferences'] == count:
                return
            await asyncio.sleep(.005)
        self.fail('bounded queue did not reach the expected count')

    async def asgi_request(self, index):
        incoming = asyncio.Queue()
        body = json.dumps({'model': self.registry.profiles[index].public_alias,
            'messages': [{'role': 'user', 'content': 'synthetic disconnected fixture'}], 'stream': True}).encode()
        await incoming.put({'type': 'http.request', 'body': body, 'more_body': False})
        outgoing = []
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST',
            'scheme': 'http', 'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions',
            'query_string': b'', 'headers': [(b'content-type', b'application/json'),
                (b'authorization', b'Bearer synthetic-backend')], 'client': ('127.0.0.1', 12345),
            'server': ('127.0.0.1', 18080), 'root_path': ''}
        async def send(message):
            outgoing.append(message)
        task = asyncio.create_task(self.app(scope, incoming.get, send))
        self.tasks.append(task)
        return task, incoming, outgoing

    async def ui_failed_load(self, index=2):
        self.guard._ticket = None
        self.guard._active_inferences = 0
        self.guard._admission_paused = False
        self.guard._pause_reason = None
        self.manager.load_fails = True
        response = await self.client.post('/api/models/load/' + self.registry.profiles[index].public_alias)
        self.manager.load_fails = False
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.guard._ticket)
        self.assertEqual((await self.guard.status())['active_inferences'], 0)
        self.assertEqual((await self.guard.status())['pause_reason'], 'resource_release_unverified')

    async def test_ui_only_failed_load_caller_recovers_after_closed_owner_revalidation(self):
        await self.ui_failed_load()
        self.manager.trace.clear()
        response = await asyncio.wait_for(self.request(2), 1)
        self.assertEqual(response.status_code, 200)
        health = '/upstream/' + self.registry.profiles[2].public_alias + '/health'
        self.assertLess(self.manager.trace.index('recover_closed'), self.manager.trace.index(health))
        self.assertLess(self.manager.trace.index('unloaded'), self.manager.trace.index(health))
        status = await self.guard.status()
        self.assertFalse(status['admission_paused'])
        self.assertEqual(status['active_inferences'], 0)

    async def test_ui_only_recovery_preserves_pending_bound_fifo_and_busy_ui409(self):
        await self.ui_failed_load()
        self.manager.recovery_gate = asyncio.Event()
        order = [[2, 0, 1][index % 3] for index in range(self.registry.pending_limit)]
        queued = []
        for index, profile_index in enumerate(order):
            queued.append(self.request(profile_index))
            await self.wait_pending(index + 1)
        self.assertEqual((await self.guard.status())['active_inferences'], 0)
        rejected = await asyncio.wait_for(self.request(2), 1)
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.headers['Retry-After'], '1')
        self.assertEqual((await self.client.post('/api/models/unload')).status_code, 409)
        self.assertEqual(self.manager.forwarded, [])
        self.manager.recovery_gate.set()
        responses = await asyncio.wait_for(asyncio.gather(*queued), 2)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(self.manager.forwarded, [self.registry.profiles[index].public_alias for index in order])
        self.assertEqual(self.manager.trace.count('recover_closed'), 1)
        self.assertEqual(self.manager.maximum_forwarded, 1)

    async def test_ui_only_cancelled_recovery_waiter_never_reloads_its_profile(self):
        await self.ui_failed_load()
        health = '/upstream/' + self.registry.profiles[2].public_alias + '/health'
        previous_loads = self.manager.trace.count(health)
        self.manager.recovery_gate = asyncio.Event()
        first, incoming, outgoing = await self.asgi_request(2)
        await asyncio.wait_for(self.manager.recovery_started.wait(), 1)
        second = self.request(0)
        await self.wait_pending(2)
        await incoming.put({'type': 'http.disconnect'})
        await asyncio.wait_for(first, 1)
        await self.wait_pending(1)
        self.assertEqual(outgoing, [])
        self.assertEqual(self.manager.forwarded, [])
        self.assertEqual((await self.guard.status())['active_inferences'], 0)
        self.manager.recovery_gate.set()
        self.assertEqual((await asyncio.wait_for(second, 1)).status_code, 200)
        self.assertEqual(self.manager.forwarded, [self.registry.default.public_alias])
        self.assertEqual(self.manager.trace.count(health), previous_loads)

    async def test_ui_only_live_manager_or_unproven_closed_owner_never_resumes(self):
        await self.ui_failed_load()
        for field in ('exit_proven', 'unloaded_proven'):
            with self.subTest(field=field):
                setattr(self.manager, field, False)
                response = await asyncio.wait_for(self.request(2), 1)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['type'], 'resource_release_unverified')
                self.assertTrue((await self.guard.status())['admission_paused'])
                self.assertIsNone(self.guard._ticket)
                self.assertEqual((await self.guard.status())['active_inferences'], 0)
                setattr(self.manager, field, True)
        self.manager.current = self.registry.default.public_alias
        response = await asyncio.wait_for(self.request(2), 1)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.manager.forwarded, [])

    async def test_ui_only_session_mutation_and_orphan_active_count_do_not_recover(self):
        await self.ui_failed_load()
        self.manager.trace.clear()
        for reason, active in (('session_maintenance', 0), ('ui_lifecycle_mutation', 0),
                               ('resource_release_unverified', 1)):
            with self.subTest(reason=reason, active=active):
                self.guard._pause_reason = reason
                self.guard._active_inferences = active
                response = await self.request(2)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['type'], 'admission_paused')
                self.assertEqual(self.manager.trace, [])

    async def test_ui_only_real_owner_probe_refuses_absent_unknown_foreign_live_or_reused_port(self):
        await self.ui_failed_load()
        fixture = ClosedOwnerProofTests('test_dead_previous_profile_lease_recovered_before_requested_profile_load')
        fixture.setUp()
        try:
            self.guard.resource_probe = fixture.probe
            self.manager.trace.clear()
            async def rejected():
                response = await asyncio.wait_for(self.request(2), 1)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['type'], 'resource_release_unverified')
            (fixture.run / 'model-state.json').unlink()
            await rejected()
            for changed in ({**fixture.state, 'profile_id': 'unknown-profile'},
                            {**fixture.state, 'installation_id': 'foreign-installation'}):
                atomic_private_json(fixture.run / 'model-state.json', changed)
                await rejected()
            atomic_private_json(fixture.run / 'model-state.json', fixture.state)
            fixture.write_lease({**fixture.record, 'startup_id': 'new-owner-startup'})
            await rejected()
            fixture.write_lease(fixture.record)
            with patch('runtime_proof.unchanged', return_value=True):
                await rejected()
            with patch.object(fixture.probe, '_port_free', return_value=False):
                await rejected()
            self.assertTrue(fixture.lease.exists())
            fixture.assert_counters_unchanged()
            self.assertEqual(self.manager.forwarded, [])
            self.assertFalse(any(path.startswith('/upstream/') for path in self.manager.trace))
            self.assertTrue((await self.guard.status())['admission_paused'])
            self.assertIsNone(self.guard._ticket)
        finally:
            fixture.tearDown()

    async def test_authorized_callers_resume_one_bounded_fifo_after_exact_exit_proof(self):
        self.manager.recovery_gate = asyncio.Event()
        first = self.request(2)
        await asyncio.wait_for(self.manager.recovery_started.wait(), 1)
        second = self.request(1)
        await self.wait_pending(2)
        third = self.request(0)
        await self.wait_pending(3)
        self.assertEqual(self.manager.forwarded, [])
        self.assertEqual((await self.guard.status())['active_inferences'], 1)
        self.assertEqual((await self.client.post('/api/models/unload')).status_code, 409)
        self.manager.recovery_gate.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second, third), 2)
        self.assertEqual([response.status_code for response in responses], [200, 200, 200])
        self.assertEqual(self.manager.forwarded, [self.registry.profiles[i].public_alias for i in (2, 1, 0)])
        self.assertEqual(self.manager.maximum_forwarded, 1)
        self.assertEqual(self.manager.trace.count('recover_closed'), 1)
        self.assertFalse((await self.guard.status())['admission_paused'])

    async def test_cancelled_recovery_waiter_never_loads_and_next_waiter_progresses(self):
        self.manager.recovery_gate = asyncio.Event()
        first, incoming, outgoing = await self.asgi_request(2)
        await asyncio.wait_for(self.manager.recovery_started.wait(), 1)
        second = self.request(0)
        await self.wait_pending(2)
        await incoming.put({'type': 'http.disconnect'})
        await asyncio.wait_for(first, 1)
        await self.wait_pending(1)
        self.assertEqual(outgoing, [])
        self.assertEqual(self.manager.forwarded, [])
        self.manager.recovery_gate.set()
        self.assertEqual((await asyncio.wait_for(second, 1)).status_code, 200)
        self.assertEqual(self.manager.forwarded, [self.registry.default.public_alias])
        self.assertNotIn('/upstream/' + self.registry.profiles[2].public_alias + '/health', self.manager.trace)

    async def test_all_recovery_waiters_cancel_without_starting_a_native_runner(self):
        self.manager.recovery_gate = asyncio.Event()
        first = self.request(2)
        await asyncio.wait_for(self.manager.recovery_started.wait(), 1)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await self.wait_pending(0)
        recovery = self.guard._cleanup_recovery_task
        self.manager.recovery_gate.set()
        await asyncio.wait_for(asyncio.shield(recovery), 1)
        self.assertEqual(self.manager.forwarded, [])
        self.assertFalse(any(path.startswith('/upstream/') for path in self.manager.trace))
        self.assertEqual((await self.guard.status())['active_inferences'], 0)

    async def test_unproven_exit_or_second_proof_refuses_resume_then_later_call_can_retry(self):
        for field in ('exit_proven', 'unloaded_proven'):
            with self.subTest(field=field):
                setattr(self.manager, field, False)
                response = await asyncio.wait_for(self.request(2), 1)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['type'], 'resource_release_unverified')
                status = await self.guard.status()
                self.assertTrue(status['admission_paused'])
                self.assertEqual(status['active_inferences'], 1)
                self.assertEqual(status['pending_inferences'], 0)
                self.assertEqual(self.manager.forwarded, [])
                setattr(self.manager, field, True)
        self.assertEqual((await asyncio.wait_for(self.request(2), 1)).status_code, 200)

    async def test_new_manager_owner_between_proofs_keeps_cleanup_permit(self):
        self.manager.after_recovery = lambda: setattr(self.manager, 'current', self.registry.default.public_alias)
        response = await asyncio.wait_for(self.request(2), 1)
        self.assertEqual(response.status_code, 503)
        self.assertTrue((await self.guard.status())['admission_paused'])
        self.assertEqual(self.manager.forwarded, [])
        self.assertNotIn('unloaded', self.manager.trace)

    async def test_session_maintenance_active_stage_and_unauthenticated_call_never_recover(self):
        self.guard._pause_reason = 'session_maintenance'
        self.assertEqual((await self.request(2)).status_code, 503)
        self.guard._pause_reason = 'resource_release_unverified'
        self.guard._ticket.stage = 'active'
        self.assertEqual((await self.request(2)).status_code, 503)
        self.guard._ticket.stage = 'cleanup'
        response = await self.client.post('/v1/chat/completions', headers={'Authorization': 'Bearer wrong'},
            json={'model': self.registry.profiles[2].public_alias})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.manager.trace, [])

    async def test_no_model_caller_rejects_before_cleanup_recovery_or_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve()
            trusted = {'registry': self.registry, 'data_root': data, 'model_configured': False,
                'config': {'installation_id': 'CPU-no-model'},
                'trust': {'binaries': {'lily': {'sha256': '1' * 64}}}}
            with patch('runtime_proof.Path.home', return_value=data):
                self.guard.registry = portable_guard_kwargs(trusted)['registry']
            response = await self.request(2)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['type'], 'profile_disabled')
        self.assertEqual(self.manager.trace, [])

    async def test_recovery_pending_limit_and_retry_after_remain_enforced(self):
        self.manager.recovery_gate = asyncio.Event()
        queued = []
        for index in range(self.registry.pending_limit):
            queued.append(self.request(index % 3))
            await self.wait_pending(index + 1)
        rejected = await asyncio.wait_for(self.request(2), 1)
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.headers['Retry-After'], '1')
        self.assertEqual(self.manager.forwarded, [])
        self.manager.recovery_gate.set()
        responses = await asyncio.wait_for(asyncio.gather(*queued), 2)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(self.manager.maximum_forwarded, 1)

    async def test_owner_proof_precedes_health_dispatch_after_recovery(self):
        self.assertEqual((await self.request(2)).status_code, 200)
        trace = self.manager.trace
        health = '/upstream/' + self.registry.profiles[2].public_alias + '/health'
        self.assertLess(trace.index('recover_closed'), trace.index(health))
        self.assertLess(trace.index('unloaded'), trace.index(health))
        self.assertLess(trace.index('before_forward'), trace.index(health))
        self.assertEqual(trace.count('before_forward'), 2)


if __name__ == '__main__':
    unittest.main()
