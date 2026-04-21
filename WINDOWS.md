# jcrefresher — Windows Setup Guide

A friendly guide for installing jcrefresher on Windows 10 or 11.

---

## Requirements

- **Windows 10 or 11** (both work)
- **winget** — the Windows Package Manager. It comes pre-installed on modern Windows. If you are not sure whether you have it, open PowerShell and type `winget --version`. If it prints a version number, you are good to go.

The installer handles everything else (Python, uv, watchdog) automatically.

---

## How to Install

1. Open the `jcrefresher` folder in File Explorer.
2. Right-click `install.ps1` and choose **Run with PowerShell**.
   - If Windows asks "Do you want to allow this app to make changes?", click **Yes**.
   - If you see a blue "Windows protected your PC" screen, click **More info** then **Run anyway**.

Alternatively, open PowerShell in the folder and run:

```powershell
.\install.ps1
```

The script will:
- Install Python 3 and uv if they are missing
- Find your jcodemunch index folder
- Create a virtual environment and install dependencies
- Register a Task Scheduler task called `jcrefresher` that starts automatically when you log in
- Start the task straight away

---

## What Happens if `.code-index` Is Not Found

jcrefresher looks for the jcodemunch index database in these locations:

- `%USERPROFILE%\.code-index`
- `%LOCALAPPDATA%\.code-index`
- `%APPDATA%\.code-index`

If none of them exist, the installer will pause and display this diagnostic snippet. Copy it and paste it to Claude Code so it can help you find the right folder:

```
Please find where jcodemunch stores its index database on this machine.
Check these locations and tell me which one contains .db files:
- $HOME\.code-index\
- $env:LOCALAPPDATA\.code-index\
- $env:APPDATA\.code-index\
Run: dir $HOME\.code-index, dir $env:LOCALAPPDATA\.code-index, dir $env:APPDATA\.code-index
Then tell me the full path of the folder that contains .db files.
```

Once Claude Code tells you the path, paste it into the installer prompt and press Enter.

---

## Checking That It Is Running

Open PowerShell and run:

```powershell
Get-ScheduledTask -TaskName jcrefresher
```

The `State` column should show `Running` or `Ready`.

To see the last run time and result:

```powershell
Get-ScheduledTaskInfo -TaskName jcrefresher
```

You can also open **Task Scheduler** from the Start menu, look under **Task Scheduler Library**, and find the `jcrefresher` entry.

---

## How to Uninstall

1. Right-click `uninstall.ps1` in File Explorer and choose **Run with PowerShell**.

Or in PowerShell:

```powershell
.\uninstall.ps1
```

This will stop the task, remove it from Task Scheduler, and delete the virtual environment.
