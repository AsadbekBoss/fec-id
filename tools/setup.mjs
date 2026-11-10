// One-time local setup:
//  1. Creates .env files from .env.example, replacing "change-me" placeholders with random secrets
//     (generated locally, never stored in source code).
//  2. Creates the Python virtualenv in apps/api/.venv and installs dependencies.
//  3. Installs frontend dependencies.
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { API_DIR, IS_WIN, ROOT, WEB_DIR, venvPython } from "./paths.mjs";

const secret = () => randomBytes(24).toString("base64url");

function sh(cmd, args, cwd) {
  console.log(`> ${cmd} ${args.join(" ")}`);
  // npm is a .cmd shim on Windows and needs a shell; pass one command string (no untrusted args).
  const r =
    IS_WIN && cmd === "npm"
      ? spawnSync([cmd, ...args].join(" "), { cwd, stdio: "inherit", shell: true })
      : spawnSync(cmd, args, { cwd, stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

// 1. env files ---------------------------------------------------------------
const encKey = () => `1:${randomBytes(32).toString("base64url")}`;
const readVar = (file, name) =>
  existsSync(file) ? new RegExp(`^${name}=(.*)$`, "m").exec(readFileSync(file, "utf8"))?.[1] : undefined;

/** Adds `name=value` to an existing env file if the variable is missing (upgrades old setups). */
function ensureVar(file, name, value) {
  const current = readVar(file, name);
  if (current && !current.includes("generate-me")) return current;
  let txt = readFileSync(file, "utf8");
  txt = current !== undefined
    ? txt.replace(new RegExp(`^${name}=.*$`, "m"), `${name}=${value}`)
    : `${txt.trimEnd()}\n${name}=${value}\n`;
  writeFileSync(file, txt);
  console.log(`Added ${name} to ${file.replace(ROOT, ".")}`);
  return value;
}

const rootEnv = join(ROOT, ".env");
const apiEnv = join(API_DIR, ".env");
// Reuse whatever already exists so the root and API env files stay consistent.
const pgPassword = readVar(rootEnv, "POSTGRES_PASSWORD") ?? readVar(apiEnv, "POSTGRES_PASSWORD") ?? secret();
const existing = (name) =>
  [readVar(rootEnv, name), readVar(apiEnv, name)].find((k) => k && !k.includes("generate-me"));
const dataKey = existing("DATA_ENCRYPTION_KEYS") ?? encKey();
const jwtKey = existing("JWT_SIGNING_KEYS") ?? encKey(); // Ed25519 seed: also 32 random bytes

if (!existsSync(rootEnv)) {
  const txt = readFileSync(join(ROOT, ".env.example"), "utf8")
    .replace("POSTGRES_PASSWORD=change-me", `POSTGRES_PASSWORD=${pgPassword}`)
    .replace("VALKEY_PASSWORD=change-me-too", `VALKEY_PASSWORD=${secret()}`)
    .replace("DATA_ENCRYPTION_KEYS=1:generate-me", `DATA_ENCRYPTION_KEYS=${dataKey}`)
    .replace("JWT_SIGNING_KEYS=1:generate-me", `JWT_SIGNING_KEYS=${jwtKey}`);
  writeFileSync(rootEnv, txt);
  console.log("Created .env with random local secrets");
} else {
  ensureVar(rootEnv, "DATA_ENCRYPTION_KEYS", dataKey);
  ensureVar(rootEnv, "JWT_SIGNING_KEYS", jwtKey);
}

if (!existsSync(apiEnv)) {
  const txt = readFileSync(join(API_DIR, ".env.example"), "utf8")
    .replace("POSTGRES_PASSWORD=change-me", `POSTGRES_PASSWORD=${pgPassword}`)
    .replace("DATA_ENCRYPTION_KEYS=1:generate-me", `DATA_ENCRYPTION_KEYS=${dataKey}`)
    .replace("JWT_SIGNING_KEYS=1:generate-me", `JWT_SIGNING_KEYS=${jwtKey}`);
  writeFileSync(apiEnv, txt);
  console.log("Created apps/api/.env (secrets match root .env)");
} else {
  ensureVar(apiEnv, "DATA_ENCRYPTION_KEYS", dataKey);
  ensureVar(apiEnv, "JWT_SIGNING_KEYS", jwtKey);
}
console.log("IMPORTANT: back up DATA_ENCRYPTION_KEYS separately; without it encrypted data is lost.");

const webEnv = join(WEB_DIR, ".env.local");
if (!existsSync(webEnv)) {
  copyFileSync(join(WEB_DIR, ".env.example"), webEnv);
  console.log("Created apps/web/.env.local");
}

// 2. Python virtualenv ---------------------------------------------------------
const venvDir = join(API_DIR, ".venv");
if (!existsSync(venvDir)) {
  const candidates = IS_WIN
    ? [["py", ["-3.12"]], ["py", ["-3.13"]], ["py", ["-3.14"]], ["py", ["-3"]]]
    : [["python3.12", []], ["python3.13", []], ["python3.14", []], ["python3", []]];
  const found = candidates.find(
    ([c, a]) => spawnSync(c, [...a, "-c", "import sys; assert (3,12) <= sys.version_info[:2] <= (3,14)"]).status === 0,
  );
  if (!found) {
    console.error("Python 3.12–3.14 not found. Install it from https://www.python.org/downloads/");
    process.exit(1);
  }
  sh(found[0], [...found[1], "-m", "venv", ".venv"], API_DIR);
}
sh(venvPython(), ["-m", "pip", "install", "-q", "--upgrade", "pip"], API_DIR);
sh(venvPython(), ["-m", "pip", "install", "-q", "-r", "requirements-dev.txt"], API_DIR);

// 3. Frontend ---------------------------------------------------------------------
sh("npm", ["install"], WEB_DIR);

console.log("\nSetup complete. Start both apps with:  npm run dev");
