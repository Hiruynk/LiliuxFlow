# Portable four-language UI recipe

`build_native.apply_ui` calls `ui_recipe.replay` to apply the selected source changes to pinned upstream trees in an installation's build directory. The active recipe is [ui-recipe-context-profiles.json](../../manifests/distribution/ui-recipe-context-profiles.json). It includes the required versioned patch chain and hash-bound extra files; retain every input referenced by that recipe.

## Schema and scope

The UTF-8 JSON recipe has `schema_version: 1`, descriptive `state` and `coverage_state` fields, and exactly two apps: `litellm` and `llama-swap`. Each binds its upstream commit and archive SHA-256 from [native-sources.json](../../manifests/distribution/native-sources.json).

| Field | Meaning |
| --- | --- |
| `patches[]` | Release-allowlisted repository-relative `path`, exact `sha256` and integer `strip` (default 1); patch targets use full upstream-relative paths. |
| `extra_files[]` | Allowlisted `source`, upstream-relative `target`, exact `sha256` and explicit `before_sha256` for replacements; null denotes a missing preimage. |
| `lockfiles[]` | Final upstream-relative `package-lock.json` paths and exact hashes for each app, checked after replay. |
| `source_tree_sha256` | Optional digest of the complete materialized UI source tree, checked before dependency installation. |

LiteLLM targets stay under `ui/litellm-dashboard/`; llama-swap targets stay under `ui/`. UI recipes cannot change manager Go code, inference, authentication or database behavior. All inputs are preflighted before replay. Absolute/traversal paths, links, special modes, binary/rename patches and silent extra-file overwrites are rejected.

Map authoring differences to the full upstream prefixes and exclude `node_modules`, `.next`, `out` and generated authoring receipts. Include required generated source inputs such as `next-env.d.ts` explicitly. Final locks, runtime catalogs, adapters, locale controls, brand assets and finite dynamic-key maps must be hash-bound. LiteLLM output remains `out/`; llama-swap output remains `internal/server/ui_dist/` before its Go `embed_ui` rebuild.

The tree digest uses SHA-256 of compact JSON (`separators=(",", ":")`) listing relative `path`/`sha256` pairs in path order. Exclusions are `.git`, `node_modules`, `.next`, `out`, `__pycache__` and `tsconfig.tsbuildinfo`. Scoped Git environment and discovery boundaries prevent an ancestor repository from silently skipping patches.

## Rebuild and CPU checks

```sh
uv run --no-project --python 3.12 python -m unittest discover -s scripts/distribution -p test_ui_recipe.py
uv run --no-project --python 3.12 python scripts/distribution/liliuxflow.py build \
  --ui-manifest manifests/distribution/ui-recipe-context-profiles.json --execute
```

The builder binds the replayed recipe, source tree, locks and resulting binaries into release trust. Replay reports `ui_locale_acceptance=false`; source integrity and browser behavior are measured separately.

## Four-language validation

Both dashboards support `en`, `zh-Hant`, `zh-Hans` and `ja`, including login, selectors, ARIA/title, errors, dates, charts, toasts and empty/busy states. Check direct entries in all four mounted catalogs against reachable literal translation calls and finite dynamic maps. Require nonempty values, matching interpolation names and valid plural/rich-slot structure. Record unmapped display consumers separately.

For English, use direct resource lookup rather than fallback-enabled `t` or `exists`. In an isolated framework fixture with fallback disabled, check `en`, `en-US`, `en-GB` normalization and saved `en` preference. Removing a required English key must fail the completeness check even while the other catalogs remain present. See i18next's [resource API](https://www.i18next.com/overview/api) and [fallback rules](https://www.i18next.com/principles/fallback).

Browser checks should toggle all four locales at login and both authenticated dashboards while preserving widget identity, focus, forms, filters, dates, sessions and active streams. Translation must not alter API fields, model names, user input or trigger another inference request.

The native validation runner checks exact `supported_locales` and `browser_locales` lists and a boolean `four_locale_browser_accepted`. Set browser acceptance only from actual observations of that source/bundle. Source-only recipes and fixtures cannot supply it.
