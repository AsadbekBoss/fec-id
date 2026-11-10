// Runs a Python service's own virtualenv (cwd = apps/<service>).
//   node tools/svc.mjs <service> setup      -> create .venv and install requirements-dev.txt
//   node tools/svc.mjs <service> <args...>  -> python <args...>
// Services: recognition, camera
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { IS_WIN, ROOT } from "./paths.mjs";

const [service, ...args] = process.argv.slice(2);
if (!["recognition", "camera"].includes(service)) {
  console.error("usage: node tools/svc.mjs <recognition|camera> <setup|python args...>");
  process.exit(2);
}
const DIR = join(ROOT, "apps", service);
const py = IS_WIN ? join(DIR, ".venv", "Scripts", "python.exe") : join(DIR, ".venv", "bin", "python");
const run = (cmd, a) => spawnSync(cmd, a, { cwd: DIR, stdio: "inherit" }).status ?? 1;

if (args[0] === "setup") {
  if (!existsSync(py)) {
    const [cmd, a] = IS_WIN ? ["py", ["-3.14", "-m", "venv", ".venv"]] : ["python3", ["-m", "venv", ".venv"]];
    if (run(cmd, a) !== 0) process.exit(1);
  }
  process.exit(run(py, ["-m", "pip", "install", "-r", "requirements-dev.txt"]));
}
if (!existsSync(py)) {
  console.error(`${service} virtualenv not found at ${py}\nRun: npm run ${service === "camera" ? "cam" : "rec"}:setup`);
  process.exit(1);
}
process.exit(run(py, args));
