# Cloudflare edge acceptance runner

The standard-library runner `check-live.py` writes one redacted JSON report to
stdout and does not save reports. It uses the product User-Agent
`LiliuxFlow-EdgeAcceptance/0.1`, sends an `Accept` header, and verifies TLS
certificates normally. It records status, a small safe response-header allowlist,
content length, and timings. It never records credentials, cookies, response
bodies, or prompt text. A `403` HTML response is classified as a possible
Cloudflare 1010 only from a bounded prefix when `Content-Length` proves the
prefix is not the whole response.

The default mode is static. Supply your own explicit endpoint; a source checkout
does not select a deployment or run these network checks automatically.
Run static checks without a key:

    python3 scripts/cloudflare-edge/check-live.py --base-url https://<staging-worker>.workers.dev --mode static

Run online non-inference checks without a key to verify the expected `/v1/models`
401, or supply an existing caller virtual key file (JSON `api_key` field or
plaintext) to verify the authenticated 200:

    python3 scripts/cloudflare-edge/check-live.py --base-url https://<staging-worker>.workers.dev --mode online-non-inference
    python3 scripts/cloudflare-edge/check-live.py --base-url https://<staging-worker>.workers.dev --mode online-non-inference --key-file <private-caller-key.json>

Run offline checks when your backend is already
unavailable. The runner itself does not stop or restart services:

    python3 scripts/cloudflare-edge/check-live.py --base-url https://<staging-worker>.workers.dev --mode offline

The stream smoke makes one inference request only with `--allow-inference`, an
existing caller virtual key file, and an explicit model ID. Use the model and
permissions configured in your installation. The client does not own the server's
GPU, change models, stop services, or manage the server's lifecycle.

    python3 scripts/cloudflare-edge/check-live.py --base-url https://<your-worker>.workers.dev --stream-smoke --allow-inference --key-file <private-caller-key.json> --model <configured-model-id>

The stream report contains first-chunk timing, SSE frame/read counts, total
response bytes, and status without retaining or printing generated text. The
request has a fixed 64-token output budget, a 1 MiB response limit, and a default
180-second stream deadline (configurable from 1 to 300 seconds). Closing the
connection on a timeout or interruption cancels the client's request. Observed
SSE frames establish streaming transport, not model payload or quality validation.

Run the CPU-only classification and input tests with:

    python3 scripts/cloudflare-edge/test_check_live.py
