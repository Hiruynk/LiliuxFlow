// SPDX-License-Identifier: Apache-2.0
import { isBackendPath, READY_PATH } from "./routes.mjs";
import { probeBackend, proxyBackend } from "./proxy.mjs";

export default {
  async fetch(request, env) {
    const pathname = new URL(request.url).pathname;
    if (pathname === READY_PATH) {
      if (request.method !== "GET") {
        return new Response(null, {
          status: 405,
          headers: { Allow: "GET", "Cache-Control": "no-store" },
        });
      }
      return probeBackend(request, env);
    }
    if (isBackendPath(pathname)) return proxyBackend(request, env);
    return env.ASSETS.fetch(request);
  },
};
