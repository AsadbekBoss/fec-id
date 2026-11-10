// Runs the API virtualenv's Python with the given args, cwd = apps/api.
import { spawnSync } from "node:child_process";
import { API_DIR, venvPython } from "./paths.mjs";

const r = spawnSync(venvPython(), process.argv.slice(2), { cwd: API_DIR, stdio: "inherit" });
process.exit(r.status ?? 1);
