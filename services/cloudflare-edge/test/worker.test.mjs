// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker from "../src/worker.mjs";
import { probeBackend } from "../src/proxy.mjs";
import {
  BACKEND_PREFIXES,
  UI_BACKEND_EXACT_PATHS,
  UI_BACKEND_PREFIXES,
  isBackendPath,
  READY_PATH,
  READINESS_PATH,
  workerFirstPatterns,
} from "../src/routes.mjs";

const PUBLIC_ORIGIN = "https://edge.example.invalid";
const req = (pathname, options) => new Request(`${PUBLIC_ORIGIN}${pathname}`, options);
const workerRunsFirst = (pathname) => workerFirstPatterns().some((pattern) =>
  new RegExp(`^${pattern.split("*").map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`).test(pathname),
);

function environment(backendFetch) {
  const backendRequests = [];
  const assetRequests = [];
  return {
    backendRequests,
    assetRequests,
    env: {
      LILIUXFLOW_BACKEND: {
        fetch(request) {
          backendRequests.push(request);
          return backendFetch(request);
        },
      },
      ASSETS: {
        fetch(request) {
          assetRequests.push(request);
          const path = new URL(request.url).pathname;
          return new Response(path === "/" ? "Existing Welcome Page" : "static asset", {
            headers: { "Content-Type": path === "/" ? "text/html" : "text/css" },
          });
        },
      },
    },
  };
}

test("Welcome and its static assets never inspect or request the backend", async () => {
  const state = environment(() => { throw new Error("backend offline"); });
  for (const path of ["/", "/index.html", "/liliuxflow-welcome/welcome.css", "/liliuxflow-welcome/welcome.js", "/liliuxflow-welcome/logo.svg"]) {
    const response = await worker.fetch(req(path), state.env);
    assert.equal(response.status, 200);
  }
  assert.equal(state.backendRequests.length, 0);
  assert.equal(state.assetRequests.length, 5);
});

test("the probe reports 204 on healthy 2xx without exposing payload or forwarding credentials", async () => {
  const canceled = [];
  const state = environment(() => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode("private health payload")); },
    cancel(reason) { canceled.push(reason); },
  }), { status: 200 }));
  const response = await worker.fetch(req(`${READY_PATH}?target=/v1/chat/completions`, {
    headers: { Authorization: "Bearer private-test-only", Cookie: "private-test-session=1" },
  }), state.env);
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(await response.text(), "");
  assert.equal(canceled.length, 1);
  assert.equal(state.backendRequests.length, 1);
  const healthRequest = state.backendRequests[0];
  assert.equal(new URL(healthRequest.url).pathname, READINESS_PATH);
  assert.equal(new URL(healthRequest.url).search, "");
  assert.equal(healthRequest.method, "GET");
  assert.equal(healthRequest.redirect, "manual");
  assert.equal(healthRequest.headers.get("Authorization"), null);
  assert.equal(healthRequest.headers.get("Cookie"), null);
  assert.equal(state.assetRequests.length, 0);
});

test("readiness network failures become empty 503, with no internal exception details", async () => {
  const state = environment(() => { throw new Error("private tunnel details /var/not-for-public"); });
  const response = await worker.fetch(req(READY_PATH), state.env);
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(await response.text(), "");
});

test("readiness health failures and redirects become 503 without returning their payload", async () => {
  for (const status of [301, 401, 404, 429, 500, 503]) {
    const state = environment(() => new Response("private health failure", { status }));
    const response = await worker.fetch(req(READY_PATH), state.env);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal(await response.text(), "");
  }
});

test("readiness has a deadline even if the binding never settles after abort", async () => {
  let healthSignal;
  const state = environment((request) => {
    healthSignal = request.signal;
    return new Promise(() => {});
  });
  const response = await probeBackend(req(READY_PATH), state.env, 10);
  assert.equal(response.status, 503);
  assert.equal(healthSignal.aborted, true);
  assert.equal(await response.text(), "");
});

test("readiness does not infer, retry, poll, or accept state-changing requests", async () => {
  const state = environment(() => new Response(null, { status: 204 }));
  for (const method of ["POST", "PUT", "DELETE", "HEAD", "OPTIONS"]) {
    const response = await worker.fetch(req(READY_PATH, { method }), state.env);
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("Allow"), "GET");
  }
  assert.equal(state.backendRequests.length, 0);
  const response = await worker.fetch(req(READY_PATH), state.env);
  assert.equal(response.status, 204);
  assert.equal(state.backendRequests.length, 1);
});

