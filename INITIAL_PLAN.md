# Python CLI Alarm Clock — Implementation Plan

## Requirements (confirmed)

| Concern | Decision |
|---|---|
| Runtime model | **Daemon** — double-fork, survives terminal close |
| Persistence | **JSON file** at `~/.alarm-clock/alarms.json` |
| Notification | **`wall`** (always) + **`notify-send`** (if on PATH, best-effort) + `\a` bell |
| Alarm types | One-shot, daily recurrence, snooze |
| CLI framework | `click` + `rich` |
| Python | ≥ 3.10 |
| Entry point | `alarm` |

---

## Project Structure

```
python-alarm-clock/
├── alarm_clock/
│   ├── __init__.py
│   ├── cli.py         # Click entry points for all commands
│   ├── daemon.py      # Double-fork, PID file, start/stop/status
│   ├── scheduler.py   # Alarm check loop + next_fire computation
│   ├── store.py       # Load/save alarms.json with fcntl.flock
│   └── notify.py      # notify-send (if available) + wall + \a bell
├── pyproject.toml
└── tests/
    ├── test_store.py
    └── test_scheduler.py
```

---

## Alarm Schema (`~/.alarm-clock/alarms.json`)

```json
{
  "alarms": [
    {
      "id": "abc12345",
      "label": "Wake up",
      "time": "07:30",
      "recurrence": "once",
      "enabled": true,
      "next_fire": "2026-06-04T07:30:00"
    }
  ]
}
```

`recurrence` is either `"once"` or `"daily"`.

---

## Data Layer — `store.py`

- `Alarm` dataclass: `id` (8-hex), `label`, `time` (HH:MM str), `recurrence`, `enabled: bool`, `next_fire: datetime`
- `AlarmStore.load()` / `.save()` — wrap every file op with `fcntl.flock(LOCK_EX)`
- `~/.alarm-clock/` directory created on first use
- `compute_next_fire(time_str: str) -> datetime` — today at HH:MM if still in the future, else tomorrow

---

## Scheduler — `scheduler.py`

- `run_loop(interval=30)`:
  - Loop: load alarms, find all where `enabled and next_fire <= now()`
  - For each fired alarm: call `notify.fire(alarm)`, then:
    - `daily` → `next_fire += timedelta(days=1)`
    - `once` → `enabled = False`
  - Save back; sleep `interval` seconds

---

## Notification — `notify.py`

- `fire(alarm)`:
  1. Try `notify-send` if `shutil.which("notify-send")` is not None
  2. Always run `wall` with `\a` (bell char) prepended to the message
  3. Message includes: label, scheduled time, snooze hint `alarm snooze <id>`

---

## Daemon — `daemon.py`

- `start()`: check no existing PID → double-fork → redirect stdio to `/dev/null` → write PID file → call `scheduler.run_loop()`
- `stop()`: read PID file → `os.kill(pid, SIGTERM)` → remove PID file
- `status()`: read PID file → `os.kill(pid, 0)` → report alive/dead
- Fork is done **before** Click context initialisation to avoid stale TTY descriptors
- PID file: `~/.alarm-clock/alarm.pid`
- Log file: `~/.alarm-clock/alarm.log` (stderr redirect from daemon)

---

## CLI Commands — `cli.py`

| Command | Behaviour |
|---|---|
| `alarm add <label> <HH:MM> [--daily]` | Validate time format, compute `next_fire`, append to store, print ID |
| `alarm list` | Rich table: ID, label, time, recurrence, next fire, enabled |
| `alarm remove <id>` | Remove by ID prefix match |
| `alarm snooze <id> [--minutes 5]` | Set `next_fire = now + N min`, re-enable if disabled |
| `alarm start` | Call `daemon.start()`, print PID |
| `alarm stop` | Call `daemon.stop()` |
| `alarm status` | Show daemon up/down + inline `alarm list` |

`alarm remove` uses prefix matching (e.g. `alarm remove abc1` matches `abc12345`).

---

## Implementation Phases

1. **Scaffold** — `pyproject.toml`, `alarm_clock/__init__.py`
2. **`store.py`** — `Alarm` dataclass, `AlarmStore`, `compute_next_fire()`
3. **`scheduler.py`** — `run_loop()`, firing + mutation logic
4. **`notify.py`** — `fire()` with `wall` + optional `notify-send`
5. **`daemon.py`** — double-fork, PID file, start/stop/status
6. **`cli.py`** — all Click subcommands wired up
7. **Tests** — `test_store.py`, `test_scheduler.py`

---

## Verification Checklist

- [ ] `pip install -e .` succeeds; `alarm --help` shows all subcommands
- [ ] `alarm add "Test" <HH:MM>` → `alarm list` shows correct `next_fire`
- [ ] `alarm start` → `alarm status` shows daemon PID
- [ ] Wait for alarm to fire → `wall` message appears with bell
- [ ] `alarm snooze <id>` → `alarm list` shows updated `next_fire`
- [ ] `alarm stop` → `alarm status` shows daemon not running
- [ ] `pytest tests/` — all tests pass

---

## Out of Scope

- Weekday/cron-style recurrence
- Audio file playback
- TUI / interactive mode
- Timezone-aware datetimes (uses local time throughout)
- Daemon auto-start on login (user can add `alarm start` to `~/.bashrc` or a systemd user unit)
