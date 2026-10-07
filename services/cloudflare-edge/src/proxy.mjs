// SPDX-License-Identifier: Apache-2.0
import { READINESS_PATH, READY_TIMEOUT_MS } from "./routes.mjs";

const OFFLINE_MESSAGE = "LiliuxFlow backend is currently offline.";

function offlineResponse() {
  return new Response(JSON.stringify({ error: OFFLINE_MESSAGE }), {
    status: 503,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function publicOriginHeaders(publicUrl, originalHeaders) {
  const headers = new Headers(originalHeaders);
  const proto = publicUrl.protocol.slice(0, -1);
  headers.set("Host", publicUrl.host);
  headers.set("X-Forwarded-Host", publicUrl.host);
  headers.set("X-Forwarded-Proto", proto);
  headers.set("X-Forwarded-Port", publicUrl.port || (proto === "https" ? "443" : "80"));
  // Replace client-supplied origin hints rather than letting them contradict the
  // URL that Cloudflare actually serves. Authentication headers are untouched.
  headers.set("Forwarded", `proto=${proto};host="${publicUrl.host}"`);
  return headers;
}

function serviceUrl(publicUrl) {
  const target = new URL(publicUrl);
  target.protocol = "http:";
  target.port = "";
  // This is a VPC SERVICE binding: its registered loopback host and HTTP port
  // 4000 always determine connectivity. The URL host sets the backend Host only.
  return target;
}

export async function probeBackend(request, env, timeoutMs = READY_TIMEOUT_MS) {
  const publicUrl = new URL(request.url);
  const target = serviceUrl(publicUrl);
  target.pathname = READINESS_PATH;
  target.search = "";
  const controller = new AbortController();
  let timeout;

  try {
    // The timeout also bounds bindings that fail to settle after an abort.
    const deadline = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(new Error("Readiness deadline"));
      }, timeoutMs);
    });
    const healthRequest = new Request(target, {
      method: "GET",
      headers: publicOriginHeaders(publicUrl, { Accept: "application/json" }),
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
    const response = await Promise.race([
      env.LILIUXFLOW_BACKEND.fetch(healthRequest),
      deadline,
    ]);
    // Health payloads never cross the public edge. Do not wait for or parse them.
    if (response.body) void response.body.cancel().catch(() => {});
    return new Response(null, {
      status: response.ok ? 204 : 503,
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return new Response(null, {
      status: 503,
      headers: { "Cache-Control": "no-store" },
    });
  } finally {
    clearTimeout(timeout);
    controller.abort();
  }
}

export async function proxyBackend(request, env) {
  const publicUrl = new URL(request.url);
  try {
    // Copy Request properties without reading the body. The second constructor
    // preserves body and cancellation while setting manual redirect semantics.
    const routedRequest = new Request(serviceUrl(publicUrl), request);
    const upstreamRequest = new Request(routedRequest, {
      headers: publicOriginHeaders(publicUrl, request.headers),
      redirect: "manual",
    });
    // Return the actual response object: preserve streaming, multiple Set-Cookie,
    // Location and backend HTTP errors. No retries, cache or inference deadline.
    return await env.LILIUXFLOW_BACKEND.fetch(upstreamRequest);
  } catch {
    // Workers VPC throws for connectivity failures. Do not expose exception text.
    return offlineResponse();
  }
}