test("all public contract paths route exclusively through the VPC Service", async () => {
  const state = environment((request) => new Response(new URL(request.url).pathname));
  for (const path of ["/v1", "/v1/", "/v1/chat/completions", "/api", "/api/current", "/ui", "/ui/", "/ui/models", "/ui/_next/file.js"]) {
    const response = await worker.fetch(req(path), state.env);
    assert.equal(await response.text(), path);
  }
  assert.equal(state.backendRequests.length, 9);
  assert.equal(state.assetRequests.length, 0);
});

test("contract prefixes cannot claim similarly named static paths", async () => {
  const state = environment(() => { throw new Error("wrong route"); });
  for (const path of ["/v10/docs.css", "/v1-logo.svg", "/apiary.js", "/ui-logo.svg", "/liliuxflow-welcome/welcome.css", "/unknown-static-path"]) {
    assert.equal(isBackendPath(path), false);
    assert.equal((await worker.fetch(req(path), state.env)).status, 200);
  }
  assert.equal(state.backendRequests.length, 0);
});

test("configured backend dependencies and selective Worker-first routes agree", () => {
  const patterns = workerFirstPatterns();
  for (const path of UI_BACKEND_EXACT_PATHS) {
    assert.equal(isBackendPath(path), true);
    assert.equal(workerRunsFirst(path), true);
  }
  for (const prefix of [...BACKEND_PREFIXES, ...UI_BACKEND_PREFIXES]) {
    assert.equal(isBackendPath(prefix), true);
    assert.equal(isBackendPath(`${prefix}/child`), true);
    assert.equal(workerRunsFirst(prefix), true);
    assert.equal(workerRunsFirst(`${prefix}/child`), true);
  }
  assert.equal(new Set(patterns).size, patterns.length);
  assert.ok(patterns.length <= 100);
  assert.equal(workerRunsFirst("/"), false);
  assert.equal(workerRunsFirst("/liliuxflow-welcome/welcome.css"), false);
});

test("Safari-observed login, models, keys and usage dependencies are served by the backend", async () => {
  const state = environment((request) => new Response(new URL(request.url).pathname));
  // Concrete observed requests, independent of the allowlist implementation.
  const observedPaths = [
    "/litellm-asset-prefix/_next/static/chunks/fixture.js",
    "/litellm-asset-prefix/_next/static/media/fixture.woff2",
    "/litellm/.well-known/litellm-ui-config",
    "/v2/login",
    "/health/license",
    "/health/readiness/details",
    "/user/available_users",
    "/user/available_roles",
    "/user/list",
    "/user/daily/activity/aggregated",
    "/team/list",
    "/key/list",
    "/project/list",
    "/tag/list",
    "/sso/get/ui_settings",
    "/get/ui_settings",
    "/get/ui_theme_settings",
    "/get/user_banner",
    "/public/litellm_blog_posts",
    "/public/litellm_model_cost_map",
    "/models",
    "/v2/guardrails/list",
    "/v2/team/list",
    "/v2/model/info",
    "/v2/user/info",
    "/policies/list",
    "/prompts/list",
    "/config/list",
    "/gateway/daily/activity",
    "/customer/list",
  ];
  for (const path of observedPaths) {
    const response = await worker.fetch(req(path, path === "/v2/login" ? {
      method: "POST", body: "fixture-only",
    } : undefined), state.env);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), path);
  }
  assert.equal(state.backendRequests.length, observedPaths.length);
  assert.equal(state.assetRequests.length, 0);
});

test("API documentation HTML, schema, self-hosted assets and OAuth callback stay on the backend", async () => {
  const state = environment((request) => new Response(new URL(request.url).pathname));
  for (const path of ["/api-docs", "/openapi.json", "/swagger/swagger-ui.css", "/swagger/swagger-ui-bundle.js", "/swagger/favicon.png", "/docs/oauth2-redirect"]) {
    assert.equal(await (await worker.fetch(req(path), state.env)).text(), path);
  }
  assert.equal(state.backendRequests.length, 6);
  assert.equal(state.assetRequests.length, 0);
});

