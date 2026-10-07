# SPDX-License-Identifier: Apache-2.0
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "cloudflare_edge_live_check", Path(__file__).with_name("check-live.py")
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LiveCheckClassificationTests(unittest.TestCase):
    def run_client(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = MODULE.main(arguments)
        return code, json.loads(output.getvalue())

    def test_stream_requires_explicit_inference_key_model_and_endpoint_without_network(self):
        base = ["--base-url", "https://operator.example.invalid", "--stream-smoke"]
        with patch.object(MODULE, "_new_connection") as connection:
            for rest in ([], ["--key-file", "not-read", "--model", "test-model"], ["--allow-inference", "--model", "test-model"], ["--allow-inference", "--key-file", "not-read"]):
                code, report = self.run_client(base + rest)
                self.assertEqual(code, 2)
                self.assertEqual(report["error"], "stream_requires_allow_inference_key_and_model")
            connection.assert_not_called()

    def test_authorized_stream_issues_one_bounded_client_call_without_private_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "caller.key"
            key_file.write_text("synthetic-caller-key", encoding="utf-8")
            synthetic_observation = {"passed": True, "classification": "sse_frames_observed", "output_size_bytes": 123}
            with patch.object(MODULE, "_run_stream_smoke", return_value=synthetic_observation) as stream, patch.object(MODULE, "_run_check") as non_inference:
                code, report = self.run_client(["--base-url", "http://127.0.0.1:4000", "--stream-smoke", "--allow-inference", "--key-file", str(key_file), "--model", "synthetic-model", "--timeout-seconds", "3", "--stream-timeout-seconds", "10"])
                self.assertEqual(code, 0)
                stream.assert_called_once_with("127.0.0.1", 4000, "http", timeout=3, stream_timeout=10, key="synthetic-caller-key", model="synthetic-model")
                non_inference.assert_not_called()
                self.assertEqual(report["checks"], [synthetic_observation])
                self.assertTrue(report["inference_authorized"])
                self.assertNotIn("synthetic-caller-key", json.dumps(report))
                self.assertNotIn("synthetic-model", json.dumps(report))

    def test_static_default_never_makes_inference_calls(self):
        with patch.object(MODULE, "_run_check", return_value={"passed": True}) as check, patch.object(MODULE, "_run_stream_smoke") as stream:
            code, report = self.run_client(["--base-url", "https://operator.example.invalid"])
            self.assertEqual(code, 0)
            self.assertEqual(check.call_count, len(MODULE.STATIC_CHECKS))
            stream.assert_not_called()
            self.assertFalse(report["inference_authorized"])

    def test_inference_options_timeout_and_model_refusals_are_redacted(self):
        with patch.object(MODULE, "_new_connection") as connection:
            for rest, error in ((["--allow-inference"], "stream_options_only_allowed_for_stream_smoke"), (["--timeout-seconds", "31"], "timeout_out_of_bounds"), (["--stream-timeout-seconds", "301"], "timeout_out_of_bounds"), (["--stream-smoke", "--allow-inference", "--key-file", "not-read", "--model", "private-model\nheader"], "invalid_model")):
                code, report = self.run_client(["--base-url", "https://operator.example.invalid"] + rest)
                self.assertEqual(code, 2)
                self.assertEqual(report["error"], error)
                self.assertNotIn("private-model", json.dumps(report))
            connection.assert_not_called()

    def test_stream_connection_closes_without_printing_generated_text(self):
        class Response:
            status = 200
            headers = {"Content-Type": "text/event-stream", "Set-Cookie": "synthetic-private-cookie"}
            chunks = [b'data: {"text":"synthetic-private-output"}\n\n', b'data: [DONE]\n\n', b'']
            def read1(self, size): return self.chunks.pop(0)
        class Connection:
            sock = None
            closed = False
            def request(self, *args, **kwargs): self.call = (args, kwargs)
            def getresponse(self): return Response()
            def close(self): self.closed = True
        connection = Connection()
        with patch.object(MODULE, "_new_connection", return_value=connection):
            result = MODULE._run_stream_smoke("operator.example.invalid", None, "https", timeout=3, stream_timeout=10, key="synthetic-private-key", model="synthetic-private-model")
        self.assertTrue(connection.closed)
        self.assertTrue(result["passed"])
        payload = json.loads(connection.call[1]["body"])
        self.assertEqual(payload["max_tokens"], 64)
        self.assertTrue(payload["stream"])
        serialized = json.dumps(result)
        for value in ("synthetic-private-key", "synthetic-private-output", "synthetic-private-cookie", "synthetic-private-model"):
            self.assertNotIn(value, serialized)

    def test_offline_contract_rejects_html_fallback_and_requires_no_store_probe(self):
        ok, classification = MODULE.evaluate_observation(
            "offline",
            "readiness",
            503,
            {"content_type": None, "cache_control_no_store": True},
        )
        self.assertTrue(ok)
        self.assertEqual(classification, "offline_probe_503")

        ok, classification = MODULE.evaluate_observation(
            "offline",
            "ui",
            503,
            {"content_type": "application/json"},
        )
        self.assertTrue(ok)
        self.assertEqual(classification, "backend_offline_503_non_html")

        ok, classification = MODULE.evaluate_observation(
            "offline",
            "api",
            503,
            {"content_type": "text/html"},
        )
        self.assertFalse(ok)
        self.assertEqual(classification, "offline_html_fallback")

    def test_model_auth_classification_and_explicit_json_key_file_format(self):
        response_headers = {"content_type": "application/json"}
        ok, classification = MODULE.evaluate_observation(
            "online-non-inference",
            "models",
            401,
            response_headers,
            key_supplied=False,
        )
        self.assertTrue(ok)
        self.assertEqual(classification, "unauthenticated_models_401")

        ok, classification = MODULE.evaluate_observation(
            "online-non-inference",
            "models",
            401,
            response_headers,
            key_supplied=True,
        )
        self.assertFalse(ok)
        self.assertEqual(classification, "models_status_mismatch")

        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "caller.json"
            key_file.write_text(
                json.dumps(
                    {
                        "api_key": "synthetic-test-key",
                        "user_id": "synthetic-user",
                        "models": ["synthetic-model"],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(MODULE._read_key_file(str(key_file)), "synthetic-test-key")

    def test_edge_1010_classification_and_header_summary_exclude_private_headers(self):
        headers = MODULE.safe_header_summary(
            {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": "2048",
                "Cache-Control": "no-store",
                "Set-Cookie": "session=private-test-value",
                "Authorization": "Bearer private-test-value",
                "Location": "https://example.invalid/login?code=private-test-value",
            }
        )
        ok, classification = MODULE.evaluate_observation(
            "online-non-inference",
            "api",
            403,
            headers,
            cloudflare_1010=True,
        )
        self.assertFalse(ok)
        self.assertEqual(classification, "cloudflare_1010")
        encoded = json.dumps(headers)
        self.assertNotIn("private-test-value", encoded)
        self.assertNotIn("set-cookie", encoded.lower())
        self.assertNotIn("authorization", encoded.lower())
        self.assertNotIn("https://example.invalid", encoded)
        self.assertTrue(headers["location_present"])

if __name__ == "__main__":
    unittest.main()
