# SPDX-License-Identifier: Apache-2.0
"""Finite native build recipe. This is not installation or live authorization.

These code-side inputs reconstruct one reviewed source tree. The normal source
generator must bind this module and the final serving boundary before a shared
runtime can be selected. No local approval document or receipt supplies pins.
"""
from common import DistributionError, no_symlinks, read_object, relative_path
from trust import verified_file, sha256
from ui_recipe import patch_paths
from mtp2_shared_runtime import (ENGINE_ID, BASELINE_PATH, BOUNDARY_SOURCES,
    FROZEN_SOURCE_EXPECTATION as EXPECTED, select_shared_recipe, _baseline, candidate_expectation)

NATIVE_BUILD_INPUTS = frozenset('scripts/distribution/' + name for name in (
    'build_native.py', 'native_recipe.py', 'mtp2_shared_recipe.py',
    'mtp2_shared_runtime.py', 'trust.py', 'optin_engine.py', 'profile_registry.py',
    'common.py', 'ui_recipe.py', 'build_budget.py', 'ownership.py')) | {
    'manifests/distribution/native-sources.json', BASELINE_PATH,
    'manifests/distribution/lily-opt64-source-baseline.json'} | {
        row['path'] for row in EXPECTED.patches}
SERVING_INPUTS = BOUNDARY_SOURCES | NATIVE_BUILD_INPUTS | frozenset(
    'scripts/distribution/' + name for name in (
        'mtp2_shared_gateway.py', 'mtp2_shared_ui.py', 'mtp2_shared_journal.py', 'mtp2_shared_upgrade.py', 'agent.py', 'liliuxflow.py',
        'litellm_profile_policy.py', 'patch_litellm_profiles.py')) | frozenset(
    'services/compat/src/compat_api/' + name for name in (
        'portable.py', 'app.py', 'profile.py', 'llama_guard.py', '__init__.py')) | {
    'services/litellm/pyproject.toml', 'services/litellm/uv.lock',
    'LICENSE', 'NOTICE', 'docs/productization/licenses/lily/NOTICE',
    'manifests/distribution/original-notices.json', 'scripts/distribution/notices.py'}

def native_build_inputs(performance_candidate=False):
    expected = candidate_expectation(performance_candidate)
    return NATIVE_BUILD_INPUTS | {expected.baseline_path} | {row['path'] for row in expected.patches}

def serving_inputs(performance_candidate=False):
    return SERVING_INPUTS | native_build_inputs(performance_candidate)

def source_preflight(source, *, performance_candidate=False):
    """Validate before download, scratch allocation or compiler invocation."""
    expected = candidate_expectation(performance_candidate)
    source = no_symlinks(source)
    lock = read_object(source / 'manifests/distribution/native-sources.json')
    pin, baseline = select_shared_recipe(lock, expected)
    rows = _baseline(source, expected)
    for item in expected.patches:
        patch = verified_file(source, item)
        for path in patch_paths(patch, 1):
            relative_path(path)
            if path not in rows:
                raise DistributionError('shared native source patch escapes the fixed inventory')
    records = {}
    for path in sorted(native_build_inputs(performance_candidate)):
        file = no_symlinks(source / path)
        if not file.is_file():
            raise DistributionError('shared native build boundary input is missing')
        records[path] = sha256(file)
    return pin, baseline, records

def serving_source_records(source, *, performance_candidate=False):
    """Normal generator hook, invoked only after final gateway source freeze.

    This computes hashes from the actual release source and rechecks them before
    publication. No hashes for in-progress gateway files are frozen here.
    """
    source_preflight(source, performance_candidate=performance_candidate)
    records = []
    for path in sorted(serving_inputs(performance_candidate)):
        file = no_symlinks(source / path)
        if not file.is_file():
            raise DistributionError('shared serving boundary input is incomplete')
        records.append({'path': path, 'sha256': sha256(file)})
    for record in records:
        verified_file(source, record)
    return records
