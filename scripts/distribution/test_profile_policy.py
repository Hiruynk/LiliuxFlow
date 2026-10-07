# SPDX-License-Identifier: Apache-2.0
"""Finite profile access policy under existing LiteLLM virtual key semantics."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import litellm_profile_policy as policy
from common import DistributionError
import patch_litellm_profiles as patcher
from patch_litellm_profiles import patch_catalog_source, MARKER

MODEL64 = policy.DEFAULT_MODEL
MODEL128 = "qwen3.8-flash-next-lily-q4-128k"
MODEL262 = "qwen3.8-flash-next-lily-q4-262k"


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.test_hash = hashlib.sha256(b"sk-synthetic-validation-only").hexdigest()
        self.control = policy.parse_policy({"schema_version": 1, "enabled_models": [MODEL64],
                                           "validation_keys": {MODEL128: [self.test_hash]}})
        policy.load_policy.cache_clear()

    def tearDown(self):
        policy.load_policy.cache_clear()

    def test_disabled_profile_needs_literal_grant_and_specific_test_key(self):
        for models in ([], ["*"], ["all-proxy-models"], [MODEL64], [MODEL128]):
            self.assertFalse(policy.explicit_profile_access(MODEL128, models, policy=self.control))
        self.assertTrue(policy.explicit_profile_access(MODEL128, [MODEL128], policy=self.control, key_fingerprint=self.test_hash))
        self.assertFalse(policy.explicit_profile_access(MODEL128, [], policy=self.control, key_fingerprint=self.test_hash))
        self.assertFalse(policy.explicit_profile_access(MODEL262, [MODEL262], policy=self.control, key_fingerprint=self.test_hash))

    def test_enabled_profile_still_needs_explicit_key_grant(self):
        accepted = policy.ProfileAccessPolicy(frozenset((MODEL64, MODEL128)))
        self.assertTrue(policy.explicit_profile_access(MODEL128, [MODEL128], policy=accepted))
        for models in ([], ["*"], ["all-proxy-models"], [MODEL64]):
            self.assertFalse(policy.explicit_profile_access(MODEL128, models, policy=accepted))
            self.assertTrue(policy.explicit_profile_access(MODEL64, models, policy=accepted))

    def test_catalog_filter_does_not_infer_permissions_from_admin_role(self):
        with patch.object(policy, "load_policy", return_value=self.control):
            caller = SimpleNamespace(models=[], api_key="ordinary-hash", user_role="proxy_admin")
            self.assertEqual(policy.filter_profile_catalog([MODEL64, MODEL128, MODEL262], caller), [MODEL64])
            caller.models = [MODEL128]; caller.api_key = self.test_hash
            self.assertEqual(policy.filter_profile_catalog([MODEL64, MODEL128, MODEL262], caller), [MODEL64, MODEL128])

    def test_caller_fingerprint_matches_litellm_already_hashed_or_raw_values(self):
        self.assertEqual(policy.caller_fingerprint("sk-synthetic-validation-only"), self.test_hash)
        self.assertEqual(policy.caller_fingerprint(self.test_hash), self.test_hash)
        self.assertIsNone(policy.caller_fingerprint("LITELLM_PROXY_MASTER_KEY_ALIAS"))

    def test_policy_loader_is_private_hash_bound_and_immutable(self):
        with tempfile.TemporaryDirectory(prefix="profile policy CPU-") as name:
            target = Path(name).resolve() / "policy.json"
            target.write_text(json.dumps({"schema_version": 1, "enabled_models": [MODEL64], "validation_keys": {MODEL128: [self.test_hash]}})); target.chmod(0o600)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            env = {"LILIUXFLOW_PROFILE_POLICY_PATH": str(target), "LILIUXFLOW_PROFILE_POLICY_SHA256": digest}
            with patch.dict(os.environ, env, clear=True):
                selected = policy.load_policy()
                self.assertEqual(selected, self.control)
                target.write_text("changed")
                self.assertIs(policy.load_policy(), selected)
                policy.load_policy.cache_clear()
                with self.assertRaises(ValueError): policy.load_policy()
            target.write_text("{}"); target.chmod(0o644)
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError): policy.load_policy()

    def test_catalog_patch_is_digest_bound_covers_list_expand_and_model_retrieve(self):
        source = b'''# existing welcome patch remains exact\nasync def model_list(user_api_key_dict):\n    if expanded:\n        all_models = get_complete_model_list(key_models=[])\n        return all_models\n    all_models = await get_available_models_for_user(user_api_key_dict=user_api_key_dict)\n    return all_models\nasync def model_info(user_api_key_dict):\n    all_models = await get_available_models_for_user(user_api_key_dict=user_api_key_dict)\n    return all_models\n'''
        digest = hashlib.sha256(source).hexdigest()
        result = patch_catalog_source(source, digest)
        self.assertEqual(result.count(MARKER.encode()), 3)
        self.assertTrue(result.startswith(b"# existing welcome patch remains exact\n"))
        self.assertEqual(result.count(b"filter_profile_catalog(all_models, user_api_key_dict)"), 3)
        compile(result, "catalog-policy-fixture", "exec")
        with self.assertRaises(DistributionError): patch_catalog_source(source, "0" * 64)
        with self.assertRaises(DistributionError): patch_catalog_source(result, hashlib.sha256(result).hexdigest())

    def test_new_runtime_install_copies_exact_helper_and_preserves_original_source_lines(self):
        source = b'''# Welcome source remains exact\nasync def model_list(user_api_key_dict):\n    if expanded:\n        all_models = get_complete_model_list(key_models=[])\n        return all_models\n    all_models = await get_available_models_for_user(user_api_key_dict=user_api_key_dict)\n    return all_models\nasync def model_info(user_api_key_dict):\n    all_models = await get_available_models_for_user(user_api_key_dict=user_api_key_dict)\n    return all_models\n'''
        with tempfile.TemporaryDirectory(prefix="isolated native policy CPU-") as name:
            folder = Path(name).resolve(); root = folder / "source"; data = folder / "data"
            helper = root / "scripts/distribution/litellm_profile_policy.py"
            helper.parent.mkdir(parents=True); helper.write_bytes(Path(policy.__file__).read_bytes())
            proxy = data / "runtime/litellm/lib/python3.12/site-packages/litellm/proxy/proxy_server.py"
            proxy.parent.mkdir(parents=True); proxy.write_bytes(source)
            digest = hashlib.sha256(source).hexdigest()
            with patch.object(patcher, "PINNED_BEFORE_DIGESTS", frozenset((digest,))):
                result = patcher.install_runtime_policy(root, data)
            module = data / result["module"]["path"]
            self.assertEqual(module.read_bytes(), helper.read_bytes())
            stripped = [line for line in proxy.read_bytes().splitlines(keepends=True)
                        if MARKER.encode() not in line and b"from litellm.proxy._liliuxflow_context_profiles" not in line
                        and b"all_models = filter_profile_catalog(" not in line]
            self.assertEqual(b"".join(stripped), source)
            self.assertEqual(result["catalog_filters"], 3)
            self.assertEqual(proxy.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(DistributionError): patcher.install_runtime_policy(root, data)

    def test_source_package_policy_requires_registry_policy_and_finite_ui_inputs(self):
        from package import eligible
        root = Path(__file__).resolve().parents[2]
        allowlist = json.loads((root / 'manifests/distribution/source-allowlist.json').read_text())
        required = ('profiles/distribution/context-registry.json', 'scripts/distribution/profile_registry.py',
                    'scripts/distribution/runtime_proof.py', 'scripts/distribution/litellm_profile_policy.py',
                    'scripts/distribution/patch_litellm_profiles.py', 'manifests/distribution/ui-recipe-context-profiles.json')
        for name in required:
            self.assertIn(name, allowlist['required_paths'])
            self.assertTrue(eligible(name, allowlist))
            self.assertTrue((root / name).is_file())
        recipe = json.loads((root / required[-1]).read_text())['apps']['llama-swap']
        inputs = [{'path': row['path'], 'sha256': row['sha256']} for row in recipe['patches']]
        inputs += [{'path': row['source'], 'sha256': row['sha256']} for row in recipe['extra_files']]
        for row in inputs:
            self.assertTrue(eligible(row['path'], allowlist))
            self.assertEqual(hashlib.sha256((root / row['path']).read_bytes()).hexdigest(), row['sha256'])
        for excluded in ('AGENTS.md', 'var/secrets/context-profiles-owner.json', 'artifacts/context-profiles/B/READY_FOR_REVIEW.json',
                         'model.safetensors', 'models/config.json'):
            self.assertFalse(eligible(excluded, allowlist))


if __name__ == "__main__":
    unittest.main()
