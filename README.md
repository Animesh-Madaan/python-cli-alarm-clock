# Python Alarm Clock

A command-line alarm clock for Linux, WSL, macOS, and Windows that runs as a background process and delivers alerts directly to your terminal.

## Features

- Set one-shot or daily recurring alarms
- Set a quick alarm N minutes from now with `alarm in`
- Enable / disable alarms without deleting them
- Edit label, time, or recurrence of an existing alarm
- Runs as a background process — survives terminal close
- ASCII art banner delivered to every open terminal window when an alarm fires
- macOS: native desktop notification via `osascript` + Glass sound
- Linux/WSL: direct PTY write reaches VS Code integrated terminals
- Windows: new console window with ASCII art banner + system ding sound (`winsound`)
- Snooze any alarm with a single command
- Live-tail the daemon log with `alarm log --follow`
- Alarms persist across restarts

## Requirements

- Python 3.10+
- Linux, WSL (Windows Subsystem for Linux), macOS, or Windows 10+

## Installation

```bash
git clone https://github.com/Animesh-Madaan/python-cli-alarm-clock.git
cd python-alarm-clock

python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux / WSL
# .venv\Scripts\activate.bat     # Windows (cmd)
# .venv\Scripts\Activate.ps1     # Windows (PowerShell)
pip install -e .
```

The daemon uses `pythonw.exe` (ships with Python) to run silently in the background. Alarm notifications use a new console window and `winsound` — both are part of the Python standard library. No third-party tools required.

## Usage

### Start the daemon

The daemon runs in the background and checks for alarms every 30 seconds.

```bash
alarm start
```

### Add an alarm

```bash
alarm add "Wake up" 07:30                # fires once tomorrow morning
alarm add "Daily standup" 09:00 --daily  # repeats every day
```

Time must be in 24-hour **HH:MM** format.

### Set an alarm N minutes from now

```bash
alarm in 25                    # fires in 25 min, auto-label
alarm in 25 "Take a break"     # fires in 25 min with custom label
alarm in 60 "Lunch" --daily    # fires in 60 min, then repeats daily
```

### List alarms

```bash
alarm list
```

```
                        Alarms
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ ID       ┃ Label           ┃ Time  ┃ Recurrence ┃ Next Fire        ┃ Enabled ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ 4c750529 │ Wake up         │ 07:30 │ once       │ 2026-06-04 07:30 │    ✓    │
│ a1b2c3d4 │ Daily standup   │ 09:00 │ daily      │ 2026-06-04 09:00 │    ✓    │
└──────────┴─────────────────┴───────┴────────────┴──────────────────┴─────────┘
```

### Snooze an alarm

```bash
alarm snooze 4c750529             # snooze for 5 minutes (default)
alarm snooze 4c750529 --minutes 10
```

You can use just the first few characters of the ID.

### Enable / disable an alarm

```bash
alarm disable 4c750529   # suspend without deleting
alarm enable  4c750529   # re-enable (next fire time is recomputed)
```

### Edit an alarm

```bash
alarm edit 4c750529 --label "New label"
alarm edit 4c750529 --time 08:00
alarm edit 4c750529 --label "Standup" --time 09:30 --daily
```

At least one of `--label`, `--time`, `--daily`, or `--once` is required.

### Remove an alarm

```bash
alarm remove 4c750529
```

### Check current time

```bash
alarm time
```

Shows the current local time and a countdown to the next enabled alarm.

### View daemon log

```bash
alarm log              # last 20 lines
alarm log --lines 50   # last 50 lines
alarm log --follow     # live tail (Ctrl-C to stop)
```

### Check status / stop

```bash
alarm status   # shows daemon PID and all alarms
alarm stop     # stops the background daemon
```

## How alerts work

When an alarm fires, an ASCII art banner is written directly to every open terminal window:

```
╔══════════════════════════════════════════════════════════╗
║                    ⏰  ALARM FIRED  ⏰                    ║
╠══════════════════════════════════════════════════════════╣
║       . . .          ALARM: Wake up                    ║
║    .  |_|_|  .                                         ║
║   /| (O   O) |\      Scheduled : 07:30                 ║
║  / |  )   (  | \     Snooze    : alarm snooze 4c750529 ║
║ |  | /|   |\ |  |    Remove    : alarm remove 4c750529 ║
║ |  |/ |   | \|  |                                      ║
║  \ |  |___|  | /                                       ║
║   \|_________|/                                        ║
║       |   |                                            ║
║     __|___|__                                          ║
║    /         \                                         ║
╚══════════════════════════════════════════════════════════╝
```

Your terminal also beeps (`\a` bell) when the alarm fires.

**macOS** — a native desktop notification with sound is shown via `osascript`, and the banner is also written to all open Terminal/iTerm windows.

**Linux / WSL** — the banner is written directly to all open terminal PTYs (`/dev/pts/*`), reaching VS Code integrated terminals and SSH sessions. On Linux with a desktop environment, `notify-send` also shows a popup if installed.

**Windows** — a system ding sound plays immediately, then a new console window opens showing the full ASCII art banner. The window stays open until you press Enter. The daemon runs silently in the background via `pythonw.exe` (no console window).

> **Note:** The daemon must be running (`alarm start`) for alerts to fire. After a reboot, run `alarm start` again.

## Data files

All runtime data is stored in `~/.alarm-clock/`:

| File | Contents |
|---|---|
| `alarms.json` | Saved alarms |
| `alarm.pid` | Daemon process ID |
| `alarm.log` | Daemon logs (useful for debugging) |

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

## Project structure

```
alarm_clock/
├── cli.py        # All CLI commands
├── daemon.py     # Background process management (POSIX + Windows)
├── scheduler.py  # Alarm check loop
├── store.py      # Read/write alarms.json with file locking
└── notify.py     # ASCII art notifications (platform-aware)
tests/
├── test_store.py
└── test_scheduler.py
```

## Platform support

| Platform | Terminal alert | Desktop notification | Background process | Notes |
|---|---|---|---|---|
| Linux | ✓ `/dev/pts/*` write | ✓ `notify-send` (if installed) | ✓ double-fork daemon | ✓ tested |
| WSL (Windows) | ✓ `/dev/pts/*` write | ✗ no D-Bus | ✓ double-fork daemon | ✓ tested |
| macOS | ✓ `/dev/ttys*` write | ✓ `osascript` + sound | ✓ double-fork daemon | ✓ tested |
| Windows (native) | ✓ new console window + ding | ✗ | ✓ `pythonw.exe` subprocess | ✓ tested |
