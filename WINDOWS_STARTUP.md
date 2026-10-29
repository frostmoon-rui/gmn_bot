# Windows startup without interactive sign-in

`tools/windows_boot.ps1` replaces login-only VBS launchers with a password-logon
Task Scheduler task. It runs the existing Mihomo core, NapCat Shell and Python
under the same Windows account, preserving that account's QQ login data.
It does not enable automatic Windows sign-in or store passwords in project files.

Create a **private**, ignored `windows_startup.local.json` in the project root:

```json
{
  "ProjectDirectory": "D:\\Bots\\private-runtime",
  "PythonExecutable": "C:\\Python312\\python.exe",
  "ProxyExecutable": "C:\\Program Files\\Clash Verge\\verge-mihomo.exe",
  "ProxyDirectory": "C:\\Users\\YOUR_USER\\AppData\\Roaming\\io.github.clash-verge-rev.clash-verge-rev",
  "ProxyConfiguration": "C:\\Users\\YOUR_USER\\AppData\\Roaming\\io.github.clash-verge-rev.clash-verge-rev\\clash-verge.yaml",
  "ProxyPort": 7897,
  "NapCatDirectory": "C:\\ProgramData\\NapCatQQ Desktop\\components\\NapCatQQ",
  "QQExecutable": "C:\\Program Files\\Tencent\\QQNT\\QQ.exe",
  "NapCatPort": 3001,
  "BotAccount": "YOUR_BOT_NUMBER",
  "UserName": "YOUR_COMPUTER\\YOUR_USER",
  "TaskName": "GMN-Bot-Startup"
}
```

Use actual absolute paths, a numeric bot account and an existing, working proxy
configuration. Preserve its node selection/cache and existing network settings.
The core itself does not run Verge's subscription/software update timers.
The supervisor sets HTTP_PROXY, HTTPS_PROXY and ALL_PROXY from the private
startup configuration's ProxyPort. The bot also accepts MODEL_PROXY in private
local_settings.json; set it to the same loopback URL to override stale desktop
proxy settings. An open listening port alone does not establish that a model
endpoint is reachable. MODEL_PROXY must not be published with proxy credentials.
Disable the GUI's automatic subscription updates separately. Do not publish this
JSON, proxy configuration, QQ data, startup logs or recovery backups.

From the private runtime, validate without launching any application:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows_boot.ps1 -Mode Validate
```

Open PowerShell **as administrator under the same Windows account**, then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows_boot.ps1 -Mode Install
```

Alternatively, double-click `Install-Windows-Startup.cmd` in the private runtime.
Its helper opens a visible elevated PowerShell window for UAC and password entry.

Enter the Windows account password in the local credential prompt, not the PIN.
Use the existing local Windows profile name even when it is Microsoft-linked.
A cloud account SID must not be compared directly with the local profile SID.
Error `0x8007052e` means Windows rejected the supplied
credentials. Stop repeated attempts; check the account/password and, if its online
password recently changed, sign in to Windows with the password once to refresh
cached credentials. No passwordless sign-in or security-policy changes are made.
The helper also supports `-UseLocalPassword` for hidden terminal password input.
If elevation is required, it passes a DPAPI-protected credential over a temporary
same-user-only named pipe after normal UAC approval. It creates no password file
and never puts a plaintext password on a process command line.

If registration repeatedly fails, run `tools/diagnose_windows_logon.ps1` from an
elevated Windows PowerShell window. It reads recent Security 4625 failure metadata
and writes a private `startup_logon_diagnostic.local.json` report. It never
submits a password or changes policy, and omits account names from event records.
Substatus `0xc000006a` is a password mismatch; `0xc000015b` indicates a missing
logon right. Do not keep retrying or change unrelated account policies.
For a Microsoft-linked profile, check Settings > Accounts > Sign-in options >
Additional settings > "Only allow Windows Hello sign-in for Microsoft accounts on
this device". If the owner chooses to allow password sign-in, turn that option
off manually and verify one successful Windows password sign-in before retrying.
The diagnostic also reports the local passwordless configuration marker; confirm
the setting in Windows Settings rather than treating that marker alone as proof
of the failure's cause. The installer does not change this security setting.
Windows stores the task credential; changing that password requires re-registering
the task. Installation backs up a previous matching task, registers a boot trigger
with a 30-second delay and Password logon, then moves `silent_start.vbs` and
`Clash Verge.lnk` from the user's Startup folder to a private timestamped backup.
It never starts/stops the live bot or reboots Windows. If registration fails,
the old login-only entries are left in place. Do not re-enable GUI auto-launch.

After a controlled reboot, leave Windows at its sign-in screen and verify the bot
from another device. This is the actual pre-login acceptance test; offline checks
cannot establish that an existing QQ login will survive a noninteractive session.
Expired QQ credentials, Windows account restrictions, inaccessible drives, OneDrive
online-only files and mismatched QQ/NapCat versions may still require intervention.
Keep all required files downloaded locally. QR verification must be done by a person.

The supervisor inspects local listening sockets, waits for the proxy and NapCat
before Python, avoids known duplicate processes, and retries exited components
with at least 60 seconds between starts. A running but unready QQ/core is logged,
not repeatedly killed. This is process/listener supervision, not a remote
connectivity or QQ-login guarantee. Logs are in `logs/startup/`; protect them as
private bot records and periodically archive older logs.

Check status after sign-in without sending QQ messages:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows_boot.ps1 -Mode Status
```

Do not run GUI managers alongside the supervised headless stack: they can compete
for the same core, ports or QQ account. To return to GUI operation, disable/stop
the `GMN-Bot-Startup` task in Task Scheduler, verify its processes have stopped,
then open the GUI. Restore the backed-up Startup entries if returning permanently
to login-only startup. Keep copies of QQ, proxy and bot data before migration.
