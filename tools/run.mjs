// One-command launcher and supervisor for the whole system (no Docker).
//   npm start                -> API + recognition + camera + web (production build of the web app)
//   npm start -- --dev       -> same with hot reload (uvicorn --reload, next dev)
//   npm run stop             -> stop the running supervisor (also the one started at logon)
//   npm run status           -> what is running, restarts, last errors
//   npm start -- --only api,web  -> start only some services (names: api, recognition, camera, web)
//
// Every service runs as a child process: a crashed or hung (health check failing for 1 minute)
// service is restarted with backoff. Output goes to logs/<service>.log (rotated at 10 MB).
// The state is written to logs/supervisor.json, which the "Tizim holati" page shows.
// PostgreSQL is not managed here: it is a Windows service that starts with the computer.
import { spawn, spawnSync } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { connect } from "node:net";
import { join } from "node:path";
import { API_DIR, IS_WIN, ROOT, WEB_DIR } from "./paths.mjs";

const LOG_DIR = join(ROOT, "logs");
const STATE = join(LOG_DIR, "supervisor.json");
const PID_FILE = join(LOG_DIR, "supervisor.pid");
const STOP_FILE = join(LOG_DIR, "stop.request");
const MAX_LOG = 10 * 1024 * 1024;
const TICK_MS = 5000;
const HEALTH_FAILS_BEFORE_RESTART = 12; // 12 x 5 s = 1 minute
const START_GRACE_MS = 180_000; // model loading / first compile may take a while

const args = process.argv.slice(2);
const DEV = args.includes("--dev");
const QUIET = args.includes("--quiet");
const onlyIdx = args.indexOf("--only");
const ONLY = onlyIdx >= 0 ? (args[onlyIdx + 1] ?? "").split(",").filter(Boolean) : null;

const venvPy = (dir) => (IS_WIN ? join(dir, ".venv", "Scripts", "python.exe") : join(dir, ".venv", "bin", "python"));
const nextBin = join(WEB_DIR, "node_modules", "next", "dist", "bin", "next");

/** Service definitions. Order matters: the API starts first, the others once it answers. */
function services() {
  const rec = join(ROOT, "apps", "recognition");
  const cam = join(ROOT, "apps", "camera");
  const reload = DEV ? ["--reload"] : [];
  return [
    {
      name: "api", title: "Core API", port: 8000, cwd: API_DIR, cmd: venvPy(API_DIR), required: true,
      args: ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", ...reload],
      health: "http://127.0.0.1:8000/health", setup: "npm run setup",
    },
    {
      name: "recognition", title: "Yuzni tanish", port: 8100, cwd: rec, cmd: venvPy(rec),
      args: ["-m", "uvicorn", "recognition.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "8100"],
      health: "http://127.0.0.1:8100/health", setup: "npm run rec:setup",
    },
    {
      name: "camera", title: "Kamera xizmati", port: 8200, cwd: cam, cmd: venvPy(cam),
      args: ["-m", "uvicorn", "camera.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "8200"],
      health: "http://127.0.0.1:8200/health", setup: "npm run cam:setup",
    },
    {
      name: "web", title: "Veb-sayt", port: 3000, cwd: WEB_DIR, cmd: process.execPath, required: true,
      args: [nextBin, DEV ? "dev" : "start", "-H", "127.0.0.1", "-p", "3000"],
      health: "http://127.0.0.1:3000/login", setup: "npm --prefix apps/web install",
    },
  ];
}

// ---------------------------------------------------------------- helpers

const isAlive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
};

const runningPid = () => {
  try {
    const pid = Number(readFileSync(PID_FILE, "utf8"));
    return pid && pid !== process.pid && isAlive(pid) ? pid : null;
  } catch {
    return null;
  }
};

const portBusy = (port) =>
  new Promise((resolve) => {
    const s = connect({ host: "127.0.0.1", port });
    s.setTimeout(1000);
    s.once("connect", () => { s.destroy(); resolve(true); });
    s.once("error", () => resolve(false));
    s.once("timeout", () => { s.destroy(); resolve(false); });
  });

async function healthy(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(4000), redirect: "manual" });
    return r.status < 500;
  } catch {
    return false;
  }
}