test("exact UI endpoints cannot expand into unobserved root routes or similarly named static assets", async () => {
  const state = environment(() => { throw new Error("unobserved route reached backend"); });
  for (const path of ["/_next/static/fixture.js", "/litellm-asset-prefix/_next/unobserved", "/litellm-asset-prefix/_next/static-logo.svg", "/v2/unobserved", "/v3/unobserved", "/get/unobserved", "/health/readiness", "/public/unobserved", "/docs/unobserved", "/sso/unobserved", "/user-guide.css", "/login-help.html"]) {
    assert.equal(isBackendPath(path), false);
    assert.equal((await worker.fetch(req(path), state.env)).status, 200);
  }
  assert.equal(state.backendRequests.length, 0);
});

test("offline UI dependencies return 503 and cannot consume an identically named edge asset", async () => {
  const state = environment(() => { throw new Error("VPC disconnected"); });
  for (const path of ["/login", "/v2/login", "/litellm-asset-prefix/_next/static/chunks/fixture.js", "/get/ui_settings", "/user/daily/activity/aggregated", "/api-docs", "/openapi.json", "/swagger/swagger-ui.css"]) {
    const response = await worker.fetch(req(path), state.env);
    assert.equal(response.status, 503);
    assert.match(response.headers.get("Content-Type"), /^application\/json/);
    assert.deepEqual(await response.json(), { error: "LiliuxFlow backend is currently offline." });
  }
  assert.equal(state.assetRequests.length, 0);
  assert.equal((await worker.fetch(req("/"), state.env)).status, 200);
  assert.equal(state.assetRequests.length, 1);
});

test("the current UI HTTP-call and backend-registration fixture is fully routed through VPC", async () => {
  const fixture = JSON.parse(await readFile(new URL("./ui-backend-fixture.json", import.meta.url), "utf8"));
  assert.ok(fixture.routes.length > 250, "the audited current UI dependency set must remain complete");
  const state = environment(async (request) => {
    assert.equal(request.headers.get("Authorization"), "Bearer fixture-only");
    assert.equal(new URL(request.url).search, "?fixture=one%20two");
    if (!["GET", "HEAD"].includes(request.method)) assert.equal(await request.text(), '{"fixture":true}');
    return new Response(null, { status: 204 });
  });
  for (const route of fixture.routes) {
    const pathname = route.path.replace(/\{[^}]*\}/g, "fixture");
    assert.equal(isBackendPath(pathname), true, `${route.method} ${route.path}`);
    assert.equal(workerRunsFirst(pathname), true, `asset execution rule: ${route.path}`);
    const response = await worker.fetch(req(`${pathname}?fixture=one%20two`, {
      method: route.method,
      headers: { Authorization: "Bearer fixture-only", "Content-Type": "application/json" },
      ...(!["GET", "HEAD"].includes(route.method) ? { body: '{"fixture":true}' } : {}),
    }), state.env);
    assert.equal(response.status, 204, `${route.method} ${route.path}`);
  }
  assert.equal(state.backendRequests.length, fixture.routes.length);
  assert.equal(state.assetRequests.length, 0);
});

test("unversioned Playground SDK modes keep real backend errors and cannot serve static HTML", async () => {
  for (const path of ["/chat/completions", "/embeddings", "/responses", "/audio/speech", "/audio/transcriptions", "/images/edits", "/images/generations", "/v1beta/interactions"]) {
    const original = new Response('{"error":{"message":"mode unsupported","type":"backend_error"}}', {
      status: 400, headers: { "Content-Type": "application/json" },
    });
    const state = environment(() => original);
    const response = await worker.fetch(req(path, { method: "POST", body: '{"fixture":true}' }), state.env);
    assert.equal(response, original, path);
    assert.equal(response.status, 400);
    assert.equal(state.backendRequests.length, 1);
    assert.equal(state.assetRequests.length, 0);
    assert.equal((await response.json()).error.type, "backend_error");
  }
});

