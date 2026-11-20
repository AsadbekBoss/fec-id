// Registers (or removes) a daily backup task in Windows Task Scheduler.
//   node tools/backup-schedule.mjs [HH:MM]   -> daily at HH:MM (default 20:00)
//   node tools/backup-schedule.mjs --remove
//
// Uses PowerShell's Register-ScheduledTask with a separate working directory: schtasks.exe
// cannot handle quoted paths with spaces (e.g. "AI FECK ID") inside /TR reliably.
import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { IS_WIN, ROOT } from "./paths.mjs";

const TASK = "AttendanceDailyBackup";
if (!IS_WIN) {
  console.error("Windows only. On Linux add to crontab:  0 20 * * *  cd <repo> && npm run backup");
  process.exit(1);
}
const arg = process.argv[2] ?? "20:00";
// Values reach PowerShell through environment variables, never by string concatenation.
const ps = (script, env = {}) =>
  spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    stdio: "inherit",
    env: { ...process.env, ...env },
  }).status ?? 1;

if (arg === "--remove") {
  const s = ps(`Unregister-ScheduledTask -TaskName $env:ATT_TASK -Confirm:$false; "Removed task $env:ATT_TASK"`, { ATT_TASK: TASK });
  process.exit(s);
}
if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(arg)) {
  console.error("Time must be HH:MM, e.g. 20:00");
  process.exit(2);
}
mkdirSync(join(ROOT, "backups"), { recursive: true });
const script = [
  `$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c npm run backup >> backups\\backup.log 2>&1' -WorkingDirectory $env:ATT_ROOT`,
  `$trigger = New-ScheduledTaskTrigger -Daily -At $env:ATT_TIME`,
  `$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries`,
  `Register-ScheduledTask -TaskName $env:ATT_TASK -Action $action -Trigger $trigger -Settings $settings -Description 'AI Employee Attendance: encrypted daily backup' -Force | Out-Null`,
  `$info = Get-ScheduledTask -TaskName $env:ATT_TASK | Get-ScheduledTaskInfo`,
  `"Daily backup scheduled at $env:ATT_TIME (task $env:ATT_TASK). Next run: $($info.NextRunTime). Log: backups\\backup.log"`,
].join("; ");
process.exit(ps(script, { ATT_ROOT: ROOT, ATT_TIME: arg, ATT_TASK: TASK }));
