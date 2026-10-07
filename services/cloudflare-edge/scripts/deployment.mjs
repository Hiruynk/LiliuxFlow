// SPDX-License-Identifier: Apache-2.0
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { checkDeployment, selectedConfig } from "./config.mjs";

const commands = {
  dev: ["dev", "--local"],
  deploy: ["deploy"],
  upload: ["versions", "upload"],
  promote: ["versions", "deploy"],
};

export async function deploymentCommand(args) {
  const [action, ...rest] = args;
  if (!Object.hasOwn(commands, action)) throw new Error("Use dev, deploy, upload, or promote with an explicit operator --config.");
  const config = await checkDeployment(selectedConfig(rest), { remoteDev: action === "dev" });
  return [...commands[action], "--config", config];
}

export async function main(args = process.argv.slice(2)) {
  const argv = await deploymentCommand(args);
  const wrangler = fileURLToPath(new URL("../node_modules/wrangler/bin/wrangler.js", import.meta.url));
  // Invoke only the pinned local CLI, without a shell or implicit npx install.
  const child = spawn(process.execPath, [wrangler, ...argv], { stdio: "inherit" });
  child.on("error", () => { process.stderr.write("The pinned Wrangler CLI could not start. Run npm ci first.\n"); process.exitCode = 2; });
  child.on("exit", (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0); });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 2; });
}
