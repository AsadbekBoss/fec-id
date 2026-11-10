// Creates the application role + database on an existing PostgreSQL server (see tools/db-init.sql).
// Reads POSTGRES_* from apps/api/.env (environment variables override). psql asks for the
// superuser password interactively, and the app password is passed via an env var, not argv.
//
//   npm run db:init                      # superuser "postgres" on POSTGRES_HOST:POSTGRES_PORT
//   npm run db:init -- --superuser admin
//   npm run db:init -- --createdb        # also allow CREATE DATABASE (needed for DB tests)
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { API_DIR, IS_WIN, ROOT } from "./paths.mjs";

function loadEnv(file) {
  const out = {};
  if (!existsSync(file)) return out;
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

function findPsql() {
  const probe = spawnSync(IS_WIN ? "where" : "which", ["psql"], { encoding: "utf8" });
  if (probe.status === 0) return probe.stdout.split(/\r?\n/)[0].trim();
  if (IS_WIN) {
    const base = "C:\\Program Files\\PostgreSQL";
    if (existsSync(base)) {
      const versions = readdirSync(base).filter((v) => /^\d+$/.test(v)).sort((a, b) => b - a);
      for (const v of versions) {
        const p = join(base, v, "bin", "psql.exe");
        if (existsSync(p)) return p;
      }
    }
  }
  return null;
}

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};

const env = { ...loadEnv(join(API_DIR, ".env")), ...process.env };
const required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"];
for (const k of required) {
  if (!env[k] || env[k] === "change-me") {
    console.error(`${k} is not set in apps/api/.env. Run "npm run setup" first.`);
    process.exit(1);
  }
}

const psql = findPsql();
if (!psql) {
  console.error("psql not found. Install PostgreSQL client tools or add psql to PATH.");
  process.exit(1);
}

const superuser = opt("--superuser", "postgres");
console.log(
  `Initialising database "${env.POSTGRES_DB}" for role "${env.POSTGRES_USER}" on ` +
    `${env.POSTGRES_HOST ?? "localhost"}:${env.POSTGRES_PORT ?? "5432"} as superuser "${superuser}"`,
);
const r = spawnSync(
  psql,
  [
    "-h", env.POSTGRES_HOST ?? "localhost",
    "-p", String(env.POSTGRES_PORT ?? "5432"),
    "-U", superuser,
    "-d", "postgres",
    "-X", "-q",
    "-f", join(ROOT, "tools", "db-init.sql"),
  ],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      APP_DB_NAME: env.POSTGRES_DB,
      APP_DB_USER: env.POSTGRES_USER,
      APP_DB_PASSWORD: env.POSTGRES_PASSWORD,
      APP_DB_CREATEDB: args.includes("--createdb") ? "true" : "false",
      PGCLIENTENCODING: "UTF8",
    },
  },
);
process.exit(r.status ?? 1);
