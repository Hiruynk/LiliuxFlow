# SPDX-License-Identifier: Apache-2.0
"""CPU-only native validation errors, resident ownership and FIFO lifecycle.

Uses an ASGI mock manager and private temporary metadata. No model, listener,
real caller key, database, or installed-runtime path is required.
"""
from __future__ import annotations

import asyncio
import copy
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
sys.path[:0] = [str(ROOT / "scripts/distribution"), str(ROOT / "services/compat/src")]

from compat_api import llama_guard
from profile_registry import ProfileRegistry, load_registry
import runtime_proof


def native_error(message="the request carries 10 images; the server accepts at most 8 per request (--max-images)",
                 kind="invalid_request_error"):
    return json.dumps({"error": {"message": message, "type": kind}}).encode()


class NativeMetadata:
    """Synthetic owned process and actual RunnerResourceProbe state contract."""

    def __init__(self, root, profile, registry):
        self.root, self.profile = root, profile
        self.run = root / "run"
        self.leases = root / "leases"
        self.run.mkdir(mode=0o700)
        self.leases.mkdir(mode=0o700)
        self.alive = True
        self.generation = 0
        trusted = {"config": {"installation_id": "cpu-installation"}, "registry": registry,
                   "trust": {"binaries": {"lily": {"sha256": "a" * 64}}}}
        self.probe = runtime_proof.RunnerResourceProbe(trusted, run_root=self.run, lease_root=self.leases)
        self.probe._port_free = lambda _port: not self.alive
        self.spawn(initial=True)

    def _write(self, path, value):
        if path.exists():
            path.unlink()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)

    def write(self):
        self._write(self.run / "model-state.json", self.state)

    def spawn(self, *, initial=False):
        self.generation += 1
        self.alive = True
        runner = {"pid": 910001 + self.generation * 2, "ppid": 910000,
                  "pgid": 910000, "uid": os.getuid(), "started": "CPU runner " + str(self.generation),
                  "command_sha256": "b" * 64}
        child = {"pid": runner["pid"] + 1, "ppid": runner["pid"], "pgid": runner["pid"] + 1,
                 "uid": os.getuid(), "started": "CPU child " + str(self.generation),
                 "command_sha256": "c" * 64}
        count = 7 if initial else 0
        self.state = {"schema_version": 1, "installation_id": "cpu-installation", "runner": runner,
                      "child": child, "profile_id": self.profile.profile_id,
                      "public_alias": self.profile.public_alias, "context_tokens": self.profile.context_tokens,
                      "backend_port": 5899, "binary_sha256": "a" * 64,
                      "startup_id": "cpu-start-" + str(self.generation), "argv_sha256": "d" * 64,
                      "native_context_tokens": self.profile.context_tokens, "proof_valid": True,
                      "stderr_closed": False, "child_exited": False, "active_lane_ids": [],
                      "acquired_sequence": count, "released_sequence": count, "event_sequence": count * 2,
                      "last_released_request_id": "req-7-1" if initial else None,
                      "last_prefill_progress": None}
        lease = self.leases / "gpu.lease"
        lease.mkdir(mode=0o700, exist_ok=True)
        self._write(lease / "lease.json", {key: self.state[key] for key in runtime_proof.OWNER_KEYS})
        self.write()

    def unchanged(self, identity):
        return self.alive and identity in (self.state["runner"], self.state["child"])

    def acquire(self):
        self.state["acquired_sequence"] += 1
        self.state["event_sequence"] += 1
        self.state["active_lane_ids"] = ["req-" + str(self.state["acquired_sequence"]) + "-1"]
        self.state["last_prefill_progress"] = None
        self.write()

    def release(self):
        request_id = self.state["active_lane_ids"][0]
        self.state["released_sequence"] += 1
        self.state["event_sequence"] += 1
        self.state["active_lane_ids"] = []
        self.state["last_released_request_id"] = request_id
        self.write()

    def exit(self):
        # Preserve counters and any old active ids; owned exit is separate proof.
        self.alive = False
        self.state["child_exited"] = self.state["stderr_closed"] = True
        self.write()
        lease = self.leases / "gpu.lease"
        (lease / "lease.json").unlink()
        lease.rmdir()


class BytesBody(httpx.AsyncByteStream):
    def __init__(self, data, started=None, release=None, *, interrupted=False):
        self.data, self.started, self.release = data, started, release
        self.interrupted = interrupted

    async def __aiter__(self):
        if self.started is not None:
            self.started.set()
        yield self.data[:20]
        if self.release is not None:
            await self.release.wait()
        yield self.data[20:]
        if self.interrupted:
            raise httpx.ReadError("synthetic interrupted response")

    async def aclose(self):
        pass


