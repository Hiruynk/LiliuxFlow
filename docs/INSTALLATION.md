# Installation

LiliuxFlow runs natively on macOS. Native serving requires **macOS arm64 (Apple Silicon)**. The tested baseline is a Mac Studio with Apple M5 Max and 128 GiB unified memory on macOS 27.0. A different chip or RAM capacity produces an advisory warning; it does not block a compatible host.

## Prepare the host

Make the following tools available on `PATH` before building:

| Tool | Version / purpose |
| --- | --- |
| uv | 0.12.19 in the supplied build manifest; manages Python environments and locked dependencies |
| Rust / Cargo | Rust 1.97.0, with the native Apple toolchain needed for Rust/Metal builds |
| PostgreSQL | Version 17, including `postgres`, `initdb`, `pg_ctl`, `psql`, `pg_dump` and `pg_restore` |
| Python | 3.12, selected through uv in the examples |

The source builder obtains checksum-pinned Go 1.27.1 and Node 24.19.0 for this installation. Node is used to build the static interfaces and run Prisma tooling. The services use native binaries and Python; no separate Node web server is required. The exact source and toolchain selections are in [native-sources.json](../manifests/distribution/native-sources.json).

## Get the source

Clone the repository and work from its root:

```sh
git clone https://github.com/Hiruynk/LiliuxFlow.git
cd LiliuxFlow
```

You can also extract an official LiliuxFlow source release asset containing `SOURCE_COMMIT.json`. GitHub’s automatic **Source code (zip/tar.gz)** archives omit that record and cannot be used directly for the default build. The builder checks the source identity before downloading or compiling dependencies. The repository is public and can be cloned without signing in.

## Choose a model setup

Run from the repository root. Define `lf` for the current shell:

```sh
lf() {
  uv run --no-project --python 3.12 python scripts/distribution/liliuxflow.py "$@"
}
export MODEL_DIR="$HOME/Models/Qwen3.8-Flash-Next-lily-q4"
```

Models are optional. Choose one of these setup routes before building. Model catalog commands are offline:

```sh
lf models list
lf models info qwen3.8-flash-next-lily-q4
```

