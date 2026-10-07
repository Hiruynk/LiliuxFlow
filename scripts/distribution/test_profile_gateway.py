"""Offline owner ACL reconciliation and DB/YAML duplicate classification."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
from common import DistributionError
from profile_registry import parse_registry

SOURCE = Path(__file__).resolve().parents[2]


class GatewayProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="profile ACL CPU 空間-")
        self.root = Path(self.folder.name).resolve()
        self.root.chmod(0o700)
        document = json.loads((SOURCE / "profiles/distribution/context-registry.json").read_text())
        for index, row in enumerate(document["profiles"]):
            row["production_enabled"] = index < 2
            row["validation_only"] = index == 2
        self.registry = parse_registry(document)
        self.alias = self.registry.default.public_alias
        self.key = "sk-synthetic-cpu-only"
        self.user = "local-owner-safe64k"
        self.caller = {"user_id": self.user, "alias": self.alias, "api_key": self.key,
                       "api_base": "http://127.0.0.1:4000/v1", "models": [self.alias]}
        self.path = self.root / "owner.json"
        self.path.write_text(json.dumps(self.caller)); self.path.chmod(0o600)
        self.key_info = {"user_id": self.user, "models": [self.alias], "max_budget": 31.5,
                         "expires": "2027-01-03T12:34:56Z", "rpm_limit": 60, "tpm_limit": 1000000,
                         "key_alias": "designated-owner", "blocked": False, "team_id": None,
                         "allowed_routes": ["llm_api_routes"], "permissions": {"allow": "inference"}}
        self.user_info = {"user_id": self.user, "models": [self.alias], "max_budget": 90,
                          "user_role": "internal_user", "user_email": None}
        self.calls = []
        self.fail_key = False
        self.change_expiry = False

    def tearDown(self):
        self.folder.cleanup()

    def http(self, url, *, method="GET", payload=None, token=None, **kwargs):
        self.calls.append((url, method, copy.deepcopy(payload), token))
        if url.endswith("/v2/key/info"):
            self.assertEqual(method, "POST")
            self.assertEqual(token, "synthetic-management-only")
            self.assertEqual(payload, {"keys": [self.key]})
            return 200, {"key": [self.key], "info": [copy.deepcopy(self.key_info)]}
        if "/user/info?user_id=" in url:
            self.assertEqual(token, "synthetic-management-only")
            return 200, {"user_info": copy.deepcopy(self.user_info)}
        if url.endswith("/user/update"):
            self.assertEqual(set(payload), {"user_id", "models"})
            self.assertEqual(payload["user_id"], self.user)
            self.user_info["models"] = list(payload["models"])
            return 200, {}
        if url.endswith("/key/update"):
            self.assertEqual(set(payload), {"key", "models"})
            self.assertEqual(payload["key"], self.key)
            if self.fail_key:
                return 500, {}
            self.key_info["models"] = list(payload["models"])
            if self.change_expiry:
                self.key_info["expires"] = "changed-by-faulty-fixture"
            return 200, {}
        self.fail("Unexpected endpoint")

    def reconcile(self, **kwargs):
        with patch.object(agent, "http_json", self.http):
            return agent.reconcile_designated_owner_caller(caller_path=self.path, expected_user_id=self.user,
                api_base=self.caller["api_base"], master_key="synthetic-management-only", registry=self.registry,
                backup_root=self.root / "backups", **kwargs)

    def test_adds_only_enabled_profile_and_preserves_key_budget_expiry_and_limits(self):
        original = copy.deepcopy(self.key_info)
        result = self.reconcile()
        enabled = [p.public_alias for p in self.registry.enabled_profiles]
        self.assertEqual(self.key_info["models"], enabled)
        self.assertEqual(self.user_info["models"], enabled)
        self.assertEqual(json.loads(self.path.read_text())["api_key"], self.key)
        for name, value in original.items():
            if name != "models": self.assertEqual(self.key_info[name], value)
        self.assertNotIn(self.registry.profiles[2].public_alias, result["key_models"])
        self.assertFalse(any("sk-" in url for url, *_ in self.calls))
        self.assertTrue(all(token == "synthetic-management-only" for _, method, _, token in self.calls if method == "POST"))
        backups = list((self.root / "backups").glob("*/before.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_dry_run_does_not_write_or_mutate(self):
        before = self.path.read_bytes()
        result = self.reconcile(dry_run=True)
        self.assertTrue(result["key_changed"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / "backups").exists())
        self.assertFalse(any(url.endswith(("/key/update", "/user/update")) for url, *_ in self.calls))

    def test_wrong_key_owner_and_unrestricted_acl_are_refused_without_mutation(self):
        for models in ([], ["*"], ["all-proxy-models"], ["all-team-models"], ["qwen*"]):
            self.key_info["models"] = models
            with self.assertRaises(DistributionError): self.reconcile()
        self.key_info["models"] = [self.alias]; self.key_info["user_id"] = "another-user"
        with self.assertRaises(DistributionError): self.reconcile()
        self.assertFalse(any(url.endswith(("/key/update", "/user/update")) for url, *_ in self.calls))

    def test_management_inventory_requires_one_matching_key_without_mutation(self):
        for response in ({"key": [self.key], "info": []},
                         {"key": [self.key], "info": [self.key_info, self.key_info]},
                         {"key": ["sk-another-synthetic-key"], "info": [self.key_info]}):
            before = self.path.read_bytes()
            def malformed(url, **kwargs):
                if url.endswith("/v2/key/info"):
                    self.assertEqual(kwargs["token"], "synthetic-management-only")
                    self.assertEqual(kwargs["method"], "POST")
                    self.assertEqual(kwargs["payload"], {"keys": [self.key]})
                    return 200, copy.deepcopy(response)
                return self.http(url, **kwargs)
            with patch.object(agent, "http_json", malformed):
                with self.assertRaises(DistributionError):
                    agent.reconcile_designated_owner_caller(caller_path=self.path, expected_user_id=self.user,
                        api_base=self.caller["api_base"], master_key="synthetic-management-only", registry=self.registry,
                        backup_root=self.root / "backups")
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(any(url.endswith(("/key/update", "/user/update")) for url, *_ in self.calls))

    def test_key_update_failure_restores_user_acl(self):
        self.fail_key = True
        before = self.path.read_bytes()
        with self.assertRaises(DistributionError): self.reconcile()
        self.assertEqual(self.user_info["models"], [self.alias])
        self.assertEqual(self.key_info["models"], [self.alias])
        self.assertEqual(self.path.read_bytes(), before)

    def test_verification_mismatch_restores_both_acl_lists(self):
        self.change_expiry = True
        with self.assertRaises(DistributionError): self.reconcile()
        self.assertEqual(self.user_info["models"], [self.alias])
        self.assertEqual(self.key_info["models"], [self.alias])

    def test_model_inventory_classifies_duplicates_and_unknown_db_owner(self):
        rows = [{"model_name": self.alias, "model_info": {"id": "liliuxflow-ctx64k"}}]
        report = agent.profile_route_inventory(rows, [], self.registry)
        self.assertFalse(report["requires_review"])
        self.assertTrue(report["rows"][0]["owned"])
        duplicate = agent.profile_route_inventory(rows, rows, self.registry)
        self.assertTrue(duplicate["requires_review"])
        unknown = agent.profile_route_inventory([], [{"model_name": self.alias, "model_info": {"id": "unowned-id"}}], self.registry)
        self.assertTrue(unknown["requires_review"])
        self.assertFalse(unknown["rows"][0]["owned"])

    def test_validation_routes_require_explicit_scoped_record_and_key_hash(self):
        document = json.loads((SOURCE / "profiles/distribution/context-registry.json").read_text())
        for row in document['profiles']:
            row['production_enabled'] = row['profile_id'] == 'ctx64k'
            row['validation_only'] = row['profile_id'] != 'ctx64k'
        registry = parse_registry(document)
        (self.root / 'run').mkdir(mode=0o700)
        trusted = {'data_root': self.root, 'registry': registry,
                   'config': {'installation_id': 'synthetic-owned-installation', 'ports': {'guard': 18080, 'manager': 18081, 'litellm': 14000}},
                   'trust': {'profile_registry': {'sha256': 'b' * 64}, 'binaries': {'lily': {'sha256': 'c' * 64}}},
                   'source_root': SOURCE, 'secrets': {'MANAGER_BACKEND_TOKEN': 'synthetic'},
                   'binaries': {'compat_python': self.root / 'synthetic-python'}}
        alias = registry.profiles[1].public_alias
        record = {'schema_version': 1, 'installation_id': trusted['config']['installation_id'],
                  'registry_sha256': 'b' * 64, 'binary_sha256': 'c' * 64,
                  'profile_ids': ['ctx128k'], 'expires_at': time.time() + 60,
                  'validation_keys': {alias: ['d' * 64]}}
        path = self.root / 'run/context-validation.json'
        path.write_text(json.dumps(record)); path.chmod(0o600)
        self.assertEqual(agent.validation_routes(trusted), ((), {}))
        profiles, keys = agent.validation_routes(trusted, ('ctx128k',))
        self.assertEqual(profiles[0].public_alias, alias)
        self.assertFalse(profiles[0].production_enabled)
        self.assertEqual(keys, {alias: ['d' * 64]})
        manager, lp = agent.configs(trusted, validation_profile_ids=('ctx128k',))
        self.assertIn('--validation', json.loads(manager.read_text())['models'][alias]['cmd'])
        policy = json.loads((self.root / 'run/context-profile-policy.json').read_text())
        self.assertEqual(policy['enabled_models'], [registry.default.public_alias])
        self.assertEqual(policy['validation_keys'], keys)
        self.assertEqual(len(json.loads(lp.read_text())['model_list']), 2)
        with self.assertRaises(DistributionError): agent.validation_routes(trusted, ('ctx262k',))
        record['expires_at'] = time.time() - 1; path.write_text(json.dumps(record))
        with self.assertRaises(DistributionError): agent.validation_routes(trusted, ('ctx128k',))

    def test_launch_agent_preserves_reviewed_interactive_scheduling(self):
        trusted = {'data_root': self.root, 'source_root': SOURCE,
                   'config': {'launchd_label': 'synthetic-owned-label', 'installation_id': 'ee50290b-e7a1-4938-a9ee-bb3c358920c8', 'owner_uid': os.getuid()},
                   'binaries': {'compat_python': self.root / 'synthetic-python'}}
        self.assertEqual(agent.launchd_plist(trusted)['ProcessType'], 'Interactive')


if __name__ == "__main__":
    unittest.main()
