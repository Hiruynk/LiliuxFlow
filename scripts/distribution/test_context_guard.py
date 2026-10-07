# SPDX-License-Identifier: Apache-2.0
"""CPU guard FSM tests. Mock transport and isolated synthetic child lifecycles."""
from __future__ import annotations
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/distribution'))
sys.path.insert(0, str(ROOT / 'services/compat/src'))
from profile_registry import ProfileRegistry, load_registry
from compat_api.llama_guard import create_app


class Body(httpx.AsyncByteStream):
    def __init__(self, manager, alias, gate=None):
        self.manager, self.alias, self.gate = manager, alias, gate

    async def __aiter__(self):
        self.manager.started.set()
        yield b'data: {"choices":[{"delta":{"content":"cpu"}}]}\n\n'
        if self.gate is not None:
            await self.gate.wait()
        if self.manager.native_release_gate is None:
            asyncio.get_running_loop().call_soon(setattr, self.manager, 'released', True)
        yield b'data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    async def aclose(self):
        self.manager.closed += 1


class MockManager:
    def __init__(self):
        self.current = None
        self.forwarded = []
        self.operations = []
        self.started = asyncio.Event()
        self.body_gates = []
        self.native_release_gate = None
        self.released = True
        self.closed = 0
        self.unloaded = True
        self.exit_gate = None
        self.fail_release = False
        self.preheader_gate = None
        self.preheader_started = asyncio.Event()
        self.load_gate = None
        self.load_started = asyncio.Event()
        self.loading_replies = 0
        self.maximum_forwarded = 0
        self.active_forwarded = 0

    async def handler(self, request):
        self.operations.append(request.url.path)
        if request.url.path == '/running':
            return httpx.Response(200, json={'running': [] if self.current is None else
                                          [{'model': self.current, 'state': 'ready'}]})
        if request.url.path == '/api/models/unload':
            self.current = None
            self.unloaded = self.exit_gate is None
            self.active_forwarded = 0
            return httpx.Response(200, json={'unloaded': True})
        if request.url.path.startswith('/upstream/') and request.url.path.endswith('/health'):
            self.current = request.url.path.split('/upstream/', 1)[1].removesuffix('/health')
            self.unloaded = False
            self.load_started.set()
            if self.load_gate is not None:
                await self.load_gate.wait()
            if self.loading_replies:
                self.loading_replies -= 1
                return httpx.Response(200, json={'status': 'loading', 'state': 'loading'})
            return httpx.Response(200, json={'status': 'ok', 'state': 'ready'})
        if request.url.path == '/v1/chat/completions':
            payload = json.loads(request.content)
            alias = payload['model']
            if self.current is not None and self.current != alias:
                raise AssertionError('new profile forwarded before old native child exit')
            if self.active_forwarded:
                raise AssertionError('generation overlap')
            self.current = alias
            self.unloaded = False
            self.released = False
            self.active_forwarded += 1
            self.maximum_forwarded = max(self.maximum_forwarded, self.active_forwarded)
            self.forwarded.append(alias)
            if self.preheader_gate is not None:
                self.preheader_started.set()
                await self.preheader_gate.wait()
            gate = self.body_gates.pop(0) if self.body_gates else None
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Body(self, alias, gate))
        return httpx.Response(200, json={'ok': True})

    async def proof(self, profile, phase):
        if phase == 'before_forward':
            return not self.active_forwarded
        if phase == 'generation_complete':
            if self.native_release_gate is not None and self.native_release_gate.is_set():
                self.released = True
            if self.released:
                self.active_forwarded = 0
            return self.released
        if phase == 'before_unload':
            return True
        if phase == 'unloaded':
            if self.fail_release:
                return False
            if self.exit_gate is not None and self.exit_gate.is_set():
                self.unloaded = True
            return self.unloaded
        return False


class ContextGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        registry = load_registry(ROOT)
        self.registry = ProfileRegistry(tuple(replace(p, production_enabled=True) for p in registry.profiles))
        self.manager = MockManager()
        self.app = create_app('http://127.0.0.1:18081', 'cpu-control', backend_token='cpu-backend',
                              transport=httpx.MockTransport(self.manager.handler), registry=self.registry,
                              resource_probe=self.manager.proof)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://guard.test',
                                      headers={'Authorization': 'Bearer cpu-backend'})
        self.guard = self.app.state.lifecycle_guard
        self.guard.cleanup_seconds = .25
        self.guard.proof_wait_seconds = .15
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    def request(self, index, **payload):
        task = asyncio.create_task(self.client.post('/v1/chat/completions',
            json={'model': self.registry.profiles[index].public_alias,
                  'messages': [{'role': 'user', 'content': 'synthetic'}], 'stream': True, **payload}))
        self.tasks.append(task)
        return task

    async def wait_pending(self, count):
        for _ in range(200):
            if (await self.guard.status())['pending_inferences'] == count:
                return
            await asyncio.sleep(.005)
        self.fail('pending queue did not reach expected bound')

    async def asgi_request(self, index):
        incoming = asyncio.Queue()
        body = json.dumps({'model': self.registry.profiles[index].public_alias,
                          'messages': [{'role': 'user', 'content': 'cpu fixture'}], 'stream': True}).encode()
        await incoming.put({'type': 'http.request', 'body': body, 'more_body': False})
        outgoing = []
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST',
                 'scheme': 'http', 'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions',
                 'query_string': b'', 'headers': [(b'content-type', b'application/json'),
                                                (b'authorization', b'Bearer cpu-backend')],
                 'client': ('127.0.0.1', 12345), 'server': ('127.0.0.1', 18080), 'root_path': ''}
        async def send(message):
            outgoing.append(message)
        task = asyncio.create_task(self.app(scope, incoming.get, send))
        self.tasks.append(task)
        return task, incoming, outgoing

    async def test_fifo_one_forwarded_across_three_profiles_and_same_profile_reuses_child(self):
        gate = asyncio.Event(); self.manager.body_gates = [gate]
        first = self.request(0)
        await asyncio.wait_for(self.manager.started.wait(), 1)
        second = self.request(1); await self.wait_pending(1)
        third = self.request(2); await self.wait_pending(2)
        self.assertEqual(len(self.manager.forwarded), 1)
        gate.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second, third), 2)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(self.manager.forwarded, [p.public_alias for p in self.registry.profiles])
        self.assertEqual(self.manager.maximum_forwarded, 1)
        unloads = self.manager.operations.count('/api/models/unload')
        self.assertEqual(unloads, 2)
        self.assertEqual((await self.request(2)).status_code, 200)
        self.assertEqual(self.manager.operations.count('/api/models/unload'), unloads)

    async def test_http_terminal_does_not_release_permit_until_native_lane_release_proof(self):
        gate = asyncio.Event(); self.manager.native_release_gate = gate
        first = self.request(0)
        await asyncio.wait_for(self.manager.started.wait(), 1)
        second = self.request(1); await self.wait_pending(1)
        await asyncio.sleep(.04)
        self.assertFalse(first.done())
        self.assertEqual(len(self.manager.forwarded), 1)
        self.assertEqual((await self.guard.status())['active_inferences'], 1)
        gate.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        self.assertEqual(len(self.manager.forwarded), 2)

    async def test_stop_response_alone_cannot_start_next_profile_before_owned_exit(self):
        self.assertEqual((await self.request(0)).status_code, 200)
        exit_gate = asyncio.Event(); self.manager.exit_gate = exit_gate
        second = self.request(1)
        for _ in range(100):
            if '/api/models/unload' in self.manager.operations:
                break
            await asyncio.sleep(.005)
        self.assertEqual(len(self.manager.forwarded), 1)
        self.assertFalse(second.done())
        exit_gate.set()
        self.assertEqual((await asyncio.wait_for(second, 1)).status_code, 200)
        self.assertEqual(len(self.manager.forwarded), 2)

    async def test_queued_cancel_removes_ticket_without_loading_it_and_later_ticket_progresses(self):
        gate = asyncio.Event(); self.manager.body_gates = [gate]
        first = self.request(0); await asyncio.wait_for(self.manager.started.wait(), 1)
        second = self.request(1); await self.wait_pending(1)
        third = self.request(2); await self.wait_pending(2)
        second.cancel(); await asyncio.gather(second, return_exceptions=True)
        await self.wait_pending(1)
        gate.set()
        await asyncio.wait_for(asyncio.gather(first, third), 2)
        self.assertEqual(self.manager.forwarded, [self.registry.profiles[i].public_alias for i in (0, 2)])

    async def test_actual_asgi_queued_disconnect_removes_ticket_and_never_loads_profile(self):
        gate = asyncio.Event(); self.manager.body_gates = [gate]
        first = self.request(0); await asyncio.wait_for(self.manager.started.wait(), 1)
        second, incoming, outgoing = await self.asgi_request(1)
        await self.wait_pending(1)
        await incoming.put({'type': 'http.disconnect'})
        await asyncio.wait_for(second, 1)
        await self.wait_pending(0)
        self.assertEqual(outgoing, [])
        third = self.request(2); await self.wait_pending(1)
        gate.set(); await asyncio.wait_for(asyncio.gather(first, third), 2)
        self.assertEqual(self.manager.forwarded, [self.registry.profiles[i].public_alias for i in (0, 2)])

    async def test_actual_asgi_stream_disconnect_waits_for_owned_cleanup(self):
        gate = asyncio.Event(); self.manager.body_gates = [gate]
        first, incoming, _outgoing = await self.asgi_request(1)
        await asyncio.wait_for(self.manager.started.wait(), 1)
        exit_gate = asyncio.Event(); self.manager.exit_gate = exit_gate
        second = self.request(0); await self.wait_pending(1)
        await incoming.put({'type': 'http.disconnect'})
        await asyncio.sleep(.035)
        self.assertFalse(first.done()); self.assertEqual(len(self.manager.forwarded), 1)
        exit_gate.set()
        await asyncio.wait_for(first, 1)
        self.assertEqual((await asyncio.wait_for(second, 1)).status_code, 200)

    async def test_queue_full_returns_429_retry_after_and_busy_ui_routes_return_409(self):
        gate = asyncio.Event(); self.manager.body_gates = [gate]
        first = self.request(0); await asyncio.wait_for(self.manager.started.wait(), 1)
        pending = []
        for index in range(4):
            pending.append(self.request(index % 3)); await self.wait_pending(index + 1)
        full = await self.request(1)
        self.assertEqual(full.status_code, 429); self.assertEqual(full.headers['retry-after'], '1')
        for method, path in [('POST', '/api/models/unload'), ('GET', '/unload'), ('POST', '/api/models/load/' + self.registry.profiles[1].public_alias),
                             ('PUT', '/api/profiles/active'), ('POST', '/api/reload'), ('PUT', '/api/config/models')]:
            response = await self.client.request(method, path, json={})
            self.assertEqual(response.status_code, 409, path)
        gate.set(); await asyncio.wait_for(asyncio.gather(first, *pending), 2)

    async def test_preheader_load_cancellation_unloads_and_proves_before_next_request(self):
        self.manager.preheader_gate = asyncio.Event()
        first = self.request(1); await asyncio.wait_for(self.manager.preheader_started.wait(), 1)
        second = self.request(0); await self.wait_pending(1)
        self.manager.preheader_gate = None
        first.cancel(); await asyncio.gather(first, return_exceptions=True)
        response = await asyncio.wait_for(second, 1)
        self.assertEqual(response.status_code, 200)
        self.assertIn('/api/models/unload', self.manager.operations)
        self.assertEqual((await self.guard.status())['active_inferences'], 0)

    async def test_http_200_loading_is_not_native_ready_and_does_not_forward_generation(self):
        self.manager.loading_replies = 2
        first = self.request(1)
        await asyncio.wait_for(self.manager.load_started.wait(), 1)
        self.assertEqual(self.manager.forwarded, [])
        self.assertEqual((await asyncio.wait_for(first, 1)).status_code, 200)
        path = '/upstream/' + self.registry.profiles[1].public_alias + '/health'
        self.assertEqual(self.manager.operations.count(path), 3)

    async def test_actual_load_disconnect_cleans_selected_child_before_next_profile(self):
        self.manager.load_gate = asyncio.Event()
        first, incoming, outgoing = await self.asgi_request(1)
        await asyncio.wait_for(self.manager.load_started.wait(), 1)
        second = self.request(0); await self.wait_pending(1)
        self.manager.load_gate = None
        await incoming.put({'type': 'http.disconnect'})
        await asyncio.wait_for(first, 1)
        self.assertEqual(outgoing, [])
        self.assertEqual((await asyncio.wait_for(second, 1)).status_code, 200)
        self.assertEqual(self.manager.forwarded, [self.registry.default.public_alias])
        self.assertIn('/api/models/unload', self.manager.operations)

    async def test_cancel_during_profile_teardown_keeps_permit_until_exit_proof(self):
        await self.request(0)
        exit_gate = asyncio.Event(); self.manager.exit_gate = exit_gate
        second = self.request(1)
        for _ in range(100):
            if '/api/models/unload' in self.manager.operations:
                break
            await asyncio.sleep(.005)
        third = self.request(2); await self.wait_pending(1)
        second.cancel(); await asyncio.sleep(.035)
        self.assertEqual(len(self.manager.forwarded), 1)
        self.assertEqual((await self.guard.status())['active_inferences'], 1)
        exit_gate.set(); await asyncio.gather(second, return_exceptions=True)
        self.assertEqual((await asyncio.wait_for(third, 1)).status_code, 200)
        self.assertEqual(self.manager.forwarded, [self.registry.profiles[i].public_alias for i in (0, 2)])

    async def test_queue_deadline_does_not_forward_and_stream_deadline_has_one_typed_terminal(self):
        profiles = tuple(replace(p, queue_wait_seconds=.055, total_deadline_seconds=.12) for p in self.registry.profiles)
        self.guard.registry = ProfileRegistry(profiles)
        gate = asyncio.Event(); self.manager.body_gates = [gate]
        first = self.request(0); await asyncio.wait_for(self.manager.started.wait(), 1)
        second = self.request(1)
        response = await asyncio.wait_for(second, 1)
        self.assertEqual(response.status_code, 504)
        stream = await asyncio.wait_for(first, 1)
        self.assertEqual(stream.content.count(b'data: [DONE]'), 1)
        self.assertIn(b'request_deadline', stream.content)
        self.assertEqual(len(self.manager.forwarded), 1)
        self.assertEqual((await self.guard.status())['active_inferences'], 0)

    async def test_failed_child_exit_pauses_admission_and_never_silently_falls_back(self):
        gate = asyncio.Event(); self.manager.body_gates = [gate]; self.manager.fail_release = True
        first = self.request(0); await asyncio.wait_for(self.manager.started.wait(), 1)
        second = self.request(1); await self.wait_pending(1)
        first.cancel(); await asyncio.gather(first, return_exceptions=True)
        rejected = await asyncio.wait_for(second, 1)
        self.assertEqual(rejected.status_code, 503)
        status = await self.guard.status()
        self.assertTrue(status['admission_paused']); self.assertEqual(status['active_inferences'], 1)
        self.assertEqual(self.manager.forwarded, [self.registry.default.public_alias])

    async def test_exact_context_admission_and_unrecognized_or_disabled_alias_reject_before_load(self):
        async def exact(payload, profile):
            return payload['fixture_prompt_tokens']
        self.guard.context_admission = exact
        for profile in self.registry.profiles:
            response = await self.client.post('/v1/chat/completions', json={'model': profile.public_alias,
                'fixture_prompt_tokens': profile.context_tokens - 8, 'max_tokens': 9})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['error']['type'], 'context_length_exceeded')
        for model, expected in [('qwen38-flash-next-q4-safe64k', 404), ('unknown', 404)]:
            response = await self.client.post('/v1/chat/completions', json={'model': model, 'max_tokens': 8})
            self.assertEqual(response.status_code, expected)
        self.guard.registry = load_registry(ROOT)
        response = await self.client.post('/v1/chat/completions', json={'model': self.registry.profiles[1].public_alias})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.manager.forwarded, [])
        self.assertNotIn('/running', self.manager.operations)

    async def test_context_override_body_cap_and_bypass_routes_cannot_load(self):
        for override in ({'num_ctx': 262144}, {'extra_body': {'profile_path': '/tmp/x'}}, {'max_tokens': True}):
            response = await self.client.post('/v1/chat/completions', json={'model': self.registry.default.public_alias, **override})
            self.assertEqual(response.status_code, 400)
        self.guard.registry = replace(self.registry, maximum_body_bytes=64)
        response = await self.client.post('/v1/chat/completions', content=b'X' * 65)
        self.assertEqual(response.status_code, 413)
        response = await self.client.post('/upstream/' + self.registry.default.public_alias + '/v1/chat/completions', json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.manager.forwarded, [])

    async def test_status_polling_never_loads_and_manual_idle_load_unload_is_owned_and_serialized(self):
        response = await self.client.get('/api/context-profiles')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p['context_tokens'] for p in response.json()['profiles']], [65536, 131072, 262144])
        self.assertEqual(self.manager.forwarded, [])
        for index in (0, 1, 2):
            response = await self.client.post('/api/models/load/' + self.registry.profiles[index].public_alias)
            self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.post('/api/models/unload')).status_code, 200)
        self.assertIsNone(self.manager.current)


if __name__ == '__main__':
    unittest.main()
