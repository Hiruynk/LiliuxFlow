# SPDX-License-Identifier: Apache-2.0
"""Isolated CPU wire tests: synthetic keys/native replies, no HTTP or models."""
import asyncio
import copy
import ctypes
from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
import litellm_profile_policy as policy
import mtp2_shared_ingress as ingress
import mtp2_shared_transport as transport
import profile_registry
from test_mtp2_shared_ingress import AuthFixture

ROOT = Path(__file__).resolve().parents[2]
SECRET = b"CPU-only-synthetic-backend-secret-0123456789"
EPOCH = "a" * 64


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.actor = transport.NativeTransport(SECRET, clock=lambda: self.now, deadline_clock=lambda: self.now)
        self.key = transport.derive_native_key(SECRET)
        document = json.loads((ROOT / profile_registry.REGISTRY_PATH).read_bytes())
        self.registry = profile_registry.parse_registry(document, enabled_optin_profiles=transport.PROFILE_IDS)
        self.policy = policy.ProfileAccessPolicy(frozenset(transport.PUBLIC_ALIASES))
        mocked = patch.object(ingress, "_official_auth_type", return_value=AuthFixture)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.binding = self.ready()

    def payload(self, value):
        return json.dumps(value, separators=(",", ":")).encode()

    def state(self, *, state="ready", epoch=EPOCH):
        return {"v": 1, "epoch": epoch, "state": state, "revision": 1, "active": 0, "pending": 0,
                "cache": {"disk_bytes": 0, "disk_attached": False, "writing": False,
                          "durable_min_tokens": 0, "write_ahead_enabled": False}}

    def reply(self, wire, document, *, epoch=EPOCH, status=200, raw=None):
        body = self.payload(document) if raw is None else raw
        headers = transport.response_headers(wire.proof, native_key=self.key, epoch=epoch, status=status, body=body)
        return transport.RawReply(status, headers, body)

    def ready(self, *, epoch=EPOCH):
        wire = self.actor.state_request()
        return self.actor.verify_response(wire, self.reply(wire, self.state(epoch=epoch), epoch=epoch)).binding

    def infer(self, index=0, *, key="sk-synthetic-key", body_extra=None, completion=False):
        document = {"model": transport.PUBLIC_ALIASES[index], "messages": [{"role": "user", "content": "synthetic prompt"}],
                    "max_tokens": 8, **(body_extra or {})}
        raw = self.payload(document)
        capability = ingress.issue_capability(raw, document["model"], AuthFixture(key=key), self.registry,
                                              backend_secret=SECRET, policy=self.policy, now=self.now, ttl_seconds=30)
        admitted = self.actor.consume_verified_ingress(capability, raw, self.registry,
                                                       deadline=self.now + 3600, completion=completion)
        return self.actor.inference_request(admitted, self.binding)

    def native_body(self, wire):
        value = json.loads(wire.body)
        value["model"] = "Qwen3.8-Flash-Next"
        value["liliuxflow_profile"] = wire.proof.profile
        return self.payload(value)

    def native_verify(self, wire, *, headers=None, body=None, target=None, method=None, epoch=EPOCH, now=None):
        return transport.verify_native_request(method or wire.method, target or wire.target,
                                               wire.headers if headers is None else headers,
                                               wire.body if body is None else body,
                                               native_key=self.key, now=self.now if now is None else now, actual_epoch=epoch)

    def row(self, handle, phase="acquired", *, revision=1):
        counts = {"preparing": (0, 0), "queued": (0, 0), "acquired": (1, 0), "cleanup": (1, 0),
                  "released": (1, 1), "rejected_without_native_state": (0, 0), "faulted": (1, 0)}
        acquired, released = counts[phase]
        result = {"nonce": handle.nonce, "alias": handle.alias, "profile": handle.profile,
                  "phase": phase, "revision": revision, "acquired": acquired, "released": released, "usage": None}
        if phase == "released":
            result["proof"] = dict(transport._RELEASE_PROOF)
        if phase == "rejected_without_native_state":
            result["proof"] = dict(transport._ZERO_PROOF)
        return result

    def status(self, handles, rows, *, wire=None):
        wire = self.actor.status_request(self.binding, handles) if wire is None else wire
        reply = self.reply(wire, {"v": 1, "epoch": EPOCH, "records": rows})
        return self.actor.verify_response(wire, reply)

    def test_exact_true_NUL_domains_and_KDF(self):
        self.assertEqual(transport.KDF_DOMAIN.hex(), "4c696c697578466c6f772f4d5450322f6e61746976652d636f6e74726f6c2f763100")
        self.assertEqual(self.key, hmac.digest(SECRET, b"LiliuxFlow/MTP2/native-control/v1\0", "sha256"))
        self.assertNotEqual(self.key, hmac.digest(SECRET, b"LiliuxFlow/MTP2/native-control/v1\\0", "sha256"))
        self.assertTrue(transport.REQUEST_DOMAIN.endswith(b"\0"))
        self.assertTrue(transport.RESPONSE_DOMAIN.endswith(b"\0"))

    def test_request_frame_has_exact12fields_BE_lengths_including_empty(self):
        wire = self.actor.state_request()
        values = ["state", "POST", "/_liliuxflow/mtp2/state", "", "", "", "", "", wire.proof.challenge,
                  "1000", "1010", hashlib.sha256(b"{}").hexdigest()]
        expected = b"LiliuxFlow/MTP2/native-request/v1\0" + b"".join(len(v).to_bytes(2, "big") + v.encode("ascii") for v in values)
        self.assertEqual(transport.request_frame(wire.proof), expected)
        self.assertIn(b"\x00\x00" * 5, expected)
        self.assertEqual(len(wire.headers), 12)

    def test_response_frame_exact_order_and_raw_body_hash(self):
        wire = self.actor.state_request()
        body = self.payload(self.state())
        values = ["state", "POST", wire.target, wire.proof.challenge, hashlib.sha256(transport.request_frame(wire.proof)).hexdigest(),
                  EPOCH, "200", hashlib.sha256(body).hexdigest()]
        expected = transport.RESPONSE_DOMAIN + b"".join(len(v).to_bytes(2, "big") + v.encode() for v in values)
        self.assertEqual(transport.response_frame(wire.proof, epoch=EPOCH, status=200, body=body), expected)

    def test_MAC_reflection_directions_and_operations_do_not_authorize(self):
        wire = self.actor.state_request()
        reply = self.reply(wire, self.state())
        request_mac = dict(wire.headers)[transport.HEADER_PREFIX + "mac"]
        headers = tuple((key, request_mac if key.endswith("-mac") else value) for key, value in reply.headers)
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, replace(reply, headers=headers))
        proof = replace(wire.proof, operation="status", target=transport.PATHS["status"][0], epoch=EPOCH)
        altered = tuple((key, value) for key, value in transport.request_headers(proof, self.key))
        # Move the state MAC into a different signed operation/frame.
        altered = tuple((key, request_mac if key.endswith("-mac") else value) for key, value in altered)
        with self.assertRaises(DistributionError):
            transport.authenticate_native_headers("POST", proof.target, altered, native_key=self.key, now=1000, actual_epoch=EPOCH)

    def test_control_body_reordering_without_new_MAC_is_rejected(self):
        handle = self.infer().handles[0]
        wire = self.actor.status_request(self.binding, (handle,))
        changed = self.payload({"requests": [handle.nonce], "epoch": EPOCH, "v": 1})
        with self.assertRaises(DistributionError):
            self.native_verify(wire, body=changed)

    def test_response_body_reordering_or_status_tamper_invalidates_MAC(self):
        wire = self.actor.state_request()
        reply = self.reply(wire, self.state())
        reordered = json.dumps(self.state(), sort_keys=True).encode()
        for tampered in (replace(reply, body=reordered), replace(reply, status=503), replace(reply, body=reply.body[:-1])):
            with self.assertRaises(DistributionError):
                self.actor.verify_response(wire, tampered)

    def test_duplicate_case_variants_unknown_and_missing_reserved_headers_reject(self):
        wire = self.actor.state_request()
        for headers in (wire.headers + ((wire.headers[0][0].upper(), "1"),),
                        wire.headers + (("x-liliuxflow-unknown", "x"),), wire.headers[:-1]):
            with self.assertRaises(DistributionError):
                self.native_verify(wire, headers=headers)
        reply = self.reply(wire, self.state())
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, replace(reply, headers=reply.headers + (("X-Liliuxflow-Control-Mac", "0" * 64),)))

    def test_wrong_methods_and_query_fragment_absolute_targets_are_rejected(self):
        wire = self.actor.state_request()
        for method in ("GET", "post", True):
            with self.assertRaises(DistributionError):
                self.native_verify(wire, method=method)
        for target in (wire.target + "?x=1", wire.target + "#x", "http://127.0.0.1" + wire.target, wire.target + "/"):
            with self.assertRaises(DistributionError):
                self.native_verify(wire, target=target)

    def test_timestamp_format_bool_bounds_and_lifetime_fail(self):
        wire = self.actor.state_request()
        for raw in ("01000", "+1000", "1000.0", " 1000", "-1", "true"):
            headers = tuple((name, raw if name.endswith("-issued") else value) for name, value in wire.headers)
            with self.assertRaises(DistributionError):
                self.native_verify(wire, headers=headers)
        for proof in (replace(wire.proof, issued=True), replace(wire.proof, expires=1031),
                      replace(wire.proof, expires=1000), replace(wire.proof, expires=transport.MAX_JSON_INT + 1)):
            with self.assertRaises(DistributionError):
                transport.request_frame(proof)
        for now in (999, 1010, 1031, True):
            with self.assertRaises(DistributionError):
                self.native_verify(wire, now=now)

    def test_header_CRLF_unicode_upperhex_and_overlength_are_rejected(self):
        wire = self.actor.state_request()
        for raw in ("A" * 32, "0" * 31, "0" * 32 + "\r\n", "雪", "0" * 70000):
            headers = tuple((name, raw if name.endswith("-challenge") else value) for name, value in wire.headers)
            with self.assertRaises(DistributionError):
                self.native_verify(wire, headers=headers)

    def test_all_three_original_alias_profile_and_tenant_nonce_bindings(self):
        for index in range(3):
            wire = self.infer(index)
            self.assertEqual(wire.proof.alias, transport.PUBLIC_ALIASES[index])
            self.assertEqual(wire.proof.profile, transport.PROFILE_IDS[index])
            self.assertEqual(wire.proof.body_sha256, "-")
            self.native_verify(wire, body=self.native_body(wire))

    def test_legitimate_internal_named_tool_property_coexists_with_forced_native_policy(self):
        wire = self.infer(body_extra={"tools": [{"type": "function", "function": {"name": "sample", "parameters": {
            "type": "object", "properties": {"liliuxflow_profile": {"type": "string"}}}}}]})
        proof = self.native_verify(wire, body=self.native_body(wire))
        self.assertEqual(proof.profile, transport.PROFILE_IDS[0])

    def test_inference_requires_verified_capability_not_body_literal_identity(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        for capability in ("fake", {"tenant": "a" * 64}, None):
            with self.assertRaises(DistributionError):
                self.actor.consume_verified_ingress(capability, raw, self.registry, deadline=self.now + 3600)
        for extra in ({"tenant": "a" * 64}, {"request_id": "0" * 32}, {"liliuxflow_profile": transport.PROFILE_IDS[-1]}):
            with self.assertRaises(DistributionError):
                self.infer(body_extra=extra)

    def test_original_alias_capability_cannot_switch_model_profile_or_privatephysical(self):
        wire = self.infer()
        value = json.loads(wire.body)
        value["model"] = transport.PUBLIC_ALIASES[-1]
        raw = self.payload(value)
        original_raw = wire.body
        capability = ingress.issue_capability(original_raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                              backend_secret=SECRET, policy=self.policy, now=self.now)
        with self.assertRaises(DistributionError):
            self.actor.consume_verified_ingress(capability, raw, self.registry, deadline=self.now + 3600)
        value = json.loads(self.native_body(wire))
        value["liliuxflow_profile"] = transport.PROFILE_IDS[-1]
        with self.assertRaises(DistributionError):
            self.native_verify(wire, body=self.payload(value))
        with self.assertRaises(DistributionError):
            self.native_verify(wire, body=wire.body)  # manager has not forced native policy/model

    def test_request_header_identity_tamper_requires_native_secret(self):
        wire = self.infer()
        for suffix, forged in (("tenant", "b" * 64), ("nonce", "b" * 32), ("alias", transport.PUBLIC_ALIASES[-1]),
                               ("epoch", "b" * 64), ("profile", transport.PROFILE_IDS[-1])):
            headers = tuple((name, forged if name.endswith("-" + suffix) else value) for name, value in wire.headers)
            with self.assertRaises(DistributionError):
                self.native_verify(wire, headers=headers, body=self.native_body(wire))

    def test_actual_epoch_mismatch_rejects_before_body_validation(self):
        wire = self.infer()
        with self.assertRaisesRegex(DistributionError, "epoch"):
            self.native_verify(wire, body=b"invalid JSON", epoch="b" * 64)

    def test_header_authentication_precedes_body_parse_but_is_not_zero_state(self):
        wire = self.infer()
        proof = transport.authenticate_native_headers(wire.method, wire.target, wire.headers,
                                                       native_key=self.key, now=1000, actual_epoch=EPOCH)
        self.assertEqual(proof.nonce, wire.handles[0].nonce)
        with self.assertRaises(DistributionError):
            self.native_verify(wire, body=b"invalid JSON")
        self.assertFalse(hasattr(proof, "release_proven"))
        self.assertFalse(hasattr(proof, "no_state_proven"))

    def test_control_and_response_JSON_duplicate_nonfinite_wrong_schema_fail(self):
        wire = self.actor.state_request()
        for raw in (b'{"x":1,"x":2}', b'{"v":NaN}', b'[]', b'\xff', b'\xef\xbb\xbf{}'):
            signed = replace(wire.proof, body_sha256=hashlib.sha256(raw).hexdigest())
            headers = transport.request_headers(signed, self.key)
            with self.assertRaises(DistributionError):
                self.native_verify(wire, headers=headers, body=raw)
        raw = b'{"v":1,"v":1}'
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, self.reply(wire, {}, raw=raw))

    def test_same_tenant_keys_nonce_unique_and_body_user_cache_does_not_override(self):
        first = self.infer(body_extra={"user": "external", "prompt_cache_key": "same"})
        second = self.infer(body_extra={"user": "other", "prompt_cache_key": "same"})
        other = self.infer(key="sk-another-key")
        self.assertEqual(first.proof.tenant, second.proof.tenant)
        self.assertNotEqual(first.proof.nonce, second.proof.nonce)
        self.assertNotEqual(first.proof.tenant, other.proof.tenant)

    def test_private_inprocess_bindings_and_handles_cannot_be_deserialized(self):
        handle = self.infer().handles[0]
        for binding in ({"epoch": EPOCH}, transport.NativeBinding(EPOCH, object())):
            with self.assertRaises(DistributionError):
                self.actor.status_request(binding, (handle,))
        for forged in ({"nonce": handle.nonce}, replace(handle, tenant="b" * 64), replace(handle, _owner=object())):
            with self.assertRaises(DistributionError):
                self.actor.cancel_request(self.binding, forged)

    def test_response_challenge_wrong_epoch_unsigned_and_MAC_failclosed(self):
        handle = self.infer().handles[0]
        wire = self.actor.status_request(self.binding, (handle,))
        reply = self.reply(wire, {"v": 1, "epoch": EPOCH, "records": [self.row(handle)]})
        for suffix, value in (("challenge", "b" * 32), ("epoch", "b" * 64), ("mac", "0" * 64)):
            changed = tuple((name, value if name.endswith("-" + suffix) else item) for name, item in reply.headers)
            with self.assertRaises(DistributionError):
                self.actor.verify_response(wire, replace(reply, headers=changed))
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, replace(reply, headers=(("Content-Type", "application/json"),)))
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, self.reply(wire, {}, epoch="b" * 64))

    def test_peer_records_finish_out_of_order_and_keep_logical_alias(self):
        first, second = self.infer(0).handles[0], self.infer(2).handles[0]
        result = self.status((first, second), [self.row(second, "released"), self.row(first, "acquired")])
        self.assertEqual(result.records[0].nonce, second.nonce)
        self.assertTrue(result.records[0].release_proven)
        self.assertFalse(result.records[1].release_proven)
        self.assertEqual(result.records[1].alias, first.alias)

    def test_first_terminal_snapshot_carries_actual_sticky_acquire_release_proof(self):
        handle = self.infer().handles[0]
        record = self.status((handle,), [self.row(handle, "released")]).records[0]
        self.assertEqual((record.acquired, record.released), (1, 1))
        self.assertTrue(record.release_proven)

    def test_zero_state_requires_all_exact_bool_no_acquire_proofs(self):
        handle = self.infer().handles[0]
        valid = self.row(handle, "rejected_without_native_state")
        for key in transport._ZERO_PROOF:
            for replacement in (int(valid["proof"][key]), "false", None, not valid["proof"][key]):
                bad = copy.deepcopy(valid)
                bad["proof"][key] = replacement
                with self.assertRaises(DistributionError):
                    self.status((handle,), [bad])
        record = self.status((handle,), [valid]).records[0]
        self.assertTrue(record.no_state_proven)
        self.assertFalse(record.release_proven)
        self.assertEqual((record.acquired, record.released), (0, 0))

    def test_release_proof_missing_false_or_int_never_releases(self):
        handle = self.infer().handles[0]
        for key in transport._RELEASE_PROOF:
            bad = self.row(handle, "released")
            bad["proof"][key] = int(bad["proof"][key])
            with self.assertRaises(DistributionError):
                self.status((handle,), [bad])
        bad = self.row(handle, "released")
        del bad["proof"]
        with self.assertRaises(DistributionError):
            self.status((handle,), [bad])

    def test_HTTP400_EOF_and_cancel_ack_never_prove_release(self):
        handle = self.infer().handles[0]
        wire = self.actor.status_request(self.binding, (handle,))
        for reply in (self.reply(wire, {"error": "invalid_request"}, status=400),
                      self.reply(wire, {}, raw=b""), None):
            with self.assertRaises(DistributionError):
                self.actor.verify_response(wire, reply)
        cancel = self.actor.cancel_request(self.binding, handle)
        result = self.actor.verify_response(cancel, self.reply(cancel, {"v": 1, "epoch": EPOCH, "nonce": handle.nonce,
                                                                      "result": "already_terminal", "revision": 1}))
        self.assertEqual(result.cancel_result, "already_terminal")
        self.assertEqual(result.records, ())

    def test_unknown_missing_expired_faulted_terminal_failclosed(self):
        handle = self.infer().handles[0]
        for rows in ([], [{"nonce": handle.nonce, "phase": "unknown"}], [self.row(handle, "faulted")]):
            with self.assertRaises(DistributionError):
                self.status((handle,), rows)
        wire = self.actor.status_request(self.binding, (handle,))
        reply = self.reply(wire, {"v": 1, "epoch": EPOCH, "records": [self.row(handle, "released")]})
        self.now += 10
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, reply)
        self.assertIn((EPOCH, handle.nonce), self.actor._handles)

    def test_phase_counts_types_schema_and_foreign_alias_fail(self):
        handle = self.infer().handles[0]
        for field, value in (("acquired", True), ("released", False), ("revision", True), ("revision", 0),
                             ("revision", transport.MAX_JSON_INT + 1), ("phase", []), ("alias", transport.PUBLIC_ALIASES[-1]),
                             ("profile", transport.PROFILE_IDS[-1]), ("nonce", "b" * 32), ("acquired", 2)):
            bad = self.row(handle)
            bad[field] = value
            with self.assertRaises(DistributionError):
                self.status((handle,), [bad])

    def test_revisions_repeated_poll_not_progress_and_regression_fail(self):
        handle = self.infer().handles[0]
        row = self.row(handle, "acquired", revision=3)
        self.assertTrue(self.status((handle,), [row]).records[0].changed)
        self.assertFalse(self.status((handle,), [dict(reversed(list(row.items())))]).records[0].changed)
        for bad in (self.row(handle, "acquired", revision=2), self.row(handle, "cleanup", revision=3),
                    self.row(handle, "queued", revision=4)):
            with self.assertRaises(DistributionError):
                self.status((handle,), [bad])
        self.status((handle,), [self.row(handle, "cleanup", revision=4)])
        with self.assertRaises(DistributionError):
            self.status((handle,), [self.row(handle, "acquired", revision=5)])

    def test_partial_peer_failure_does_not_commit_other_peer_observation(self):
        first, second = self.infer().handles[0], self.infer(1).handles[0]
        bad = self.row(second)
        bad["acquired"] = True
        with self.assertRaises(DistributionError):
            self.status((first, second), [self.row(first, "released"), bad])
        self.assertNotIn((EPOCH, first.nonce), self.actor._observations)

    def test_duplicate_records_or_extra_fields_fail(self):
        first, second = self.infer().handles[0], self.infer(1).handles[0]
        with self.assertRaises(DistributionError):
            self.status((first, second), [self.row(first), self.row(first)])
        bad = self.row(first)
        bad["tenant"] = "b" * 64
        with self.assertRaises(DistributionError):
            self.status((first,), [bad])

    def test_authorization_expiry_does_not_release_accepted_generation(self):
        handle = self.infer().handles[0]
        self.now += 1000
        wire = self.actor.cancel_request(self.binding, handle)
        self.assertEqual(wire.proof.issued, self.now)
        self.assertEqual(wire.proof.expires, self.now + 10)
        self.native_verify(wire, now=self.now)
        result = self.status((handle,), [self.row(handle, "cleanup")])
        self.assertFalse(result.records[0].release_proven)
        self.assertIn((EPOCH, handle.nonce), self.actor._handles)

    def test_same_inference_nonce_replay_after_terminal_forget_rejects(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        admitted = self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        wire = self.actor.inference_request(admitted, self.binding)
        handle = wire.handles[0]
        record = self.status((handle,), [self.row(handle, "released")]).records[0]
        self.actor.forget_terminal(handle, record)
        with self.assertRaises(DistributionError):
            self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)

    def test_original_ingress_TTL_does_not_reject_queued_coldload_forward_after61seconds(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now, ttl_seconds=30)
        admitted = self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        self.now += 61
        wire = self.actor.inference_request(admitted, self.binding)
        self.assertEqual((wire.proof.issued, wire.proof.expires), (1061, 1071))
        self.native_verify(wire, body=self.native_body(wire))
        self.assertIs(wire.body, raw)

    def test_generation_deadline_expired_refuses_forward_without_native_issuance(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        admitted = self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 20)
        self.now += 21
        with self.assertRaisesRegex(DistributionError, "generation_deadline"):
            self.actor.inference_request(admitted, self.binding)
        self.assertNotIn(admitted.nonce, self.actor._forwarded)
        self.actor.discard_unforwarded(admitted)

    def test_expired_capability_first_consumption_rejects(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        self.now += 31
        with self.assertRaises(DistributionError):
            self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)

    def test_one_ingress_nonce_cannot_be_consumed_again_across_new_epoch(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        admitted = self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        other_binding = self.ready(epoch="b" * 64)
        with self.assertRaises(DistributionError):
            self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        self.actor.inference_request(admitted, other_binding)
        with self.assertRaises(DistributionError):
            self.actor.inference_request(admitted, self.binding)

    def test_admission_authority_raw_hash_path_and_literals_cannot_be_forged(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        admitted = self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600, completion=True)
        for forged in ({"nonce": admitted.nonce}, replace(admitted, target=transport.PATHS["infer"][0]),
                       replace(admitted, body=b"changed"), replace(admitted, tenant="b" * 64)):
            with self.assertRaises(DistributionError):
                self.actor.inference_request(forged, self.binding)
        wire = self.actor.inference_request(admitted, self.binding)
        self.assertEqual(wire.target, transport.PATHS["infer"][1])

    def test_bounded_admission_capacity_does_not_age_release_accepted_handles(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        for _ in range(transport.MAX_HANDLES):
            cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                           backend_secret=SECRET, policy=self.policy, now=self.now)
            self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        self.now += 61
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        with self.assertRaisesRegex(DistributionError, "capacity"):
            self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        self.assertEqual(len(self.actor._admissions), transport.MAX_HANDLES)

    def test_discard_only_never_forwarded_local_admission(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        admitted = self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)
        self.actor.inference_request(admitted, self.binding)
        with self.assertRaisesRegex(DistributionError, "ambiguous"):
            self.actor.discard_unforwarded(admitted)

    def test_deadline_and_clock_types_are_strict(self):
        raw = self.payload({"model": transport.PUBLIC_ALIASES[0], "messages": []})
        cap = ingress.issue_capability(raw, transport.PUBLIC_ALIASES[0], AuthFixture(), self.registry,
                                       backend_secret=SECRET, policy=self.policy, now=self.now)
        for deadline in (True, "3600", float("nan"), float("inf"), self.now):
            with self.assertRaises(DistributionError):
                self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=deadline)
        self.actor._deadline_clock = lambda: float("nan")
        with self.assertRaises(DistributionError):
            self.actor.consume_verified_ingress(cap, raw, self.registry, deadline=self.now + 3600)

    def test_only_actor_issued_terminal_can_drop_local_bookkeeping(self):
        handle = self.infer().handles[0]
        active = self.status((handle,), [self.row(handle)]).records[0]
        with self.assertRaises(DistributionError):
            self.actor.forget_terminal(handle, active)
        terminal = replace(active, phase="released", acquired=1, released=1)
        with self.assertRaises(DistributionError):
            self.actor.forget_terminal(handle, terminal)
        actual = self.status((handle,), [self.row(handle, "released", revision=2)]).records[0]
        self.actor.forget_terminal(handle, actual)
        with self.assertRaises(DistributionError):
            self.actor.cancel_request(self.binding, handle)

    def test_received_response_replay_and_fresh_challenge(self):
        wire = self.actor.state_request()
        reply = self.reply(wire, self.state())
        self.actor.verify_response(wire, reply)
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, reply)
        other = self.actor.state_request()
        self.assertNotEqual(other.proof.challenge, wire.proof.challenge)
        with self.assertRaises(DistributionError):
            self.actor.verify_response(other, reply)

    def test_shared_disk0_no_tier_no_writing_invariants_types(self):
        for field, value in (("disk_bytes", 1), ("disk_bytes", False), ("disk_attached", True),
                             ("writing", True), ("durable_min_tokens", 1), ("write_ahead_enabled", True)):
            wire = self.actor.state_request()
            document = self.state()
            document["cache"][field] = value
            with self.assertRaises(DistributionError):
                self.actor.verify_response(wire, self.reply(wire, document))

    def test_loading_state_has_no_ready_binding_and_empty_epoch_is_limited(self):
        wire = self.actor.state_request()
        reply = self.reply(wire, self.state(state="loading", epoch=""), epoch="")
        self.assertIsNone(self.actor.verify_response(wire, reply).binding)
        wire = self.actor.state_request()
        with self.assertRaises(DistributionError):
            self.actor.verify_response(wire, self.reply(wire, self.state(epoch=""), epoch=""))

    def test_body_query_response_bounds_and_wrong_key(self):
        handle = self.infer().handles[0]
        for handles in ((), (handle, handle), (handle,) * 9):
            with self.assertRaises(DistributionError):
                self.actor.status_request(self.binding, handles)
        wire = self.actor.state_request()
        with self.assertRaises(DistributionError):
            self.native_verify(wire, body=b" " * 257)
        with self.assertRaises(DistributionError):
            transport.response_frame(wire.proof, epoch=EPOCH, status=200, body=b" " * 32769)
        with self.assertRaises(DistributionError):
            transport.authenticate_native_headers(wire.method, wire.target, wire.headers,
                                                   native_key=b"b" * 32, now=1000, actual_epoch=EPOCH)

    def test_static_errors_and_reprs_never_dump_secret_body_header_MAC(self):
        wire = self.infer()
        self.assertNotIn("synthetic prompt", repr(wire))
        self.assertNotIn(SECRET.decode(), repr(self.actor))
        self.assertNotIn(dict(wire.headers)[transport.HEADER_PREFIX + "mac"], repr(wire.proof))
        try:
            self.native_verify(wire, body=b"SECRET_UNTRUSTED_BODY")
        except DistributionError as error:
            self.assertNotIn("SECRET_UNTRUSTED_BODY", str(error))
            self.assertNotIn(SECRET.decode(), str(error))

    @unittest.skipUnless(
        sys.platform == "darwin",
        "NOT_AVAILABLE: Apple CommonCrypto CCHmac requires macOS; exercised by the macos-15 CPU job.",
    )
    def test_sdk_CCHmac_matches_frozen_request_and_response_frames(self):
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        fn = library.CCHmac
        fn.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
                       ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        fn.restype = None
        wire = self.actor.state_request()
        for frame in (transport.request_frame(wire.proof),
                      transport.response_frame(wire.proof, epoch=EPOCH, status=200, body=self.payload(self.state()))):
            key_buffer = ctypes.create_string_buffer(self.key)
            data_buffer = ctypes.create_string_buffer(frame)
            out = (ctypes.c_ubyte * 32)()
            fn(2, key_buffer, len(self.key), data_buffer, len(frame), out)
            self.assertEqual(bytes(out), hmac.digest(self.key, frame, "sha256"))


class TransportCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.actor = transport.NativeTransport(SECRET, clock=lambda: 1000)
        self.key = transport.derive_native_key(SECRET)

    def reply(self, wire):
        body = json.dumps({"v": 1, "epoch": EPOCH, "state": "ready", "revision": 1, "active": 0, "pending": 0,
                           "cache": {"disk_bytes": 0, "disk_attached": False, "writing": False,
                                     "durable_min_tokens": 0, "write_ahead_enabled": False}}).encode()
        return transport.RawReply(200, transport.response_headers(wire.proof, native_key=self.key,
                                                                  epoch=EPOCH, status=200, body=body), body)

    async def test_callback_gets_exact_raw_wire_and_returns_only_verified_reply(self):
        wire = self.actor.state_request()
        async def send(actual):
            self.assertIs(actual, wire)
            self.assertEqual(actual.body, b"{}")
            return self.reply(actual)
        result = await self.actor.exchange(wire, send)
        self.assertEqual(result.binding.epoch, EPOCH)

    async def test_callback_EOF_and_exception_are_static_unknown_not_release(self):
        async def send(wire):
            raise RuntimeError("SECRET_CALLBACK_DETAILS")
        with self.assertRaisesRegex(DistributionError, "mtp2_transport_exchange_failed") as error:
            await self.actor.exchange(self.actor.state_request(), send)
        self.assertNotIn("SECRET_CALLBACK_DETAILS", str(error.exception))
        async def eof(wire):
            return None
        with self.assertRaises(DistributionError):
            await self.actor.exchange(self.actor.state_request(), eof)

    async def test_callback_cancel_preserves_cancellation_and_burns_old_challenge(self):
        entered = asyncio.Event()
        wire = self.actor.state_request()
        async def waiting(actual):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(self.actor.exchange(wire, waiting))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(DistributionError):
            await self.actor.exchange(wire, waiting)

    async def test_callback_timeout_cancels_only_control_IO_and_never_creates_release(self):
        cancelled = asyncio.Event()
        async def waiting(wire):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        wire = self.actor.state_request()
        with self.assertRaisesRegex(DistributionError, "exchange_failed"):
            await self.actor.exchange(wire, waiting)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.actor._handles, {})


if __name__ == "__main__":
    unittest.main()
