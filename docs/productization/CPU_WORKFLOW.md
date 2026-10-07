# CPU checks and source inventory

[Distribution CPU guards](../../.github/workflows/distribution-cpu.yml) and [Cloudflare Edge CPU](../../.github/workflows/cloudflare-edge-cpu.yml) run source checks on pull requests, default-branch pushes and manual dispatches. They use a pinned checkout action, read-only repository permissions and disabled credential persistence. Tests use synthetic data and local fixtures; model downloads, inference, deployments and artifact publication are separate explicit operations.

Run the distribution checks from the repository root:

```sh
uv run --no-project --python 3.12 python scripts/distribution/notices.py
for suite in test_notices.py test_ui_recipe.py test_runtime_inventory.py test_public_screenshot_policy.py; do
  uv run --no-project --python 3.12 python -m unittest discover -s scripts/distribution -p "$suite"
done
```

Tests that import the Compat adapter require its locked Python environment. To run those tests with the project dependencies:

```sh
uv run --project services/compat --locked --python 3.12 python -m unittest discover -s scripts/distribution -p 'test_*.py'
```

The optional downloader SDK fixtures use their separate locked helper environment:

```sh
uv run --locked --project services/model-installer python -m unittest discover -s scripts/distribution -p test_optional_model_sdk.py
```

This fixture command uses simulated HTTP and does not download the checkpoint. The SDK fixtures must be run here in addition to the Compat suite; an unavailable-SDK skip in another environment is not a pass.

Run the Edge CPU checks with Node 22 or newer:

```sh
node --test services/cloudflare-edge/test/*.test.mjs
node scripts/cloudflare-edge/test-frontend.mjs
uv run --no-project --python 3.12 python scripts/cloudflare-edge/test_check_live.py
```

The workflow files list the exact hosted-runner subset. Archive/bootstrap fixtures retain the real 64 GiB free-disk requirement. Native Rust/Metal builds, user LaunchAgents, native installation checks and model serving require a compatible Mac; source tests cannot establish those results. Four-language rendering and preservation of forms, focus and active streams also need browser checks.

## Source scans and SBOM

The separately pinned Gitleaks and Syft tools are described in [native packaging](PORTABLE_INSTALL.md). Their supplied tool lock targets Darwin arm64. Scan the selected source tree, source archive and relevant Git history separately. A source-only SBOM inventories supplied inputs; inspect the exact built runtime and bundles before redistributing them.

`audit.py --private-findings PATH` can write detailed redacted findings into a new mode-0600 file in an owned mode-0700 directory outside the product repository. Regular reports expose source-relative locations and digests. Model weights, database files, credentials and personal assets must stay outside scan staging and release archives.
