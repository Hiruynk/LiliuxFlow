# SPDX-License-Identifier: Apache-2.0
"""Portable CPU regressions for finite image admission on the pinned engine.

No image decoding, native/model process, HTTP listener or production data is used.
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/distribution'))
sys.path.insert(0, str(ROOT / 'services/compat/src'))
import model_runner
from profile_registry import OPENAI_MAX_IMAGES, load_registry
from compat_api.app import MAX_MEDIA_IMAGES, MAX_MEDIA_BYTES, MAX_TOTAL_MEDIA_BYTES
from compat_api.llama_guard import _GuardError, create_app


def image_body(alias, counts):
    """Synthetic descriptors only; admission must precede data-URI decoding."""
    return {'model': alias, 'messages': [
        {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,CPU_ONLY'}}
                                   for _ in range(count)]}
        for count in counts]}


class ImageAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = load_registry(ROOT)
        self.app = create_app(manager_url='http://127.0.0.1:18081', control_token='synthetic-control',
                              backend_token='synthetic-backend', registry=self.registry,
                              resource_probe=lambda *_: True,
                              validation_aliases=tuple(p.public_alias for p in self.registry.profiles))
        self.guard = self.app.state.lifecycle_guard

    async def test_count_0_10_12_64_preserves_all_parts_and_selected_profile(self):
        for profile in self.registry.profiles:
            for count in (0, 10, 12, 64):
                with self.subTest(profile=profile.profile_id, count=count):
                    body = image_body(profile.public_alias, [count])
                    before = copy.deepcopy(body)
                    selected = await self.guard._profile(body)
                    self.assertEqual(selected, profile)
                    self.assertEqual(body, before)
                    self.assertEqual(sum(len(m['content']) for m in body['messages']), count)

    async def test_65_total_across_messages_rejected_before_context_observer(self):
        for counts in ([65], [32, 32, 1]):
            body = image_body(self.registry.default.public_alias, counts)
            before = copy.deepcopy(body)
            self.guard.context_admission = AsyncMock(side_effect=AssertionError('must not preprocess images'))
            with self.assertRaises(_GuardError) as caught:
                await self.guard._profile(body)
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(caught.exception.code, 'too_many_images')
            self.assertEqual(body, before)
            self.guard.context_admission.assert_not_awaited()

    async def test_guard_HTTP_65_never_acquires_or_loads_a_model(self):
        calls = []
        def manager(request):
            calls.append(request.url.path)
            raise AssertionError('oversize image count must not contact manager')
        self.guard.client = httpx.AsyncClient(transport=httpx.MockTransport(manager))
        acquire = AsyncMock(side_effect=AssertionError('must not acquire a native lane'))
        try:
            with patch.object(self.guard, '_acquire', acquire):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://guard.test') as client:
                    response = await client.post('/v1/chat/completions', headers={'Authorization': 'Bearer synthetic-backend'},
                                                 json=image_body(self.registry.default.public_alias, [64, 1]))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['error']['type'], 'too_many_images')
            acquire.assert_not_awaited()
            self.assertEqual(calls, [])
            status = await self.guard.status()
            self.assertEqual(status['active_inferences'], 0)
            self.assertEqual(status['pending_inferences'], 0)
            self.assertFalse(status['admission_paused'])
        finally:
            await self.guard.client.aclose()
            self.guard.client = None

    async def test_request_image_count_override_cannot_change_finite_policy(self):
        for location in ('top', 'options', 'extra_body'):
            body = image_body(self.registry.default.public_alias, [10])
            if location == 'top':
                body['max_images'] = 999
            else:
                body[location] = {'max_images': 999}
            with self.assertRaises(_GuardError) as caught:
                await self.guard._profile(body)
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(caught.exception.code, 'invalid_request')
            self.assertEqual(self.registry.maximum_images, 64)

    async def test_context_and_output_budget_contract_is_preserved(self):
        self.assertEqual([p.context_tokens for p in self.registry.profiles], [65536, 131072, 262144])
        self.assertEqual([p.default_output_tokens for p in self.registry.profiles], [65536] * 3)
        for profile in self.registry.profiles:
            body = image_body(profile.public_alias, [10])
            body['max_tokens'] = 65536
            self.assertEqual(await self.guard._profile(body), profile)
            body['max_tokens'] = 65537
            with self.assertRaises(_GuardError) as caught:
                await self.guard._profile(body)
            self.assertEqual(caught.exception.code, 'invalid_request')


class ImageArgvContractTests(unittest.TestCase):
    def test_registry_limit_is_fixed_and_request_body_cap_remains_8MiB(self):
        registry = load_registry(ROOT)
        self.assertEqual(OPENAI_MAX_IMAGES, 64)
        self.assertEqual(registry.maximum_images, OPENAI_MAX_IMAGES)
        self.assertEqual(registry.maximum_body_bytes, 8 * 1024**2)
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            registry.maximum_images = 999

    def test_actual_three_profile_argv_adds_pinned_image_limit_without_mode_changes(self):
        registry = load_registry(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            trusted = {'registry': registry, 'data_root': Path(temporary).resolve(),
                       'binaries': {'lily': Path('/synthetic/pinned-lily')},
                       'config': {'model_dir': '/synthetic/same-Q4', 'ports': {'guard': 18080}}}
            caches = []
            for profile in registry.profiles:
                argv = model_runner.lily_argv(trusted, 19000, profile_id=profile.profile_id, allow_validation=True)
                values = dict(zip(argv[1::2], argv[2::2]))
                self.assertEqual(values['--max-images'], '64')
                self.assertEqual(argv.count('--max-images'), 1)
                self.assertEqual(values['--max-seq'], str(profile.context_tokens))
                self.assertEqual(values['--mtp-drafts'], '0')
                self.assertEqual(values['--ngram-table'], 'paged')
                self.assertEqual(values['--vision'], 'auto')
                self.assertEqual(values['--cache-bytes'], str(profile.cache_bytes))
                self.assertEqual(values['--max-sessions'], str(profile.max_sessions))
                self.assertEqual(values['--model'], '/synthetic/same-Q4')
                self.assertNotIn('--thinking', argv)
                self.assertNotIn('--image-max-pixels', argv)
                self.assertNotIn('--image-min-pixels', argv)
                caches.append(values['--disk-cache-dir'])
            self.assertEqual(len(set(caches)), 3)

    def test_compat_png_contract_remains_separate(self):
        self.assertEqual(MAX_MEDIA_IMAGES, 1)
        self.assertEqual(MAX_MEDIA_BYTES, 5 * 1024**2)
        self.assertEqual(MAX_TOTAL_MEDIA_BYTES, 5 * 1024**2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
