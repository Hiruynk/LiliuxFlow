// SPDX-License-Identifier: Apache-2.0
import { readFile, realpath, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { workerFirstPatterns } from "../src/routes.mjs";

export const sourceConfigPath = fileURLToPath(new URL("../wrangler.json", import.meta.url));
const edgeRoot = dirname(sourceConfigPath);

export async function readConfig(path) {
  try {
    const config = JSON.parse(await readFile(path, "utf8"));
    if (config === null || typeof config !== "object" || Array.isArray(config)) throw new Error();
    return config;
  } catch {
    throw new Error("Configuration must be a readable JSON object. Values are withheld.");
  }
}

export function checkRoutes(config, configPath = sourceConfigPath) {
  const expected = workerFirstPatterns();
  if (JSON.stringify(config.assets?.run_worker_first) !== JSON.stringify(expected)) {
    throw new Error("Worker routes and selective Static Assets routing differ. Run npm run config:sync.");
  }
  if (expected.length > 100) throw new Error("Cloudflare supports at most 100 Static Assets Worker-first rules.");
  if (config.assets.binding !== "ASSETS" || config.assets.not_found_handling !== "none") {
    throw new Error("ASSETS and no static SPA fallback are required.");
  }
  if (typeof config.main !== "string" || resolve(dirname(configPath), config.main) !== resolve(edgeRoot, "src/worker.mjs") ||
      typeof config.assets.directory !== "string" || resolve(dirname(configPath), config.assets.directory) !== resolve(edgeRoot, "public")) {
    throw new Error("Configuration must use this Worker source and its built public assets.");
  }
  if (config.vpc_networks?.length) throw new Error("VPC Network bindings are not supported; use one scoped VPC Service.");
}

export function checkSource(config) {
  checkRoutes(config);
  const sourceFields = new Set(["$schema", "name", "main", "compatibility_date", "workers_dev", "preview_urls", "assets"]);
  if (Object.keys(config).some((field) => !sourceFields.has(field)) ||
      config.workers_dev !== false || config.preview_urls !== false ||
      /"remote"\s*:\s*true/.test(JSON.stringify(config))) {
    throw new Error("Source configuration must contain no deployment resources or remote bindings.");
  }
}

export async function checkDeployment(configPath, { remoteDev = false } = {}) {
  if (!configPath) throw new Error("Select an operator configuration explicitly with --config <private-wrangler.json>.");
  let selectedPath;
  try { selectedPath = await realpath(resolve(configPath)); }
  catch { throw new Error("Operator configuration is missing or inaccessible. Values are withheld."); }
  if (selectedPath === await realpath(sourceConfigPath)) {
    throw new Error("The shipped source configuration cannot deploy. Select a separate operator configuration.");
  }
  const config = await readConfig(selectedPath);
  checkRoutes(config, selectedPath);
  if (config.vpc_services?.length !== 1 || config.vpc_services[0].binding !== "LILIUXFLOW_BACKEND") {
    throw new Error("Exactly one scoped LILIUXFLOW_BACKEND VPC Service binding is required.");
  }
  const serviceId = config.vpc_services[0].service_id;
  if (typeof serviceId !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(serviceId)) {
    throw new Error("Configure your verified VPC Service UUID before deploying. Never put credentials in config.");
  }
  if (remoteDev && config.vpc_services[0].remote !== true) {
    throw new Error("Remote development requires remote: true on the selected VPC Service.");
  }
  const operatorFields = new Set(["$schema", "name", "main", "compatibility_date", "workers_dev", "preview_urls", "assets", "vpc_services", "routes", "account_id"]);
  if (Object.keys(config).some((field) => !operatorFields.has(field))) {
    throw new Error("Use a complete operator configuration with only Static Assets, one VPC Service, and your deployment settings.");
  }
  if (typeof config.name !== "string" || !/^[a-z0-9][a-z0-9_-]{0,62}$/.test(config.name)) {
    throw new Error("Set your Worker name in the operator configuration.");
  }
  if (config.preview_urls !== false) throw new Error("Version preview URLs must remain disabled.");
  if (config.routes !== undefined && (!Array.isArray(config.routes) || config.routes.some((route) =>
    typeof route !== "object" || route === null || route.custom_domain !== true ||
    typeof route.pattern !== "string" || !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i.test(route.pattern)))) {
    throw new Error("Operator routes must be exact Custom Domain hostnames.");
  }
  if (config.workers_dev !== true && !config.routes?.length) {
    throw new Error("Select your own Custom Domain or explicitly enable workers_dev in the operator configuration.");
  }
  return selectedPath;
}

export function selectedConfig(args) {
  if (args.length !== 2 || args[0] !== "--config" || !args[1]) {
    throw new Error("Select an operator configuration explicitly with --config <private-wrangler.json>; no other flags are accepted.");
  }
  return args[1];
}

export async function main(args = process.argv.slice(2)) {
  const [flag, ...rest] = args;
  if (flag === "--deployment-check") {
    await checkDeployment(selectedConfig(rest));
    process.stdout.write("Operator configuration routes and syntax passed; remote resources were not contacted.\n");
    return;
  }
  if (rest.length || !["--write", "--check"].includes(flag)) throw new Error("Use --check, --write, or --deployment-check --config <private-wrangler.json>.");
  const config = await readConfig(sourceConfigPath);
  if (flag === "--write") {
    config.assets.run_worker_first = workerFirstPatterns();
    checkSource(config);
    await writeFile(sourceConfigPath, `${JSON.stringify(config, null, 2)}\n`);
  } else checkSource(config);
  process.stdout.write("Cloudflare edge source configuration checks passed.\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 2; });
}
