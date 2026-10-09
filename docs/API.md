# API

LiliuxFlow offers local OpenAI-compatible **Chat Completions** through LiteLLM and a custom legacy chat interface through LiliuxFlow Compat. All requests use the calling application's LiteLLM virtual key.

A management-only installation has no inference-ready model until a checkpoint is explicitly attached and verified. The API examples below require a configured model and a caller key granted access to it. If you use an optional Edge deployment, replace the loopback base with your own deployment's API base; include `/v1` once and keep keys out of URL parameters.

## Client settings

| Setting | Default |
| --- | --- |
| Chat Completions API base | `http://127.0.0.1:4000/v1` |
| Chat Completions endpoint | `POST /v1/chat/completions` |
| Legacy chat endpoint | `POST http://127.0.0.1:8001/api/chat` |
| Model | `qwen3.8-flash-next-lily-q4-64k` |
| Authentication | `Authorization: Bearer <application virtual key>` |
| Default total context | 65,536 tokens, input plus output; selected model determines the profile |
| Default output budget | 65,536 tokens, including thinking |

Create application keys and assign model permissions and limits in the [API management dashboard](http://127.0.0.1:4000/ui/). The installation's own caller settings are stored locally in `~/Library/Application Support/LiliuxFlow/secrets/caller.json`. Use an application's virtual key for inference, rather than management credentials.

The supplied example requires no additional Python client package and reads the installation's key without printing it. Run from the repository root:

```sh
uv run --no-project --python 3.12 python scripts/distribution/client_example.py --api openai --stream --prompt "Introduce yourself in one sentence."
uv run --no-project --python 3.12 python scripts/distribution/client_example.py --api legacy --stream --prompt "Introduce yourself in one sentence."
```

Omit `--stream` for a nonstreaming example. Use `--data-root "/path/to/installation"` for another installation. The helper preserves each interface's own wire format. Add `--model qwen3.8-flash-next-lily-q4-128k` or `--model qwen3.8-flash-next-lily-q4-262k` only with that profile explicitly granted to this caller. The API default remains the 64K model.

Profile availability comes from the installed release's trusted registry. A release can enable additional profiles; startup generates its routes and updates the installation's designated caller with only those enabled model names. Other application keys keep their existing model permissions. Select an enabled model by name; there is no request parameter that enables a larger profile.

The 128K model is enabled and has been verified through Chat Completions, legacy SSE and legacy plain text. For an installation caller granted that model, use:

```sh
uv run --no-project --python 3.12 python scripts/distribution/client_example.py --api openai --model qwen3.8-flash-next-lily-q4-128k --stream
```

Its total context is 131,072 tokens; the default output budget is 65,536. The 64K model stays the default. The 262K model is also enabled with a 262,144-token total context. Use `--model qwen3.8-flash-next-lily-q4-262k` with a caller explicitly granted that model; the helper applies its longer bounded timeout. Profile switches and cold cache restores can add delay.

## Optional MTP2 model names

| Model | Total context | Selection |
| --- | ---: | --- |
| `qwen3.8-flash-next-lily-q4-64k` | 65,536 | MTP0; API default |
| `qwen3.8-flash-next-lily-q4-128k` | 131,072 | MTP0 |
| `qwen3.8-flash-next-lily-q4-262k` | 262,144 | MTP0 |
| `qwen3.8-flash-next-lily-q4-mtp2-64k` | 65,536 | Optional MTP2 |
| `qwen3.8-flash-next-lily-q4-mtp2-128k` | 131,072 | Optional MTP2 |
| `qwen3.8-flash-next-lily-q4-mtp2-262k` | 262,144 | Optional MTP2 |

All three MTP2 profiles have been validated and enabled on the tested M5 Max / 128 GiB installation. Availability on another installation depends on its trusted configuration. A fresh source catalog starts with MTP2 disabled; the optional-engine build initially enables only MTP2 64K. Long profiles require separate operator validation and trusted enablement. Each caller also needs an explicit grant for the selected alias; empty or wildcard grants do not authorize MTP2. The API default stays MTP0 64K.

For an installation with the selected profile enabled and granted to your virtual key, send either of these bodies to `POST /v1/chat/completions` with the usual `Authorization: Bearer <application virtual key>` header:

```json
{"model":"qwen3.8-flash-next-lily-q4-mtp2-128k","messages":[{"role":"user","content":"Hello."}],"max_tokens":4096}
```

```json
{"model":"qwen3.8-flash-next-lily-q4-mtp2-262k","messages":[{"role":"user","content":"Hello."}],"max_tokens":4096}
```

Omitting thinking controls preserves checkpoint-default HIGH thinking. Input and output share the selected total context; reasoning counts toward the output budget, whose maximum is 65,536 tokens for every profile. The aliases also select enabled profiles in legacy requests. Request parameters and caller grants cannot enable a disabled profile or override its engine, KV precision or context.

For an explicitly selected UI login user, preview `lf profiles grant-ui-user my-ui-user ctx64k-mtp2 ctx128k-mtp2 ctx262k-mtp2 --dry-run`, then use `--execute` to append only enabled profiles. This models-only update preserves the user's role and limits; sign out and sign in again so a new dashboard session inherits the user models. It does not change ordinary API keys.

## Images in Chat Completions

The OpenAI-compatible interface accepts up to **64 image inputs across the entire conversation**, including images resent in earlier user messages. Use PNG or JPEG data URLs in `image_url` content parts. The whole request remains limited to 8 MiB; image decoding, per-image resizing and the selected model's total context limit also apply. Images are preserved rather than silently removed to fit a limit.

An excessive image count is rejected before the model is loaded. A native validation error keeps an already loaded model available when the guard verifies that the same owned process remains idle and no inference lane was acquired. Other cleanup and cancellation protections remain in effect. The legacy Compat image subset described below retains its separate single-PNG limit.

## Legacy request and response

Send JSON with `model`, `messages` and an optional `stream` flag:

```json
{
  "model": "qwen3.8-flash-next-lily-q4-64k",
  "messages": [{"role": "user", "content": "Hello."}],
  "stream": true,
  "options": {"num_predict": 4096}
}
```

A successful nonstreaming response is **UTF-8 plain text**. Streaming uses `text/event-stream` with JSON `data:` frames. Thinking and visible content are separate fields:

```text
data: {"message":{"thinking":"Let me consider...","content":""},"done":false}

data: {"message":{"thinking":"","content":"Hello!"},"done":false}

data: {"message":{"thinking":"","content":""},"done":true,"done_reason":"stop"}
```

There is one terminal frame. When supplied by the upstream response, terminal metrics may also be included. Errors use a terminal frame with `done: true`, `error` and `error_type` (`protocol` or `unavailable`). Validate HTTP status before consuming a stream, and handle typed terminal errors during it. This custom SSE format requires explicit client adaptation; it is not the native Ollama response format.

## Supported legacy options

| Option | Behavior |
| --- | --- |
| `num_predict` | Positive integer from 1 to 65,536; maps to `max_tokens`. Thinking shares the output budget. Lily clamps output to the remaining total context and reports `length` on exhaustion. |
| `num_ctx` | The 64K model accepts only `65536`; omit this option for larger profiles. Select context through the model name; request-level context overrides are unsupported. |
| `temperature` | Finite number from 0 to 2 |
| `top_p`, `min_p` | Finite number from 0 to 1 |
| `top_k` | Integer from 0 to 2048 |
| `seed` | Integer from 0 to 2⁶³−1 |
| `repeat_penalty` | Finite number from 0.01 to 10; maps to `repetition_penalty` |
| `presence_penalty`, `frequency_penalty` | Finite number from −2 to 2 |
| `stop` | One string or an array of one to four strings; each string is at most 1,024 characters |

Unknown options are rejected. Omitting `think`, or setting it to `true`, preserves checkpoint-default thinking. `think: false` disables thinking for that request; `think: "low"` selects low thinking. Output budgets include reasoning as well as visible content.

`format: "json"` and JSON Schema formats are unsupported by this profile and return an explicit error. Per-request `keep_alive` is unsupported; model lifetime belongs to the supervisor. Audio and video are unsupported. The Compat image subset accepts **one verified PNG per request**, up to 5 MiB, using a PNG data URL or its supported media representation; remote image URLs and other image formats are rejected. Applications should validate generated tool arguments before executing them.

Authenticated `GET /api/capabilities` describes the Compat subset. The diagnostic routes `/api/model-blob-identity` and `/api/ollama-runtime-environment` do not expose GGUF blob identity or claim a native Ollama runtime.

## Cancellation and lifecycle

Client disconnection closes the upstream request through Compat, LiteLLM and the lifecycle guard. Compat uses an internal stream even when assembling a plain-text response, allowing disconnects to cancel pending work. Cancellation is processed at safe inference boundaries; it does not promise immediate GPU preemption.

Use ordinary HTTP client behavior. This Lily integration treats a TCP write-side half-close as cancellation, so clients must keep the request connection open while reading the response.

Manual model unload is refused with HTTP 409 while inference is active. The model is loaded lazily and unloaded after its configured idle interval. The local API scope is Chat Completions; other OpenAI API families or features visible in upstream dashboards are not implied by this model profile.

An interrupted model shutdown can leave a closed owner record. The lifecycle guard verifies that exact owner has exited, that its backend port is closed, and that its lease matches before recovering the queue. It never clears an active or foreign owner. Requests waiting for recovery remain bounded and cancellable.
