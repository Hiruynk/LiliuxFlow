# Actual native dependency and notice inventory

`runtime_inventory.py` prepares a bounded, private input stage for the pinned Syft tool from an actual native build. It collects dependency metadata and original notices without executing Lily or starting services. Credentials, database data, runtime caches and model weights are excluded. A source-only SBOM describes build inputs; inspect the installed artifacts to determine redistributed content.

The successful builder writes `runtime/dependency-inputs.json`, binding the actual own UUID/source commit/build attempt and the Rust/Go/UI package locks by SHA-256. Release trust hashes that record. The collector requires the exact own UUID/source commit, verifies release trust and those retained lock bytes, and refuses an unrelated build directory. Its output must be new and outside the installation to prevent recursion or accidental private-input capture.

```sh
uv run --no-project --python 3.12 python scripts/distribution/runtime_inventory.py --execute --data-root "$OWN_DATA_ROOT" --build-root "$OWN_BUILD_ATTEMPT" --output "$NEW_PRIVATE_INVENTORY" --installation-id "$OWN_UUID" --source-commit "$CLEAN_OID" --go "$VERIFIED_GO" --cargo "$VERIFIED_CARGO" --cargo-home "$OWN_CARGO_HOME" --tools "$PINNED_SECURITY_TOOLS"
```

Without `--execute`, the command reports plan-only. Collect against the actual installation and matching build records; isolated metadata fixtures cover collector behavior.

