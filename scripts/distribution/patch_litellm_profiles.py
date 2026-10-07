# SPDX-License-Identifier: Apache-2.0
"""Produce a hash-bound catalog patch for the pinned LiteLLM proxy source.

This generator writes a proposed source file only. Deployment and private
backups remain an explicit operator lifecycle operation.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile

from common import DistributionError, no_symlinks

MARKER = "# LILIUXFLOW_CONTEXT_PROFILE_CATALOG_V1"
POLICY_MODULE = "litellm.proxy._liliuxflow_context_profiles"
PINNED_BEFORE_DIGESTS = frozenset((
    "8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7",
    "0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8",
))


def _atomic(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=".profile-policy-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600); os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def install_runtime_policy(source_root: Path, data_root: Path):
    """Patch only an unstarted new installation runtime with reviewed inputs."""
    source_root = no_symlinks(source_root); data_root = no_symlinks(data_root)
    proxy = no_symlinks(data_root / "runtime/litellm/lib/python3.12/site-packages/litellm/proxy/proxy_server.py")
    module = no_symlinks(proxy.parent / "_liliuxflow_context_profiles.py")
    helper = no_symlinks(source_root / "scripts/distribution/litellm_profile_policy.py")
    if module.exists() or (data_root / "runtime/release-trust.json").exists() or (data_root / "run/agent.json").exists():
        raise DistributionError("profile policy installation requires a new unstarted runtime")
    before = proxy.read_bytes(); before_sha256 = hashlib.sha256(before).hexdigest()
    if before_sha256 not in PINNED_BEFORE_DIGESTS:
        raise DistributionError("pinned LiteLLM proxy before digest is unreviewed")
    patched = patch_catalog_source(before, before_sha256)
    policy = helper.read_bytes(); compile(policy, str(module), "exec")
    try:
        _atomic(module, policy)
        if proxy.read_bytes() != before:
            raise DistributionError("LiteLLM source changed during isolated policy installation")
        _atomic(proxy, patched)
    except BaseException:
        if module.exists() and module.read_bytes() == policy: module.unlink()
        if proxy.read_bytes() == patched: _atomic(proxy, before)
        raise
    return {"module": {"path": str(module.relative_to(data_root)), "sha256": hashlib.sha256(policy).hexdigest()},
            "proxy": {"path": str(proxy.relative_to(data_root)), "before_sha256": before_sha256,
                      "sha256": hashlib.sha256(patched).hexdigest()}, "catalog_filters": 3}


def patch_catalog_source(content: bytes, expected_sha256: str) -> bytes:
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise DistributionError("LiteLLM proxy source differs from the reviewed before digest")
    text = content.decode()
    if MARKER in text:
        raise DistributionError("LiteLLM context catalog patch is already present")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    inserts = []
    for function_name, expected_count in (("model_list", 2), ("model_info", 1)):
        functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name]
        if len(functions) != 1:
            raise DistributionError("LiteLLM model catalog function differs")
        matched = []
        for node in ast.walk(functions[0]):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or node.targets[0].id != "all_models":
                continue
            call = node.value.value if isinstance(node.value, ast.Await) else node.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in ("get_complete_model_list", "get_available_models_for_user"):
                matched.append(node)
        if len(matched) != expected_count:
            raise DistributionError("LiteLLM model catalog assignment topology differs")
        for node in matched:
            indent = " " * node.col_offset
            addition = (indent + MARKER + "\n" + indent + "from " + POLICY_MODULE + " import filter_profile_catalog\n"
                        + indent + "all_models = filter_profile_catalog(all_models, user_api_key_dict)\n")
            inserts.append((node.end_lineno, addition))
    for line, addition in sorted(inserts, reverse=True):
        lines.insert(line, addition)
    result = "".join(lines)
    ast.parse(result)
    if result.count(MARKER) != 3:
        raise DistributionError("LiteLLM catalog filter coverage differs")
    return result.encode()


def generate(source: Path, output: Path, expected_sha256: str):
    source = no_symlinks(source); output = no_symlinks(output)
    if source == output:
        raise DistributionError("catalog proposal must not overwrite its source")
    content = source.read_bytes()
    result = patch_catalog_source(content, expected_sha256)
    if output.exists():
        raise DistributionError("catalog proposal output must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return {"status": "READY_FOR_REVIEW", "before_sha256": hashlib.sha256(content).hexdigest(),
            "after_sha256": hashlib.sha256(result).hexdigest(), "catalog_filters": 3,
            "standard_auth_unchanged": True, "generation_policy": "official CustomLogger pre-call hook",
            "policy_module": POLICY_MODULE}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-before-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.source, args.output, args.expected_before_sha256), indent=2))
