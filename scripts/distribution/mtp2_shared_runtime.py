# SPDX-License-Identifier: Apache-2.0
"""Unwired CPU adapters for one finite shared MTP2 native recipe.

The expectation below is an INTERNAL, code-side review input. It must be frozen
into the distributed recipe by the normal generator after master review. Never
derive it from an installation receipt, inference input, or a private approval
file. No shared recipe is enabled by default in this prototype.

Existing MTP0 and unshared OPT64 selectors are deliberately not replaced. These
helpers do not build, launch, create a lease, grant an alias, or write trust.
Native stderr is evidence only: it cannot authorize a live request release.
An authenticated owned native transport and request-ID binding remain required.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from common import DistributionError, no_symlinks, read_object, relative_path
from native_recipe import OPT64_ENGINE, apply_lily_source_patches, select_lily_recipe
from optin_engine import LATEST_COMMIT, PATCHES, effective_env
from profile_registry import MAX_OUTPUT_TOKENS, OPT_TARGETS, ProfileRegistry, SHARED
from trust import sha256, verified_file
from ui_recipe import git_environment, patch_paths


ENGINE_ID = 'latest13f-defer-pc123-mtp2-shared-context-v1'
BINARY_PATH = 'runtime/bin/lily-mtp2-shared'
BASELINE_PATH = 'manifests/distribution/lily-mtp2-shared-source-baseline.json'
REPOSITORY = 'fabiogreter/lily-qwen3.8-flash-next'
ARCHIVE_SHA256 = '4406664bdb4df433a660dee85b6338b28e968d92ee5756c2656add17affe8758'
RESIDENT_CONTEXT_TOKENS = 262144
ACTIVE_LIMIT = 2
CACHE_BYTES = 24 * 1024**3
MAX_SESSIONS = 2
PENDING_LIMIT = 4
CACHE_NAMESPACE = 'lily-q4-afde8b8e-13f7b540-shared-mtp2-v1'
PROFILE_IDS = tuple(row[0] for row in OPT_TARGETS)
CONTEXTS = dict((row[0], row[2]) for row in OPT_TARGETS)
BASE_PATCH_PATHS = tuple('scripts/distribution/patches/lily-opt64/' + name for name in (
    '00-verified-event-defer.patch', '01-prefix-privacy.patch',
    '02-cancel-drop-queue-terminal.patch', '03-effective-native-metadata.patch'))
SHARED_PATCH_PATHS = (
    'scripts/distribution/patches/lily-mtp2-shared/04-shared-context-policy.patch',
    'scripts/distribution/patches/lily-mtp2-shared/05-shared-mtp2-scheduler.patch',
)
SERVICE_PATCH_PATH = 'scripts/distribution/patches/lily-mtp2-shared/06-authenticated-control-tenant-cache.patch'
PERFORMANCE_PATCH_PATH = 'scripts/distribution/patches/lily-mtp2-shared/07-shared-idle-cpu-copy.patch'
COPY_BASELINE_PATH = 'manifests/distribution/lily-mtp2-shared-cpu-copy-source-baseline.json'
OUTPUT_PATCH_PATH = 'scripts/distribution/patches/lily-mtp2-shared/08-controlled-output-queue.patch'
OUTPUT_BASELINE_PATH = 'manifests/distribution/lily-mtp2-shared-bounded-output-source-baseline.json'
OUTPUT_PATCH_SHA256 = '45683e2f314c400cfdf6a70401b3152aab8c2cd5df8e507143f2b12c746f1f2d'
PERFORMANCE_PATCH_SHA256 = 'a24bc39fe50e6f4c4d94663623355ba4fd39ab06c46c36634b7d6822d86db6f3'
# These must be bound by generated release trust before installation selection.
# Runner and transport require the final frozen native recipe; merely finding
# these helpers is insufficient to admit an installed shared runtime.
BOUNDARY_SOURCES = frozenset('scripts/distribution/' + name for name in (
    'mtp2_shared_runtime.py', 'mtp2_shared_runner.py', 'mtp2_shared_transport.py',
    'mtp2_shared_key_fd.py', 'mtp2_shared_owner.py',
    'mtp2_shared_config.py', 'mtp2_shared_coordinator.py', 'mtp2_shared_ingress.py',
    'build_native.py', 'native_recipe.py', 'optin_engine.py', 'profile_registry.py',
    'trust.py', 'ownership.py', 'model_runner.py', 'runtime_proof.py',
    'native_metadata.py', 'common.py', 'ui_recipe.py'))
BOUNDARY_SOURCES = BOUNDARY_SOURCES | {
    'manifests/distribution/native-sources.json', 'profiles/distribution/context-registry.json'}
HEX64 = re.compile(r'[0-9a-f]{64}')


def _same(actual, expected):
    """Exact JSON types; True must never compare equal to an integer one."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(_same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, (tuple, list)):
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RecipeExpectation:
    """Master-bound candidate inputs, not an approval or a trust receipt.

    P1 and P2 hashes include all final reviewed source/test/transport changes.
    The final 237-file candidate is not implicitly a shipping expectation.
    """
    p1_patch_sha256: str
    p2_patch_sha256: str
    baseline_sha256: str
    source_inventory_sha256: str
    source_file_count: int
    service_patch_sha256: str | None = None
    performance_patch_sha256: str | None = None
    output_patch_sha256: str | None = None

    def __post_init__(self):
        values = (self.p1_patch_sha256, self.p2_patch_sha256,
                  self.baseline_sha256, self.source_inventory_sha256)
        if (any(not isinstance(v, str) or HEX64.fullmatch(v) is None for v in values)
                or type(self.source_file_count) is not int or not 1 <= self.source_file_count <= 4096
                or self.service_patch_sha256 is not None and (type(self.service_patch_sha256) is not str
                    or HEX64.fullmatch(self.service_patch_sha256) is None)
                or self.performance_patch_sha256 is not None and (self.service_patch_sha256 is None
                    or type(self.performance_patch_sha256) is not str
                    or self.performance_patch_sha256 != PERFORMANCE_PATCH_SHA256)
                or self.output_patch_sha256 is not None and (self.performance_patch_sha256 is None
                    or type(self.output_patch_sha256) is not str
                    or self.output_patch_sha256 != OUTPUT_PATCH_SHA256)):
            raise DistributionError('shared MTP2 code-side recipe expectation is malformed')

    @property
    def patches(self):
        service = () if self.service_patch_sha256 is None else (SERVICE_PATCH_PATH,)
        hashes = () if self.service_patch_sha256 is None else (self.service_patch_sha256,)
        performance = () if self.performance_patch_sha256 is None else (PERFORMANCE_PATCH_PATH,)
        performance_hash = () if self.performance_patch_sha256 is None else (self.performance_patch_sha256,)
        output = () if self.output_patch_sha256 is None else (OUTPUT_PATCH_PATH,)
        output_hash = () if self.output_patch_sha256 is None else (self.output_patch_sha256,)
        return tuple({'path': path, 'sha256': digest, 'unidiff_zero': False}
                     for path, digest in zip(BASE_PATCH_PATHS + SHARED_PATCH_PATHS + service + performance + output,
                                             PATCHES + (self.p1_patch_sha256, self.p2_patch_sha256) + hashes + performance_hash + output_hash))

    @property
    def baseline_path(self):
        if self.output_patch_sha256 is not None:
            return OUTPUT_BASELINE_PATH
        return BASELINE_PATH if self.performance_patch_sha256 is None else COPY_BASELINE_PATH

    @property
    def pin_key(self):
        if self.output_patch_sha256 is not None:
            return 'lily_mtp2_shared_bounded_output'
        return 'lily_mtp2_shared' if self.performance_patch_sha256 is None else 'lily_mtp2_shared_cpu_copy'

    def pin(self):
        return {'repository': REPOSITORY, 'commit': LATEST_COMMIT, 'archive_sha256': ARCHIVE_SHA256,
                'patches': list(self.patches), 'baseline': self.baseline_path,
                'source_inventory_sha256': self.source_inventory_sha256,
                'source_file_count': self.source_file_count, 'default': False}


