# SPDX-License-Identifier: Apache-2.0
"""Source CI security policy, including public and fork PR counterexamples."""
import copy
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ("distribution-cpu.yml", "cloudflare-edge-cpu.yml")


def verify_source_workflow(document):
    events = document.get("on", document.get(True, {}))
    if not {"pull_request", "push", "workflow_dispatch"}.issubset(events) or "pull_request_target" in events:
        raise ValueError("source CI must cover ordinary PR, push and manual events")
    if document.get("permissions") != {"contents": "read"}:
        raise ValueError("source CI must use read-only repository permission")
    for job in document["jobs"].values():
        if "if" in job or "permissions" in job or "secrets" in job:
            raise ValueError("source jobs must not require visibility gates, secrets or extra permissions")
        if re.search(r"\$\{\{\s*secrets\.", str(job)):
            raise ValueError("source jobs must not receive secrets")
        for step in job["steps"]:
            action = step.get("uses")
            if action and not re.fullmatch(r"[\w./-]+@[a-f0-9]{40}", action):
                raise ValueError("actions must use exact immutable revisions")
            if action and action.startswith("actions/checkout@") and step.get("with", {}).get("persist-credentials") is not False:
                raise ValueError("checkout credentials must not persist")
            command = step.get("run", "")
            if re.search(r"--allow-inference|models\s+install\b[^\n]*--execute|npm\s+run\s+(?:deploy|upload|promote|dev)\b|wrangler\s+versions\s+(?:upload|deploy)", command):
                raise ValueError("source CI must not infer, download models or deploy")
    return events


class PublicCIPolicyTests(unittest.TestCase):
    def setUp(self):
        self.documents = [yaml.safe_load((ROOT / ".github/workflows" / name).read_text()) for name in WORKFLOWS]

    def test_private_public_and_fork_pr_contexts_have_same_secret_free_source_jobs(self):
        for document in self.documents:
            for context in ({"private": True, "fork": False}, {"private": False, "fork": False}, {"private": False, "fork": True}):
                with self.subTest(context=context):
                    events = verify_source_workflow(document)
                    self.assertIn("pull_request", events)
                    self.assertTrue(document["jobs"])
                    self.assertTrue(all("if" not in job for job in document["jobs"].values()))

    def test_visibility_gate_and_privileged_pr_event_refused(self):
        for document in self.documents:
            changed = copy.deepcopy(document)
            next(iter(changed["jobs"].values()))["if"] = "github.event.repository.private == true"
            with self.assertRaises(ValueError):
                verify_source_workflow(changed)
            changed = copy.deepcopy(document)
            changed.get("on", changed.get(True))["pull_request_target"] = None
            with self.assertRaises(ValueError):
                verify_source_workflow(changed)

    def test_write_permissions_and_secrets_refused(self):
        for field in ({"contents": "write"}, {"contents": "read", "id-token": "write"}):
            changed = copy.deepcopy(self.documents[0])
            changed["permissions"] = field
            with self.assertRaises(ValueError):
                verify_source_workflow(changed)
        changed = copy.deepcopy(self.documents[0])
        next(iter(changed["jobs"].values()))["env"] = {"TOKEN": "${{ secrets.API_TOKEN }}"}
        with self.assertRaises(ValueError):
            verify_source_workflow(changed)

    def test_mutable_action_and_persisted_checkout_auth_refused(self):
        for action, persisted in (("actions/checkout@main", False), ("actions/checkout@" + "a" * 40, True)):
            changed = copy.deepcopy(self.documents[0])
            next(iter(changed["jobs"].values()))["steps"][0] = {"uses": action, "with": {"persist-credentials": persisted}}
            with self.assertRaises(ValueError):
                verify_source_workflow(changed)

    def test_model_download_inference_and_deployment_refused(self):
        for command in ("lf models install model --execute", "python check-live.py --allow-inference", "npm run deploy", "wrangler versions upload"):
            changed = copy.deepcopy(self.documents[0])
            next(iter(changed["jobs"].values()))["steps"].append({"run": command})
            with self.assertRaises(ValueError):
                verify_source_workflow(changed)


if __name__ == "__main__":
    unittest.main()
