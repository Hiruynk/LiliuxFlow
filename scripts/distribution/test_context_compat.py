"""CPU request isolation and legacy wire contracts for canonical profiles."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/compat/src"))
from compat_api.app import CompatModelProfile, Settings, create_app

PROFILES = tuple(CompatModelProfile("qwen3.8-flash-next-lily-q4-" + name, tokens, deadline)
                 for name, tokens, deadline in (("64k", 65536, 3672), ("128k", 131072, 4272), ("262k", 262144, 4872)))


class ProfilesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        async def handler(request):
            if request.url.path == "/v1/models":
                allowed = PROFILES[:1] if request.headers["authorization"] == "Bearer restricted" else PROFILES
                return httpx.Response(200, json={"data": [{"id": p.public_alias} for p in allowed]})
            self.calls.append((json.loads(request.content), request.headers["authorization"], request.extensions["timeout"]))
            await asyncio.sleep(0)
            payload = b'data: {"choices":[{"delta":{"content":"visible"},"finish_reason":null}]}\n\n'
            payload += b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)
        self.settings = Settings("http://litellm.test", PROFILES[0].public_alias, "Qwen3.8-Flash-Next", 65536, "rev", "a" * 64, 67, profiles=PROFILES)
        self.app = create_app(self.settings, transport=httpx.MockTransport(handler))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://compat.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def chat(self, profile, *, budget=4096, stream=False, token="caller", think=None):
        return await self.client.post("/api/chat", headers={"authorization": "Bearer " + token}, json={
            "model": profile.public_alias, "messages": [{"role": "user", "content": "test"}],
            "stream": stream, "options": {"num_predict": budget}, "think": think})

    async def test_simultaneous_requests_keep_model_budget_timeout_key_and_thinking_local(self):
        responses = await asyncio.gather(self.chat(PROFILES[0], token="first", think=False),
                                         self.chat(PROFILES[1], budget=32768, token="second", think="low"),
                                         self.chat(PROFILES[2], budget=65536, token="third"))
        self.assertEqual([r.status_code for r in responses], [200, 200, 200])
        by_model = {body["model"]: (body, key, timeout) for body, key, timeout in self.calls}
        for profile, budget, token in zip(PROFILES, (4096, 32768, 65536), ("first", "second", "third")):
            body, key, timeout = by_model[profile.public_alias]
            self.assertEqual(body["max_tokens"], budget)
            self.assertEqual(key, "Bearer " + token)
            self.assertEqual(timeout["read"], profile.total_deadline_seconds + 30)
        self.assertFalse(by_model[PROFILES[0].public_alias][0]["enable_thinking"])
        self.assertEqual(by_model[PROFILES[1].public_alias][0]["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", by_model[PROFILES[2].public_alias][0])
        self.assertEqual(self.settings.context_limit_tokens, 65536)
        with self.assertRaises(FrozenInstanceError):
            PROFILES[0].context_tokens = 131072

    async def test_output_over_profile_cap_rejected_without_forward(self):
        self.assertEqual((await self.chat(PROFILES[0], budget=65537)).status_code, 400)
        self.assertEqual((await self.chat(PROFILES[1], budget=70000)).status_code, 400)
        self.assertEqual((await self.chat(PROFILES[2], budget=140000)).status_code, 400)
        self.assertEqual(self.calls, [])

    async def test_acl_denial_precedes_generation(self):
        self.assertEqual((await self.chat(PROFILES[1], token="restricted")).status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_disabled_and_retired_aliases_are_not_forwarded(self):
        with patch.object(self.app.state, "compat_settings", Settings("http://litellm.test", PROFILES[0].public_alias, "Qwen3.8-Flash-Next", 65536, "rev", "a" * 64, 67, profiles=PROFILES[:1])):
            self.assertEqual((await self.chat(PROFILES[1])).status_code, 404)
        for name in ("qwen38-flash-next-q4-safe64k", "Qwen3.8-Flash-Next"):
            response = await self.client.post("/api/chat", headers={"authorization": "Bearer caller"}, json={"model": name, "messages": []})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.calls, [])

    async def test_capability_and_shared_checkpoint_identity_select_requested_profile(self):
        for profile in PROFILES:
            cap = await self.client.get("/api/capabilities", params={"model": profile.public_alias}, headers={"authorization": "Bearer caller"})
            self.assertEqual(cap.json()["runtime"]["configured_context_limit"], profile.context_tokens)
            self.assertEqual(cap.json()["generation"]["total_deadline_seconds"], profile.total_deadline_seconds)
            blob = await self.client.get("/api/model-blob-identity", params={"model": profile.public_alias}, headers={"authorization": "Bearer caller"})
            self.assertEqual(blob.json()["requested_model"], profile.public_alias)
            self.assertEqual(blob.json()["checkpoint_manifest_sha256"], "a" * 64)
        self.assertEqual(self.calls, [])

    async def test_plain_text_and_sse_remain_distinct_with_single_terminal(self):
        plain = await self.chat(PROFILES[1])
        self.assertEqual(plain.text, "visible")
        self.assertEqual(plain.headers["x-legacy-done-reason"], "length")
        stream = await self.chat(PROFILES[2], stream=True)
        self.assertNotIn("[DONE]", stream.text)
        frames = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(sum(frame.get("done") is True for frame in frames), 1)
        self.assertEqual(frames[-1]["done_reason"], "length")

    async def test_deadline_counts_from_arrival_before_authentication(self):
        import compat_api.app as app_module
        real_send = app_module._send_before_disconnect
        records = []
        async def spy(request, upstream, built, *, deadline):
            records.append(deadline - request.scope["compat_received_at"])
            return await real_send(request, upstream, built, deadline=deadline)
        with patch.object(app_module, "_send_before_disconnect", spy):
            self.assertEqual((await self.chat(PROFILES[1])).status_code, 200)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0], 4272, places=8)


if __name__ == "__main__":
    unittest.main()
