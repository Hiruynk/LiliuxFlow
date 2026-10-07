# Architecture

LiliuxFlow integrates native inference, API access management and model process management on one Apple Silicon Mac. Its original service code coordinates these components and adapts their protocols.

## Request path

```text
Application with its own LiteLLM virtual key
    │
    ├── Chat Completions → LiteLLM :4000 ───────────┐
    └── Legacy chat → Compat :8001 → LiteLLM :4000 ┤
                                                   ▼
                                     LiliuxFlow lifecycle guard :8080
                                                   │
                                            llama-swap :8081
                                                   │
                                              Lily child

LiteLLM → PostgreSQL :15432
User LaunchAgent → LiliuxFlow supervisor → native services
```

All listeners bind to `127.0.0.1`. The default external application endpoints are LiteLLM's `/v1/chat/completions` and Compat's `/api/chat`. The manager backend sits behind the lifecycle guard. PostgreSQL stores LiteLLM's application and management data.

## Optional Cloudflare Edge

The optional edge deployment serves the welcome page from Worker Static Assets and forwards approved backend routes through a VPC Service scoped to HTTP loopback port 4000:

```text
Browser / API client → Cloudflare Worker
    ├── Welcome page → Static Assets
    └── Approved backend routes → scoped VPC Service
                                      │
                               existing Tunnel
                                      │
                               LiteLLM 127.0.0.1:4000
```

Static assets remain available while the local backend is offline. Welcome-page backend actions trigger a readiness check on demand; the check does not run inference. The proxy preserves caller authorization and SSE streaming. This deployment exposes only the selected LiteLLM routes through the port-4000 service. Workers VPC is beta; see [Cloudflare Edge](cloudflare-edge.md) for configuration, validation and rollback.

## Responsibilities and sources

| Component | Responsibility | Main source |
| --- | --- | --- |
| Lily | Native Rust/Metal inference for the selected Q4 checkpoint | [Upstream Lily](https://github.com/fabiogreter/lily-qwen3.8-flash-next) |
| LiteLLM | API management, virtual keys, model permissions, usage and limits | [Upstream LiteLLM](https://github.com/BerriAI/litellm) |
| llama-swap | Model process loading, idle lifetime and management interface | [Upstream llama-swap](https://github.com/mostlygeek/llama-swap) |
| Native installer/builder | Isolated installation identity, credentials, reproducible runtime assembly | [liliuxflow.py](../scripts/distribution/liliuxflow.py), [build_native.py](../scripts/distribution/build_native.py) |
| Installation supervisor | Native PostgreSQL/services, configurations, caller setup and user launchd | [agent.py](../scripts/distribution/agent.py) |
| Model runner | Controlled Lily arguments/environment, single-model lease, RAM monitor and owned-child lifecycle | [model_runner.py](../scripts/distribution/model_runner.py) |
| Lifecycle guard | Coordinates inference admission and lifecycle mutation with a shared lock; rejects busy unload | [llama_guard.py](../services/compat/src/compat_api/llama_guard.py) |
| Compat adapter | Caller-authenticated legacy requests, option mapping, plain text and thinking/content SSE, disconnect handling | [app.py](../services/compat/src/compat_api/app.py) |
| Operations tools | Ownership checks, verification, logical backup and isolated restore | [ownership.py](../scripts/distribution/ownership.py), [trust.py](../scripts/distribution/trust.py), [backup_native.py](../scripts/distribution/backup_native.py) |

LiliuxFlow's UI integration adds branding, English/Traditional Chinese/Simplified Chinese/Japanese catalogs, language controls and display adapters to the upstream dashboards. Language preferences carry only locale values. They do not translate API fields, model names or application input. Homepage theme preferences are separate from personal management UI settings.

## Serving profile

[The default profile](../profiles/distribution/safe64k.json) defines public model name `qwen3.8-flash-next-lily-q4-64k`, 65,536-token total context, checkpoint-default high thinking, QSA Split and zero MTP drafts. The finite [context registry](../profiles/distribution/context-registry.json) also defines 131,072- and 262,144-token profiles; 64K, 128K and 262K are enabled; 64K remains the API default. All profiles use the same Qwen3.8-Flash-Next Lily Q4 checkpoint. The default output budget is 4,096 tokens and the model idle interval is 1,800 seconds.

The model runner uses the installation's verified runtime and external weights. It allows one model runner through a shared lease, checks owned child processes before signaling them, and monitors resource headroom. llama-swap owns demand loading and idle unloading; the LiliuxFlow guard coordinates these lifecycle actions with active requests. These checks support local service operation and are not a reservation of physical RAM or a distributed scheduler.

## Optional model catalog

[Native source metadata](../manifests/distribution/native-sources.json) contains a typed optional model entry that references the existing [checkpoint inventory](../manifests/distribution/checkpoint-files.json). `models list` and `models info` read it offline. Explicit install commands use a separate locked Hugging Face downloader and the fixed revision; downloading does not attach or load the model.

An installation may be created without a model. The supervisor can then run the management services while reporting model configuration, control-plane readiness and inference readiness separately. Explicit attachment requires a stopped installation and full inventory verification, preserves identity/credentials/database state, and leaves startup to the operator. All enabled context profiles share the one attached checkpoint.

## Service-layer integration patches

The native build replays five Lily patches listed in [native-sources.json](../manifests/distribution/native-sources.json):

| Patch | Integration behavior |
| --- | --- |
| [Durable prefix log redaction](../scripts/distribution/patches/lily/01-0001-redact-durable-prefix-divergence.patch) | Replaces durable-prefix content in divergence logging with bounded metadata and counters |
| [Early queue/prefill cancellation](../scripts/distribution/patches/lily/02-0001-lily-queue-prefill-early-cancel-v3.patch) | Observes disconnects before response headers, while queued and during prefill; cancellation respects coherent GPU chunk boundaries |
| [First prefill progress](../scripts/distribution/patches/lily/03-0001-first-prefill-progress-v4.patch) | Adds metadata for the first completed prefill chunk to support service observability |
| [Effective QSA dispatch](../scripts/distribution/patches/lily/04-0005-r1-effective-qsa-dispatch.patch) | Exposes effective QSA route selection and sanitized dispatch labels in the Rust wrapper |
| [Cancelled session handling](../scripts/distribution/patches/lily/05-cancelled-session-no-lru-release.patch) | Drops cancelled decode-session cache state instead of returning it through the normal reusable LRU path |

These are local service, lifecycle and observability changes. Lily supplies the inference algorithms and Metal kernels. The distribution also applies a [llama-swap token-metric patch](../scripts/distribution/patches/llama-swap/01-manager47-reported-token-metrics.patch) to integrate reported token metrics with its activity storage. The dashboard source recipe is [ui-recipe-context-profiles.json](../manifests/distribution/ui-recipe-context-profiles.json).

## Identity, cancellation and distribution

Applications authenticate with their own virtual keys at LiteLLM. Compat retains that caller authorization when forwarding requests and checks access to the configured alias. Internal management credentials remain separate from application keys. When a client disconnects, the adapter closes upstream work; nonstreaming legacy output is assembled from an internal stream so cancellation remains observable.

Source manifests and dependency locks describe the runtime assembly. Model weights are obtained separately and referenced in place. Installation credentials, database data, caches and personal assets are outside the product source package. LiliuxFlow's original code and project-owned assets use [Apache License 2.0](../LICENSE); upstream code and models retain their own licenses and attribution in [third-party notices](../THIRD_PARTY_NOTICES.md).
