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

The rollback helper stops the current installation and starts a previously prepared installation. Both must belong to the same user and use the same port configuration:

```sh
lf --data-root "$HOME/Library/Application Support/LiliuxFlow new" rollback \
  --previous-data-root "$HOME/Library/Application Support/LiliuxFlow previous" --execute
```

Without `--execute`, the helper shows a rollback plan. Prepare and verify the previous runtime before using it as a rollback target.

`lf uninstall` stops the installation and marks it uninstalled while preserving its data, keys, database, caches and external weights. It is not a command to erase installation data.