Inputs are constrained to actual native binaries and Prisma Node tool, installed Python `.dist-info` identities/requirements/licenses, actual installed UI build dependencies, retained build locks, resolved Rust target metadata and actual Go binary module build-info. Python descriptions/authors/RECORD/entrypoints/`.pth` are not copied; only derived package identity/requirement/license headers are cataloged, with the original metadata SHA retained. Original license/notice files are copied unchanged. Cargo uses [documented metadata output](https://doc.rust-lang.org/cargo/commands/cargo-metadata.html), `--locked --offline --format-version 1 --filter-platform aarch64-apple-darwin`; Go module discovery uses `go version -m`, with network proxies disabled. Cargo paths are confined to the own Lily build source and explicit registry/git cache roots.

The input stage is limited to 512 MiB; individual original notices are bounded to 16 MiB. Symlinks, traversal, overwrites and foreign package-cache paths are rejected. No entire private installation, PostgreSQL directory or model directory is scanned. The existing pinned [Syft](https://github.com/anchore/syft/blob/v1.52.0/README.md) runs offline against this stage and emits CycloneDX with source-relative names.

The component scope is deliberately explicit: Python is actually installed; Go modules come from actual binary build-info; Rust and UI graphs may conservatively include build/optional/dev or unlinked dependencies. System/stdlib linkage and any absent original notice still require final review. Finding notice files for every listed component is not equivalent to complete runtime license acceptance. Original LiliuxFlow source, documentation, configuration and brand artwork are licensed under [Apache-2.0](../../LICENSE); upstream-derived material, dependencies and external models retain their original terms. The collector verifies the selected source's first-party LICENSE and NOTICE before recording that grant. `first_party_license_adopted=true` is separate from `new_third_party_terms_accepted=false` and `final_license_acceptance=false`, which reflects incomplete review of the full runtime.

For a source documentation/license update, `uv run --no-project --python 3.12 python scripts/distribution/notices.py --refresh-first-party` regenerates only the first-party policy and LICENSE/NOTICE records. It requires the official Apache-2.0 text and preserves all original upstream records and unrelated provenance fields. `uv run --no-project --python 3.12 python scripts/distribution/notices.py` verifies the resulting inventory, including the original Lily attribution inside root NOTICE. Neither command refreshes historical receipts or installed release trust.

## Meaningful resource boundaries

The 8 GiB transient bound covers the own build payload, extracted sources, compiler targets and build caches, plus own cache namespaces. Permanent runtime binaries/venvs and the immutable source snapshot are measured separately and are never automatically deleted to satisfy this cache bound. Every build still requires the real 64 GiB free-disk floor.

A resource failure stops only unchanged owned compiler/launcher identities and writes a separate resource-stop snapshot. Measurement failure also stops work safely. No cleanup, cache eviction, global purge or unrelated service stop is performed. Retain source, Go/Cargo/npm dependency caches and original notices until the actual native inventory has been collected. If a build exceeds the cap, inspect the category sizes and resource-stop record before retrying with an appropriate build budget.

## CPU verification

These commands exercise isolated collector and serving-boundary fixtures. Use the locked Compat environment for tests that import FastAPI/httpx:

```sh
uv run --no-project --python 3.12 python -m unittest discover -s scripts/distribution -p test_runtime_inventory.py
uv run --project services/compat --locked --python 3.12 python -m unittest discover -s scripts/distribution -p test_runtime.py
uv run --no-project --python 3.12 python -m unittest discover -s scripts/distribution -p test_native_cleanroom.py
uv run --no-project --python 3.12 python scripts/distribution/runtime_inventory.py
```

CPU metadata fixtures exercise inventory, source trust, boundary and refusal behavior. They do not prove complete native license coverage or UI acceptance.

## Finite missing-notice and system-boundary review

Perform one review of the actual final native `inventory.json` and `sbom.cdx.json`, tied to its exact installation UUID/source commit/native binary hashes and retained dependency-input SHA. Keep an immutable copy of the original collector output; record corrections in a separate review. Do not repeat broad source or model scans to resolve a single missing notice.

For each `missing_original_notice_files` row, inspect only that package's installed metadata or lock-bound source artifact. Record name/version/ecosystem, declared license, source/lock/package hash and one of: original notice located with exact relative path/SHA; declared-license-only with no original file; original LiliuxFlow component covered by the verified first-party Apache-2.0 grant; or unresolved provenance/license. Copy any found original file unchanged into a new reviewed notice supplement. No fetching an unrelated/latest license, broad license inference from a parent package, or automatic third-party grant/term acceptance closes the row.

Then review the finite boundary list:

| Boundary | Exact input to review | Acceptance meaning |
| --- | --- | --- |
| Python stdlib/interpreter | Actual copied interpreter identity and its original version-matched distribution LICENSE | Verify the runtime source; never infer PSF terms solely from the language name. |
| Node/Prisma tool runtime | Trusted Node binary, bundled original Node LICENSE and actual installed Prisma package metadata/notices | Separate its tool/runtime role from the static UI dependency graph. |
| Go runtime and native system libraries | Actual Go build-info plus retained matching Go LICENSE; metadata-only Mach-O link listing | Record system-provided versus redistributed libraries; do not copy macOS frameworks into the package. |
| Rust/system linkage | Locked target Cargo graph, actual native binary identity and metadata-only link listing | A resolved graph can include build/unlinked entries; review omissions and supplied system libraries without claiming exact linkage from package counts. |
| Static UI bundle | Actual recipe/tree/lock/bundle identity and installed build dependency notices | Keep build/dev dependencies labeled conservatively; do not claim all lock entries are runtime code. |

A metadata-only `otool -L` inspection of the **new own native binaries** can record linked system library names without executing Lily. Never use it to inspect or mutate unrelated services. SDK/system-provided components should be recorded as such; an absent redistributed notice remains unresolved until its exact artifact is reviewed.

The reviewer signs a separate bounded assessment: candidate identities; counts by scope; each unresolved package/boundary; original-notice supplement hashes; the verified first-party Apache-2.0 grant; no new third-party terms accepted; and whether the notices cover the intended redistributed material. Collector success, a zero missing-file count or a source-only SBOM alone does not set complete runtime/licensing acceptance.

## Python interpreter and standard-library notices

The collector's `runtime/{compat,litellm}/lib/python3.12/site-packages` roots intentionally match the native Python 3.12 lock. They inventory installed packages, **not** the interpreter/stdlib license boundary.

For the new own installation only, bind `runtime/compat/bin/python` and `runtime/litellm/bin/python` to their release-trust hashes. Review each `runtime/{compat,litellm}/pyvenv.cfg` and the actual matching base-interpreter prefix. A short `-I -S` metadata probe may identify `sys.version`, `sys.base_prefix` and the stdlib location without importing the application or a model. Keep machine-specific base paths private; final evidence records version/artifact identity and relative notice paths instead.

Inspect the version-matched base prefix and stdlib root (`lib/python3.12`) for original `LICENSE`, `LICENSE.txt`, `LICENSE.rst`, `COPYING` and `NOTICE`, plus the Python standalone distribution's root `licenses/`/documented notice inventory if present. Join those with the exact standalone/uv artifact provenance and any bundled native library notices (for example OpenSSL/libffi), rather than assuming site-package metadata covers them. Record an absent/unresolved root notice explicitly. Do not copy the whole stdlib, resolve an unrelated system Python, fetch a latest license, or infer a new first-party grant. Review this boundary against the actual native artifact; site-package inventory does not cover it.
