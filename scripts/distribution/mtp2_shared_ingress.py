# SPDX-License-Identifier: Apache-2.0
"""Unwired MTP2 ingress capability helpers, for reviewed integration only.

The raw body is captured before normalization. Issuance is called after the
official LiteLLM authentication dependency, with its actual UserAPIKeyAuth.
No HTTP route, middleware, callback, installation or secret is created here.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import time

from common import DistributionError
from litellm_profile_policy import caller_fingerprint, explicit_profile_access
from mtp2_shared_config import PUBLIC_ALIASES, external_mtp2_profile, parse_external_mtp2_body

CAPABILITY_HEADER = "x-liliuxflow-mtp2-admission"
_DOMAIN = b"LiliuxFlow/MTP2/authenticated-ingress/v1\x00"
_TENANT_DOMAIN = b"LiliuxFlow/MTP2/virtual-key-cache-partition/v1\x00"
_MAX_TTL = 60


@dataclass(frozen=True)
class VerifiedIngress:
    request_id: str
    tenant: str
    alias: str
    profile_id: str
    expires_at: int


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _subkey(backend_secret):
    if not isinstance(backend_secret, bytes) or not 24 <= len(backend_secret) <= 4096:
        raise DistributionError("MTP2 capability requires the existing trusted backend secret")
    return hmac.digest(backend_secret, _DOMAIN + b"subkey", "sha256")


def _official_auth_type():
    from litellm.proxy._types import UserAPIKeyAuth
    return UserAPIKeyAuth


def _reject_internal(value, *, depth=0, routing=True):
    if depth > 64:
        raise DistributionError("MTP2 request nesting exceeds the bounded ingress limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or routing and key.lower().startswith(("liliuxflow_", "x-liliuxflow-")):
                raise DistributionError("caller cannot supply internal MTP2 identity or policy")
            if routing and key in {"tenant", "tenant_id", "request_id", "native_request_id", "resident_epoch", "runtime_epoch"}:
                raise DistributionError("caller cannot supply internal MTP2 correlation")
            # Root tools/schema/messages are payload data, not routing
            # authority. Once inside a routing container, keep its nested
            # objects under the same reserved-field policy.
            _reject_internal(item, depth=depth + 1,
                             routing=routing and (depth > 0 or key in {"options", "extra_body", "chat_template_kwargs", "metadata", "litellm_metadata", "extra_headers", "headers"}))
    elif isinstance(value, list):
        for item in value:
            _reject_internal(item, depth=depth + 1, routing=routing)


def validate_raw_ingress(raw_body, registry, *, headers=()):
    """Run before the first parser; do not use reconstructed JSON as raw_body."""
    try:
        if not isinstance(raw_body, bytes) or raw_body.startswith(b"\xef\xbb\xbf"):
            raise ValueError("MTP2 raw JSON requires UTF-8 without a BOM")
        raw_body.decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise DistributionError("MTP2 inference body must contain UTF-8 JSON") from error
    value = parse_external_mtp2_body(raw_body)
    _reject_internal(value)
    for key in headers:
        if not isinstance(key, str) or key.lower().startswith("x-liliuxflow-"):
            raise DistributionError("caller cannot supply internal MTP2 headers")
    return value, external_mtp2_profile(value, registry)


def issue_capability(raw_body, normalized_model, auth, registry, *, backend_secret,
                     policy=None, headers=(), now=None, ttl_seconds=30):
    """Issue only from the authenticated LiteLLM request path.

    Body user, cache keys, user/team metadata and caller call IDs never select
    the tenant or native request ID. A virtual key is its own conservative cache
    partition, including separate keys belonging to the same user.
    """
    value, profile = validate_raw_ingress(raw_body, registry, headers=headers)
    if not isinstance(auth, _official_auth_type()) or getattr(auth, "via_virtual_key", False) is not True:
        raise DistributionError("MTP2 admission requires official virtual-key authentication")
    fingerprint = caller_fingerprint(auth.api_key)
    if fingerprint is None or not explicit_profile_access(profile.public_alias, auth.models,
                                                          key_fingerprint=fingerprint, policy=policy):
        raise DistributionError("MTP2 logical alias requires its exact authenticated key grant")
    if normalized_model != value["model"]:
        raise DistributionError("MTP2 logical alias changed during normalization or alias resolution")
    current = int(time.time()) if now is None else now
    if type(current) is not int or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL:
        raise DistributionError("MTP2 ingress capability lifetime is invalid")
    payload = {"v": 1, "id": secrets.token_hex(16),
               "tenant": hashlib.sha256(_TENANT_DOMAIN + fingerprint.encode("ascii")).hexdigest(),
               "alias": profile.public_alias, "iat": current, "exp": current + ttl_seconds}
    data = _canonical(payload)
    signature = hmac.digest(_subkey(backend_secret), _DOMAIN + data, "sha256").hex()
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=") + "." + signature


def verify_capability(capability, logical_body, registry, *, backend_secret, now=None):
    """Verify at the private guard boundary, before manager model rewriting.

    The coordinator must additionally consume the ID once and bind it to the
    resident epoch. This function does not grant a physical model or bypass
    LiteLLM. The existing backend transport authentication remains required.
    """
    if not isinstance(capability, str) or len(capability) > 1024 or capability.count(".") != 1:
        raise DistributionError("MTP2 capability is malformed")
    encoded, signature = capability.split(".")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[a-f0-9]{64}", signature):
        raise DistributionError("MTP2 capability encoding is malformed")
    try:
        data = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        payload = parse_external_mtp2_body(data, maximum_body_bytes=1024)
    except (ValueError, UnicodeError) as error:
        raise DistributionError("MTP2 capability is malformed") from error
    expected = hmac.digest(_subkey(backend_secret), _DOMAIN + data, "sha256").hex()
    if not hmac.compare_digest(signature, expected) or _canonical(payload) != data:
        raise DistributionError("MTP2 capability authentication failed")
    if set(payload) != {"v", "id", "tenant", "alias", "iat", "exp"} or type(payload["v"]) is not int or payload["v"] != 1:
        raise DistributionError("MTP2 capability schema differs")
    current = int(time.time()) if now is None else now
    if (type(current) is not int or type(payload["iat"]) is not int or type(payload["exp"]) is not int
            or not 0 <= current - payload["iat"] <= _MAX_TTL
            or not 1 <= payload["exp"] - payload["iat"] <= _MAX_TTL or current >= payload["exp"]):
        raise DistributionError("MTP2 capability has expired or has an invalid lifetime")
    if (not isinstance(payload["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", payload["id"])
            or not isinstance(payload["tenant"], str) or not re.fullmatch(r"[a-f0-9]{64}", payload["tenant"])
            or payload["alias"] not in PUBLIC_ALIASES):
        raise DistributionError("MTP2 capability identity differs")
    _reject_internal(logical_body)
    profile = external_mtp2_profile(logical_body, registry)
    if profile.public_alias != payload["alias"]:
        raise DistributionError("MTP2 capability belongs to another logical alias")
    return VerifiedIngress(payload["id"], payload["tenant"], profile.public_alias,
                           profile.profile_id, payload["exp"])
