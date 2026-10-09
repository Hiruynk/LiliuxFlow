# SPDX-License-Identifier: Apache-2.0
"""Finite additive policy for explicit access to long context profiles.

LiteLLM remains the authentication, expiry, budget and usage authority. Its
empty/wildcard model grants historically cover every configured deployment;
this additional profile policy preserves their existing 64K scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat

LONG_PROFILE_MODELS = frozenset((
    "qwen3.8-flash-next-lily-q4-128k",
    "qwen3.8-flash-next-lily-q4-262k",
))
DEFAULT_MODEL = "qwen3.8-flash-next-lily-q4-64k"
OPTIN_MODEL = 'qwen3.8-flash-next-lily-q4-mtp2-64k'
OPTIN_MODELS=frozenset('qwen3.8-flash-next-lily-q4-mtp2-'+size for size in ('64k','128k','262k'))
EXPLICIT_PROFILE_MODELS = LONG_PROFILE_MODELS | OPTIN_MODELS
CANONICAL_MODELS = EXPLICIT_PROFILE_MODELS | {DEFAULT_MODEL}


@dataclass(frozen=True)
class ProfileAccessPolicy:
    enabled_models: frozenset[str]
    validation_keys: tuple[tuple[str, frozenset[str]], ...] = ()


def parse_policy(document):
    if not isinstance(document, dict) or set(document) != {"schema_version", "enabled_models", "validation_keys"} or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("context profile access policy schema differs")
    enabled = document["enabled_models"]
    if not isinstance(enabled, list) or any(not isinstance(model, str) or model not in CANONICAL_MODELS for model in enabled) or len(set(enabled)) != len(enabled) or enabled and DEFAULT_MODEL not in enabled:
        raise ValueError("context profile access enablement differs")
    validation = document["validation_keys"]
    if not isinstance(validation, dict) or any(model not in EXPLICIT_PROFILE_MODELS for model in validation) or not enabled and validation:
        raise ValueError("context profile validation models differ")
    rows = []
    for model, fingerprints in validation.items():
        if not isinstance(fingerprints, list) or not 1 <= len(fingerprints) <= 4 or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in fingerprints):
            raise ValueError("context profile validation key hashes differ")
        rows.append((model, frozenset(fingerprints)))
    return ProfileAccessPolicy(frozenset(enabled), tuple(sorted(rows)))


@lru_cache(maxsize=1)
def load_policy():
    value = os.environ.get("LILIUXFLOW_PROFILE_POLICY_PATH")
    digest = os.environ.get("LILIUXFLOW_PROFILE_POLICY_SHA256")
    if value is None and digest is None:
        return ProfileAccessPolicy(frozenset((DEFAULT_MODEL,)))
    if not value or not digest or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("context profile access policy trust is incomplete")
    path = Path(value)
    if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("context profile access policy path is unsafe")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 16 * 1024:
        raise ValueError("context profile access policy must be a bounded private owned file")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("context profile access policy digest differs")
    return parse_policy(json.loads(content))


def caller_fingerprint(authenticated_key):
    if not isinstance(authenticated_key, str):
        return None
    if re.fullmatch(r"[a-f0-9]{64}", authenticated_key):
        return authenticated_key
    if authenticated_key.startswith("sk-"):
        return hashlib.sha256(authenticated_key.encode()).hexdigest()
    return None


def explicit_profile_access(requested_model, key_models, *, key_fingerprint=None, policy=None) -> bool:
    policy = policy or load_policy()
    if not policy.enabled_models:
        return False
    if requested_model not in EXPLICIT_PROFILE_MODELS:
        return True
    if not isinstance(key_models, (list, tuple)) or requested_model not in key_models:
        return False
    if requested_model in policy.enabled_models:
        return True
    return any(model == requested_model and key_fingerprint in hashes for model, hashes in policy.validation_keys)


def filter_profile_catalog(model_names, user_api_key_dict):
    fingerprint = caller_fingerprint(user_api_key_dict.api_key)
    return [model for model in model_names if explicit_profile_access(model, user_api_key_dict.models, key_fingerprint=fingerprint)]


def create_callback():
    from fastapi import HTTPException
    from litellm.integrations.custom_logger import CustomLogger

    class ContextProfilePolicy(CustomLogger):
        async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
            if not explicit_profile_access(data.get("model"), user_api_key_dict.models,
                                           key_fingerprint=caller_fingerprint(user_api_key_dict.api_key)):
                raise HTTPException(status_code=403, detail="This context profile requires an explicit model grant for this virtual key")
            return None

    return ContextProfilePolicy()


# The official callback loader imports this instance only in the LiteLLM runtime.
try:
    import litellm  # noqa: F401
except ImportError:
    context_profile_policy = None
else:
    context_profile_policy = create_callback()
