// SPDX-License-Identifier: Apache-2.0

export const READY_PATH = "/_liliuxflow/backend/ready";
export const READINESS_PATH = "/health/readiness";
export const READY_TIMEOUT_MS = 2500;

// Public backend contract. Match whole path segments, never /v10 or /ui-logo.
export const BACKEND_PREFIXES = Object.freeze(["/v1", "/api", "/ui"]);

// Installed LiteLLM 1.102.1: Safari's login/keys/models/usage trace (2026-10-06)
// verifies the core backend namespaces. The rest are current UI HTTP-call sites
// cross-checked against installed compiled JavaScript and FastAPI registration.
// Swagger dependencies are verified from live /api-docs HTML and installed source.
export const UI_BACKEND_PREFIXES = Object.freeze([
  "/litellm-asset-prefix/_next/static",
  "/user",
  "/team",
  "/key",
  "/project",
  "/tag",
  "/swagger",
  "/a2a",
  "/access_group",
  "/auto_router",
  "/budget",
  "/cache/settings",
  "/claude-code/plugins",
  "/cloudzero",
  "/compliance",
  "/config",
  "/config_overrides/cyberark",
  "/config_overrides/hashicorp_vault",
  "/coordination_redis/settings",
  "/credentials",
  "/global/activity",
  "/global/spend",
  "/guardrails",
  "/model",
  "/model_group",
  "/organization",
  "/policies",
  "/policy",
  "/prompts",
  "/router",
  "/search_tools",
  "/spend/logs",
  "/vector_store",
  "/v2/organization",
  "/schedule/model_cost_map_reload",
]);

// Keep settings and versioned routes exact so unrelated /get, /public, /health,
// /v2 or root /_next paths are not claimed by this migration.
export const UI_BACKEND_EXACT_PATHS = Object.freeze([
  "/litellm/.well-known/litellm-ui-config",
  "/login", // Installed source's registered compatibility fallback.
  "/v2/login", // Observed Safari logout -> login flow uses this endpoint.
  "/v3/login", // Current compiled SDK/source; not a runtime acceptance claim.
  "/v3/login/exchange",
  "/health/license",
  "/health/readiness/details",
  "/sso/get/ui_settings",
  "/sso/key/generate", // LoginPage's configured SSO navigation.
  "/sso/callback", // Registered callback for that existing auth flow.
  "/sso/saml/login",
  "/sso/saml/callback",
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
  "/gateway/daily/activity",
  "/customer/list",
  "/api-docs",
  "/openapi.json",
  "/docs/oauth2-redirect",
  "/authorize/flow",
  "/authorize/complete",
  "/agent/daily/activity",
  "/claude-code/marketplace.json",
  "/alerting/settings",
  "/callbacks/configs",
  "/cost/estimate",
  "/customer/daily/activity",
  "/health",
  "/health/services",
  "/health/latest",
  "/health/test_connection",
  "/get/allowed_ips",
  "/get/config/callbacks",
  "/get/default_team_settings",
  "/get/internal_user_settings",
  "/get/mcp_semantic_filter_settings",
  "/get/mcp_tool_search_settings",
  "/get/sso_settings",
  "/update/default_team_settings",
  "/update/internal_user_settings",
  "/update/mcp_semantic_filter_settings",
  "/update/mcp_tool_search_settings",
  "/update/sso_settings",
  "/update/ui_settings",
  "/update/ui_theme_settings",
  "/update/user_banner",
  "/add/allowed_ip",
  "/delete/allowed_ip",
  "/invitation/new",
  "/model_hub/update_useful_links",
  "/onboarding/get_token",
  "/onboarding/claim_token",
  "/public/agent_hub",
  "/public/agents/fields",
  "/public/autorouter_presets",
  "/public/complexity_router/scorer_defaults",
  "/public/mcp_hub",
  "/public/model_hub",
  "/public/model_hub/info",
  "/public/providers/fields",
  "/public/skill_hub",
  "/rag/ingest",
  "/reload/model_cost_map",
  "/usage/ai/chat",
  "/utils/dotprompt_json_converter",
  "/utils/test_policies_and_guardrails",
  "/utils/transform_request",
  // Playground SDKs use browser origin as baseURL, without a /v1 suffix.
  // These aliases exist on this backend. Unsupported modes retain origin errors.
  "/chat/completions",
  "/embeddings",
  "/responses",
  "/audio/speech",
  "/audio/transcriptions",
  "/images/edits",
  "/images/generations",
  "/v1beta/interactions",
]);

export function isBackendPath(pathname) {
  return (
    UI_BACKEND_EXACT_PATHS.includes(pathname) ||
    [...BACKEND_PREFIXES, ...UI_BACKEND_PREFIXES].some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    )
  );
}

export function workerFirstPatterns() {
  const paths = [
    READY_PATH,
    // Wildcard execution includes the namespace root as well as descendants;
    // similarly named assets still fail the strict proxy matcher and use ASSETS.
    ...[...BACKEND_PREFIXES, ...UI_BACKEND_PREFIXES].map((prefix) => `${prefix}*`),
    ...UI_BACKEND_EXACT_PATHS,
  ];
  // Cloudflare allows 100 execution rules. Collapse dense root namespaces only
  // for Worker execution; isBackendPath() above remains the strict proxy policy.
  // An unapproved path still calls ASSETS with not_found_handling="none".
  const groups = new Map();
  for (const path of paths) {
    const root = `/${path.split("/")[1]}`;
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(path);
  }
  const patterns = [];
  for (const [root, group] of groups) {
    if (group.length > 2) {
      if (group.includes(root)) patterns.push(root);
      patterns.push(`${root}/*`);
    } else patterns.push(...group);
  }
  // Wrangler rejects rules already covered by another execution glob.
  const compact = [...new Set(patterns)].filter((pattern, _, all) => !all.some(
    (other) => other !== pattern && other.endsWith("*") && pattern.startsWith(other.slice(0, -1)),
  ));
  if (compact.length > 100) throw new Error("Static Assets Worker-first rule limit exceeded.");
  return compact;
}
