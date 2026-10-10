"""Candidate-only CPU configuration/policy checks; no services or model loads.

SPDX-License-Identifier: Apache-2.0
"""
from pathlib import Path
import copy
import json
import shlex
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts/distribution"))
import agent
from common import DistributionError
import mtp2_shared_config as shared
import profile_registry as profiles


DOCUMENT = json.loads((ROOT / profiles.REGISTRY_PATH).read_bytes())


class SharedMtp2ConfigTests(unittest.TestCase):
    def registry(self, enabled=shared.PROFILE_IDS):
        return profiles.parse_registry(copy.deepcopy(DOCUMENT), enabled_optin_profiles=enabled)

    def baseline(self, root, registry):
        trusted = {
            "data_root": root, "source_root": ROOT, "registry": registry,
            "config": {"ports": {"guard": 18080, "manager": 18081, "litellm": 14000,
                                  "compat": 18001, "postgresql": 15432}},
            "secrets": {"MANAGER_BACKEND_TOKEN": "CPU-only-synthetic"},
            "binaries": {"compat_python": Path(sys.executable)},
        }
        manager, litellm = agent.configs(trusted)
        return json.loads(manager.read_bytes()), json.loads(litellm.read_bytes())

    def adapt(self, manager, registry, root):
        return shared.shared_manager_config(manager, registry, runner_python=Path(sys.executable),
                                            source_root=ROOT, data_root=root)

    def test_one_physical_row_three_aliases_and_hard_native_policy(self):
        with tempfile.TemporaryDirectory(prefix="shared MTP2 CPU ") as directory:
            root = Path(directory).resolve()
            (root / "run").mkdir(mode=0o700)
            registry = self.registry()
            before, litellm = self.baseline(root, registry)
            original = copy.deepcopy(before)
            candidate = self.adapt(before, registry, root)
            self.assertEqual(before, original)
            self.assertEqual(len(candidate["models"]), 4)
            record = candidate["models"][shared.PHYSICAL_MODEL_ID]
            self.assertEqual(record["aliases"], list(shared.PUBLIC_ALIASES))
            self.assertEqual(record["filters"]["setParamsByID"], {
                alias: {"liliuxflow_profile": pid}
                for pid, alias in zip(shared.PROFILE_IDS, shared.PUBLIC_ALIASES)
            })
            self.assertEqual(record["concurrencyLimit"], 1)
            self.assertTrue(record["unlisted"])
            self.assertEqual(record["capabilities"]["context"], 262144)
            self.assertEqual(record["useModelName"], "Qwen3.8-Flash-Next")
            argv = shlex.split(record["cmd"])
            self.assertEqual(argv[1], str(ROOT / "scripts/distribution/mtp2_shared_runner.py"))
            self.assertEqual(argv[argv.index("--data-root") + 1], str(root))
            self.assertEqual(argv[-2:], ["--port", "${PORT}"])
            self.assertNotIn("--profile", argv)
            self.assertNotIn(shared.PHYSICAL_MODEL_ID,
                             [row["model_name"] for row in litellm["model_list"]])

    def test_all_MTP0_records_globals_and_logical_routes_keep_exact_JSON_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "run").mkdir(mode=0o700)
            registry = self.registry()
            before, litellm = self.baseline(root, registry)
            litellm_before = (root / "run/litellm.json").read_bytes()
            candidate = self.adapt(before, registry, root)
            encode = lambda value: json.dumps(value, ensure_ascii=False).encode()
            for profile in registry.profiles[:3]:
                self.assertEqual(encode(before["models"][profile.public_alias]),
                                 encode(candidate["models"][profile.public_alias]))
            self.assertEqual(encode({k: v for k, v in before.items() if k != "models"}),
                             encode({k: v for k, v in candidate.items() if k != "models"}))
            self.assertEqual((root / "run/litellm.json").read_bytes(), litellm_before)
            self.assertEqual([row["model_name"] for row in litellm["model_list"]],
                             [profile.public_alias for profile in registry.enabled_profiles])
            self.assertEqual(registry.default.profile_id, "ctx64k")
            self.assertEqual(registry.default.as_dict()["mtp_drafts"], 0)

    def test_partial_enablement_exposes_only_original_enabled_aliases(self):
        for enabled in ((), ("ctx64k-mtp2",), ("ctx64k-mtp2", "ctx128k-mtp2")):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / "run").mkdir(mode=0o700)
                registry = self.registry(enabled)
                before, _ = self.baseline(root, registry)
                candidate = self.adapt(before, registry, root)
                if not enabled:
                    self.assertEqual(candidate, before)
                else:
                    aliases = [registry.by_id(pid).public_alias for pid in enabled]
                    record = candidate["models"][shared.PHYSICAL_MODEL_ID]
                    self.assertEqual(record["aliases"], aliases)
                    self.assertEqual(set(record["filters"]["setParamsByID"]), set(aliases))

    def test_conflicting_physical_ID_or_routing_cannot_silently_rewrite_baseline(self):
        registry = self.registry()
        manager = {"models": {alias: {} for alias in shared.PUBLIC_ALIASES}}
        for bad in ({**manager, "groups": {}},
                    {"models": {**manager["models"], shared.PHYSICAL_MODEL_ID: {}}},
                    {"models": {shared.PUBLIC_ALIASES[0]: {}}}):
            original = copy.deepcopy(bad)
            with self.assertRaises(DistributionError):
                self.adapt(bad, registry, Path("/CPU/trusted"))
            self.assertEqual(bad, original)

    def test_external_fields_cannot_raise_profile_or_override_runtime(self):
        registry = self.registry()
        baseline = {"model": shared.PUBLIC_ALIASES[0], "messages": [], "max_tokens": 65536}
        for field, value in (("liliuxflow_profile", "ctx262k-mtp2"),
                             ("liliuxflow_request_id", "req-1-1"),
                             ("liliuxflow_tenant", "foreign"),
                             ("context_tokens", 262144), ("drafts", 0), ("max_batch", 2)):
            for container in (None, *shared.ROUTING_CONTAINERS):
                bad = copy.deepcopy(baseline)
                if container is None:
                    bad[field] = value
                else:
                    bad[container] = {field: value}
                with self.subTest(field=field, container=container), self.assertRaises(DistributionError):
                    shared.external_mtp2_profile(bad, registry)
        with self.assertRaises(DistributionError):
            shared.external_mtp2_profile({"model": shared.PHYSICAL_MODEL_ID}, registry)
        with self.assertRaises(DistributionError):
            shared.external_mtp2_profile({**baseline, "extra_body": {"model": shared.PUBLIC_ALIASES[2]}}, registry)

    def test_standard_OpenAI_values_are_preserved_and_request_profiles_detached(self):
        registry = self.registry()
        request = {"model": shared.PUBLIC_ALIASES[0], "messages": [], "user": "synthetic-public-user",
                   "tools": [{"type": "function", "function": {"name": "sample", "parameters": {
                       "type": "object", "properties": {"liliuxflow_profile": {"type": "string"}}}}}],
                   "chat_template_kwargs": {"enable_thinking": True}, "max_tokens": 65536}
        before = copy.deepcopy(request)
        profile = shared.external_mtp2_profile(request, registry)
        self.assertEqual(request, before)
        self.assertEqual(profile.context_tokens, 65536)
        self.assertEqual(shared.external_mtp2_profile({"model": shared.PUBLIC_ALIASES[2]}, registry).context_tokens, 262144)
        self.assertEqual(profile.context_tokens, 65536)

    def test_raw_JSON_duplicates_nonfinite_and_nested_overrides_are_rejected(self):
        alias = shared.PUBLIC_ALIASES[0]
        bodies = [
            ('{"model":"' + alias + '","model":"' + shared.PUBLIC_ALIASES[2] + '"}').encode(),
            ('{"model":"' + alias + '","chat_template_kwargs":{"enable_thinking":true,"enable_thinking":false}}').encode(),
            ('{"model":"' + alias + '","temperature":NaN}').encode(),
            ('{"model":"' + alias + '","temperature":1e999}').encode(),
            b"[]", b"null", b"{",
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(DistributionError):
                shared.parse_external_mtp2_body(body)
        request = shared.parse_external_mtp2_body(json.dumps({"model": alias, "messages": []}).encode())
        self.assertEqual(shared.external_mtp2_profile(request, self.registry()).profile_id, "ctx64k-mtp2")
        with self.assertRaises(DistributionError):
            shared.parse_external_mtp2_body(b"{}", maximum_body_bytes=1)

    def test_output_budget_types_limits_and_conflicts_fail_before_mapping(self):
        registry = self.registry()
        for budgets in ({"max_tokens": True}, {"max_tokens": 0}, {"max_tokens": 65537},
                        {"max_tokens": 32, "max_completion_tokens": 64}):
            with self.subTest(budgets=budgets), self.assertRaises(DistributionError):
                shared.external_mtp2_profile({"model": shared.PUBLIC_ALIASES[0], **budgets}, registry)
        shared.external_mtp2_profile({"model": shared.PUBLIC_ALIASES[0],
                                     "max_tokens": 65536, "max_completion_tokens": 65536}, registry)


if __name__ == "__main__":
    unittest.main()
