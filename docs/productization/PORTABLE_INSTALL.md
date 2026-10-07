# Native build and source packaging

For normal setup, model selection and service operation, use [Installation](../INSTALLATION.md) and [Operations](../OPERATIONS.md). This document covers reproducible builds, source archives and isolated validation tools.

## Installation and build identity

Setup creates an installation UUID, mode-0700 data directories and mode-0600 credentials. An unchanged setup preserves identity and credentials; conflicting configuration or unrelated data is refused. Source, installation data and external weights use separate paths. Serving validates the installed runtime source view, selected profiles, actual binary hashes and model verification receipt.

The native builder uses the fixed sources and locks in [native-sources.json](../../manifests/distribution/native-sources.json). It replays the five Lily integration patches, the llama-swap token-metric patch and the active [four-language UI recipe](../../manifests/distribution/ui-recipe-context-profiles.json). Archive extraction rejects absolute/traversal paths, links and special files. Pinned Go and Node toolchains are project build dependencies; Rust selection is per process. Node builds the static dashboards and runs Prisma tooling.

```sh
uv run --no-project --python 3.12 python scripts/distribution/liliuxflow.py build \
  --ui-manifest manifests/distribution/ui-recipe-context-profiles.json --execute
```

The build uses locked Python 3.12 environments, records source/tool/notices identities and copies required runtime source into the data root. Restarts use that installed runtime. Native binaries are unsigned and unnotarized unless a separate signing receipt is supplied. The build needs at least 64 GiB free disk; its resource watchdog stops only unchanged owned compiler identities.

## Model verification and lifecycle

[Checkpoint inventory](../../manifests/distribution/checkpoint-files.json) binds 67 runtime files: 56 safetensors shards and 11 metadata files. Full `verify-model` hashes the payload and writes a private receipt. Launch rechecks file fingerprints and active metadata. `verify-model --metadata-only` reports `payload_verified=false` and cannot authorize a model load.

The 64K, 128K and 262K profiles use the same Q4 checkpoint, QSA Split, MTP0 and checkpoint-default high thinking. 64K is the API default. RAM recommendations are advisory; model ownership, trust, disk/cache bounds and cancellation protections remain enforced. A shared instance lease permits one model runner. Manual unload/stop returns busy while requests are active; shutdown confirms the owned child has exited before releasing resources.

## Source archives

`build-release` packages bytes and allowlist policy from the same exact Git commit, embeds source provenance and ignores working-tree changes:

```sh
uv run --no-project --python 3.12 python scripts/distribution/liliuxflow.py build-release \
  --commit "$SOURCE_COMMIT" --output "$NEW_SOURCE_ARCHIVE"
```

Source packages exclude weights, database data, credentials, personal assets, local reports and build caches. The packager checks membership, paths, file types, size caps and LFS pointers, then reads extracted file hashes back. Use the resulting receipt with the same archive for validation.

Pinned Gitleaks and Syft tools scan selected source inputs and produce a source-only SBOM. Keep each output in a new private directory outside the repository:

```sh
uv run --no-project --python 3.12 python scripts/distribution/install_security_tools.py
uv run --no-project --python 3.12 python scripts/distribution/audit.py --commit "$SOURCE_COMMIT" --output "$NEW_AUDIT_DIR"
uv run --no-project --python 3.12 python scripts/distribution/audit.py --history --history-ref origin/main --output "$NEW_HISTORY_AUDIT_DIR"
uv run --no-project --python 3.12 python scripts/distribution/audit.py --archive "$SOURCE_ARCHIVE" --output "$NEW_ARCHIVE_AUDIT_DIR"
```

Tree, reachable history and remote release/CI artifacts require separate review. See [runtime inventory](RUNTIME_INVENTORY.md) for exact built dependency and original-notice inputs.

## Same-archive CPU validation

```sh
uv run --no-project --python 3.12 python scripts/distribution/cleanroom.py \
  --archive "$SOURCE_ARCHIVE" --manifest "$SOURCE_MANIFEST" \
  --work-parent "$PRIVATE_WORK_PARENT" --evidence "$NEW_EVIDENCE_FILE"
```

The owned work parent must be mode 0700 with at least 64 GiB free. The runner verifies checksum, membership, file hashes and source identity, then uses a fresh path containing spaces and Unicode. Tiny synthetic metadata exercises setup identity preservation, build/init plans, unbuilt-runtime refusal, bootstrap backup and uninstall. Its `PASS_CPU_BOOTSTRAP_ONLY` result covers these CPU operations.

## Same-archive native validation

`native_cleanroom.py` has explicit `build`, `verify` and `smoke` actions. Without `--execute`, it prints a plan. Supply the exact archive, source and recipe identities from your reviewed source package:

```sh
uv run --no-project --python 3.12 python scripts/distribution/native_cleanroom.py build --execute \
  --archive "$SOURCE_ARCHIVE" --manifest "$SOURCE_MANIFEST" --work-parent "$PRIVATE_WORK_PARENT" \
  --model-dir "$MODEL_DIR" --run-id "$RUN_ID" --expected-source-commit "$SOURCE_COMMIT" \
  --expected-archive-sha256 "$ARCHIVE_SHA" --expected-ui-recipe-sha256 "$UI_RECIPE_SHA"
uv run --no-project --python 3.12 python scripts/distribution/native_cleanroom.py verify --execute \
  --task-root "$TASK_ROOT" --run-id "$RUN_ID" --expected-source-commit "$SOURCE_COMMIT" \
  --expected-archive-sha256 "$ARCHIVE_SHA" --expected-ui-recipe-sha256 "$UI_RECIPE_SHA"
uv run --no-project --python 3.12 python scripts/distribution/native_cleanroom.py smoke --execute \
  --task-root "$TASK_ROOT" --run-id "$RUN_ID" --expected-source-commit "$SOURCE_COMMIT" \
  --expected-archive-sha256 "$ARCHIVE_SHA" --expected-ui-recipe-sha256 "$UI_RECIPE_SHA"
```

Use the `TASK_ROOT` returned by build. It creates a separate installation and alternate loopback ports 14000/18001/18080/18081/25432; the weights remain an external reference. Build is bounded to 60 minutes and verification to 30 minutes. `--source-candidate` permits source-complete recipe validation with browser acceptance pending; it does not set browser acceptance.

Smoke starts only the isolated installation, sends three bounded own-caller requests (Chat Completions, legacy SSE and legacy plain text), verifies response/child identity and stops its owned services. It has a 15-minute work budget plus bounded cleanup. Schedule model smoke when the shared checkpoint is available; the runner does not stop another installation. Busy or unconfirmed ownership preserves its records for diagnosis. Check that its processes and listeners are gone after cleanup.
