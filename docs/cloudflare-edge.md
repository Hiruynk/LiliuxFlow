# Optional Cloudflare Edge

The optional Worker serves the welcome page from Static Assets and proxies approved API/dashboard routes through a scoped Workers VPC Service. Configure the deployment with your own Cloudflare resources. The native services keep their loopback listeners.

```text
Browser / API client → Worker
    ├── Welcome page → Static Assets
    └── Approved backend routes → HTTP VPC Service
                                      │
                                   Tunnel
                                      │
                               LiteLLM loopback :4000
```

| Request | Handling |
| --- | --- |
| `/` and `/liliuxflow-welcome/*` | Worker Static Assets |
| `GET /_liliuxflow/backend/ready` | Bounded readiness probe through the VPC Service |
| `/v1`, `/v1/*`, `/api`, `/api/*`, `/ui`, `/ui/*` | Backend proxy through the VPC Service |
| Approved root-level LiteLLM UI dependencies | Backend proxy through the VPC Service |

[The route allowlist](../services/cloudflare-edge/src/routes.mjs) matches exact paths and complete namespace segments. Its compact `workerFirstPatterns()` configure Static Assets execution within Cloudflare's rule limit. An unapproved path goes to assets without a SPA fallback; backend failures cannot fall back to the welcome page.

## Request behavior

The readiness route calls LiteLLM `/health/readiness` with a 2500 ms deadline. A healthy response returns empty 204; health failure, connection exception or deadline returns empty 503. Both use `Cache-Control: no-store`; backend payloads and exception details are discarded. It requests no inference.

On the generated edge welcome page, selecting a backend action triggers this probe with a 3000 ms browser deadline. Loading or hovering over the page does not probe. Four-language offline messages use the existing announcement toast. The source/native welcome page keeps its ordinary backend links.

The proxy forwards caller authorization and cookies only along approved backend routes and streams response bodies transparently, including SSE. Client cancellation closes upstream work. The Worker preserves backend status, response headers, multiple `Set-Cookie` fields and `Location` without consuming the body or following redirects. Connectivity failures return a generic 503 with `no-store`. There is no retry or Worker inference deadline. See the Worker tests for route, header, redirect and cancellation cases.

## Build without a deployment configuration

From `services/cloudflare-edge/`, use Node 22 or newer and uv:

```sh
npm ci
npm test
npm run config:check
npm run build
npm run dev
```

The tracked `wrangler.json` contains source settings and the asset route map; it has no live VPC Service or Custom Domain. Source tests/builds require no Cloudflare login. Local dev uses the local source configuration. The locked Wrangler dependency is build/deployment tooling.

The asset builder copies the existing HTML, CSS, JavaScript, font and brand assets from `assets/welcome/`. Generated HTML adds `data-edge-backend-guard="true"` to enable action-time readiness checks. Its API samples use the current HTTP(S) origin. An installer may explicitly set `data-api-base` on the body to a validated HTTP(S) API base; file previews fall back to `http://127.0.0.1:4000/v1`. Keys stay in the example placeholder and never enter the URL or preference storage.

`build:assets` replaces only an output whose complete inventory matches its build receipt. Generated `public/`, `dist/`, dependencies and local authentication files are excluded from Git.

## Operator deployment configuration

Copy `wrangler.json` to the ignored sibling `wrangler.local.json`. Set your Worker name, your intended custom-domain route or workers.dev policy and a `LILIUXFLOW_BACKEND` VPC Service binding with your service ID. Register an HTTP VPC Service scoped to your LiteLLM loopback port 4000 through your Tunnel. Choose VPC Service rather than a broad VPC Network binding. Consult Cloudflare's [VPC Service configuration](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/), [binding API](https://developers.cloudflare.com/workers-vpc/api/) and [Tunnel requirements](https://developers.cloudflare.com/workers-vpc/configuration/tunnel/).

Use the explicit configuration path for deployment operations:

```sh
npm run config:deployment:check -- --config wrangler.local.json
npm run upload -- --config wrangler.local.json
npm run deploy -- --config wrangler.local.json
npm run promote -- --config wrangler.local.json
```

Choose the operation appropriate to your release: upload stages a version, deploy publishes the selected build and promote selects a version to serve. These commands require your authenticated Cloudflare session and valid operator configuration. For remote development, mark the VPC binding `remote: true` and run `npm run dev:remote -- --config wrangler.local.json`. Keep deployment settings and credentials outside Git and release archives.

Workers VPC is beta. Verify the current platform limits and service availability before choosing it for a deployment. Keep your previous Worker version and hostname configuration available for rollback.

## Validation and rollback

Run the [Edge smoke client](../scripts/cloudflare-edge/README.md) against your explicit deployment URL for static, online and offline behavior. Online non-inference checks can use an existing application virtual key file. Add `--allow-inference` only for a bounded streaming model request with an explicit key file and model name.

Before changing a hostname, check the actual dashboard dependency routes, offline/recovery navigation, caller permissions, SSE streaming and desktop/narrow-screen appearance. Record the previous Worker version, route/hostname target and rollback steps. A rollback restores those operator settings and previous Worker version; it does not require stopping the Tunnel or native model services.