# Source-replayed native candidate only. This does not enable an installation
# or certify live safety; the normal builder must produce separate local trust.
FROZEN_SOURCE_EXPECTATION = RecipeExpectation(
    '285e22c430a77d91cb124e8b6d67af87a6edb41a31013de7ad8dc1a97d50ffcf',
    'ecdc592566f80817915d3a481869d14e238dcfe08d342d6428457ac2a1c72d41',
    'de058950f78b762102fbe434e91dca687235bcc49728c65188f495f55c3a4858',
    '98c7641171782f1bf78ade4bd2578a18a314d8513ddabe73c73aaf46d959b26b',
    238,
    service_patch_sha256='c136811ae91658a5964f6519f8f62eeebc286021974ea7339e587fc0d8e7bb9f',
)


# Native source candidate only; these constants do not enable production.
CPU_COPY_EXPECTATION = RecipeExpectation(
    FROZEN_SOURCE_EXPECTATION.p1_patch_sha256, FROZEN_SOURCE_EXPECTATION.p2_patch_sha256,
    '699a443529986225f5c226d04784d828c401acb861f61e0b8aac3752702e9d8b',
    'bb65024dfe6946ccfc05621ff60308ac5e949e27b2a280a01ffbc7549cb24893',
    240, service_patch_sha256=FROZEN_SOURCE_EXPECTATION.service_patch_sha256,
    performance_patch_sha256=PERFORMANCE_PATCH_SHA256,
)