function killTree(pid) {
  if (!pid) return;
  if (IS_WIN) spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
  else try { process.kill(pid, "SIGTERM"); } catch { /* already gone */ }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();

// ---------------------------------------------------------------- stop / status commands

if (args[0] === "--stop") {
  const pid = runningPid();
  if (!pid) {
    console.log("Tizim ishlamayapti (supervisor topilmadi).");
    process.exit(0);
  }
  writeFileSync(STOP_FILE, now());
  for (let i = 0; i < 30 && isAlive(pid); i++) await sleep(500);
  if (isAlive(pid)) killTree(pid);
  rmSync(STOP_FILE, { force: true });
  console.log("Tizim to'xtatildi.");
  process.exit(0);
}

if (args[0] === "--status") {
  let st;
  try { st = JSON.parse(readFileSync(STATE, "utf8")); } catch { st = null; }
  const pid = runningPid();
  if (!st || !pid) {
    console.log("Tizim ishlamayapti. Ishga tushirish: npm start");
    process.exit(1);
  }
  console.log(`Supervisor PID ${st.pid}, rejim: ${st.mode}, ishga tushgan: ${st.started_at}\n`);
  for (const s of Object.values(st.services)) {
    const err = s.last_error ? `  (${s.last_error})` : "";
    console.log(`  ${s.title.padEnd(16)} ${s.state.padEnd(12)} port ${s.port}  qayta ishga tushish: ${s.restarts}${err}`);
  }
  console.log(`\nLoglar: ${LOG_DIR}`);
  process.exit(0);
}

// ---------------------------------------------------------------- supervisor

mkdirSync(LOG_DIR, { recursive: true });
const own = createWriteStream(join(LOG_DIR, "supervisor.log"), { flags: "a" });
const say = (msg) => {
  const line = `${now()} ${msg}\n`;
  own.write(line);
  if (!QUIET) process.stdout.write(`\x1b[33m[run]\x1b[0m ${msg}\n`);
};

const other = runningPid();
if (other) {
  console.error(`Tizim allaqachon ishlayapti (PID ${other}). Holat: npm run status, to'xtatish: npm run stop`);
  process.exit(1);
}

const defs = services().filter((d) => !ONLY || ONLY.includes(d.name));
if (!defs.some((d) => d.name === "api")) {
  console.error("Core API (api) har doim kerak: --only ro'yxatiga api ni qo'shing.");
  process.exit(2);
}
const missing = defs.filter((d) => !existsSync(d.cmd) || (d.name === "web" && !existsSync(nextBin)));
for (const d of missing) {
  if (d.required) {
    console.error(`${d.title} o'rnatilmagan. Avval: ${d.setup}`);
    process.exit(1);
  }
  say(`${d.title} o'rnatilmagan, o'tkazib yuborildi (o'rnatish: ${d.setup})`);
}
const active = defs.filter((d) => !missing.includes(d));

const busy = [];
for (const d of active) if (await portBusy(d.port)) busy.push(d);
/** Windows: which process listens on a port ("python.exe PID 2300"), for a helpful message. */
function holder(port) {
  if (!IS_WIN) return "";
  const net = spawnSync("netstat", ["-ano", "-p", "tcp"], { encoding: "utf8" }).stdout ?? "";
  const line = net.split(/\r?\n/).find((l) => /LISTENING/.test(l) && l.trim().split(/\s+/)[1]?.endsWith(`:${port}`));
  const pid = line?.trim().split(/\s+/).pop();
  if (!pid) return "";
  const task = spawnSync("tasklist", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"], { encoding: "utf8" }).stdout ?? "";
  const name = task.split(",")[0]?.replace(/"/g, "").trim();
  return ` — ${name || "jarayon"} PID ${pid}`;
}

if (busy.length) {
  console.error(
    "Quyidagi portlar band:\n" + busy.map((d) => `  ${d.port} (${d.title})${holder(d.port)}`).join("\n") +
    "\nEski terminallarda ishlayotgan npm run dev:api / dev:web / rec:dev / cam:dev ni Ctrl+C bilan yoping va qayta urinib ko'ring.\n" +
    (IS_WIN ? "Terminal topilmasa, jarayonni PID bo'yicha yoping:  taskkill /PID <PID> /T /F" : ""),
  );
  process.exit(1);
}

writeFileSync(PID_FILE, String(process.pid));
rmSync(STOP_FILE, { force: true });

/** Rebuild the web app when its sources are newer than the last production build. */
function webBuildIsStale() {
  const id = join(WEB_DIR, ".next", "BUILD_ID");
  if (!existsSync(id)) return true;
  const built = statSync(id).mtimeMs;
  const newer = (p) => {
    if (!existsSync(p)) return false;
    const st = statSync(p);
    if (!st.isDirectory()) return st.mtimeMs > built;
    return readdirSync(p).some((n) => newer(join(p, n)));
  };
  return ["src", "public", "next.config.ts", "package.json", ".env.local", "package-lock.json"].some((p) => newer(join(WEB_DIR, p)));
}

if (!DEV && active.some((d) => d.name === "web") && webBuildIsStale()) {
  say("Veb-sayt yangilangan: production build tayyorlanmoqda (1-3 daqiqa)...");
  const log = join(LOG_DIR, "web-build.log");
  const r = spawnSync(process.execPath, [nextBin, "build"], {
    cwd: WEB_DIR, env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" }, encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
  });
  writeFileSync(log, `${now()}\n${r.stdout ?? ""}\n${r.stderr ?? ""}`);
  if (r.status !== 0) {
    say(`Veb-sayt build xatosi. Batafsil: ${log}`);
    rmSync(PID_FILE, { force: true });
    process.exit(1);
  }
  say("Build tayyor.");
}

const state = {
  pid: process.pid,
  mode: DEV ? "dev" : "production",
  started_at: now(),
  updated_at: now(),
  services: Object.fromEntries(
    defs.map((d) => [d.name, {
      name: d.name, title: d.title, port: d.port, state: missing.includes(d) ? "not_installed" : "stopped",
      pid: null, started_at: null, last_healthy_at: null, restarts: 0, last_exit_code: null, last_exit_at: null,
      last_error: null, log: `logs/${d.name}.log`,
    }]),
  ),
};
const run = {}; // name -> { child, stream, fails, backoff, timer, startedMs }
let stopping = false;

function saveState() {
  state.updated_at = now();
  try {
    writeFileSync(STATE + ".tmp", JSON.stringify(state, null, 2));
    renameSync(STATE + ".tmp", STATE);
  } catch { /* the next tick retries */ }
}

function openLog(d) {
  const path = join(LOG_DIR, `${d.name}.log`);
  try {
    if (existsSync(path) && statSync(path).size > MAX_LOG) renameSync(path, path + ".1");
  } catch { /* file in use: rotate next time */ }
  return createWriteStream(path, { flags: "a" });
}

function start(d) {
  const r = (run[d.name] ??= { fails: 0, backoff: 2000 });
  const s = state.services[d.name];
  r.stream?.end();
  r.stream = openLog(d);
  r.stream.write(`\n===== ${now()} start (${state.mode}) =====\n`);
  const child = spawn(d.cmd, d.args, {
    cwd: d.cwd,
    env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", NEXT_TELEMETRY_DISABLED: "1", FORCE_COLOR: "0" },
    windowsHide: true,
  });
  r.child = child;
  r.fails = 0;
  r.startedMs = Date.now();
  Object.assign(s, { state: "starting", pid: child.pid, started_at: now(), last_error: null });
  const pipe = (stream) =>
    stream.on("data", (buf) => {
      r.stream.write(buf);
      if (!QUIET) for (const l of buf.toString().split(/\r?\n/)) if (l) process.stdout.write(`\x1b[36m[${d.name}]\x1b[0m ${l}\n`);
    });
  pipe(child.stdout);
  pipe(child.stderr);
  child.on("error", (e) => { s.last_error = e.message; });
  child.on("exit", (code) => {
    if (r.child !== child) return;
    r.child = null;
    Object.assign(s, { pid: null, last_exit_code: code, last_exit_at: now() });
    r.stream.write(`===== ${now()} exited with code ${code} =====\n`);
    if (stopping) { s.state = "stopped"; return; }
    s.state = "crashed";
    s.last_error ??= `to'xtadi (kod ${code})`;
    if (Date.now() - r.startedMs > 120_000) r.backoff = 2000; // it ran fine for a while
    say(`${d.title} to'xtadi (kod ${code}); ${r.backoff / 1000} s dan keyin qayta ishga tushiriladi`);
    r.timer = setTimeout(() => { s.restarts += 1; start(d); }, r.backoff);
    r.backoff = Math.min(r.backoff * 2, 60_000);
    saveState();
  });
  say(`${d.title} ishga tushirildi (PID ${child.pid}, port ${d.port})`);
  saveState();
}

async function checkHealth(d) {
  const r = run[d.name];
  const s = state.services[d.name];
  if (!r?.child) return;
  if (await healthy(d.health)) {
    if (s.state !== "running") say(`${d.title} tayyor`);
    Object.assign(s, { state: "running", last_healthy_at: now(), last_error: null });
    r.fails = 0;
    return;
  }
  if (s.state === "starting" && Date.now() - r.startedMs < START_GRACE_MS) return;
  r.fails += 1;
  s.state = "unhealthy";
  s.last_error = "javob bermayapti";
  if (r.fails >= HEALTH_FAILS_BEFORE_RESTART) {
    say(`${d.title} 1 daqiqa javob bermadi: qayta ishga tushirilmoqda`);
    r.fails = 0;
    killTree(r.child.pid); // the exit handler restarts it
  }
}

async function shutdown(reason) {
  if (stopping) return;
  stopping = true;
  say(`To'xtatilmoqda (${reason})...`);
  for (const r of Object.values(run)) clearTimeout(r.timer);
  for (const d of [...active].reverse()) {
    const r = run[d.name];
    if (r?.child) killTree(r.child.pid);
    state.services[d.name].state = "stopped";
    state.services[d.name].pid = null;
  }
  state.pid = null;
  saveState();
  rmSync(PID_FILE, { force: true });
  rmSync(STOP_FILE, { force: true });
  say("To'xtatildi.");
  own.end(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000);
}
process.on("SIGINT", () => void shutdown("Ctrl+C"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGBREAK", () => void shutdown("SIGBREAK"));

// API first: the camera and recognition services sync their configuration from it.
const api = active.find((d) => d.name === "api");
start(api);
for (let i = 0; i < 60 && !stopping; i++) {
  if (await healthy(api.health)) break;
  await sleep(1000);
}
await checkHealth(api);
for (const d of active) if (d !== api && !stopping) start(d);

say(`Tizim ishga tushdi: http://localhost:3000  (to'xtatish: Ctrl+C yoki npm run stop)`);

setInterval(async () => {
  if (stopping) return;
  if (existsSync(STOP_FILE)) return void shutdown("npm run stop");
  await Promise.all(active.map(checkHealth));
  for (const d of active) {
    const r = run[d.name];
    if (r?.stream && r.child) {
      try {
        if (statSync(join(LOG_DIR, `${d.name}.log`)).size > MAX_LOG) {
          r.stream.end();
          r.stream = openLog(d);
        }
      } catch { /* ignore */ }
    }
  }
  saveState();
}, TICK_MS);
