# SPDX-License-Identifier: Apache-2.0
"""CPU adversarial ingress tests; the official auth type is a test-only stand-in."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
import litellm_profile_policy as policy
import mtp2_shared_config as config
import mtp2_shared_ingress as ingress
import profile_registry

ROOT = Path(__file__).resolve().parents[2]
SECRET = b"synthetic-backend-secret-cpu-only-0123456789"
KEY = "sk-synthetic-virtual-key-cpu-only"


class AuthFixture:
    def __init__(self, *, key=KEY, models=None, virtual=True, session=False):
        self.api_key = key
        self.models = list(config.PUBLIC_ALIASES) if models is None else models
        self.via_virtual_key = virtual
        self.is_session_token = session
        self.metadata = {"tenant": "untrusted-metadata"}


class IngressTests(unittest.TestCase):
    def setUp(self):
        document = json.loads((ROOT / profile_registry.REGISTRY_PATH).read_bytes())
        self.registry = profile_registry.parse_registry(document, enabled_optin_profiles=config.PROFILE_IDS)
        self.policy = policy.ProfileAccessPolicy(frozenset(config.PUBLIC_ALIASES))
        self.auth = AuthFixture()
        self.patch = patch.object(ingress, "_official_auth_type", return_value=AuthFixture)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def body(self, alias=None, **extra):
        return {"model": alias or config.PUBLIC_ALIASES[0], "messages": [{"role": "user", "content": "synthetic"}], **extra}

    def issue(self, body=None, auth=None, **kw):
        body = self.body() if body is None else body
        return ingress.issue_capability(json.dumps(body).encode(), kw.pop("normalized_model", body["model"]),
                                        auth or self.auth, self.registry, backend_secret=SECRET,
                                        policy=self.policy, now=1000, **kw)

    def verify(self, cap, body=None, **kw):
        return ingress.verify_capability(cap, self.body() if body is None else body,
                                         self.registry, backend_secret=kw.pop("secret", SECRET), now=kw.pop("now", 1000), **kw)

    def test_three_aliases_keep_exact_logical_profile(self):
        for alias, profile_id in zip(config.PUBLIC_ALIASES, config.PROFILE_IDS):
            body = self.body(alias)
            verified = self.verify(self.issue(body), body)
            self.assertEqual((verified.alias, verified.profile_id), (alias, profile_id))

    def test_same_alias_gets_unique_request_ids(self):
        first, second = self.verify(self.issue()), self.verify(self.issue())
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertEqual(first.tenant, second.tenant)

    def test_user_cache_key_and_metadata_never_choose_tenant(self):
        baseline = self.verify(self.issue())
        changed = self.body(user="attacker", prompt_cache_key="peer", metadata={"user_id": "peer"})
        result = self.verify(self.issue(changed), changed)
        self.assertEqual(result.tenant, baseline.tenant)
        self.assertNotEqual(result.tenant, "peer")

    def test_different_authenticated_keys_are_separate(self):
        self.assertNotEqual(self.verify(self.issue()).tenant,
                            self.verify(self.issue(auth=AuthFixture(key="sk-other-synthetic-key"))).tenant)

    def test_raw_and_already_hashed_authenticated_key_have_same_partition(self):
        hashed = hashlib.sha256(KEY.encode()).hexdigest()
        self.assertEqual(self.verify(self.issue()).tenant,
                         self.verify(self.issue(auth=AuthFixture(key=hashed))).tenant)

    def test_private_physical_and_MTP0_are_rejected(self):
        for alias in (config.PHYSICAL_MODEL_ID, policy.DEFAULT_MODEL, "arbitrary"):
            with self.assertRaises(DistributionError):
                self.issue(self.body(alias))

    def test_disabled_logical_profile_is_rejected(self):
        document = json.loads((ROOT / profile_registry.REGISTRY_PATH).read_bytes())
        self.registry = profile_registry.parse_registry(document, enabled_optin_profiles=(config.PROFILE_IDS[0],))
        with self.assertRaises(DistributionError):
            self.issue(self.body(config.PUBLIC_ALIASES[-1]))

    def test_exact_grant_required_for_original_alias(self):
        for grants in ([], ["*"], ["all-proxy-models"], [config.PUBLIC_ALIASES[1]], [policy.DEFAULT_MODEL]):
            with self.assertRaises(DistributionError):
                self.issue(auth=AuthFixture(models=grants))

    def test_non_official_type_nonvirtual_and_master_fail(self):
        for auth in (object(), AuthFixture(virtual=False),
                     AuthFixture(key="LITELLM_PROXY_MASTER_KEY_ALIAS")):
            with self.assertRaises(DistributionError):
                self.issue(auth=auth)

    def test_validated_DB_virtual_key_is_not_rejected_for_session_flag(self):
        self.verify(self.issue(auth=AuthFixture(session=True)))

    def test_non_UTF8_and_BOM_bodies_are_rejected(self):
        raw = json.dumps(self.body())
        for body in (raw.encode("utf-16"), b"\xef\xbb\xbf" + raw.encode()):
            with self.assertRaises(DistributionError):
                ingress.validate_raw_ingress(body, self.registry)

    def test_normalized_alias_drift_is_rejected(self):
        for target in (config.PUBLIC_ALIASES[1], config.PHYSICAL_MODEL_ID, "Qwen3.8-Flash-Next"):
            with self.assertRaises(DistributionError):
                self.issue(normalized_model=target)

    def test_duplicate_fields_and_nonfinite_numbers_rejected_before_parser(self):
        for raw in (b'{"model":"'+config.PUBLIC_ALIASES[0].encode()+b'","model":"other"}',
                    json.dumps(self.body()).replace('"synthetic"', 'NaN').encode(),
                    json.dumps(self.body()).replace('"synthetic"', '1e999').encode(), b'[]'):
            with self.assertRaises(DistributionError):
                ingress.validate_raw_ingress(raw, self.registry)

    def test_internal_fields_headers_and_nested_policy_are_rejected(self):
        for extra in ({"liliuxflow_profile": "ctx262k-mtp2"}, {"request_id": "caller"},
                      {"tenant": "caller"}, {"resident_epoch": "caller"},
                      {"options": {"num_ctx": 262144}}, {"extra_body": {"model": config.PUBLIC_ALIASES[-1]}},
                      {"metadata": {"liliuxflow_tenant": "caller"}},
                      {"extra_headers": {"X-Liliuxflow-Mtp2-Admission": "fake"}}):
            with self.assertRaises(DistributionError):
                self.issue(self.body(**extra))
        with self.assertRaises(DistributionError):
            self.issue(headers={"X-Liliuxflow-Mtp2-Admission": "fake"})

    def test_tool_schema_and_messages_internal_named_keys_are_payload_data(self):
        body = self.body(tools=[{"type": "function", "function": {"name": "sample", "parameters": {
            "type": "object", "properties": {"liliuxflow_profile": {"type": "string"},
                                              "tenant": {"type": "string"}}}}}])
        body["messages"] = [{"role": "user", "content": [{"liliuxflow_profile": "literal-data"}]}]
        before = copy.deepcopy(body)
        self.verify(self.issue(body), body)
        self.assertEqual(body, before)

    def test_nested_routing_containers_still_reject_internal_named_keys(self):
        for container in ("options", "extra_body", "chat_template_kwargs", "metadata", "headers"):
            body = self.body(**{container: {"app": [{"liliuxflow_profile": "injection"}]}})
            with self.assertRaises(DistributionError):
                self.issue(body)

    def test_output_budget_policy_stays_bounded(self):
        for extra in ({"max_tokens": True}, {"max_tokens": 65537}, {"max_tokens": 8, "max_completion_tokens": 9}):
            with self.assertRaises(DistributionError):
                self.issue(self.body(**extra))
        self.verify(self.issue(self.body(max_tokens=65536)), self.body(max_tokens=65536))

    def test_capability_signature_tamper_and_other_secret_fail(self):
        cap = self.issue()
        encoded, signature = cap.split(".")
        with self.assertRaises(DistributionError):
            self.verify(encoded + "." + ("0" if signature[0] != "0" else "1") + signature[1:])
        with self.assertRaises(DistributionError):
            self.verify(cap, secret=b"another-synthetic-backend-secret-012345")

    def test_capability_cannot_be_used_for_another_alias(self):
        with self.assertRaises(DistributionError):
            self.verify(self.issue(), self.body(config.PUBLIC_ALIASES[-1]))

    def test_expired_future_and_lifetime_bounds_rejected(self):
        cap = self.issue(ttl_seconds=30)
        for now in (999, 1030, 1100, True):
            with self.assertRaises(DistributionError):
                self.verify(cap, now=now)
        for ttl in (0, 61, True):
            with self.assertRaises(DistributionError):
                self.issue(ttl_seconds=ttl)

    def test_external_document_is_never_mutated_or_enriched(self):
        body = self.body(user="caller")
        original = copy.deepcopy(body)
        self.verify(self.issue(body), body)
        self.assertEqual(body, original)

    def test_capability_does_not_include_key_or_user_metadata(self):
        cap = self.issue()
        payload = ingress.base64.urlsafe_b64decode(cap.split(".")[0] + "==").decode()
        self.assertNotIn(KEY, payload)
        self.assertNotIn("untrusted-metadata", payload)


@unittest.skipUnless(importlib.util.find_spec("litellm"), "run with the pinned LiteLLM Python 3.12 runtime for actual auth type checks")
class ActualPinnedAuthTypeTests(unittest.TestCase):
    """Actual installed type and field semantics, with synthetic keys; no DB/auth flow."""
    def setUp(self):
        from litellm.proxy._types import UserAPIKeyAuth
        self.auth_type = UserAPIKeyAuth
        document = json.loads((ROOT / profile_registry.REGISTRY_PATH).read_bytes())
        self.registry = profile_registry.parse_registry(document, enabled_optin_profiles=config.PROFILE_IDS)
        self.policy = policy.ProfileAccessPolicy(frozenset(config.PUBLIC_ALIASES))
        self.raw = json.dumps({"model": config.PUBLIC_ALIASES[0], "messages": []}).encode()
        self.auth = UserAPIKeyAuth(api_key=hashlib.sha256(KEY.encode()).hexdigest(), models=list(config.PUBLIC_ALIASES))

    def issue(self):
        return ingress.issue_capability(self.raw, config.PUBLIC_ALIASES[0], self.auth, self.registry,
                                        backend_secret=SECRET, policy=self.policy, now=1000)

    def test_constructor_cannot_forge_server_only_virtual_key_marker(self):
        self.auth = self.auth_type(api_key=hashlib.sha256(KEY.encode()).hexdigest(),
                                   models=list(config.PUBLIC_ALIASES), via_virtual_key=True)
        self.assertFalse(self.auth.via_virtual_key)
        with self.assertRaises(DistributionError):
            self.issue()

    def test_actual_marked_type_and_exact_model_grants_issue_capability(self):
        self.auth.via_virtual_key = True  # Same post-validation assignment as pinned DB/cache auth source.
        cap = self.issue()
        result = ingress.verify_capability(cap, json.loads(self.raw), self.registry, backend_secret=SECRET, now=1000)
        self.assertEqual(result.alias, config.PUBLIC_ALIASES[0])

    def test_actual_unmarked_UI_JWT_fallback_stays_failclosed(self):
        self.auth.is_session_token = True
        with self.assertRaises(DistributionError):
            self.issue()

    def test_actual_marked_DB_key_session_flag_keeps_exact_grant_requirement(self):
        self.auth.via_virtual_key = True
        self.auth.is_session_token = True
        self.issue()
        self.auth.models = [config.PUBLIC_ALIASES[1]]
        with self.assertRaises(DistributionError):
            self.issue()


if __name__ == "__main__":
    unittest.main()