# Source-only append08. Original 238/240 expectations and pins remain available.
# The existing explicit candidate factory selects these latest closed inputs;
# no caller/environment chooses an output queue recipe or supplies its hashes.
BOUNDED_OUTPUT_EXPECTATION = RecipeExpectation(
    CPU_COPY_EXPECTATION.p1_patch_sha256, CPU_COPY_EXPECTATION.p2_patch_sha256,
    '0fa668fefee42b0d1a40452f02dbb65dc29e110f4482ab13c5136acc9a28f28c', '9a37b17dd44c8da9f5c1d70594251c04497c55c1f16b703040fada01f2c94652', 241,
    service_patch_sha256=CPU_COPY_EXPECTATION.service_patch_sha256,
    performance_patch_sha256=PERFORMANCE_PATCH_SHA256,
    output_patch_sha256=OUTPUT_PATCH_SHA256,
)

def candidate_expectation(performance_candidate=False):
    if type(performance_candidate) is not bool:
        raise DistributionError('shared MTP2 performance candidate must be an exact boolean')
    return BOUNDED_OUTPUT_EXPECTATION if performance_candidate else FROZEN_SOURCE_EXPECTATION

def _expectation(value):
    if type(value) is not RecipeExpectation:
        raise DistributionError('shared MTP2 requires a separate frozen code-side recipe expectation')
    return value


def select_shared_recipe(lock, expected):
    """A finite additive selector, without changing either existing selector."""
    expected = _expectation(expected)
    select_lily_recipe(lock, OPT64_ENGINE)  # Verify the accepted four-patch lineage.
    pin = lock.get(expected.pin_key)
    if not _same(pin, expected.pin()):
        raise DistributionError('shared MTP2 recipe differs from the frozen code-side inputs')
    return pin, expected.baseline_path


