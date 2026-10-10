"""Staged MTP2-only manager and request policy adapter.

This module is not wired into the installed supervisor. A caller must validate
the installation and the shared native recipe before using its output. MTP0
records and logical LiteLLM routes stay in their established generators.

SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import shlex

from common import DistributionError
from profile_registry import MAX_OUTPUT_TOKENS, OPT64_ENGINE, OPT_TARGETS, ProfileRegistry


PHYSICAL_MODEL_ID = "liliuxflow-internal-mtp2-shared"
PROFILE_FIELD = "liliuxflow_profile"
RESIDENT_CONTEXT_TOKENS = 262144
# G1 is sequential no-reload proof. Two active native requests require G2.
P1_ACTIVE_LIMIT = 1
PROFILE_IDS = tuple(row[0] for row in OPT_TARGETS)
PUBLIC_ALIASES = tuple(row[1] for row in OPT_TARGETS)
CONTEXTS = dict((row[0], row[2]) for row in OPT_TARGETS)
REQUEST_PROFILE_FIELDS = frozenset((
    "num_ctx", "max_seq", "context_tokens", "context_length", "profile_path",
    "model_path", "extra_args", "truncate", "truncation", "max_images",
    "profile_id", "engine_id", "mtp_drafts", "drafts", "kv_cache", "max_batch",
))
ROUTING_CONTAINERS = ("options", "extra_body", "chat_template_kwargs")


def is_private_model(model):
    return model == PHYSICAL_MODEL_ID


def _finite_profiles(registry):
    if not isinstance(registry, ProfileRegistry):
        raise DistributionError("shared MTP2 requires the validated context registry")
    selected = []
    for profile in registry.profiles:
        if profile.profile_id not in PROFILE_IDS:
            continue
        index = PROFILE_IDS.index(profile.profile_id)
        if (profile.engine_id != OPT64_ENGINE
                or profile.public_alias != PUBLIC_ALIASES[index]
                or type(profile.context_tokens) is not int
                or profile.context_tokens != CONTEXTS[profile.profile_id]
                or profile.runtime_model_id != "Qwen3.8-Flash-Next"
                or profile.default_output_tokens != MAX_OUTPUT_TOKENS
                or profile.idle_ttl_seconds != 1800):
            raise DistributionError("shared MTP2 logical profile differs from the finite contract")
        if profile.production_enabled:
            selected.append(profile)
    if (len({p.profile_id for p in selected}) != len(selected)
            or tuple(p.profile_id for p in selected) != tuple(pid for pid in PROFILE_IDS if any(p.profile_id == pid for p in selected))):
        raise DistributionError("shared MTP2 logical profiles are duplicated or unordered")
    return tuple(selected)


def _absolute_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise DistributionError("shared MTP2 runner paths must come from the trusted installation")
    return str(path)


def shared_manager_config(manager, registry, *, runner_python, source_root, data_root, candidate_validation=False):
    """Return a candidate config with one MTP2 row; never write or launch it.

    The arguments are internal installation values, never inference input. The
    shared runner is a separate candidate entry point. Existing manager globals,
    every MTP0/other record, and the input object remain byte-equivalent when
    serialized by their original JSON serializer.
    """
    if type(candidate_validation) is not bool:
        raise DistributionError("shared validation mode must be explicit")
    if not isinstance(manager, dict) or not isinstance(manager.get("models"), dict):
        raise DistributionError("shared MTP2 requires the generated manager model inventory")
    profiles = _finite_profiles(registry)
    result = copy.deepcopy(manager)
    aliases = tuple(p.public_alias for p in profiles)
    existing = set(manager["models"]).intersection(PUBLIC_ALIASES)
    if existing != set(aliases):
        raise DistributionError("shared MTP2 enablement differs from the generated manager records")
    if not profiles:
        if PHYSICAL_MODEL_ID in result["models"]:
            raise DistributionError("disabled shared MTP2 cannot retain a physical manager record")
        return result
    if PHYSICAL_MODEL_ID in result["models"]:
        raise DistributionError("shared MTP2 physical manager ID already exists")
    if any(key in manager for key in ("groups", "matrix", "routing")):
        raise DistributionError("explicit manager routing requires a reviewed MTP2-only remap")
    command = [
        _absolute_path(runner_python),
        str(Path(_absolute_path(source_root)) / "scripts/distribution/mtp2_shared_runner.py"),
        "--data-root", _absolute_path(data_root),
        *(["--candidate-validation"] if candidate_validation else []), "--port",
    ]
    for alias in aliases:
        del result["models"][alias]
    result["models"][PHYSICAL_MODEL_ID] = {
        "cmd": shlex.join(command) + " ${PORT}",
        "name": "Qwen3.8-Flash-Next Lily Q4 · MTP2 shared context",
        "aliases": list(aliases),
        "unlisted": True,
        "useModelName": "Qwen3.8-Flash-Next",
        "proxy": "http://127.0.0.1:${PORT}",
        "checkEndpoint": "/health",
        "ttl": 1800,
        "unloadTimeout": 60,
        "concurrencyLimit": P1_ACTIVE_LIMIT,
        "capabilities": {
            "in": ["text", "image"], "out": ["text"],
            "context": RESIDENT_CONTEXT_TOKENS, "disableAuto": True,
        },
        "filters": {
            "setParamsByID": {
                p.public_alias: {PROFILE_FIELD: p.profile_id} for p in profiles
            },
        },
    }
    return result


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DistributionError("MTP2 inference JSON has duplicate object fields")
        value[key] = item
    return value


def _finite_float(raw):
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("nonfinite MTP2 JSON number")
    return value


def _nonfinite_constant(_):
    raise ValueError("nonfinite MTP2 JSON constant")


def parse_external_mtp2_body(body, *, maximum_body_bytes=8 * 1024**2):
    """Strict raw-body parser for an MTP2 ingress, before JSON normalization.

    Call only on the MTP2-specific branch. Public ingress needs this check before
    its framework/provider parser; a normalized downstream body cannot prove
    that the original request contained no duplicate fields.
    """
    if (type(maximum_body_bytes) is not int or not 0 < maximum_body_bytes <= 8 * 1024**2
            or not isinstance(body, bytes) or len(body) > maximum_body_bytes):
        raise DistributionError("MTP2 inference body exceeds the bounded body limit")
    try:
        value = json.loads(body, object_pairs_hook=_unique_object,
                           parse_float=_finite_float, parse_constant=_nonfinite_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DistributionError("MTP2 inference body must contain finite JSON") from error
    if not isinstance(value, dict):
        raise DistributionError("MTP2 inference body must be a JSON object")
    return value


def external_mtp2_profile(value, registry):
    """Validate logical routing before the manager injects its internal policy.

    Authentication and exact logical-alias ACLs must already have succeeded in
    LiteLLM. This never maps physical IDs into an alias or grants a caller access.
    Native tokenization remains authoritative for the remaining context window.
    """
    if not isinstance(value, dict):
        raise DistributionError("MTP2 inference policy requires a JSON object")
    alias = value.get("model")
    if alias not in PUBLIC_ALIASES:
        raise DistributionError("MTP2 inference requires a public logical alias")
    profiles = _finite_profiles(registry)
    profile = next((p for p in profiles if p.public_alias == alias), None)
    if profile is None:
        raise DistributionError("requested MTP2 logical profile is disabled")
    for container in (value, *(value.get(key) for key in ROUTING_CONTAINERS)):
        if not isinstance(container, dict):
            continue
        if any(not isinstance(key, str) or key.startswith("liliuxflow_") or key in REQUEST_PROFILE_FIELDS
               for key in container):
            raise DistributionError("request cannot supply internal MTP2 profile or runtime policy")
        if container is not value and "model" in container:
            raise DistributionError("nested request fields cannot replace the logical MTP2 alias")
    budgets = [value[key] for key in ("max_tokens", "max_completion_tokens")
               if key in value and value[key] is not None]
    if (any(type(budget) is not int or not 0 < budget <= MAX_OUTPUT_TOKENS for budget in budgets)
            or len(budgets) == 2 and budgets[0] != budgets[1]):
        raise DistributionError("MTP2 output budgets must agree and stay within 65536 tokens")
    return profile
