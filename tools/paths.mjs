import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const API_DIR = join(ROOT, "apps", "api");
export const WEB_DIR = join(ROOT, "apps", "web");
export const IS_WIN = process.platform === "win32";

/** Python inside the API virtualenv (created by `npm run setup`). */
export function venvPython() {
  const p = IS_WIN
    ? join(API_DIR, ".venv", "Scripts", "python.exe")
    : join(API_DIR, ".venv", "bin", "python");
  if (!existsSync(p)) {
    console.error(`Python virtualenv not found at ${p}\nRun: npm run setup`);
    process.exit(1);
  }
  return p;
}
