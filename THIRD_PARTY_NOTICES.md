# Third-party notices and source provenance

LiliuxFlow by Diurnoctra integrates a management UI **powered by LiteLLM**, model management by **llama-swap**, and inference by **Lily**. Original LiliuxFlow source code, documentation, configuration and original brand artwork are licensed under [Apache License 2.0](LICENSE), SPDX `Apache-2.0`. [NOTICE](NOTICE) records project attribution and preserves Lily's original NOTICE. Third-party code, upstream-derived material and external models retain their original terms; the first-party grant does not relicense them or grant upstream endorsement or trademark rights.

| Component | Exact source/version | Original notice included |
| --- | --- | --- |
| Lily | `fabiogreter/lily-qwen3.8-flash-next`, `db3f8a7cdb33f1e88b68c6889331abd31098c923`, with five separately pinned safety patches | [Apache-2.0 LICENSE](docs/productization/licenses/lily/LICENSE), [NOTICE](docs/productization/licenses/lily/NOTICE). The original NOTICE includes Perplexity AI attribution and the MLX/MLX-LM notices. |
| LiteLLM | `BerriAI/litellm`, `d09bbae1c6df463e425558f60d460437193635da` (`v1.102.1`) | [Original LICENSE](docs/productization/licenses/litellm/LICENSE), including its MIT text and Enterprise-directory restriction. No blanket relicensing or Enterprise grant is asserted. |
| llama-swap | `mostlygeek/llama-swap`, `fcefa7b7bebc326790937bbebc32ae87a958747c` (`v260`) | [MIT LICENSE.md](docs/productization/licenses/llama_swap/LICENSE.md), preserving Benson Wong's attribution. |
| i18next | `26.4.2`, exact npm lock integrity in the provenance manifest | [MIT LICENSE](docs/productization/licenses/i18next/LICENSE) |
| react-i18next | `17.0.15`, exact npm lock integrity | [MIT LICENSE](docs/productization/licenses/react-i18next/LICENSE) |
| Moment | `2.30.1`, exact npm lock integrity; original and separate display-only locale bundle | [MIT LICENSE](docs/productization/licenses/moment/LICENSE) |
| Go | `1.27.1`, checksum-pinned native build toolchain | [Original BSD LICENSE](docs/productization/licenses/go/LICENSE); this source archive contains no Go toolchain binary. |
| Gitleaks | `8.30.1`, separately checksum-pinned scanner tool | [MIT LICENSE](docs/productization/licenses/gitleaks/LICENSE) |
| Syft | `1.52.0`, separately checksum-pinned SBOM tool | [Apache-2.0 LICENSE](docs/productization/licenses/syft/LICENSE) |
| actions/checkout | Official `v7`, commit `3d3c42e5aac5ba805825da76410c181273ba90b1`; CPU workflow only | [MIT LICENSE](docs/productization/licenses/actions-checkout/LICENSE) |
| Instrument Sans | `@fontsource-variable/instrument-sans` 5.3.0; unchanged Latin variable WOFF2 | [Original SIL Open Font License 1.1](assets/welcome/fonts/OFL.txt), including the Instrument Sans Project Authors copyright; [font provenance](assets/welcome/fonts/provenance.json). |
| Qwen3.8-Flash-Next Lily Q4 | `fabiogreter/Qwen3.8-Flash-Next-lily-q4`, `afde8b8e824c57bfe264c2ef3f9396537249979f` | [Original Qwen Community License 1.0](docs/productization/MODEL_LICENSE.txt). Weights are external and excluded from Git, archives, CI and release artifacts. |

[Original-notice provenance](manifests/distribution/original-notices.json) records the original notice files, upstream paths, pinned sources and file digests. The notice checker verifies those bytes. Model weights are downloaded separately and retain their model license.

The pinned LiteLLM source archive contains an `enterprise/` subtree, but the `enterprise/LICENSE` referenced by its root notice is absent from that archive. This source distribution excludes that subtree. Preserve LiteLLM's original restriction when building or redistributing it.

## Distribution scope

| Material | License and notice handling |
| --- | --- |
| Product source archive | Includes original LiliuxFlow code, upstream integration patches, locale/brand assets and direct-component notices. Native executables and installed dependency trees are built separately. |
| Native build inputs | Pinned toolchains and locked dependency graphs are retrieved during the build. Retain the licenses supplied with the selected artifacts. |
| Installed runtime and UI bundles | Review the exact binary, Python environments, Node/Prisma tool and static bundles before redistributing them. A build dependency list alone does not establish which code is embedded. |
| System components | User-installed PostgreSQL and macOS frameworks are outside this source archive. |

Direct-component notices are included. Complete transitive notices for a separately redistributed native runtime or static bundle require review of that exact artifact; use [runtime inventory](docs/productization/RUNTIME_INVENTORY.md) to collect bounded dependency and original-notice inputs. In particular, Sharp's binary package can carry LGPL obligations for bundled libraries; its Apache build-source notice does not replace those terms.

## Cloudflare edge build tooling

The Worker runtime is first-party source with no bundled third-party runtime library. The pinned development/deployment tool Wrangler 4.147.0 declares `MIT OR Apache-2.0`; its package is downloaded from the official npm distribution and is not vendored in the source archive. Its locked build graph remains in `services/cloudflare-edge/package-lock.json`. The self-hosted Instrument Sans font retains its original OFL and provenance listed above.

## Optional model downloader

The optional downloader uses [huggingface_hub 1.1.4](https://github.com/huggingface/huggingface_hub/tree/v1.1.4), licensed under [Apache-2.0](https://github.com/huggingface/huggingface_hub/blob/v1.1.4/LICENSE). Its dependency graph is locked separately in [services/model-installer/uv.lock](services/model-installer/uv.lock). The SDK and its dependencies are installed on demand and are not vendored in this source archive. Retain their original notices when redistributing that environment. The model checkpoint remains subject to its separate model license.
