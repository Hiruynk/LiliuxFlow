# Legacy wire contract v1

This document defines the supported request and response wire contract. Product CPU contract tests use explicit settings and synthetic callers; serving uses the portable factory with independently verified release trust.

## Routes

| Method | Path | Transport commitment |
|---|---|---|
| POST | `/api/chat` | Legacy request fields; plain-text nonstream success or custom SSE stream |
| GET | `/api/stream-test` | Ten numbered SSE deltas, then terminal; no model call |
| GET | `/api/capabilities` | `anif_llm_gateway_v2` transport version and truthful backend capabilities |
| GET | `/api/model-blob-identity` | Preserve route and old keys; no invented GGUF digest for safetensors |
| GET | `/api/ollama-runtime-environment` | Preserve route; Lily-only service reports `source:not_applicable` and null values |

## `/api/chat`

Declared request fields are `model`, `messages` (`List[Dict]`), `stream` (default true), `options`, `think`, `format`, `keep_alive`, and `media`. No top-level legacy `tools` field is declared. Media and format validation occur before model preflight in the reference.

- With `stream=false`, a successful answer is UTF-8 `text/plain; charset=utf-8` containing only `message.content`. It is never an OpenAI completion object or JSON envelope.
- With `stream=true`, each event is `data: <JSON>\n\n`. A delta is `{"message":{"thinking":"...","content":"..."},"done":false}`. Both message keys appear even if one is empty. Normal completion emits one terminal frame with `done:true`, empty message, `done_reason`, and only known metrics. There is no `[DONE]`, OpenAI `choices` payload, or NDJSON.
- A generation failure yields one typed terminal (`error_type` `protocol` or `unavailable`); nonstream uses HTTP 400/503 JSON, and an already-started stream uses the same shape inside SSE. Disconnect cancellation closes the upstream async stream and emits no fake success terminal.
- `format="json"` and JSON Schema are valid legacy requests after bounded validation, but Lily's source currently does not implement constrained JSON modes. The new adapter must report an explicit unsupported protocol error. It may not discard `format`, alter the prompt to simulate a guarantee, or switch backends silently.
- Unknown Ollama options and non-null `keep_alive` have no presumed Lily equivalent and return explicit unsupported errors. Audio and video remain unsupported; no fallback to another model is permitted.
- New ingress auth uses the caller's LiteLLM virtual key on every inference. The adapter may not use a shared/master inference key or call Lily directly. New 401/403/429 responses are an explicit security increment at the new endpoint.

This endpoint preserves the documented custom wire format; it is not the native Ollama API.