class SuccessBody(httpx.AsyncByteStream):
    def __init__(self, manager):
        self.manager = manager

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"cpu"}}]}\n\n'
        self.manager.metadata.release()
        self.manager.active_forwarded -= 1
        yield b'data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    async def aclose(self):
        pass


class MockManager:
    def __init__(self, metadata, *, status=400, body=None, interrupted=False, held=False, after_lane=False):
        self.metadata = metadata
        self.current = metadata.profile.public_alias
        self.status, self.body = status, native_error() if body is None else body
        self.interrupted, self.held, self.after_lane = interrupted, held, after_lane
        self.reject_once = True
        self.operations = []
        self.active_forwarded = self.maximum_forwarded = self.load_count = 0
        self.rejection_started, self.rejection_release = asyncio.Event(), asyncio.Event()
        self.unload_started = asyncio.Event()
        self.unload_gate = None

    async def handler(self, request):
        path = request.url.path
        self.operations.append(path)
        if path == "/running":
            return httpx.Response(200, json={"running": [] if self.current is None else
                                          [{"model": self.current, "state": "ready"}]})
        if path.startswith("/upstream/") and path.endswith("/health"):
            if self.current is None:
                self.metadata.spawn()
                self.current = self.metadata.profile.public_alias
                self.load_count += 1
            return httpx.Response(200, json={"status": "ok", "state": "ready"})
        if path == "/api/models/unload":
            self.unload_started.set()
            if self.unload_gate is not None:
                await self.unload_gate.wait()
            self.current = None
            self.metadata.exit()
            return httpx.Response(200, json={"unloaded": True})
        if path == "/v1/chat/completions":
            if self.reject_once:
                self.reject_once = False
                if self.after_lane:
                    self.metadata.acquire()
                return httpx.Response(self.status, headers={"content-type": "application/json"},
                    stream=BytesBody(self.body, self.rejection_started,
                                     self.rejection_release if self.held else None, interrupted=self.interrupted))
            if self.active_forwarded:
                raise AssertionError("generation overlap")
            self.metadata.acquire()
            self.active_forwarded += 1
            self.maximum_forwarded = max(self.maximum_forwarded, self.active_forwarded)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SuccessBody(self))
        return httpx.Response(200, json={"ok": True})


class NativeErrorClassifierTests(unittest.TestCase):
    def test_native_image_and_other_validation_errors_are_recognized(self):
        for message in ("the request carries 10 images; the server accepts at most 8 per request (--max-images)",
                        "image 1: invalid PNG or JPEG data URI", "parsing the chat request: missing field messages"):
            self.assertTrue(llama_guard._pre_admission_validation_rejection(
                400, "application/json; charset=utf-8", native_error(message)))

    def test_status_content_type_malformed_oversize_and_duplicate_fields_are_rejected(self):
        body = native_error()
        cases = [(status, "application/json", body) for status in (200, 401, 403, 404, 422, 429, 500)]
        cases += [(400, "text/event-stream", body), (400, "application/json", b""),
                  (400, "application/json", b"{"), (400, "application/json", body + b" " * 4097),
                  (400, "application/json", b'{"error":{"type":"server_error","type":"invalid_request_error","message":"cpu"}}')]
        for status, content_type, content in cases:
            with self.subTest(status=status, content_type=content_type, bytes=len(content)):
                self.assertFalse(llama_guard._pre_admission_validation_rejection(status, content_type, content))

    def test_wrong_type_nonstring_message_and_unknown_fields_are_rejected(self):
        for value in ({"error": {"type": "server_error", "message": "cpu"}},
                      {"error": {"type": "invalid_request_error", "message": 4}},
                      {"error": {"type": "invalid_request_error", "message": "cpu", "code": "unknown"}},
                      {"error": {"type": "invalid_request_error", "message": "cpu"}, "output": "unknown"}):
            self.assertFalse(llama_guard._pre_admission_validation_rejection(
                400, "application/json", json.dumps(value).encode()))


class NativeRejectionProofTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-rejection-cpu-")
        self.addCleanup(temporary.cleanup)
        registry = load_registry(ROOT)
        self.profile = registry.default
        self.metadata = NativeMetadata(Path(temporary.name).resolve(), self.profile, registry)
        context = patch.object(runtime_proof, "unchanged", side_effect=self.metadata.unchanged)
        context.start()
        self.addCleanup(context.stop)
        self.assertTrue(self.metadata.probe._probe(self.profile, "before_forward"))
        self.before = copy.deepcopy(self.metadata.state)

    def test_unchanged_idle_owner_is_not_fabricated_generation_completion(self):
        self.assertTrue(self.metadata.probe._probe(self.profile, "pre_admission_rejected"))
        self.assertFalse(self.metadata.probe._probe(self.profile, "generation_complete"))
        self.assertEqual(self.metadata.state, self.before)

    def test_every_owner_identity_change_rejects_retention(self):
        for field in runtime_proof.OWNER_KEYS:
            self.metadata.state = copy.deepcopy(self.before)
            self.metadata.state[field] = "changed"
            self.metadata.write()
            with self.subTest(field=field):
                self.assertFalse(self.metadata.probe._probe(self.profile, "pre_admission_rejected"))

    def test_native_activity_sequence_closed_context_or_progress_changes_reject_retention(self):
        changes = [{"active_lane_ids": ["req-8-1"]}, {"acquired_sequence": 8},
                   {"acquired_sequence": 8, "released_sequence": 8, "event_sequence": 16},
                   {"event_sequence": True}, {"released_sequence": True}, {"event_sequence": -1},
                   {"proof_valid": False}, {"stderr_closed": True}, {"child_exited": True},
                   {"native_context_tokens": 131072}, {"last_released_request_id": "req-8-1"},
                   {"last_prefill_progress": {"request_id": "req-8-1"}}]
        for mutation in changes:
            self.metadata.state = {**copy.deepcopy(self.before), **mutation}
            self.metadata.write()
            with self.subTest(mutation=mutation):
                self.assertFalse(self.metadata.probe._probe(self.profile, "pre_admission_rejected"))

    def test_missing_baseline_or_dead_owned_child_cannot_prove_retention(self):
        self.metadata.probe.baseline = None
        self.assertFalse(self.metadata.probe._probe(self.profile, "pre_admission_rejected"))
        self.metadata.probe.baseline = self.before
        self.metadata.alive = False
        self.assertFalse(self.metadata.probe._probe(self.profile, "pre_admission_rejected"))

    def test_changed_runtime_lease_cannot_prove_retention(self):
        lease = {key: self.metadata.state[key] for key in runtime_proof.OWNER_KEYS}
        lease["startup_id"] = "another-cpu-owner"
        self.metadata._write(self.metadata.leases / "gpu.lease/lease.json", lease)
        self.assertFalse(self.metadata.probe._probe(self.profile, "pre_admission_rejected"))


class NativeRejectionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-rejection-guard-cpu-")
        self.registry = ProfileRegistry(tuple(replace(profile, production_enabled=True)
                                              for profile in load_registry(ROOT).profiles))
        self.metadata = NativeMetadata(Path(self.temporary.name).resolve(), self.registry.default, self.registry)
        self.original_child = copy.deepcopy(self.metadata.state["child"])
        self.identity_patch = patch.object(runtime_proof, "unchanged", side_effect=self.metadata.unchanged)
        self.identity_patch.start()
        self.manager = None
        self.client = self.lifespan = None
        self.phases = []

    async def asyncTearDown(self):
        if self.client is not None:
            await self.client.aclose()
            await self.lifespan.__aexit__(None, None, None)
        self.identity_patch.stop()
        self.temporary.cleanup()

    async def setup_guard(self, **manager_options):
        self.manager = MockManager(self.metadata, **manager_options)

        async def proof(profile, phase):
            self.phases.append(phase)
            return self.metadata.probe._probe(profile, phase)

        app = llama_guard.create_app(manager_url="http://127.0.0.1:8081", control_token="synthetic-control",
                                     backend_token="synthetic-backend", registry=self.registry,
                                     resource_probe=proof, transport=httpx.MockTransport(self.manager.handler))
        self.guard = app.state.lifecycle_guard
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://guard.test",
                                       headers={"Authorization": "Bearer synthetic-backend"})

    def payload(self):
        return {"model": self.registry.default.public_alias, "messages": [{"role": "user", "content": "CPU only"}],
                "stream": True, "max_tokens": 32}

    async def test_complete_native400_retains_same_child_then_next_request_succeeds(self):
        await self.setup_guard()
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual((response.status_code, response.content), (400, native_error()))
        self.assertNotIn("/api/models/unload", self.manager.operations)
        self.assertIn("pre_admission_rejected", self.phases)
        status = await self.guard.status()
        self.assertEqual((status["active_inferences"], status["pending_inferences"]), (0, 0))
        self.assertFalse(status["admission_paused"])
        self.assertEqual(status["last_release_method"], "native_pre_admission_rejected")
        self.assertEqual(self.metadata.state["child"], self.original_child)
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"[DONE]", response.content)
        self.assertEqual(self.manager.maximum_forwarded, 1)
        self.assertEqual(self.manager.load_count, 0)
        self.assertEqual(self.metadata.state["child"], self.original_child)

    async def test_bad_image_uri_native400_keeps_identical_wire_and_child(self):
        body = native_error("image 1: invalid PNG or JPEG data URI")
        await self.setup_guard(body=body)
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual((response.status_code, response.content), (400, body))
        self.assertNotIn("/api/models/unload", self.manager.operations)
        self.assertEqual(self.metadata.state["child"], self.original_child)

    async def test_pending_same_profile_advances_without_unload_or_generation_overlap(self):
        await self.setup_guard(held=True)
        first = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.payload()))
        await asyncio.wait_for(self.manager.rejection_started.wait(), 1)
        second = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.payload()))
        for _ in range(100):
            if (await self.guard.status())["pending_inferences"] == 1:
                break
            await asyncio.sleep(.001)
        self.assertEqual((await self.guard.status())["pending_inferences"], 1)
        self.manager.rejection_release.set()
        bad, good = await asyncio.wait_for(asyncio.gather(first, second), 2)
        self.assertEqual((bad.status_code, good.status_code), (400, 200))
        self.assertNotIn("/api/models/unload", self.manager.operations)
        self.assertEqual((self.manager.maximum_forwarded, self.manager.load_count), (1, 0))
        self.assertEqual(self.metadata.state["child"], self.original_child)

    async def test_post_lane400_requires_owned_exit_before_pending_request_can_forward(self):
        await self.setup_guard(after_lane=True)
        self.manager.unload_gate = asyncio.Event()
        first = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.payload()))
        await asyncio.wait_for(self.manager.unload_started.wait(), 1)
        second = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.payload()))
        for _ in range(100):
            if (await self.guard.status())["pending_inferences"] == 1:
                break
            await asyncio.sleep(.001)
        status = await self.guard.status()
        self.assertEqual((status["active_inferences"], status["pending_inferences"]), (1, 1))
        self.assertEqual(self.manager.maximum_forwarded, 0)
        self.assertTrue(self.metadata.state["active_lane_ids"])
        self.manager.unload_gate.set()
        bad, good = await asyncio.wait_for(asyncio.gather(first, second), 2)
        self.assertEqual((bad.status_code, good.status_code), (400, 200))
        self.assertIn("/api/models/unload", self.manager.operations)
        self.assertEqual(self.manager.load_count, 1)
        self.assertNotEqual(self.metadata.state["child"], self.original_child)

    async def test_unknown4xx_preserves_existing_owned_cleanup(self):
        await self.setup_guard(status=422)
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("pre_admission_rejected", self.phases)
        self.assertIn("/api/models/unload", self.manager.operations)

    async def test_partial_response_never_claims_native_rejection_completion(self):
        await self.setup_guard(interrupted=True)
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("pre_admission_rejected", self.phases)
        self.assertIn("/api/models/unload", self.manager.operations)

    async def test_caller_disconnect_before_error_eof_requires_owned_exit_then_releases_fifo(self):
        await self.setup_guard(held=True)
        first = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.payload()))
        await asyncio.wait_for(self.manager.rejection_started.wait(), 1)
        second = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.payload()))
        for _ in range(100):
            if (await self.guard.status())["pending_inferences"] == 1:
                break
            await asyncio.sleep(.001)
        self.assertEqual((await self.guard.status())["pending_inferences"], 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        following = await asyncio.wait_for(second, 2)
        self.assertEqual(following.status_code, 200)
        self.assertIn("/api/models/unload", self.manager.operations)
        self.assertNotIn("pre_admission_rejected", self.phases)
        self.assertEqual((self.manager.maximum_forwarded, self.manager.load_count), (1, 1))
        self.assertNotEqual(self.metadata.state["child"], self.original_child)

    async def test_oversized_error_never_claims_native_rejection_completion(self):
        await self.setup_guard(body=native_error() + b" " * 4096)
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("pre_admission_rejected", self.phases)
        self.assertIn("/api/models/unload", self.manager.operations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
