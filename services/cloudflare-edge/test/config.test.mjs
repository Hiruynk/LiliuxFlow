// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { checkDeployment, checkSource, readConfig, selectedConfig, sourceConfigPath } from "../scripts/config.mjs";
import { deploymentCommand } from "../scripts/deployment.mjs";

const source = JSON.parse(await readFile(sourceConfigPath, "utf8"));
const edgeRoot = dirname(sourceConfigPath);

async function operatorFixture(t, change = () => {}) {
  const directory = await mkdtemp(join(tmpdir(), "liliuxflow-config-cpu-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const config = structuredClone(source);
  config.name = "operator-cpu-fixture";
  config.main = join(edgeRoot, "src/worker.mjs");
  config.assets.directory = join(edgeRoot, "public");
  config.workers_dev = true;
  // Synthetic CPU-only UUID; no fixture is sent to Wrangler or a remote API.
  config.vpc_services = [{ binding: "LILIUXFLOW_BACKEND", service_id: "12345678-1234-4123-8123-123456789abc" }];
  change(config);
  const path = join(directory, "operator.json");
  await writeFile(path, JSON.stringify(config));
  return realpath(path);
}

test("shipped source is offline and cannot contain resource fields or remote bindings", () => {
  assert.doesNotThrow(() => checkSource(source));
  for (const [field, value] of [["vpc_services", []], ["account_id", "synthetic"], ["routes", []], ["kv_namespaces", []], ["env", {}], ["build", {}]]) {
    assert.throws(() => checkSource({ ...source, [field]: value }), /no deployment resources/);
  }
  assert.throws(() => checkSource({ ...source, workers_dev: true }), /no deployment resources/);
});

test("all publishing and remote development commands require an explicit separate config", async () => {
  for (const action of ["deploy", "upload", "promote", "dev"]) {
    await assert.rejects(deploymentCommand([action]), /explicitly with --config/);
    await assert.rejects(deploymentCommand([action, "--config", sourceConfigPath]), /shipped source configuration cannot deploy/);
    await assert.rejects(deploymentCommand([action, "--config", sourceConfigPath, "--env", "production"]), /no other flags/);
  }
  assert.throws(() => selectedConfig(["-c", "operator.json"]), /explicitly/);
});

test("configured publish commands select only the validated file; CPU tests never launch Wrangler", async (t) => {
  const path = await operatorFixture(t);
  assert.deepEqual(await deploymentCommand(["deploy", "--config", path]), ["deploy", "--config", path]);
  assert.deepEqual(await deploymentCommand(["upload", "--config", path]), ["versions", "upload", "--config", path]);
  assert.deepEqual(await deploymentCommand(["promote", "--config", path]), ["versions", "deploy", "--config", path]);
});

test("remote dev opt-in requires the selected service's remote binding", async (t) => {
  const path = await operatorFixture(t);
  await assert.rejects(deploymentCommand(["dev", "--config", path]), /remote: true/);
  const remotePath = await operatorFixture(t, (config) => { config.vpc_services[0].remote = true; });
  assert.deepEqual(await deploymentCommand(["dev", "--config", remotePath]), ["dev", "--local", "--config", remotePath]);
});

test("operator config rejects missing UUID, broader network bindings, routes, and backend fallbacks", async (t) => {
  const cases = [
    [(config) => { config.vpc_services[0].service_id = "YOUR_VPC_SERVICE_ID"; }, /verified VPC Service UUID/],
    [(config) => { config.vpc_services[0].service_id = "00000000-0000-0000-0000-000000000000"; }, /verified VPC Service UUID/],
    [(config) => { config.vpc_services.push({ binding: "EXTRA" }); }, /Exactly one/],
    [(config) => { config.vpc_networks = [{ binding: "NETWORK" }]; }, /VPC Network/],
    [(config) => { config.routes = [{ pattern: "*.example.invalid/*" }]; }, /exact Custom Domain/],
    [(config) => { config.workers_dev = false; }, /Select your own Custom Domain/],
    [(config) => { config.assets.not_found_handling = "single-page-application"; }, /no static SPA fallback/],
    [(config) => { config.assets.run_worker_first = ["/v1/*"]; }, /routing differ/],
    [(config) => { config.main = "unreviewed-worker.mjs"; }, /this Worker source/],
    [(config) => { config.env = { production: {} }; }, /complete operator configuration/],
  ];
  for (const [change, message] of cases) await assert.rejects(checkDeployment(await operatorFixture(t, change)), message);
});

test("configuration errors never echo malformed private contents", async (t) => {
  const path = await operatorFixture(t);
  await writeFile(path, '{"service_id":"synthetic-private-value",broken');
  await assert.rejects(readConfig(path), (error) => {
    assert.equal(error.message, "Configuration must be a readable JSON object. Values are withheld.");
    assert.equal(error.message.includes("synthetic-private-value"), false);
    return true;
  });
});