def _baseline(package, expected):
    value = read_object(verified_file(package, {'path': expected.baseline_path, 'sha256': expected.baseline_sha256}))
    if (set(value) != {'schema_version', 'source_commit', 'files'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['source_commit'] != LATEST_COMMIT
            or not isinstance(value['files'], list) or len(value['files']) != expected.source_file_count):
        raise DistributionError('shared MTP2 complete source baseline differs')
    rows = {}
    for row in value['files']:
        if (not isinstance(row, dict) or set(row) != {'path', 'sha256'}
                or not isinstance(row['path'], str) or not isinstance(row['sha256'], str)
                or HEX64.fullmatch(row['sha256']) is None):
            raise DistributionError('shared MTP2 source hash record is malformed')
        relative_path(row['path'])
        if row['path'] in rows or any(p in {'.git', 'target', '__pycache__'} for p in Path(row['path']).parts):
            raise DistributionError('shared MTP2 source baseline contains duplicate or generated paths')
        rows[row['path']] = row['sha256']
    if _digest(rows) != expected.source_inventory_sha256:
        raise DistributionError('shared MTP2 source inventory is not the frozen complete inventory')
    return rows


def verify_source_inventory(package, target, expected):
    """Hash all extracted source files; no ignored or unlisted bytes are allowed."""
    expected = _expectation(expected)
    package, target = no_symlinks(package), no_symlinks(target)
    rows = _baseline(package, expected)
    if not target.is_dir():
        raise DistributionError('shared MTP2 extracted source root is missing')
    actual = set()
    for file in target.rglob('*'):
        file = no_symlinks(file)
        mode = file.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise DistributionError('shared MTP2 source contains a special file')
        actual.add(file.relative_to(target).as_posix())
        if len(actual) > expected.source_file_count:
            raise DistributionError('shared MTP2 source contains unlisted files')
    if actual != set(rows):
        raise DistributionError('shared MTP2 complete extracted source inventory differs')
    for path, digest in rows.items():
        verified_file(target, {'path': path, 'sha256': digest})
    return rows


def replay_shared_source(package, target, lock, expected):
    """CPU-only replay for the normal builder's fresh extracted scratch tree.

    The builder must separately verify the fixed archive before extraction.
    This function is not a compiler and its return value is not a build receipt.
    """
    pin, _ = select_shared_recipe(lock, expected)
    package, target = no_symlinks(package), no_symlinks(target)
    rows = _baseline(package, expected)
    planned = [verified_file(package, item) for item in expected.patches]
    for file in planned:
        for path in patch_paths(file, 1):
            relative_path(path)
            if path not in rows:
                raise DistributionError('shared MTP2 patch escapes its frozen source inventory')
            no_symlinks(target / path)
    # This first replay independently proves the accepted 235-file OPT64 base.
    apply_lily_source_patches(package, target, lock, OPT64_ENGINE)
    for patch in planned[len(PATCHES):]:
        for check in (True, False):
            result = subprocess.run(['git', 'apply', '-p1'] + (['--check'] if check else []) + [str(patch)],
                                    cwd=target, env=git_environment(target), capture_output=True)
            if result.returncode:
                raise DistributionError('shared MTP2 canonical source replay failed')
    verify_source_inventory(package, target, expected)
    return {'engine_id': ENGINE_ID, 'source_commit': pin['commit'], 'archive_sha256': pin['archive_sha256'],
            'patch_sha256': [row['sha256'] for row in expected.patches],
            'source_inventory_sha256': expected.source_inventory_sha256,
            'source_file_count': expected.source_file_count, 'replay': 'PASS',
            'native_build': False, 'runtime_acceptance': False}


@dataclass(frozen=True, slots=True)
class SharedEngine:
    source_inventory_sha256: str
    binary_sha256: str
    patch_sha256: tuple[str, ...]
    source_file_count: int


def from_generated_trust(record, expected):
    """Validate normal builder output; do not create or infer such output."""
    expected = _expectation(expected)
    fixed = {'engine_id': ENGINE_ID, 'source_commit': LATEST_COMMIT, 'archive_sha256': ARCHIVE_SHA256,
             'patch_sha256': [row['sha256'] for row in expected.patches],
             'source_inventory_sha256': expected.source_inventory_sha256,
             'source_file_count': expected.source_file_count, 'default': False}
    if (not isinstance(record, dict) or set(record) != set(fixed) | {'binary_sha256'}
            or any(not _same(record[k], v) for k, v in fixed.items())
            or not isinstance(record['binary_sha256'], str) or HEX64.fullmatch(record['binary_sha256']) is None):
        raise DistributionError('shared MTP2 generated engine identity differs')
    return SharedEngine(expected.source_inventory_sha256, record['binary_sha256'],
                        tuple(record['patch_sha256']), expected.source_file_count)


def runtime_policy():
    return {'resident_context_tokens': RESIDENT_CONTEXT_TOKENS, 'kv_cache': 'bf16', 'mtp_drafts': 2,
            'qsa_route': 'split', 'qsa_scores': 'scalar', 'max_batch': ACTIVE_LIMIT,
            'cache_bytes': CACHE_BYTES, 'max_sessions': MAX_SESSIONS, 'disk_cache_bytes': 0,
            'pending_limit': PENDING_LIMIT, 'default_output_tokens': MAX_OUTPUT_TOKENS,
            'max_output_tokens': MAX_OUTPUT_TOKENS, 'thinking': 'checkpoint default high',
            'pin_weights': 'off', 'ngram_preload': True, 'ngram_lock': 'off',
            'thinking_budget': 'off', 'thinking_nudges': False, 'tool_call_ends_thinking': False}


@dataclass(frozen=True, slots=True)
class SharedInstallation:
    """Internal result of additive trust validation; not a public input type."""
    engine: SharedEngine
    binary: Path
    model: Path
    data_root: Path
    enabled_profile_ids: tuple[str, ...]
    control_ports: tuple[int, ...]
    validation_bundle_id: str | None = None


def validate_shared_extension(trusted, expected=None):
    """Read an optional extension after existing trust.validate(require_checkpoint=True).

    Keep the old top-level executable inventory and both original engine paths.
    New helper/runner/transport/recipe bytes are hash-bound in source_files; the
    shared executable is bound in this separate extension, never arbitrary argv.
    This prototype deliberately cannot enable an installation without the future
    reviewed runner and transport source files. No private review file is read.
    """
    extension = trusted['trust'].get('mtp2_shared_runtime')
    if extension is None:
        return None
    expected = _expectation(expected)
    if (not isinstance(extension, dict) or set(extension) != {
            'schema_version', 'engine', 'binary', 'enabled_profiles', 'runtime_policy'}
            or type(extension['schema_version']) is not int or extension['schema_version'] != 1
            or not _same(extension['runtime_policy'], runtime_policy())):
        raise DistributionError('shared MTP2 installation policy differs')
    enabled = extension['enabled_profiles']
    if (not isinstance(enabled, list) or any(not isinstance(pid, str) for pid in enabled)
            or not enabled or enabled != [pid for pid in PROFILE_IDS if pid in enabled]):
        raise DistributionError('shared MTP2 installation profile selection differs')
    registry = trusted['registry']
    if not isinstance(registry, ProfileRegistry) or registry.default.profile_id != 'ctx64k':
        raise DistributionError('shared MTP2 cannot replace the MTP0 default')
    for pid in enabled:
        profile = registry.by_id(pid)
        alias, context = next((row[1], row[2]) for row in OPT_TARGETS if row[0] == pid)
        if (profile.engine_id != OPT64_ENGINE or profile.public_alias != alias
                or type(profile.context_tokens) is not int or profile.context_tokens != context
                or profile.default_output_tokens != MAX_OUTPUT_TOKENS):
            raise DistributionError('shared MTP2 logical registry binding differs')
    if (trusted.get('model_configured') is not True or not isinstance(trusted.get('checkpoint'), dict)
            or trusted['checkpoint'].get('payload_verified') is not True
            or trusted['checkpoint'].get('revision') != SHARED['model_revision']):
        raise DistributionError('shared MTP2 requires the same fully verified Q4 checkpoint')
    package, data = no_symlinks(trusted['source_root']), no_symlinks(trusted['data_root'])
    lock = read_object(package / 'manifests/distribution/native-sources.json')
    select_shared_recipe(lock, expected)
    required = BOUNDARY_SOURCES | {expected.baseline_path} | {row['path'] for row in expected.patches}
    if expected.output_patch_sha256 is not None:
        # Final normal serving requires every generator/LP/guard boundary,
        # including gateway/journal/portable code, rather than prototype inputs.
        from mtp2_shared_recipe import serving_inputs
        required = required | serving_inputs(True)
    source_rows = trusted['trust']['source_files']
    if not isinstance(source_rows, list) or any(not isinstance(row, dict) for row in source_rows):
        raise DistributionError('shared MTP2 boundary source inventory is malformed')
    paths = [row.get('path') for row in source_rows]
    if any(not isinstance(path, str) for path in paths) or len(paths) != len(set(paths)):
        raise DistributionError('shared MTP2 boundary source records are duplicated or malformed')
    records = dict(zip(paths, source_rows))
    if not required.issubset(records):
        raise DistributionError('shared MTP2 release trust omits a serving or recipe boundary')
    for path in required:
        verified_file(package, records[path])
    for row in expected.patches:
        if records[row['path']].get('sha256') != row['sha256']:
            raise DistributionError('shared MTP2 source trust patch differs from its recipe')
    _baseline(package, expected)
    engine = from_generated_trust(extension['engine'], expected)
    if not _same(extension['binary'], {'path': BINARY_PATH, 'sha256': engine.binary_sha256}):
        raise DistributionError('shared MTP2 executable path or hash differs')
    binary = verified_file(data, extension['binary'])
    if not os.access(binary, os.X_OK):
        raise DistributionError('shared MTP2 executable is not executable')
    model = no_symlinks(Path(trusted['config']['model_dir']))
    if not model.is_dir():
        raise DistributionError('shared MTP2 verified checkpoint path is unavailable')
    return SharedInstallation(engine, binary, model, data, tuple(enabled),
                              tuple(trusted['config']['ports'].values()))


def normal_shared_view(trusted):
    """Select only the generated installed extension after normal base validation.

    Legacy opt-in authorization remains in its original fields. This temporary
    view is derived from the separately verified extension, never a validation
    pointer, environment recipe, build-only receipt or unchecked profile flag.
    """
    from mtp2_shared_runner import RELEASE_EXPECTATION
    expected = _expectation(RELEASE_EXPECTATION)
    if expected != BOUNDED_OUTPUT_EXPECTATION:
        raise DistributionError('shared MTP2 normal release recipe is unavailable')
    if '_mtp2_validation_binding' in trusted or '_mtp2_config_binding' in trusted:
        raise DistributionError('validation bindings cannot select the normal shared extension')
    extension = trusted['trust'].get('mtp2_shared_runtime')
    if not isinstance(extension, dict) or not _same(extension.get('enabled_profiles'), list(PROFILE_IDS)):
        raise DistributionError('shared MTP2 normal installation requires its three finite profiles')
    from profile_registry import parse_registry, REGISTRY_PATH
    registry_record = trusted['trust'].get('profile_registry')
    if not isinstance(registry_record, dict) or registry_record.get('path') != REGISTRY_PATH:
        raise DistributionError('shared MTP2 normal registry record is unavailable')
    source = no_symlinks(trusted['source_root'])
    registry = parse_registry(read_object(verified_file(source, registry_record)),
                              enabled_optin_profiles=list(PROFILE_IDS))
    if (not isinstance(trusted['registry'], ProfileRegistry)
            or registry.default != trusted['registry'].default):
        raise DistributionError('shared MTP2 cannot replace the original MTP0 default')
    before = tuple(p for p in trusted['registry'].profiles if p.profile_id not in PROFILE_IDS)
    after = tuple(p for p in registry.profiles if p.profile_id not in PROFILE_IDS)
    if before != after:
        raise DistributionError('shared MTP2 cannot replace original MTP0 profiles')
    selected = dict(trusted)
    selected['registry'] = registry
    selected['profiles'] = registry.enabled_profiles
    installation = validate_shared_extension(selected, expected)
    if installation is None:
        raise DistributionError('shared MTP2 normal extension is not installed')
    return selected


def shared_argv(installation, port):
    """Closed internal launch plan: no profile-dependent physical parameters."""
    if type(installation) is not SharedInstallation or type(installation.engine) is not SharedEngine:
        raise DistributionError('shared MTP2 runner requires validated additive installation trust')
    if type(port) is not int or not 1024 <= port <= 65535 or port in installation.control_ports:
        raise DistributionError('shared MTP2 backend port conflicts with its trusted control plane')
    binary, model, data = (no_symlinks(path) for path in (
        installation.binary, installation.model, installation.data_root))
    expected_binary = shared_binary_path(data, installation.validation_bundle_id)
    if (binary != expected_binary or sha256(binary) != installation.engine.binary_sha256
            or not os.access(binary, os.X_OK) or not model.is_dir()):
        raise DistributionError('shared MTP2 verified launch inputs changed')
    cache = no_symlinks(data / 'cache' / CACHE_NAMESPACE)
    return [str(binary), '--model', str(model), '--bind', '127.0.0.1:' + str(port),
            '--liliuxflow-mtp2-shared-context', '--max-seq', str(RESIDENT_CONTEXT_TOKENS),
            '--mtp-drafts', '2', '--max-batch', '2', '--kv-cache', 'bf16',
            '--cache-bytes', str(CACHE_BYTES), '--max-sessions', str(MAX_SESSIONS),
            '--disk-cache-dir', str(cache), '--disk-cache-bytes', '0',
            '--ngram-table', 'paged', '--ngram-preload', 'true', '--pin-weights', 'off',
            '--pin-hold', '1m', '--thinking', 'true', '--thinking-budget', 'off',
            '--thinking-nudges', 'false', '--tool-call-ends-thinking', 'false',
            '--idle-unload', '0', '--queue', str(PENDING_LIMIT)]


def shared_binary_path(data, validation_bundle_id=None):
    """None is the unchanged production path; a bundle is a finite owned root."""
    data = no_symlinks(data)
    if validation_bundle_id is None:
        return no_symlinks(data / BINARY_PATH)
    if (type(validation_bundle_id) is not str
            or re.fullmatch('[0-9a-f]{32}', validation_bundle_id) is None):
        raise DistributionError('shared validation bundle identity is malformed')
    return no_symlinks(data / 'runtime/mtp2-shared-bundles' / validation_bundle_id / BINARY_PATH)


def shared_env(base):
    return effective_env(base)  # Reuse the established closed nonsecret environment.


def global_gpu_lease():
    """The established physical lease path; this helper does not create it."""
    return no_symlinks(Path.home() / 'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease')


def runtime_identity_fields(engine):
    if type(engine) is not SharedEngine:
        raise DistributionError('shared MTP2 physical identity requires a generated engine')
    return {'engine_id': ENGINE_ID, 'engine_source_commit': LATEST_COMMIT,
            'engine_source_inventory_sha256': engine.source_inventory_sha256,
            'resident_context_tokens': RESIDENT_CONTEXT_TOKENS}


# Bounded scalar grammar for private evidence. None of these text records is an
# authenticated release or request-ID transport; no NativeEvent is manufactured.
_ID = r'(?P<id>req-[0-9]{1,20}-[0-9]{1,20})'
_N = r'[0-9]{1,20}'
_EPOCH = r'[0-9a-f]{64}'
_CLOCK = 'mach_absolute_time_ns'
_ENGINE = re.compile(r'LILY_ENGINE_EFFECTIVE context_tokens=(?P<context>' + _N + r') kv_cache=(?P<kv>bf16|q8) mtp_drafts=(?P<drafts>' + _N + r') max_batch=(?P<batch>' + _N + ')')
_ROUTE = re.compile(r'LILY_QSA_ROUTE_EFFECTIVE requested=(?P<requested>unset|query|split|invalid) route=(?P<route>query|split)')
_RESIDENT = re.compile(r'LILY_MTP2_RESIDENT protocol=1 resident_context_tokens=262144 kv_cache=bf16 mtp_drafts=2 max_batch=2 weight_load_generation=(?P<generation>' + _N + r') weight_load_count=(?P<load_count>' + _N + r') weight_allocation_id=(?P<epoch>' + _EPOCH + r') weight_buffer_count=(?P<buffers>' + _N + r') weight_allocation_bytes=(?P<bytes>' + _N + r') paged_weight_bytes=(?P<paged>' + _N + ')')
_PREFIX = r'\[lily-meta\] request_id=' + _ID + ' phase='
_ENTER = re.compile(_PREFIX + 'queue_enter')
_ACQUIRE = re.compile(_PREFIX + r'lane_acquired queue_wait_ms=' + _N)
_POLICY = re.compile(_PREFIX + r'mtp2_policy protocol=1 clock=' + _CLOCK + r' admitted_ns=(?P<ns>' + _N + r') profile_id=(?P<profile>ctx64k-mtp2|ctx128k-mtp2|ctx262k-mtp2) context_tokens=(?P<context>' + _N + r') prompt_tokens=(?P<prompt>' + _N + r') output_tokens=(?P<output>' + _N + r') resident_context_tokens=262144 weight_load_generation=(?P<generation>' + _N + r') weight_load_count=(?P<load_count>' + _N + r') weight_allocation_id=(?P<epoch>' + _EPOCH + ') mtp_drafts=2')
_PROGRESS = re.compile(_PREFIX + r'prefill_progress completed_chunks=(?P<chunks>' + _N + r') prefilled_tokens=(?P<tokens>' + _N + ')')
_LANE_RELEASE = re.compile(_PREFIX + r'lane_released cancelled=(?P<cancelled>true|false) lane_ms=' + _N)
_RELEASE = re.compile(_PREFIX + r'mtp2_released clock=' + _CLOCK + r' released_ns=(?P<ns>' + _N + r') resident_epoch=(?P<epoch>' + _EPOCH + r') cancelled=(?P<cancelled>true|false)')
_QUEUE_TERMINAL = re.compile(_PREFIX + r'(?:queue_rejected status=(?:500|503) queue_slot_released=true|queue_cancelled queue_wait_ms=' + _N + r' queue_slot_released=true|cancelled_after_engine_ready queue_wait_ms=' + _N + r' model_load_aborted=false)')


class NativeEvidence:
    """Bounded no-reload/request-release observations, never a live resource probe.

    Feed only the fixed metadata subset above. Complete step/state/overlap
    analysis remains in the private collector. Unknown critical input fails
    closed; arbitrary stderr text is neither retained nor returned.
    """
    def __init__(self, *, request_limit=4096):
        if type(request_limit) is not int or not ACTIVE_LIMIT + PENDING_LIMIT <= request_limit <= 4096:
            raise DistributionError('shared MTP2 evidence request inventory is out of bounds')
        self.request_limit = request_limit
        self.proof_valid = True
        self.engine = self.route = self.resident = None
        self.pending, self.active, self.requests = set(), set(), {}
        self.acquired = self.released = self.peak_active = 0
        self.closed = False

    def _fixed(self, key, value):
        previous = getattr(self, key)
        if previous is not None and previous != value:
            raise DistributionError('shared MTP2 native resident identity changed')
        setattr(self, key, value)

    def _request(self, request_id):
        if request_id not in self.requests:
            raise DistributionError('shared MTP2 native event has no queued request')
        return self.requests[request_id]

    def consume(self, line):
        if (not isinstance(line, str) or len(line) > 8192 or '\n' in line or '\r' in line or self.closed):
            self.proof_valid = False
            return False
        patterns = (_ENGINE, _ROUTE, _RESIDENT, _ENTER, _ACQUIRE, _POLICY,
                    _PROGRESS, _LANE_RELEASE, _RELEASE, _QUEUE_TERMINAL)
        found = next(((p, m) for p in patterns if (m := p.fullmatch(line))), None)
        if found is None:
            if line.startswith(('LILY_ENGINE_', 'LILY_QSA_', 'LILY_MTP2_', '[lily-meta]')):
                self.proof_valid = False
            return False
        if not self.proof_valid:
            return False
        pattern, match = found
        values = match.groupdict()
        try:
            if pattern is _ENGINE:
                actual = {'context_tokens': int(values['context']), 'kv_cache': values['kv'],
                          'mtp_drafts': int(values['drafts']), 'max_batch': int(values['batch'])}
                if actual != {'context_tokens': RESIDENT_CONTEXT_TOKENS, 'kv_cache': 'bf16', 'mtp_drafts': 2, 'max_batch': 2}:
                    raise DistributionError('shared MTP2 native physical policy differs')
                self._fixed('engine', actual)
            elif pattern is _ROUTE:
                if values != {'requested': 'split', 'route': 'split'}:
                    raise DistributionError('shared MTP2 native route differs')
                self._fixed('route', values)
            elif pattern is _RESIDENT:
                value = {k: int(v) if k != 'epoch' else v for k, v in values.items()}
                if (not 0 < value['load_count'] <= value['generation'] < 2**64
                        or not 0 < value['buffers'] <= 1000000
                        or not 0 < value['bytes'] < 2**64 or not 0 <= value['paged'] < 2**64):
                    raise DistributionError('shared MTP2 native weight allocation is unknown')
                self._fixed('resident', value)
            elif pattern is _ENTER:
                request_id = values['id']
                if (request_id in self.requests or len(self.pending) >= PENDING_LIMIT
                        or len(self.requests) >= self.request_limit):
                    raise DistributionError('shared MTP2 evidence queue or replay bound exceeded')
                self.requests[request_id] = {'policy': None, 'acquired': False, 'lane_released': False,
                                            'released': False, 'cancelled': None, 'progress': None}
                self.pending.add(request_id)
            else:
                request_id, row = values['id'], self._request(values['id'])
                if pattern is _ACQUIRE:
                    if request_id not in self.pending or len(self.active) >= ACTIVE_LIMIT or row['acquired']:
                        raise DistributionError('shared MTP2 native acquire is not request-correlated')
                    self.pending.remove(request_id)
                    self.active.add(request_id)
                    row['acquired'] = True
                    self.acquired += 1
                    self.peak_active = max(self.peak_active, len(self.active))
                elif pattern is _QUEUE_TERMINAL:
                    if request_id not in self.pending or row['acquired']:
                        raise DistributionError('shared MTP2 queue terminal is not request-correlated')
                    self.pending.remove(request_id)
                    row['queue_terminal'] = True
                elif request_id not in self.active or row['released']:
                    raise DistributionError('shared MTP2 native event lacks its active request')
                elif pattern is _POLICY:
                    context, prompt, output = (int(values[k]) for k in ('context', 'prompt', 'output'))
                    if (row['policy'] is not None or self.resident is None
                            or context != CONTEXTS[values['profile']] or not 0 < prompt < context
                            or not 0 < output <= MAX_OUTPUT_TOKENS or prompt + output > context
                            or not 0 < int(values['ns']) < 2**64
                            or any(self.resident[k] != (values[k] if k == 'epoch' else int(values[k]))
                                   for k in ('epoch', 'generation', 'load_count'))):
                        raise DistributionError('shared MTP2 native request policy or residency differs')
                    row['policy'] = {'profile_id': values['profile'], 'context_tokens': context,
                                     'prompt_tokens': prompt, 'output_tokens': output,
                                     'admitted_ns': int(values['ns'])}
                elif pattern is _PROGRESS:
                    chunks, tokens = int(values['chunks']), int(values['tokens'])
                    if (row['policy'] is None or row['progress'] is not None or row['lane_released']
                            or not 0 < chunks <= tokens <= row['policy']['prompt_tokens']):
                        raise DistributionError('shared MTP2 native progress differs')
                    row['progress'] = {'completed_chunks': chunks, 'prefilled_tokens': tokens}
                elif pattern is _LANE_RELEASE:
                    if row['lane_released'] or row['policy'] is None:
                        raise DistributionError('shared MTP2 lane release lacks its policy or is duplicated')
                    row['lane_released'] = True
                    row['cancelled'] = values['cancelled'] == 'true'
                elif pattern is _RELEASE:
                    if (not row['lane_released'] or self.resident is None
                            or values['epoch'] != self.resident['epoch']
                            or row['cancelled'] != (values['cancelled'] == 'true')
                            or not row['policy']['admitted_ns'] <= int(values['ns']) < 2**64):
                        raise DistributionError('shared MTP2 native release epoch, time or request differs')
                    row['released'] = True
                    self.active.remove(request_id)
                    self.released += 1
        except (DistributionError, ValueError, KeyError, TypeError):
            self.proof_valid = False
            return False
        return True

    def release_observed(self, request_id):
        """Private evidence predicate, explicitly insufficient for live cleanup."""
        row = self.requests.get(request_id)
        return (self.proof_valid and self.engine is not None and self.route is not None
                and self.resident is not None and row is not None and row['released'])

    def require_live_release(self, request_id):
        raise DistributionError('shared MTP2 native internal release transport is not integrated')

    def snapshot(self):
        return {'schema_version': 1, 'scope': 'private_scalar_evidence_only',
                'proof_valid': self.proof_valid, 'native_engine_effective': copy.deepcopy(self.engine),
                'qsa_route_effective': copy.deepcopy(self.route), 'weight_residency': copy.deepcopy(self.resident),
                'acquired_sequence': self.acquired, 'released_sequence': self.released,
                'active_request_ids': sorted(self.active), 'queued_request_ids': sorted(self.pending),
                'observed_request_count': len(self.requests), 'peak_active': self.peak_active,
                'live_release_authorized': False, 'live_event_transport': 'NOT_IMPLEMENTED'}

    def channel_closed(self):
        self.closed = True
        self.proof_valid = False  # EOF does not fabricate releases or zero-active.
