# HTTP readiness diagnostics

Startup readiness uses explicit per-service contracts. LiteLLM `/health/readiness` requires JSON with a healthy status and connected configured database. Only manager/guard `GET /health` accepts HTTP 200, `text/plain` and exact `OK`; HTML, other text, trailing newlines, redirects and administration routes do not meet that rule. Management APIs retain strict JSON parsing.

`agent.http_json` records stage, HTTP status, normalized Content-Type, body length, JSON decode success, exception class and numeric errno. It excludes response bodies, request headers, URLs and credentials. An HTTP 200 with undecodable JSON is recorded as an HTTP response whose decoding failed. Automatic control redirects are rejected to prevent credentials crossing origins.

`wait_http` uses the same helper and per-service credentials and writes safe startup fields to the installation's private `logs/startup-http.jsonl`. For a bounded comparison with an already running installation, use:

```sh
uv run --no-project --python 3.12 python scripts/distribution/readiness_probe.py \
  --data-root "$DATA_ROOT" --service litellm --output "$NEW_PRIVATE_REPORT" --timeout 3
```

Choose `litellm`, `manager` or `guard`. The probe makes at most two observations with a socket timeout of at most five seconds and requests no inference. Its output path must be new. Service readiness and model payload verification are separate states.

For an intentional source-only update of a stopped, unregistered installation, `prepare_runtime_source.py --data-root "$DATA_ROOT" --commit "$SOURCE_COMMIT"` verifies the existing binaries/tools and the exact source snapshot, then creates a versioned runtime source view and rebinds its private source hashes. It preserves the installation's credentials, database, identity and ports. Metadata fingerprints do not substitute for full tensor verification.

The CPU fixtures in `test_http_diagnostics.py` exercise transport, status, decoding and redaction. See Python's [urllib.request](https://docs.python.org/3.12/library/urllib.request.html), [urllib.error](https://docs.python.org/3.12/library/urllib.error.html) and [JSON](https://docs.python.org/3.12/library/json.html) documentation for the underlying response and error behavior.