The catalog references the existing [67-file checkpoint inventory](../manifests/distribution/checkpoint-files.json) and fixed upstream revision. The three context profiles share one checkpoint. The Lily Q4 download is approximately **98.3 GiB**; review the [Qwen Community License 1.0](productization/MODEL_LICENSE.txt) on the [upstream model page](https://huggingface.co/fabiogreter/Qwen3.8-Flash-Next-lily-q4).

### Use an existing checkpoint

Keep the full configuration, tokenizer, chat template, index, license and tensor shards together in one external directory. Setup references those files in place:

```sh
lf setup --model-dir "$MODEL_DIR"
```

The source, model and installation data directories must be separate, without symlinked paths. An unchanged setup preserves configuration and credentials; conflicting settings are refused.

### Download the manifest-listed checkpoint

```sh
mkdir -p "$(dirname "$MODEL_DIR")"
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --dry-run
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --execute
lf setup --model-dir "$MODEL_DIR"
```

The command creates only the parent directory; the downloader creates the checkpoint directory. `--dry-run` explicitly contacts Hugging Face for download metadata. `--execute` downloads the inventory files at the fixed revision using the [separately locked Hugging Face downloader](../services/model-installer/uv.lock). Downloading does not load the model or attach it to an existing installation. The catalog and normal build/status operations do not download weights.

### Start with management services only

```sh
lf setup --without-model
```

This creates an installation without a model path. Build and initialize it normally, omitting `verify-model`. `initialize`, `start` and `doctor` select the management-service path automatically. Doctor and status distinguish `model_configured`, `controlplane_ready` and `inference_ready`. Management services can then run; attaching and verifying a model enables inference. Until then, `inference_ready` and `payload_verified` remain `false`.

To add an existing or downloaded checkpoint later, stop this installation and attach it explicitly:

```sh
lf stop
lf models attach qwen3.8-flash-next-lily-q4 --model-dir "$MODEL_DIR"
lf doctor
lf start
```

Attach requires a stopped installation and verifies the complete inventory. It preserves installation identity, credentials and database data. Start the services after the attachment; attach itself does not restart or load the model.

## Storage and memory

Allow **at least 64 GiB of free build space in addition to model storage**, with further room for runtime caches. This disk floor is enforced. The enabled profiles share one Q4 checkpoint: 64K (default), 128K and 262K. They preserve checkpoint-default high thinking, QSA Split and MTP0.

Recommended RAM headroom is 15 GiB for 64K/128K and 13 GiB for 262K, with a target near 24 GiB. Low headroom produces warnings; ownership, cancellation, trust and disk/cache limits remain enforced.

## Build and start

After choosing a setup route, build and start the installation. For a model-free installation, omit `lf verify-model`:

```sh
lf build --ui-manifest manifests/distribution/ui-recipe-context-profiles.json --execute
lf verify-model
lf initialize --execute
lf doctor
lf start
lf status
```

| Step | Result |
| --- | --- |
| `setup` | Creates identity, private credentials and configuration; checks model metadata when a model is supplied |
| `build --execute` | Builds pinned upstream sources, applies the supplied integration patches and assembles the native runtime and dashboards |
| `verify-model` | Verifies all downloaded model files against the selected inventory |
| `initialize --execute` | Initializes this installation's PostgreSQL database and schema |
| `doctor` | Checks hardware, tools, disk, ports and runtime readiness and configured-model verification |
| `start` | Starts the user LaunchAgent and local service chain; prepares the installation's caller key |
| `status` | Reports installation state and which configured ports are listening |

`build` and `initialize` without `--execute` show plans. Full `verify-model` is required for model serving. The `--metadata-only` verification and `--controlplane-only` diagnostics for an attached model do not authorize a model load. Model-free setup selects management operation without those flags.

The default data directory is `~/Library/Application Support/LiliuxFlow`. Management login details are in its private `secrets/bootstrap.json`; application connection settings are in `secrets/caller.json`. Read these files locally. An unchanged setup preserves existing credentials and data; a setup with conflicting settings refuses to overwrite them.

Open [API management](http://127.0.0.1:4000/ui/) or [model management](http://127.0.0.1:8080/ui/). After a model is attached and verified, use [the included client](API.md) to send a request. Model loading is lazy, so the first request includes loading time.

## Shared MTP2

Shared mode uses one resident Q4 model for the 64K, 128K and 262K MTP2 aliases, with at most two active requests and four pending requests. Each request has its own context limit, session and cancellation lifecycle. The build and start selection are explicit; the original MTP0 installation remains the default.

Use the `lf` function and `MODEL_DIR` defined above. This is an alternative **fresh installation** using the complete existing checkpoint in place. A new data directory still uses the same default ports: if an existing stack is running, either add the alternate `--port` settings from the next section to `setup`, or stop the old stack during a planned handover before starting the new one. Build before initializing the database.

```sh
SHARED_DATA="$HOME/Library/Application Support/LiliuxFlow shared"
lf --data-root "$SHARED_DATA" setup --model-dir "$MODEL_DIR"
lf --data-root "$SHARED_DATA" build \
  --optin-engine latest13f-defer-pc123-mtp2-shared-context-v1 \
  --ui-manifest manifests/distribution/ui-recipe-mtp2-shared.json --execute
lf --data-root "$SHARED_DATA" verify-model
lf --data-root "$SHARED_DATA" initialize --execute
lf --data-root "$SHARED_DATA" doctor
lf --data-root "$SHARED_DATA" start --mtp2-shared-context
lf --data-root "$SHARED_DATA" status
lf --data-root "$SHARED_DATA" profiles list --mtp2-shared-context
lf --data-root "$SHARED_DATA" profiles info ctx128k-mtp2 --mtp2-shared-context
lf --data-root "$SHARED_DATA" profiles grant-owner ctx128k-mtp2 --mtp2-shared-context --dry-run
lf --data-root "$SHARED_DATA" profiles grant-owner ctx128k-mtp2 --mtp2-shared-context --execute
lf --data-root "$SHARED_DATA" profiles grant-owner ctx262k-mtp2 --mtp2-shared-context --execute

uv run --no-project --python 3.12 python scripts/distribution/client_example.py \
  --data-root "$SHARED_DATA" --api openai \
  --model qwen3.8-flash-next-lily-q4-mtp2-128k --stream
uv run --no-project --python 3.12 python scripts/distribution/client_example.py \
  --data-root "$SHARED_DATA" --api openai \
  --model qwen3.8-flash-next-lily-q4-mtp2-262k --stream
```

Startup creates the designated installation caller; grants append only the selected enabled aliases to that caller. Other application keys need their own exact model grants in API management. Empty or wildcard grants do not authorize MTP2. Parallel limits on each key still apply; to demonstrate two callers, use two explicitly authorized application keys, or one key whose parallel allowance supports two requests.

To authorize one dashboard login user, replace `my-ui-user` with that user's ID:

```sh
lf --data-root "$SHARED_DATA" profiles grant-ui-user my-ui-user ctx128k-mtp2 ctx262k-mtp2 \
  --mtp2-shared-context --dry-run
lf --data-root "$SHARED_DATA" profiles grant-ui-user my-ui-user ctx128k-mtp2 ctx262k-mtp2 \
  --mtp2-shared-context --execute
```

Sign out and sign in again to refresh the user's session models. This preserves the user's role and limits and does not change other application keys. Use application virtual keys for inference; management credentials belong in the management tools.

An already built installation cannot be overwritten with `build`. Prepare a separate source checkout and data directory for an upgrade, then follow [Upgrade and rollback](OPERATIONS.md#upgrade-and-rollback) for backup, restoration and handover. See [Shared MTP2 operations](OPERATIONS.md#shared-mtp2-operations) for stopping or changing modes.

## Separate installations and port conflicts

Use a dedicated data directory and choose all alternate ports before initialization if the defaults are occupied:

```sh
lf --data-root "$HOME/Library/Application Support/LiliuxFlow test" setup \
  --model-dir "$MODEL_DIR" \
  --port litellm=14000 --port compat=18001 --port guard=18080 \
  --port manager=18081 --port postgresql=25432
```

Pass the same `--data-root` **before the subcommand** for each later installation command, and pass it to the client example too. All five ports must be distinct and between 1024 and 65535. The installer checks occupied ports; it does not stop unrelated processes. `configure --port NAME=PORT` can adjust ports only while an installation is uninitialized and stopped.

Separate installations can reference the same read-only model directory. A shared model lease prevents two LiliuxFlow model runners from loading it simultaneously. See [Operations](OPERATIONS.md) before upgrading or switching installations.
