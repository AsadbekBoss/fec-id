// Starts the dev servers without Docker.
//   node tools/dev.mjs        -> API (http://localhost:8000) + Web (http://localhost:3000)
//   node tools/dev.mjs api    -> API only
//   node tools/dev.mjs web    -> Web only
import { spawn } from "node:child_process";
import { API_DIR, IS_WIN, WEB_DIR, venvPython } from "./paths.mjs";

const which = process.argv[2] ?? "all";
const children = [];

function run(name, cmd, args, cwd, color) {
  // npm is a .cmd shim on Windows and needs a shell; pass one command string (fixed args only).
  const child =
    IS_WIN && cmd === "npm"
      ? spawn([cmd, ...args].join(" "), { cwd, shell: true, env: process.env })
      : spawn(cmd, args, { cwd, env: process.env });
  const tag = `\x1b[${color}m[${name}]\x1b[0m `;
  const pipe = (stream, out) =>
    stream.on("data", (d) =>
      d.toString().split(/\r?\n/).filter(Boolean).forEach((l) => out.write(tag + l + "\n")),
    );
  pipe(child.stdout, process.stdout);
  pipe(child.stderr, process.stderr);
  child.on("exit", (code) => {
    console.log(`${tag}exited with code ${code}`);
    shutdown(code ?? 0);
  });
  children.push(child);
}

function shutdown(code) {
  for (const c of children) if (!c.killed) c.kill();
  process.exit(code);
}
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

if (which === "all" || which === "api") {
  run(
    "api",
    venvPython(),
    ["-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
    API_DIR,
    "36",
  );
}
if (which === "all" || which === "web") {
  run("web", "npm", ["run", "dev"], WEB_DIR, "35");
}
