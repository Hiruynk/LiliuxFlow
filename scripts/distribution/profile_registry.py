"""Finite, immutable runtime profiles for one trusted Q4 checkpoint.

SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from common import DistributionError, no_symlinks


REGISTRY_PATH = 'profiles/distribution/context-registry.json'
DEFAULT_ALIAS = 'qwen3.8-flash-next-lily-q4-64k'
MAX_OUTPUT_TOKENS = 65536
# Pinned db3f8a7c supports this process flag; latest13f removed it.
OPENAI_MAX_IMAGES = 64
TARGETS = (
    ('ctx64k', DEFAULT_ALIAS, 65536, 3672, 300, 4, 32 * 1024**3),
    ('ctx128k', 'qwen3.8-flash-next-lily-q4-128k', 131072, 4272, 600, 2, 10 * 1024**3),
    ('ctx262k', 'qwen3.8-flash-next-lily-q4-262k', 262144, 4872, 900, 1, 10 * 1024**3),
)
LEGACY_ENGINE = 'legacy-db3-mtp0'
OPT64_ENGINE = 'latest13f-defer-pc123-mtp2-opt64k'
OPT64_PROFILE = 'ctx64k-mtp2'
OPT64_ALIAS = 'qwen3.8-flash-next-lily-q4-mtp2-64k'
MINIMUM_RAM_HEADROOM_GIB = {'ctx64k-mtp2': 15, 'ctx64k': 15, 'ctx128k': 15, 'ctx262k': 13}
SHARED = {
    'runtime_model_id': 'Qwen3.8-Flash-Next',
    'model_repository': 'fabiogreter/Qwen3.8-Flash-Next-lily-q4',
    'model_revision': 'afde8b8e824c57bfe264c2ef3f9396537249979f',
    'model_format': 'qwen4_exp-affine-v1',
    'thinking': 'checkpoint default high', 'qsa_route': 'split', 'mtp_drafts': 0,
    'pin_weights': 'off', 'ngram_lock': 'off',
    'minimum_ram_headroom_gib': 15, 'target_ram_headroom_gib': 24,
}


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    profile_id: str
    public_alias: str
    runtime_model_id: str
    context_tokens: int
    default_output_tokens: int
    total_deadline_seconds: int
    queue_wait_seconds: int
    idle_ttl_seconds: int
    cache_bytes: int
    max_sessions: int
    disk_cache_bytes: int
    cache_namespace: str
    production_enabled: bool
    validation_only: bool
    minimum_ram_headroom_gib: int = 15
    engine_id: str = LEGACY_ENGINE

    def as_dict(self):
        # Returning a detached value cannot mutate a running request's profile.
        value = {**SHARED, **asdict(self)}
        if self.engine_id == OPT64_ENGINE:
            value.update(mtp_drafts=2, kv_cache='bf16', max_batch=1, qsa_scores='scalar',
                         ngram_preload=True, thinking_budget='off', thinking_nudges=False, tool_call_ends_thinking=False)
        return value


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    profiles: tuple[RuntimeProfile, ...]
    default_profile_id: str = 'ctx64k'
    pending_limit: int = 4
    maximum_body_bytes: int = 8 * 1024**2
    legacy: bool = False
    total_disk_cache_cap_bytes: int = 64 * 1024**3
    temporary_cache_reserve_bytes: int = 12 * 1024**3
    maximum_added_cache_bytes: int = 32 * 1024**3
    minimum_free_disk_bytes: int = 64 * 1024**3

    @property
    def maximum_images(self):
        return OPENAI_MAX_IMAGES

    @property
    def default(self):
        return next(p for p in self.profiles if p.profile_id == self.default_profile_id)

    @property
    def enabled_profiles(self):
        return tuple(p for p in self.profiles if p.production_enabled)

    def _available(self, profile, allow_validation):
        if type(allow_validation) is not bool:
            raise DistributionError('validation admission must be an explicit trusted boolean')
        if not profile.production_enabled and not (allow_validation and profile.validation_only):
            raise DistributionError('requested context profile is disabled')
        return profile

    def resolve(self, public_alias, *, allow_validation=False):
        for profile in self.profiles:
            if public_alias == profile.public_alias:
                return self._available(profile, allow_validation)
        raise DistributionError('requested model is outside the context registry')

    def by_id(self, profile_id, *, allow_validation=False):
        for profile in self.profiles:
            if profile_id == profile.profile_id:
                return self._available(profile, allow_validation)
        raise DistributionError('requested profile ID is outside the context registry')


def _same(actual, expected):
    return type(actual) is type(expected) and actual == expected


def parse_registry(document, *, enabled_optin_profiles=()):
    expected_keys = {'schema_version', 'default_profile_id', 'pending_limit', 'maximum_body_bytes',
                     'total_disk_cache_cap_bytes', 'temporary_cache_reserve_bytes', 'maximum_added_cache_bytes',
                     'minimum_free_disk_bytes', 'shared', 'profiles'}
    if not isinstance(document, dict) or set(document) not in (expected_keys, expected_keys | {'optin_profiles'}):
        raise DistributionError('context registry schema fields differ')
    fixed = {'schema_version': 1, 'default_profile_id': 'ctx64k', 'pending_limit': 4,
             'maximum_body_bytes': 8 * 1024**2, 'total_disk_cache_cap_bytes': 64 * 1024**3,
             'temporary_cache_reserve_bytes': 12 * 1024**3,
             'maximum_added_cache_bytes': 32 * 1024**3, 'minimum_free_disk_bytes': 64 * 1024**3}
    if any(not _same(document.get(k), v) for k, v in fixed.items()):
        raise DistributionError('context registry bounds differ')
    shared = document['shared']
    if not isinstance(shared, dict) or set(shared) != set(SHARED) or any(not _same(shared[k], v) for k, v in SHARED.items()):
        raise DistributionError('context registry checkpoint or runtime policy differs')
    rows = document['profiles']
    if not isinstance(rows, list) or len(rows) != len(TARGETS):
        raise DistributionError('context registry must contain exactly three canonical profiles')
    profiles = []
    fields = set(RuntimeProfile.__dataclass_fields__) - {'runtime_model_id', 'engine_id'}
    for row, (pid, alias, context, deadline, wait, sessions, disk) in zip(rows, TARGETS):
        if not isinstance(row, dict) or set(row) != fields:
            raise DistributionError('context profile fields differ')
        fixed_profile = {'profile_id': pid, 'public_alias': alias, 'context_tokens': context,
                         'default_output_tokens': MAX_OUTPUT_TOKENS, 'total_deadline_seconds': deadline,
                         'queue_wait_seconds': wait, 'idle_ttl_seconds': 1800,
                         'max_sessions': sessions,
                         'minimum_ram_headroom_gib': MINIMUM_RAM_HEADROOM_GIB[pid],
                         'cache_namespace': 'lily-safe64k' if pid == 'ctx64k' else
                         'lily-q4-afde8b8e-db3f8a7c-split-mtp0-' + pid}
        if any(not _same(row.get(k), v) for k, v in fixed_profile.items()):
            raise DistributionError('context profile is outside the finite runtime allowlist')
        if type(row['cache_bytes']) is not int or row['cache_bytes'] not in (
            (8 * 1024**3,) if pid == 'ctx64k' else (8 * 1024**3, 12 * 1024**3)
        ):
            raise DistributionError('context session cache is outside the bounded allowlist')
        disk_choices = (32 * 1024**3,) if pid == 'ctx64k' else tuple(n * 1024**3 for n in (4, 8, 10, 12, 16))
        if type(row['disk_cache_bytes']) is not int or row['disk_cache_bytes'] not in disk_choices:
            raise DistributionError('context disk retention is outside the finite allowlist')
        if any(type(row[k]) is not bool for k in ('production_enabled', 'validation_only')):
            raise DistributionError('context enablement must use boolean values')
        profiles.append(RuntimeProfile(runtime_model_id=SHARED['runtime_model_id'], **row))
    if not profiles[0].production_enabled:
        raise DistributionError('the established default 64K profile must remain enabled')
    if sum(p.disk_cache_bytes for p in profiles) + document['temporary_cache_reserve_bytes'] > document['total_disk_cache_cap_bytes']:
        raise DistributionError('context disk retention does not leave the shared temporary reserve')
    opt_rows = document.get('optin_profiles', [])
    if (not isinstance(enabled_optin_profiles, (tuple, list))
        or list(enabled_optin_profiles) not in ([], [OPT64_PROFILE])
        or not isinstance(opt_rows, list) or len(opt_rows) > 1):
        raise DistributionError('opt-in profile authorization differs')
    if opt_rows:
        row = opt_rows[0]
        expected = {'profile_id':OPT64_PROFILE, 'public_alias':OPT64_ALIAS, 'context_tokens':65536,
                    'default_output_tokens':65536, 'total_deadline_seconds':3672, 'queue_wait_seconds':300,
                    'idle_ttl_seconds':1800, 'cache_bytes':8*1024**3, 'max_sessions':1, 'disk_cache_bytes':0,
                    'cache_namespace':'lily-q4-afde8b8e-13f7b540-pc123-split-scalar-bf16-mtp2-ctx64k',
                    'production_enabled':False, 'validation_only':False, 'minimum_ram_headroom_gib':15,
                    'engine_id':OPT64_ENGINE}
        if not isinstance(row, dict) or set(row)!=set(expected) or any(not _same(row[k],v) for k,v in expected.items()):
            raise DistributionError('opt-in profile differs from the finite 64K contract')
        selected = {**row, 'production_enabled': bool(enabled_optin_profiles)}
        profiles.append(RuntimeProfile(runtime_model_id=SHARED['runtime_model_id'], **selected))
    elif enabled_optin_profiles:
        raise DistributionError('authorized opt-in profile is missing from the trusted registry')
    return ProfileRegistry(tuple(profiles))


def load_registry(source_root):
    path = no_symlinks(Path(source_root) / REGISTRY_PATH)
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as error:
        raise DistributionError('context registry is unavailable or invalid') from error
    return parse_registry(document)


def legacy_registry(profile):
    """Read an established single-profile trust receipt without granting new models."""
    rows = []
    for pid, alias, context, deadline, wait, sessions, disk in TARGETS:
        rows.append(RuntimeProfile(pid, alias, SHARED['runtime_model_id'], context, MAX_OUTPUT_TOKENS, deadline,
                                   wait, 1800, 8 * 1024**3, sessions, disk,
                                   'lily-safe64k' if pid == 'ctx64k' else
                                   'lily-q4-afde8b8e-db3f8a7c-split-mtp0-' + pid,
                                   pid == 'ctx64k', False,
                                   minimum_ram_headroom_gib=MINIMUM_RAM_HEADROOM_GIB[pid]))
    return ProfileRegistry(tuple(rows), legacy=True)
