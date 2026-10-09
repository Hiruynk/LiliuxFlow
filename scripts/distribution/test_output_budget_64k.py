"""CPU checks for bounded output budgets and unchanged context/key semantics.

SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(os.environ.get("LILIUXFLOW_OUTPUT_TEST_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(ROOT / "services/compat/src"))
sys.path.insert(0, str(ROOT / "scripts/distribution"))
from compat_api.app import CompatModelProfile, Settings, create_app
from compat_api.llama_guard import create_app as create_guard, _GuardError
from profile_registry import MAX_OUTPUT_TOKENS, load_registry


PROFILES = tuple(CompatModelProfile("qwen3.8-flash-next-lily-q4-" + name, context, deadline)
                 for name, context, deadline in (("64k", 65536, 3672), ("128k", 131072, 4272), ("262k", 262144, 4872)))


class OutputBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        async def upstream(request):
            if request.url.path == "/v1/models":
                allowed = PROFILES[:1] if request.headers["authorization"] == "Bearer restricted" else PROFILES
                return httpx.Response(200, json={"data": [{"id": p.public_alias} for p in allowed]})
            self.calls.append((json.loads(request.content), request.headers["authorization"]))
            await asyncio.sleep(0)
            data = b'data: {"choices":[{"delta":{"reasoning_content":"thinking","content":"visible"},"finish_reason":null}]}\n\n'
            data += b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)
        settings = Settings("http://litellm.test", PROFILES[0].public_alias, "Qwen3.8-Flash-Next", 65536,
                            "rev", "a" * 64, 67, profiles=PROFILES)
        self.app = create_app(settings, transport=httpx.MockTransport(upstream))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://compat.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def chat(self, profile, *, budget=None, token="caller", stream=False):
        payload = {"model": profile.public_alias, "messages": [{"role": "user", "content": "test"}], "stream": stream}
        if budget is not None:
            payload["options"] = {"num_predict": budget}
        return await self.client.post("/api/chat", headers={"authorization": "Bearer " + token}, json=payload)

    async def test_nonempty_64k_input_uses_native_remaining_window_clamp(self):
        registry = load_registry(ROOT)
        async def proof(_profile, _phase): return True
        async def count(_value, profile): return profile.context_tokens - 1
        app = create_guard('http://127.0.0.1:18081', 'control', backend_token='backend', registry=registry,
                           resource_probe=proof, context_admission=count)
        guard = app.state.lifecycle_guard
        profile = await guard._profile({'model': registry.default.public_alias, 'max_tokens': 65536})
        self.assertEqual(profile.context_tokens, 65536)
        async def overflow(_value, profile): return profile.context_tokens
        guard.context_admission = overflow
        with self.assertRaises(_GuardError):
            await guard._profile({'model': registry.default.public_alias, 'max_tokens': 65536})

    def test_registry_defaults_allow_64k_output_with_unchanged_total_contexts(self):
        registry = load_registry(ROOT)
        self.assertEqual(MAX_OUTPUT_TOKENS, 65536)
        self.assertEqual([p.default_output_tokens for p in registry.profiles], [65536] * 6)
        self.assertEqual([p.context_tokens for p in registry.enabled_profiles], [65536, 131072, 262144])
        self.assertEqual([p.context_tokens for p in registry.profiles if p.engine_id != 'legacy-db3-mtp0'], [65536, 131072, 262144])
        self.assertTrue(all(not p.production_enabled for p in registry.profiles if p.engine_id != 'legacy-db3-mtp0'))

    async def test_omitted_budgets_are_64k_without_changing_caller_or_thinking(self):
        responses = await asyncio.gather(*(self.chat(profile, token=str(index)) for index, profile in enumerate(PROFILES)))
        self.assertEqual([r.status_code for r in responses], [200] * 3)
        forwarded = {body["model"]: (body, key) for body, key in self.calls}
        for index, profile in enumerate(PROFILES):
            body, key = forwarded[profile.public_alias]
            self.assertEqual(body["max_tokens"], 65536)
            self.assertEqual(key, "Bearer " + str(index))
            self.assertNotIn("enable_thinking", body)
            self.assertNotIn("reasoning_effort", body)
            self.assertNotIn("num_ctx", body)

    async def test_explicit_small_or_64k_budget_and_length_signal_survive_both_wire_modes(self):
        for profile in PROFILES:
            for budget, stream in ((32, False), (65536, False), (65536, True)):
                response = await self.chat(profile, budget=budget, stream=stream)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.calls[-1][0]["max_tokens"], budget)
                if stream:
                    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    self.assertEqual(frames[-1]["done_reason"], "length")
                    self.assertEqual(sum(frame.get("done") is True for frame in frames), 1)
                else:
                    self.assertEqual(response.text, "visible")
                    self.assertEqual(response.headers["x-legacy-done-reason"], "length")

    async def test_budget_over_64k_and_invalid_types_never_forward(self):
        for profile in PROFILES:
            for budget in (65537, 131072, True, 1.0, 0, -1):
                self.assertEqual((await self.chat(profile, budget=budget)).status_code, 400)
        self.assertEqual(self.calls, [])

    async def test_long_profile_acl_remains_required(self):
        self.assertEqual((await self.chat(PROFILES[2], token="restricted")).status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_capabilities_distinguish_output_budget_from_total_context(self):
        for profile in PROFILES:
            response = await self.client.get("/api/capabilities", params={"model": profile.public_alias},
                                             headers={"authorization": "Bearer caller"})
            facts = response.json()
            self.assertEqual(facts["generation"]["default_num_predict"], 65536)
            self.assertEqual(facts["generation"]["maximum_requested_num_predict"], 65536)
            self.assertTrue(facts["generation"]["thinking_tokens_included_in_output_budget"])
            self.assertEqual(facts["runtime"]["configured_context_limit"], profile.context_tokens)


if __name__ == "__main__":
    unittest.main()
