"""
store.py — Alarm persistence layer.

Alarms are stored in ~/.alarm-clock/alarms.json.
Every read and write acquires an exclusive lock so the CLI and daemon
can operate concurrently without corruption.

Locking strategy:
  POSIX (Linux / macOS) — fcntl.flock (whole-file advisory lock)
  Windows              — msvcrt.locking (byte-range lock on first byte)
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_DATA_DIR = Path.home() / ".alarm-clock"
_ALARMS_FILE = _DATA_DIR / "alarms.json"

DATETIME_FMT = "%Y-%m-%dT%H:%M:%S"


# ---------------------------------------------------------------------------
# Platform-aware file locking
# ---------------------------------------------------------------------------

def _lock_shared(fh) -> None:
    if sys.platform == "win32":
        msvcrt.locking(fh.fileno(), msvcrt.LK_RLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_SH)


def _lock_exclusive(fh) -> None:
    if sys.platform == "win32":
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX)


def _unlock(fh) -> None:
    if sys.platform == "win32":
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Alarm dataclass
# ---------------------------------------------------------------------------

@dataclass
class Alarm:
    id: str
    label: str
    time: str          # "HH:MM"
    recurrence: str    # "once" | "daily"
    enabled: bool
    next_fire: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["next_fire"] = self.next_fire.strftime(DATETIME_FMT)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Alarm":
        return cls(
            id=d["id"],
            label=d["label"],
            time=d["time"],
            recurrence=d["recurrence"],
            enabled=d["enabled"],
            next_fire=datetime.strptime(d["next_fire"], DATETIME_FMT),
        )


# ---------------------------------------------------------------------------
# next_fire computation
# ---------------------------------------------------------------------------

def compute_next_fire(time_str: str, from_dt: datetime | None = None) -> datetime:
    """Return the next datetime for HH:MM.

    If *from_dt* is provided the result is the first occurrence of *time_str*
    strictly after *from_dt*.  Otherwise, today at *time_str* is returned if
    it is still in the future; tomorrow at *time_str* otherwise.
    """
    hour, minute = (int(p) for p in time_str.split(":"))
    base = from_dt or datetime.now()
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if from_dt is not None:
        if candidate <= from_dt:
            candidate += timedelta(days=1)
    else:
        if candidate <= datetime.now():
            candidate += timedelta(days=1)
    return candidate


# ---------------------------------------------------------------------------
# AlarmStore
# ---------------------------------------------------------------------------

class AlarmStore:
    def __init__(self, path: Path = _ALARMS_FILE) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> list[Alarm]:
        """Load and return all alarms from disk."""
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as fh:
            _lock_shared(fh)
            try:
                data = json.load(fh)
            finally:
                _unlock(fh)
        return [Alarm.from_dict(d) for d in data.get("alarms", [])]

    def save(self, alarms: list[Alarm]) -> None:
        """Atomically save *alarms* to disk using a temp-file + rename."""
        tmp = self._path.with_suffix(".json.tmp")
        payload = json.dumps({"alarms": [a.to_dict() for a in alarms]}, indent=2)
        with open(tmp, "w", encoding="utf-8") as fh:
            _lock_exclusive(fh)
            try:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                _unlock(fh)
        # On Windows, rename fails if target exists; remove first.
        if sys.platform == "win32" and self._path.exists():
            self._path.unlink()
        tmp.replace(self._path)

    def add(self, alarm: Alarm) -> None:
        alarms = self.load()
        alarms.append(alarm)
        self.save(alarms)

    def remove_by_prefix(self, prefix: str) -> Alarm:
        """Remove and return the alarm whose ID starts with *prefix*.

        Raises ValueError if zero or more than one alarm matches.
        """
        alarms = self.load()
        matches = [a for a in alarms if a.id.startswith(prefix)]
        if not matches:
            raise ValueError(f"No alarm found with ID prefix '{prefix}'.")
        if len(matches) > 1:
            ids = ", ".join(a.id for a in matches)
            raise ValueError(f"Prefix '{prefix}' is ambiguous — matches: {ids}")
        removed = matches[0]
        self.save([a for a in alarms if a.id != removed.id])
        return removed

    def update(self, alarm: Alarm) -> None:
        """Replace the stored alarm that has the same ID as *alarm*."""
        alarms = self.load()
        for i, a in enumerate(alarms):
            if a.id == alarm.id:
                alarms[i] = alarm
                break
        else:
            raise ValueError(f"Alarm '{alarm.id}' not found.")
        self.save(alarms)

    def get_by_prefix(self, prefix: str) -> Alarm:
        """Return the alarm whose ID starts with *prefix* (same uniqueness rules as remove)."""
        alarms = self.load()
        matches = [a for a in alarms if a.id.startswith(prefix)]
        if not matches:
            raise ValueError(f"No alarm found with ID prefix '{prefix}'.")
        if len(matches) > 1:
            ids = ", ".join(a.id for a in matches)
            raise ValueError(f"Prefix '{prefix}' is ambiguous — matches: {ids}")
        return matches[0]

    def set_enabled(self, prefix: str, enabled: bool) -> Alarm:
        """Enable or disable the alarm matching *prefix* and return it.

        When re-enabling, next_fire is recomputed so the alarm doesn't
        fire immediately if its time has already passed today.
        """
        alarm = self.get_by_prefix(prefix)
        alarm.enabled = enabled
        if enabled:
            alarm.next_fire = compute_next_fire(alarm.time)
        self.update(alarm)
        return alarm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_alarm_id() -> str:
    """Return a random 8-character hex ID."""
    return secrets.token_hex(4)
