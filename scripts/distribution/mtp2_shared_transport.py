# SPDX-License-Identifier: Apache-2.0
"""Unwired MTP2 native-control v1 wire helpers and verified CPU adapter.

This module performs no HTTP itself and installs no route. Inference is signed
only after verifying the actual-auth ingress capability; its MAC covers the
fixed identity/profile tuple across pinned manager body rewriting. Controls
bind exact raw body bytes. Replies bind a fresh challenge and raw response
bytes. Exact owned process/port/lease checks and native atomic epoch/cleanup
proof remain required integration gates. Nothing here clears a coordinator.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import secrets
import time

from common import DistributionError
from mtp2_shared_config import PROFILE_IDS, PUBLIC_ALIASES, parse_external_mtp2_body
from mtp2_shared_ingress import validate_raw_ingress, verify_capability, _reject_internal

KDF_DOMAIN = b"LiliuxFlow/MTP2/native-control/v1\x00"
REQUEST_DOMAIN = b"LiliuxFlow/MTP2/native-request/v1\x00"
RESPONSE_DOMAIN = b"LiliuxFlow/MTP2/native-response/v1\x00"
HEADER_PREFIX = "x-liliuxflow-control-"
REQUEST_HEADERS = tuple(HEADER_PREFIX + key for key in (
    "version", "operation", "epoch", "nonce", "tenant", "alias", "profile",
    "challenge", "issued", "expires", "body-sha256", "mac"))
RESPONSE_HEADERS = tuple(HEADER_PREFIX + key for key in ("version", "challenge", "epoch", "mac"))
PATHS = {
    "infer": ("/v1/chat/completions", "/v1/completions"),
    "state": ("/_liliuxflow/mtp2/state",),
    "status": ("/_liliuxflow/mtp2/status",),
    "cancel": ("/_liliuxflow/mtp2/cancel",),
}
BODY_LIMITS = {"infer": 8 * 1024**2, "state": 256, "status": 8192, "cancel": 512}
MAX_JSON_INT = 2**53 - 1
MAX_TTL_SECONDS = 30
MAX_RESPONSE_BYTES = 32768
MAX_QUERY_IDS = 8
MAX_HANDLES = 64
MAX_REPLAY_IDS = 4096
ALIAS_PROFILES = dict(zip(PUBLIC_ALIASES, PROFILE_IDS))
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_RELEASE_PROOF = {"registered": True, "queue_reservation_returned": True,
                  "job_disposed": True, "session_disposed": True,
                  "pin_released": True, "gpu_fault": False}
_ZERO_PROOF = {"registered": True, "engine_acquired": False, "pin_acquired": False,
               "session_acquired": False, "gpu_work_started": False,
               "queue_reservation_returned": True, "job_disposed": True}


class UnknownNativeRecord(DistributionError):
    """Authenticated lookup absence; never resource-release authority."""
    def __init__(self, nonce, owner):
        super().__init__("mtp2_transport_record_unknown")
        self.nonce, self._owner = nonce, owner


def _fail(code):
    # Never include key, MAC, header values, prompt or native raw diagnostics.
    raise DistributionError("mtp2_transport_" + code) from None


def _int(value, low=0, high=MAX_JSON_INT):
    if type(value) is not int or not low <= value <= high:
        _fail("integer")
    return value


def _hex(value, length):
    if type(value) is not str or not (_HEX32 if length == 32 else _HEX64).fullmatch(value):
        _fail("hex")
    return value


def _ascii(value):
    if type(value) is not str:
        _fail("field_type")
    try:
        data = value.encode("ascii")
    except UnicodeError:
        _fail("field_encoding")
    if len(data) > 65535 or any(byte < 0x21 or byte > 0x7e for byte in data):
        _fail("field_encoding")
    return data


def _lp(domain, values):
    result = bytearray(domain)
    for value in values:
        encoded = _ascii(value)
        result.extend(len(encoded).to_bytes(2, "big"))
        result.extend(encoded)
    return bytes(result)


def _key(value):
    if type(value) is not bytes or len(value) != 32:
        _fail("key")
    return value


def derive_native_key(backend_secret):
    if type(backend_secret) is not bytes or not 24 <= len(backend_secret) <= 4096:
        _fail("backend_key")
    return hmac.digest(backend_secret, KDF_DOMAIN, "sha256")


def _json(body, limit):
    if type(body) is not bytes or len(body) > limit or body.startswith(b"\xef\xbb\xbf"):
        _fail("body")
    try:
        body.decode("utf-8")
        return parse_external_mtp2_body(body, maximum_body_bytes=limit)
    except (DistributionError, UnicodeError):
        _fail("json")


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail("schema")


def _body(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _headers(rows, required):
    if type(rows) not in (tuple, list):
        _fail("headers")
    found = {}
    for row in rows:
        if type(row) not in (tuple, list) or len(row) != 2:
            _fail("headers")
        name, value = row
        if type(name) is not str or type(value) is not str:
            _fail("headers")
        _ascii(name)
        lowered = name.lower()
        if lowered in found:
            _fail("duplicate_header")
        found[lowered] = value
        if lowered.startswith("x-liliuxflow-"):
            if lowered not in required:
                _fail("reserved_header")
            _ascii(value)
    if not set(required).issubset(found):
        _fail("missing_header")
    return found


@dataclass(frozen=True, slots=True, repr=False)
class RequestProof:
    operation: str
    method: str
    target: str
    epoch: str
    nonce: str
    tenant: str
    alias: str
    profile: str
    challenge: str
    issued: int
    expires: int
    body_sha256: str


def _validate(proof, now=None):
    if type(proof) is not RequestProof or type(proof.operation) is not str or proof.operation not in PATHS:
        _fail("operation")
    if proof.method != "POST" or type(proof.method) is not str or proof.target not in PATHS[proof.operation] or type(proof.target) is not str:
        _fail("target")
    if proof.operation == "state" and proof.epoch == "":
        pass
    else:
        _hex(proof.epoch, 64)
    if proof.operation in {"infer", "cancel"}:
        _hex(proof.nonce, 32)
        _hex(proof.tenant, 64)
        if type(proof.alias) is not str or ALIAS_PROFILES.get(proof.alias) != proof.profile:
            _fail("profile_binding")
    elif any(type(v) is not str or v != "" for v in (proof.nonce, proof.tenant, proof.alias, proof.profile)):
        _fail("control_identity")
    _hex(proof.challenge, 32)
    _int(proof.issued)
    _int(proof.expires)
    if not proof.issued < proof.expires <= proof.issued + MAX_TTL_SECONDS:
        _fail("expiry")
    if proof.operation == "infer":
        if proof.body_sha256 != "-" or type(proof.body_sha256) is not str:
            _fail("inference_digest_scope")
    else:
        _hex(proof.body_sha256, 64)
    if now is not None:
        _int(now)
        if not 0 <= now - proof.issued <= MAX_TTL_SECONDS or now >= proof.expires:
            _fail("expired")


def request_frame(proof):
    """Complete frozen MAC bytes; JSON order is not an identity frame."""
    _validate(proof)
    return _lp(REQUEST_DOMAIN, (
        proof.operation, proof.method, proof.target, proof.epoch, proof.nonce,
        proof.tenant, proof.alias, proof.profile, proof.challenge,
        str(proof.issued), str(proof.expires), proof.body_sha256))


def response_frame(proof, *, epoch, status, body):
    _validate(proof)
    _int(status, 100, 599)
    if not (proof.operation == "state" and epoch == ""):
        _hex(epoch, 64)
    if type(body) is not bytes or len(body) > MAX_RESPONSE_BYTES:
        _fail("response_body")
    return _lp(RESPONSE_DOMAIN, (
        proof.operation, proof.method, proof.target, proof.challenge,
        hashlib.sha256(request_frame(proof)).hexdigest(), epoch, str(status),
        hashlib.sha256(body).hexdigest()))


def request_headers(proof, native_key):
    mac = hmac.digest(_key(native_key), request_frame(proof), "sha256").hex()
    values = ("1", proof.operation, proof.epoch, proof.nonce, proof.tenant,
              proof.alias, proof.profile, proof.challenge, str(proof.issued),
              str(proof.expires), proof.body_sha256, mac)
    return tuple(zip(REQUEST_HEADERS, values))


def response_headers(proof, *, native_key, epoch, status, body):
    """Pure server-vector helper; native must publish its own proven ledger state."""
    mac = hmac.digest(_key(native_key), response_frame(proof, epoch=epoch, status=status, body=body), "sha256").hex()
    return (("Content-Type", "application/json"), ("Cache-Control", "no-store"),
            *tuple(zip(RESPONSE_HEADERS, ("1", proof.challenge, epoch, mac))))


def authenticate_native_headers(method, target, headers, *, native_key, now, actual_epoch):
    """Authenticate before inference ledger registration/body preparation.

    Native must additionally register/retire under one lock, recheck before
    queue publication, and recheck BEFORE engine acquire/pin/cache/GPU work.
    This returns identity authority only; it neither registers a row nor proves
    zero state. An unparsed/invalid HTTP request has no such authenticated proof.
    """
    found = _headers(headers, REQUEST_HEADERS)
    if found[REQUEST_HEADERS[0]] != "1":
        _fail("version")
    timestamps = []
    for key in ("issued", "expires"):
        raw = found[HEADER_PREFIX + key]
        if not _DECIMAL.fullmatch(raw) or len(raw) > 16:
            _fail("timestamp")
        timestamps.append(int(raw))
    proof = RequestProof(found[HEADER_PREFIX + "operation"], method, target,
                         found[HEADER_PREFIX + "epoch"], found[HEADER_PREFIX + "nonce"],
                         found[HEADER_PREFIX + "tenant"], found[HEADER_PREFIX + "alias"],
                         found[HEADER_PREFIX + "profile"], found[HEADER_PREFIX + "challenge"],
                         *timestamps, found[HEADER_PREFIX + "body-sha256"])
    _validate(proof, now)
    supplied = _hex(found[HEADER_PREFIX + "mac"], 64)
    if not hmac.compare_digest(supplied, hmac.digest(_key(native_key), request_frame(proof), "sha256").hex()):
        _fail("request_authentication")
    if proof.operation != "state" or proof.epoch:
        if proof.epoch != _hex(actual_epoch, 64):
            _fail("epoch")
    return proof


def verify_native_request(method, target, headers, body, *, native_key, now, actual_epoch):
    """Validate the full wire after header authentication; no ownership mutation."""
    proof = authenticate_native_headers(method, target, headers, native_key=native_key,
                                        now=now, actual_epoch=actual_epoch)
    document = _json(body, BODY_LIMITS[proof.operation])
    if proof.operation != "infer" and hashlib.sha256(body).hexdigest() != proof.body_sha256:
        _fail("request_body_digest")
    if proof.operation == "state":
        _exact(document, ())
    elif proof.operation == "status":
        _exact(document, ("v", "epoch", "requests"))
        _int(document["v"], 1, 1)
        if document["epoch"] != proof.epoch:
            _fail("epoch")
        ids = document["requests"]
        if type(ids) is not list or not 1 <= len(ids) <= MAX_QUERY_IDS:
            _fail("query")
        for nonce in ids:
            _hex(nonce, 32)
        if len(set(ids)) != len(ids):
            _fail("query")
    elif proof.operation == "cancel":
        _exact(document, ("v", "epoch", "nonce"))
        _int(document["v"], 1, 1)
        if document["epoch"] != proof.epoch or document["nonce"] != proof.nonce:
            _fail("cancel_binding")
    else:
        if document.get("model") != "Qwen3.8-Flash-Next" or document.get("liliuxflow_profile") != proof.profile:
            _fail("native_profile_binding")
        clean = dict(document)
        clean.pop("liliuxflow_profile")
        try:
            _reject_internal(clean)
        except DistributionError:
            _fail("internal_body_fields")
    return proof


@dataclass(frozen=True, slots=True)
class NativeBinding:
    epoch: str
    _owner: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AdmissionHandle:
    nonce: str
    alias: str
    profile: str
    target: str
    deadline: float
    body_sha256: str = field(repr=False)
    tenant: str = field(repr=False)
    body: bytes = field(repr=False)
    _owner: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RequestHandle:
    epoch: str
    nonce: str
    alias: str
    profile: str
    tenant: str = field(repr=False)
    _owner: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class WireRequest:
    method: str
    target: str
    headers: tuple = field(repr=False)
    body: bytes = field(repr=False)
    proof: RequestProof = field(repr=False)
    handles: tuple[RequestHandle, ...] = field(default=(), repr=False)
    _owner: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RawReply:
    status: int
    headers: tuple = field(repr=False)
    body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class VerifiedRecord:
    epoch: str
    nonce: str
    alias: str
    profile: str
    phase: str
    revision: int
    acquired: int
    released: int
    changed: bool
    _owner: object = field(repr=False, compare=False)

    @property
    def release_proven(self):
        return self.phase == "released"

    @property
    def no_state_proven(self):
        return self.phase == "rejected_without_native_state"


@dataclass(frozen=True, slots=True)
class VerifiedReply:
    operation: str
    binding: NativeBinding | None = None
    records: tuple[VerifiedRecord, ...] = ()
    cancel_result: str | None = None


class NativeTransport:
    """Trusted guard-side actor. Construct from existing private backend secret.

    AdmissionHandle, NativeBinding, RequestHandle, WireRequest and VerifiedRecord are internal
    objects returned by this actor, never reconstructed from public JSON.
    No plaintext record, HTTP error or TTL/EOF creates release authority.
    """
    def __init__(self, backend_secret, *, clock=time.time, deadline_clock=time.monotonic):
        self._key = derive_native_key(backend_secret)
        self._backend_secret = backend_secret
        self._clock = clock
        self._deadline_clock = deadline_clock
        self._owner = object()
        self._admissions = {}
        self._forwarded = set()
        self._handles = {}
        self._seen = {}
        self._observations = {}
        self._verified_records = {}
        self._received = {}
        self._sent = {}

    def _now(self):
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= MAX_JSON_INT:
            _fail("clock")
        try:
            return _int(int(value))
        except (OverflowError, ValueError):
            _fail("clock")

    def _binding(self, binding):
        if type(binding) is not NativeBinding or binding._owner is not self._owner:
            _fail("binding_origin")
        _hex(binding.epoch, 64)

    def _deadline_now(self):
        value = self._deadline_clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            _fail("deadline_clock")
        return value

    def _handle(self, handle, binding=None):
        if (type(handle) is not RequestHandle or handle._owner is not self._owner
                or self._handles.get((handle.epoch, handle.nonce)) is not handle):
            _fail("handle_origin")
        if binding is not None:
            self._binding(binding)
            if handle.epoch != binding.epoch:
                _fail("epoch")

    def _wire(self, operation, body, *, binding=None, handle=None, handles=(), target=None):
        now = self._now()
        proof = RequestProof(operation, "POST", target or PATHS[operation][0],
                             binding.epoch if binding else "", handle.nonce if handle else "",
                             handle.tenant if handle else "", handle.alias if handle else "",
                             handle.profile if handle else "", secrets.token_hex(16), now,
                             now + 10,
                             "-" if operation == "infer" else hashlib.sha256(body).hexdigest())
        return WireRequest(proof.method, proof.target, request_headers(proof, self._key),
                           body, proof, tuple(handles), self._owner)

    def state_request(self):
        return self._wire("state", b"{}")

    def consume_verified_ingress(self, capability, logical_body, registry, *, deadline, completion=False):
        """First guard admission only: verify raw/capability and consume nonce once.

        Preserve this opaque handle through queue/cold load. Capability expiry
        never releases or invalidates an already accepted generation request.
        The independent deadline is in the supplied monotonic clock domain.
        """
        if type(completion) is not bool:
            _fail("request_kind")
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline <= self._deadline_now()):
            _fail("generation_deadline")
        now = self._now()
        document, _ = validate_raw_ingress(logical_body, registry)
        ingress = verify_capability(capability, document, registry, backend_secret=self._backend_secret, now=now)
        _hex(ingress.request_id, 32)
        _hex(ingress.tenant, 64)
        self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
        nonce = ingress.request_id
        if nonce in self._seen or nonce in self._admissions:
            _fail("inference_replay")
        if len(self._admissions) >= MAX_HANDLES or len(self._seen) >= MAX_REPLAY_IDS:
            _fail("capacity")
        admitted = AdmissionHandle(nonce, ingress.alias, ingress.profile_id,
                                   PATHS["infer"][1 if completion else 0], deadline,
                                   hashlib.sha256(logical_body).hexdigest(), ingress.tenant,
                                   logical_body, self._owner)
        self._seen[nonce] = ingress.expires_at
        self._admissions[nonce] = admitted
        return admitted

    def _admission(self, admitted):
        if (type(admitted) is not AdmissionHandle or admitted._owner is not self._owner
                or self._admissions.get(admitted.nonce) is not admitted
                or hashlib.sha256(admitted.body).hexdigest() != admitted.body_sha256):
            _fail("admission_origin")

    def inference_request(self, admitted, binding):
        """Forward accepted opaque admission with fresh native authorization.

        Do not revalidate its now-expired ingress capability after queue/load.
        This constructor conservatively marks issuance before any native I/O;
        ambiguous failure subsequently needs authenticated terminal/owned proof.
        """
        self._binding(binding)
        self._admission(admitted)
        if admitted.deadline <= self._deadline_now():
            _fail("generation_deadline")
        if admitted.nonce in self._forwarded:
            _fail("inference_replay")
        handle = RequestHandle(binding.epoch, admitted.nonce, admitted.alias,
                               admitted.profile, admitted.tenant, self._owner)
        wire = self._wire("infer", admitted.body, binding=binding, handle=handle,
                          handles=(handle,), target=admitted.target)
        self._forwarded.add(admitted.nonce)
        self._seen[admitted.nonce] = max(self._seen.get(admitted.nonce, 0), wire.proof.expires)
        self._handles[(binding.epoch, admitted.nonce)] = handle
        return wire

    def discard_unforwarded(self, admitted):
        """Local queue cancellation only; no native/coordinator release authority."""
        self._admission(admitted)
        if admitted.nonce in self._forwarded:
            _fail("native_issuance_ambiguous")
        del self._admissions[admitted.nonce]

    def status_request(self, binding, handles):
        self._binding(binding)
        if type(handles) not in (tuple, list) or not 1 <= len(handles) <= MAX_QUERY_IDS:
            _fail("query")
        for handle in handles:
            self._handle(handle, binding)
        ids = [handle.nonce for handle in handles]
        if len(set(ids)) != len(ids):
            _fail("query")
        body = _body({"v": 1, "epoch": binding.epoch, "requests": ids})
        return self._wire("status", body, binding=binding, handles=handles)

    def cancel_request(self, binding, handle):
        self._handle(handle, binding)
        body = _body({"v": 1, "epoch": binding.epoch, "nonce": handle.nonce})
        return self._wire("cancel", body, binding=binding, handle=handle, handles=(handle,))

    def _record(self, row, handle):
        if type(row) is dict and row.get("phase") == "unknown":
            _exact(row, ("nonce", "phase"))
            if row["nonce"] != handle.nonce:
                _fail("record_binding")
            raise UnknownNativeRecord(handle.nonce, self._owner)
        if type(row) is not dict or type(row.get("phase")) is not str:
            _fail("record_phase")
        keys = {"nonce", "alias", "profile", "phase", "revision", "acquired", "released"}
        terminal = row["phase"] in {"released", "rejected_without_native_state"}
        _exact(row, keys | ({"proof"} if terminal else set()))
        if row["nonce"] != handle.nonce or row["alias"] != handle.alias or row["profile"] != handle.profile:
            _fail("record_binding")
        revision = _int(row["revision"], 1)
        acquired, released = _int(row["acquired"], 0, 1), _int(row["released"], 0, 1)
        phase = row["phase"]
        expected = {"preparing": (0, 0), "queued": (0, 0), "acquired": (1, 0),
                    "cleanup": (1, 0), "released": (1, 1), "rejected_without_native_state": (0, 0)}
        if type(phase) is not str or phase == "faulted" or phase not in expected:
            _fail("record_phase")
        if (acquired, released) != expected[phase]:
            _fail("record_counts")
        if terminal:
            expected_proof = _RELEASE_PROOF if phase == "released" else _ZERO_PROOF
            _exact(row["proof"], expected_proof)
            if any(type(row["proof"][key]) is not bool or row["proof"][key] is not value
                   for key, value in expected_proof.items()):
                _fail("terminal_proof")
        previous = self._observations.get((handle.epoch, handle.nonce))
        canonical = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        changed = previous is None or revision > previous[0]
        if previous is not None:
            old_revision, old_payload, old_phase, old_acquired = previous
            if revision < old_revision or revision == old_revision and canonical != old_payload:
                _fail("record_revision")
            if old_phase in {"released", "rejected_without_native_state"} and canonical != old_payload:
                _fail("record_terminal_regression")
            if acquired < old_acquired:
                _fail("record_acquire_regression")
            order = {"preparing": 0, "queued": 1, "acquired": 2, "cleanup": 3,
                     "released": 4, "rejected_without_native_state": 4}
            if order[phase] < order[old_phase]:
                _fail("record_phase_regression")
        return (VerifiedRecord(handle.epoch, handle.nonce, handle.alias, handle.profile,
                               phase, revision, acquired, released, changed, self._owner),
                (revision, canonical, phase, acquired))

    def _call_slot(self, table, wire):
        now = self._now()
        for key, expiry in tuple(table.items()):
            if expiry <= now:
                del table[key]
        key = hashlib.sha256(request_frame(wire.proof)).hexdigest()
        if key in table:
            _fail("response_replay")
        if len(table) >= MAX_REPLAY_IDS:
            _fail("capacity")
        return key

    def verify_response(self, wire, reply):
        if type(wire) is not WireRequest or wire._owner is not self._owner:
            _fail("reply_origin")
        key = self._call_slot(self._received, wire)
        result = self._verify_response(wire, reply)
        self._received[key] = wire.proof.expires
        return result

    def _verify_response(self, wire, reply):
        if type(wire) is not WireRequest or wire._owner is not self._owner or type(reply) is not RawReply:
            _fail("reply_origin")
        if wire.proof.operation == "infer":
            _fail("inference_response_not_release")
        _validate(wire.proof, self._now())
        _int(reply.status, 100, 599)
        headers = _headers(reply.headers, RESPONSE_HEADERS)
        if (headers[HEADER_PREFIX + "version"] != "1" or headers.get("content-type") != "application/json"
                or headers.get("cache-control") != "no-store" or headers[HEADER_PREFIX + "challenge"] != wire.proof.challenge):
            _fail("response_headers")
        epoch = headers[HEADER_PREFIX + "epoch"]
        frame = response_frame(wire.proof, epoch=epoch, status=reply.status, body=reply.body)
        supplied = _hex(headers[HEADER_PREFIX + "mac"], 64)
        if not hmac.compare_digest(supplied, hmac.digest(self._key, frame, "sha256").hex()):
            _fail("response_authentication")
        if wire.proof.operation != "state" and epoch != wire.proof.epoch:
            _fail("epoch")
        if reply.status != 200:
            _fail("control_rejected")
        document = _json(reply.body, MAX_RESPONSE_BYTES)
        operation = wire.proof.operation
        if operation == "state":
            _exact(document, ("v", "epoch", "state", "revision", "active", "pending", "cache"))
            _int(document["v"], 1, 1)
            _int(document["revision"])
            _int(document["active"], 0, 2)
            _int(document["pending"], 0, 4)
            if document["epoch"] != epoch or type(document["state"]) is not str or document["state"] not in {"ready", "loading", "retiring", "faulted"}:
                _fail("state")
            cache = document["cache"]
            required = {"disk_bytes": 0, "disk_attached": False, "writing": False,
                        "durable_min_tokens": 0, "write_ahead_enabled": False}
            _exact(cache, required)
            if any(type(cache[key]) is not type(value) or cache[key] != value for key, value in required.items()):
                _fail("shared_store_invariant")
            if not epoch and document["state"] != "loading":
                _fail("state_epoch")
            binding = NativeBinding(_hex(epoch, 64), self._owner) if document["state"] == "ready" else None
            return VerifiedReply(operation, binding=binding)
        if operation == "cancel":
            _exact(document, ("v", "epoch", "nonce", "result", "revision"))
            _int(document["v"], 1, 1)
            _int(document["revision"])
            if document["epoch"] != epoch or document["nonce"] != wire.proof.nonce:
                _fail("cancel_binding")
            if type(document["result"]) is not str or document["result"] not in {"cancel_requested", "already_terminal"}:
                _fail("cancel_unknown")
            return VerifiedReply(operation, cancel_result=document["result"])
        _exact(document, ("v", "epoch", "records"))
        _int(document["v"], 1, 1)
        if document["epoch"] != epoch or type(document["records"]) is not list or len(document["records"]) != len(wire.handles):
            _fail("status_schema")
        expected = {handle.nonce: handle for handle in wire.handles}
        staged, records = {}, []
        for row in document["records"]:
            if type(row) is not dict or type(row.get("nonce")) is not str or row["nonce"] not in expected or row["nonce"] in staged:
                _fail("status_binding")
            handle = expected[row["nonce"]]
            self._handle(handle)
            record, observation = self._record(row, handle)
            records.append(record)
            staged[row["nonce"]] = observation
        # Apply none of the peer observations unless every row verified.
        self._observations.update({(epoch, nonce): value for nonce, value in staged.items()})
        self._verified_records.update({(record.epoch, record.nonce): record for record in records})
        return VerifiedReply(operation, records=tuple(records))

    def require_verified_record(self, handle, record):
        """Return only actor-issued snapshot authority, never a reconstructed dict."""
        self._handle(handle)
        if (type(record) is not VerifiedRecord or record._owner is not self._owner
                or self._verified_records.get((handle.epoch, handle.nonce)) is not record
                or record.epoch != handle.epoch or record.nonce != handle.nonce
                or record.alias != handle.alias or record.profile != handle.profile):
            _fail("record_origin")
        return record

    def is_initial_unknown(self, error, handle):
        self._handle(handle)
        return (type(error) is UnknownNativeRecord and error._owner is self._owner
                and error.nonce == handle.nonce
                and (handle.epoch, handle.nonce) not in self._observations)

    async def exchange(self, wire, send):
        """Inject a callback sending wire.body exactly; no condition/physical lock.

        Each call has a fresh challenge/expiry. HTTP errors, EOF, timeout or
        parse failure raise static errors and never return a terminal proof.
        """
        if type(wire) is not WireRequest or wire._owner is not self._owner or wire.proof.operation == "infer":
            _fail("control_exchange")
        _validate(wire.proof, self._now())
        key = self._call_slot(self._sent, wire)
        if key in self._received:
            _fail("response_replay")
        # Failed/ambiguous I/O also burns this call's challenge. Retry with a
        # new wire; accepted native requests are still retained separately.
        self._sent[key] = wire.proof.expires
        try:
            reply = await asyncio.wait_for(send(wire), timeout=2)
        except asyncio.CancelledError:
            raise
        except Exception:
            _fail("exchange_failed")
        return self.verify_response(wire, reply)

    def forget_terminal(self, handle, record):
        """Drop only local transport bookkeeping after an authenticated receipt.

        This is not a coordinator/native/physical lease release. Replay
        tombstones are independently retained through admission expiry.
        """
        self._handle(handle)
        if (type(record) is not VerifiedRecord or record._owner is not self._owner
                or record.epoch != handle.epoch or record.nonce != handle.nonce
                or not (record.release_proven or record.no_state_proven)):
            _fail("terminal_origin")
        previous = self._observations.get((handle.epoch, handle.nonce))
        if previous is None or previous[0] != record.revision or previous[2] != record.phase:
            _fail("terminal_origin")
        del self._handles[(handle.epoch, handle.nonce)]
        del self._observations[(handle.epoch, handle.nonce)]
        self._verified_records.pop((handle.epoch, handle.nonce), None)
        del self._admissions[handle.nonce]
        self._forwarded.remove(handle.nonce)
