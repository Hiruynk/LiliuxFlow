# Operations

Commands below use the `lf` shell function defined in [Installation](INSTALLATION.md), run from the repository root. For a separate installation, add `--data-root "/path/to/installation"` before the subcommand.

## Status and service control

```sh
lf status
lf doctor
lf start
lf stop
```

`status` reports installation configuration and listening ports. `doctor` checks hardware, native tools, disk space, runtime/model verification and port readiness; it exits with code 2 when blocked. A chip or memory size different from the tested M5 Max / 128 GiB configuration produces an advisory warning, not a refusal. Native serving still requires macOS arm64.

`start` runs a macOS user LaunchAgent for the installation and creates its application caller settings. The model is loaded on demand. An idle service with no model resident is normal; the model profile's idle interval is 1,800 seconds. The model runner maintains a shared single-model lease and monitors RAM headroom. The 15 GiB recommendation for 64K/128K and 13 GiB recommendation for 262K are advisory: falling below them logs a warning and does not block startup or stop the model. Keep sufficient memory available for the model and other applications; successful operation on other hardware is not guaranteed. Model ownership, trusted configuration, disk/cache limits and observer-failure handling remain enforced.

Use [model management](http://127.0.0.1:8080/ui/) for model state and lifecycle actions, and [API management](http://127.0.0.1:4000/ui/) for application keys, permissions and usage. Ordinary manual unload is rejected with HTTP 409 while inference is active. `stop` checks the installation's ownership and lifecycle state before removing its LaunchAgent and stopping owned processes; a busy or unconfirmed state prevents the operation. Let active requests finish or cancel them through their clients before trying again.

## Shared MTP2 operations

After the [shared build and installation](INSTALLATION.md#shared-mtp2), explicitly select shared serving when starting it. Reuse the `SHARED_DATA` defined there:

```sh
lf --data-root "$SHARED_DATA" start --mtp2-shared-context
lf --data-root "$SHARED_DATA" status
lf --data-root "$SHARED_DATA" doctor
lf --data-root "$SHARED_DATA" profiles list --mtp2-shared-context
lf --data-root "$SHARED_DATA" profiles info ctx262k-mtp2 --mtp2-shared-context
```

The shared extension serves only the three MTP2 aliases with one resident physical model, two active requests and four pending requests. Changing context within that family does not reload the weights. The original MTP0 routes keep their separate engine and lifecycle. A caller grant selects allowed aliases, not the startup mode; use the shared flag for `profiles grant-owner` and `profiles grant-ui-user` as shown in Installation.

Unload and idle lifetime operate on the whole physical MTP2 runtime. Ordinary unload returns HTTP 409 while any peer is active or waiting; one alias cannot unload another request's model. The 1,800-second idle interval belongs to that physical runtime. Disconnecting one client cancels only its request, and resource release waits for its owned native lifecycle to finish.

Tenant caches stay in resident memory and share the fixed 24 GiB cache/reservation policy; shared disk caching is disabled. Session snapshots, speculative continuations and scratch state also consume memory. Observe actual headroom and swap rather than assuming that one copy of the weights makes additional sessions free.

To stop normally, finish or cancel outstanding requests first:

```sh
lf --data-root "$SHARED_DATA" stop
```

When you explicitly need to cancel work and stop your installation, use:

```sh
lf --data-root "$SHARED_DATA" stop --force
```

Force stop cancels this installation's requests and stops only verified owned processes, with bounded termination and exit checks. PostgreSQL uses its normal fast-stop path after the installation data directory is verified. Credentials, database data, weights and caches are preserved. An uncertain or foreign process identity refuses cleanup; do not remove active lease files or signal unrelated processes.

To change modes, stop the installation before restarting. Plain `start` selects MTP0; `start --mtp2-shared-context` explicitly restores shared mode. Do not assume a generic restart or rollback remembers the shared selection.

## Model catalog and attachment

`lf models list` and `lf models info qwen3.8-flash-next-lily-q4` inspect the fixed catalog offline. The optional download flow requires explicit `--dry-run` (network metadata) or `--execute` (payload download); see [Installation](INSTALLATION.md).

For an installation created with `setup --without-model`, `start` runs the management services and `doctor` checks their readiness automatically. Doctor and status report `model_configured=false` and `inference_ready=false`; doctor also reports `payload_verified=false`. To add a model, stop the installation and run `lf models attach qwen3.8-flash-next-lily-q4 --model-dir "$MODEL_DIR"`. Attachment verifies the inventory, preserves credentials/database/identity and leaves the installation stopped. Restart it explicitly afterward.

## Select a context profile

Use one of the three enabled API model names: `qwen3.8-flash-next-lily-q4-64k`, `qwen3.8-flash-next-lily-q4-128k` or `qwen3.8-flash-next-lily-q4-262k`. They share the checkpoint; 64K remains the default. Grant the chosen model to each application's virtual key. Requests use a bounded queue and profile switching waits for the prior generation's resources to be released. Loading, switching or restoring a cold cache can add delay. See [API](API.md) for request limits.

## Data and logs

The default data directory is `~/Library/Application Support/LiliuxFlow`:

| Location | Purpose |
| --- | --- |
| `install.json` | Installation identity, source/model locations and port configuration |
| `secrets/bootstrap.json` | Management and database credentials |
| `secrets/caller.json` | Installation caller's virtual key and connection settings |
| `logs/` | Installation service, startup and database logs |
| `run/` | Ownership and active-process records |
| `postgres/` | Installation PostgreSQL data |
| `cache/` | Runtime caches |
| `backups/` | Local backup destination and temporary backup work |

The user LaunchAgent's stdout/stderr log is under `~/Library/Logs/LiliuxFlow/`, named for the installation ID. If startup fails, inspect the private startup logs and ownership records. Resolve port conflicts by selecting unused ports for a new installation; do not stop unrelated listeners. `--dry-run` on `start` or `stop` validates and describes the operation without executing it.

Logs and credentials may contain private operational information. Review and redact them before sharing an issue report. Startup uses the installed runtime and does not upgrade dependencies.

## Backup

Choose a new output filename in the installation's existing private backup directory:

```sh
lf backup --output "$HOME/Library/Application Support/LiliuxFlow/backups/manual.tar.gz"
```

For an initialized installation, the backup contains a logical database snapshot, installation settings and required credentials. Before initialization, it contains settings and bootstrap credentials only. Weights and caches are excluded. The archive is **not encrypted**; it is created with mode 0600 in a private directory. Store a copy according to your own backup policy and protect it as you would the credentials it contains. Existing output files are not overwritten.

## Restore to a separate installation

Prepare a new installation with its own data directory, build its runtime, verify the model and initialize its empty database. Use alternate ports when the original installation remains present. Stop the target installation before restoring:

```sh
lf --data-root "$HOME/Library/Application Support/LiliuxFlow restored" stop
lf --data-root "$HOME/Library/Application Support/LiliuxFlow restored" restore \
  --archive "$HOME/Library/Application Support/LiliuxFlow/backups/manual.tar.gz" --execute
lf --data-root "$HOME/Library/Application Support/LiliuxFlow restored" doctor
lf --data-root "$HOME/Library/Application Support/LiliuxFlow restored" start
```

Restore requires an independently initialized target with no application records. It imports into that target, preserving the source database and external model files. The target keeps its installation identity, data path and PostgreSQL owner credentials; restored application credentials are carried over as needed. `restore` without `--execute` shows a plan.

## Upgrade and rollback

Prepare an upgrade in a new data directory instead of overwriting an existing runtime. Keep the previous installation and a backup until the replacement is working. Multiple installations can reference one external model directory, but only one model runner may hold the model lease at a time.

Use a separate source checkout as well as a new data directory, so building an upgrade does not change the running installation's source bindings. Back up the previous installation and restore into the independently initialized replacement if you need to carry over application data. Both data directories use the same default ports unless you explicitly configure alternatives; they cannot run on those ports together. Separate ports do not permit two copies of the model to hold the shared lease.

The rollback helper stops the current installation and starts a previously prepared installation in the default mode. Both must belong to the same user and use the same port configuration:

```sh
lf --data-root "$HOME/Library/Application Support/LiliuxFlow new" rollback \
  --previous-data-root "$HOME/Library/Application Support/LiliuxFlow previous" --execute
```

Without `--execute`, the helper shows a rollback plan. Prepare and verify the previous runtime before using it as a rollback target. The helper does not preserve the shared MTP2 startup selection. To return to a shared installation, stop the current one and explicitly start the verified previous shared installation instead:

```sh
lf --data-root "$HOME/Library/Application Support/LiliuxFlow new" stop
lf --data-root "$HOME/Library/Application Support/LiliuxFlow previous" start --mtp2-shared-context
lf --data-root "$HOME/Library/Application Support/LiliuxFlow previous" status
lf --data-root "$HOME/Library/Application Support/LiliuxFlow previous" doctor
```

Keep caller grants and connection settings aligned with the chosen installation. Do not overwrite trust receipts or runtime files to make an older installation accept new source.

`lf uninstall` stops the installation and marks it uninstalled while preserving its data, keys, database, caches and external weights. It is not a command to erase installation data.
