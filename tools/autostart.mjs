// Starts the whole system automatically when this Windows user logs on (Task Scheduler).
//   node tools/autostart.mjs            -> register (and start now if it is not running)
//   node tools/autostart.mjs --remove   -> unregister
//
// The task runs `node tools/run.mjs --quiet` in a hidden window, 20 s after logon, with no time
// limit. The supervisor itself restarts crashed services; the task restarts the supervisor.
import { spawnSync } from "node:child_process";
import { IS_WIN, ROOT } from "./paths.mjs";

const TASK = "AttendanceSystem";
if (!IS_WIN) {
  console.error("Windows only. On Linux use a systemd unit that runs: node tools/run.mjs --quiet");
  process.exit(1);
}
// Values reach PowerShell through environment variables, never by string concatenation.
const ps = (script, env = {}) =>
  spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    stdio: "inherit",
    env: { ...process.env, ATT_TASK: TASK, ...env },
  }).status ?? 1;

if (process.argv[2] === "--remove") {
  process.exit(ps(`Unregister-ScheduledTask -TaskName $env:ATT_TASK -Confirm:$false; "Avtomatik ishga tushirish o'chirildi ($env:ATT_TASK). Tizimni to'xtatish: npm run stop"`));
}

const script = [
  `$user = "$env:USERDOMAIN\\$env:USERNAME"`,
  `$cmd = "& '" + $env:ATT_NODE.Replace("'", "''") + "' tools\\run.mjs --quiet"`,
  `$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -Command "' + $cmd + '"') -WorkingDirectory $env:ATT_ROOT`,
  `$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user`,
  `$trigger.Delay = 'PT20S'`,
  `$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)`,
  `$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited`,
  `Register-ScheduledTask -TaskName $env:ATT_TASK -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'AI Employee Attendance: API, recognition, camera and web (tools/run.mjs)' -Force | Out-Null`,
  `"Avtomatik ishga tushirish yoqildi: kompyuterga kirgandan 20 soniya keyin tizim o'zi yonadi (vazifa: $env:ATT_TASK)."`,
].join("; ");
const status = ps(script, { ATT_ROOT: ROOT, ATT_NODE: process.execPath });
if (status !== 0) process.exit(status);

// Start it now unless the system is already running (e.g. from a terminal).
const running = spawnSync(process.execPath, ["tools/run.mjs", "--status"], { cwd: ROOT, stdio: "ignore" }).status === 0;
if (running) {
  console.log("Tizim hozir terminalda ishlayapti; keyingi safar kompyuter yonganda vazifa o'zi ishga tushiradi.");
} else {
  ps(`Start-ScheduledTask -TaskName $env:ATT_TASK; "Tizim fonda ishga tushirildi. Holat: npm run status (1-2 daqiqada tayyor bo'ladi)"`);
}