test("compacted Worker-first globs never grant backend routing to unknown UI settings or similarly named assets", async () => {
  const state = environment(() => { throw new Error("unapproved route reached backend"); });
  for (const path of ["/get/unapproved", "/public/unapproved", "/health/unapproved", "/update/unapproved", "/utils/unapproved", "/v2/unapproved", "/sso/unapproved", "/model-logo.svg", "/ui-logo.svg", "/config-theme.css"]) {
    assert.equal(workerRunsFirst(path), true, path);
    assert.equal(isBackendPath(path), false, path);
    const response = await worker.fetch(req(path), state.env);
    assert.equal(response.status, 200);
  }
  assert.equal(state.backendRequests.length, 0);
  assert.equal(state.assetRequests.length, 10);
});

test("Logs, model panels, settings and Playground connectivity failures keep 503 while Welcome remains available", async () => {
  const state = environment(() => { throw new Error("VPC unavailable"); });
  for (const path of ["/spend/logs/ui", "/spend/logs/ui/fixture", "/spend/logs/v2", "/model/new", "/model/fixture/update", "/model/delete", "/config/field/info", "/get/default_team_settings", "/chat/completions"]) {
    const response = await worker.fetch(req(path), state.env);
    assert.equal(response.status, 503, path);
    assert.equal(response.headers.get("Location"), null);
    assert.deepEqual(await response.json(), { error: "LiliuxFlow backend is currently offline." });
  }
  assert.equal(state.assetRequests.length, 0);
  assert.equal((await worker.fetch(req("/"), state.env)).status, 200);
  assert.equal((await worker.fetch(req("/liliuxflow-welcome/welcome.css"), state.env)).status, 200);
});

test("POST body, authorization, content type, Accept, cookies and query are preserved", async () => {
  const body = '{ "model": "unchanged", "stream": true, "messages": [] }\n';
  const state = environment(async (request) => {
    assert.equal(request.method, "POST");
    assert.equal(await request.text(), body);
    assert.equal(request.headers.get("Authorization"), "Bearer fixture-only-not-a-real-key");
    assert.equal(request.headers.get("Content-Type"), "application/json");
    assert.equal(request.headers.get("Accept"), "text/event-stream");
    assert.equal(request.headers.get("Cookie"), "session=fixture-only");
    assert.equal(request.headers.get("X-Request-ID"), "fixture-request");
    assert.equal(new URL(request.url).search, "?a=one%20two&b=%2B&a=three");
    assert.equal(request.redirect, "manual");
    return new Response("original response");
  });
  const response = await worker.fetch(req("/v1/chat/completions?a=one%20two&b=%2B&a=three", {
    method: "POST",
    body,
    headers: {
      Authorization: "Bearer fixture-only-not-a-real-key",
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Cookie: "session=fixture-only",
      "X-Request-ID": "fixture-request",
    },
  }), state.env);
  assert.equal(await response.text(), "original response");
});

test("VPC Service keeps public Host and validated HTTPS origin hints", async () => {
  const state = environment((request) => {
    assert.equal(request.url, "http://liliuxflow.diurnoctra.com/ui/?lang=ja");
    assert.equal(request.headers.get("Host"), "liliuxflow.diurnoctra.com");
    assert.equal(request.headers.get("X-Forwarded-Host"), "liliuxflow.diurnoctra.com");
    assert.equal(request.headers.get("X-Forwarded-Proto"), "https");
    assert.equal(request.headers.get("X-Forwarded-Port"), "443");
    assert.equal(request.headers.get("Forwarded"), 'proto=https;host="liliuxflow.diurnoctra.com"');
    return new Response("origin");
  });
  await worker.fetch(req("/ui/?lang=ja", { headers: {
    Host: "attacker.invalid",
    "X-Forwarded-Host": "attacker.invalid",
    "X-Forwarded-Proto": "http",
    Forwarded: 'proto=http;host="attacker.invalid"',
  } }), state.env);
});

test("backend 401/404/429/500 responses remain the original response", async () => {
  for (const status of [200, 401, 404, 429, 500, 503]) {
    const original = new Response(`original ${status}`, { status, headers: { "X-Origin": "unchanged" } });
    const state = environment(() => original);
    const response = await worker.fetch(req("/api/test"), state.env);
    assert.equal(response, original);
    assert.equal(response.status, status);
    assert.equal(response.headers.get("X-Origin"), "unchanged");
    assert.equal(await response.text(), `original ${status}`);
  }
});

test("redirect status, Location and separate Set-Cookie headers remain unchanged", async () => {
  const headers = new Headers({ Location: "/ui/?login=success" });
  headers.append("Set-Cookie", "session=fixture-only; Path=/; HttpOnly; Secure; SameSite=Lax");
  headers.append("Set-Cookie", "locale=ja; Path=/; SameSite=Lax");
  const original = new Response(null, { status: 303, headers });
  const state = environment(() => original);
  const response = await worker.fetch(req("/ui/login", { method: "POST", body: "user=fixture" }), state.env);
  assert.equal(response, original);
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("Location"), "/ui/?login=success");
  assert.deepEqual(response.headers.getSetCookie(), [
    "session=fixture-only; Path=/; HttpOnly; Secure; SameSite=Lax",
    "locale=ja; Path=/; SameSite=Lax",
  ]);
});

test("real backend network exceptions become sanitized JSON 503, never Welcome HTML", async () => {
  const state = environment(() => { throw new Error("private internal origin /var/path and tunnel metadata"); });
  for (const path of ["/v1/chat/completions", "/api/test", "/ui"]) {
    const response = await worker.fetch(req(path, { method: "POST", body: "fixture" }), state.env);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.match(response.headers.get("Content-Type"), /^application\/json/);
    assert.equal(response.headers.get("Location"), null);
    assert.deepEqual(await response.json(), { error: "LiliuxFlow backend is currently offline." });
  }
  assert.equal(state.assetRequests.length, 0);
});

test("streaming response delivers its first SSE chunk while the backend is still producing", async () => {
  for (const path of ["/v1/chat/completions", "/chat/completions"]) {
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const original = new Response(body, { headers: { "Content-Type": "text/event-stream" } });
  const state = environment(() => original);
  const response = await worker.fetch(req(path, { method: "POST", body: '{"stream":true}' }), state.env);
  assert.equal(response, original);
  assert.equal(response.body, body);
  const reader = response.body.getReader();
  const first = reader.read();
  controller.enqueue(new TextEncoder().encode('data: {"delta":"first"}\n\n'));
  assert.equal(new TextDecoder().decode((await first).value), 'data: {"delta":"first"}\n\n');
  // The later chunk is not enqueued until the consumer receives the first chunk.
  controller.enqueue(new TextEncoder().encode("data: [DONE]\n\n"));
  controller.close();
  assert.equal(new TextDecoder().decode((await reader.read()).value), "data: [DONE]\n\n");
  assert.equal((await reader.read()).done, true);
  }
});

test("streaming request is not buffered, and the backend can consume before upload finishes", async () => {
  let upload;
  let backendRead;
  const body = new ReadableStream({ start(value) { upload = value; } });
  const firstConsumed = new Promise((resolve) => { backendRead = resolve; });
  const state = environment(async (request) => {
    const reader = request.body.getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "first-");
    backendRead();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "second");
    assert.equal((await reader.read()).done, true);
    return new Response("uploaded");
  });
  const pending = worker.fetch(req("/api/upload", { method: "POST", body, duplex: "half" }), state.env);
  upload.enqueue(new TextEncoder().encode("first-"));
  await firstConsumed;
  upload.enqueue(new TextEncoder().encode("second"));
  upload.close();
  assert.equal(await (await pending).text(), "uploaded");
});

test("client abort propagates to the upstream Request", async () => {
  const controller = new AbortController();
  let upstreamSignal;
  const state = environment((request) => {
    upstreamSignal = request.signal;
    return new Response("stream accepted");
  });
  await worker.fetch(req("/v1/chat/completions", { signal: controller.signal }), state.env);
  assert.equal(upstreamSignal.aborted, false);
  controller.abort();
  assert.equal(upstreamSignal.aborted, true);
});

test("static asset configuration cannot absorb backend routes or widen the binding scope", async () => {
  const config = JSON.parse(await readFile(new URL("../wrangler.json", import.meta.url), "utf8"));
  assert.deepEqual(config.assets.run_worker_first, workerFirstPatterns());
  assert.equal(config.assets.binding, "ASSETS");
  assert.equal(config.assets.not_found_handling, "none");
  assert.equal(config.assets.directory, "./public");
  assert.equal(config.vpc_services, undefined);
  assert.equal(config.vpc_networks, undefined);
  assert.equal(config.account_id, undefined);
  assert.equal(config.routes, undefined);
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
});
