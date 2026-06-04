"""
daemon.py — Cross-platform background daemon management.

POSIX (Linux / macOS):
  Uses the classic double-fork technique to fully detach from the
  controlling terminal, then calls scheduler.run_loop() in the grandchild.

Windows:
  Uses subprocess.Popen with DETACHED_PROCESS | CREATE_NO_WINDOW flags,
  which runs alarm_clock.scheduler as a hidden background process.
  Stopped via TerminateProcess (os.kill with CTRL_BREAK_EVENT then kill).

Both paths write a PID file to ~/.alarm-clock/alarm.pid.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

_DATA_DIR = Path.home() / ".alarm-clock"
_PID_FILE = _DATA_DIR / "alarm.pid"
_LOG_FILE = _DATA_DIR / "alarm.log"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class DaemonError(RuntimeError):
    pass


def start() -> int:
    """Start the background daemon and return its PID.

    Raises DaemonError if a daemon is already running.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    alive, existing_pid = status()
    if alive:
        raise DaemonError(f"Daemon already running (PID {existing_pid}).")

    # Remove stale PID file if daemon is dead.
    if _PID_FILE.exists():
        _PID_FILE.unlink()

    if sys.platform == "win32":
        return _start_windows()
    else:
        return _start_posix()


def _start_windows() -> int:
    """Launch a detached background process on Windows.

    Uses pythonw.exe (the windowless Python launcher) so no console window
    ever appears.  pythonw.exe is a GUI-subsystem binary: it has no console,
    does not inherit the parent's console, and survives terminal close without
    needing DETACHED_PROCESS (which conflicts with CREATE_NO_WINDOW and causes
    a spurious console window to flash on launch).
    """
    import subprocess
    from pathlib import Path as _Path

    py = _Path(sys.executable)
    pythonw = py.parent / "pythonw.exe"
    if pythonw.exists():
        executable = str(pythonw)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Fall back to python.exe hidden behind CREATE_NO_WINDOW.
        executable = str(py)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

    log_fh = open(_LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [executable, "-m", "alarm_clock.scheduler"],
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        creationflags=flags,
    )
    log_fh.close()
    _PID_FILE.write_text(str(proc.pid))
    return proc.pid


def _start_posix() -> int:
    """Double-fork daemon on POSIX (Linux / macOS)."""
    import time as _time

    # --- first fork ---
    pid = os.fork()
    if pid > 0:
        # Parent: wait for first child, then poll for grandchild's PID file.
        os.waitpid(pid, 0)
        for _ in range(30):  # up to 3 seconds
            try:
                return int(_PID_FILE.read_text().strip())
            except (FileNotFoundError, ValueError):
                _time.sleep(0.1)
        raise DaemonError("Daemon started but PID file not found.")

    # --- first child: become session leader ---
    os.setsid()

    # --- second fork: detach from session ---
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # --- grandchild: this becomes the daemon ---
    _setup_daemon_process()
    _write_pid()
    _run_scheduler()
    os._exit(0)


def stop() -> int:
    """Stop the daemon.  Returns the PID that was stopped.

    Raises DaemonError if no daemon is running.
    """
    alive, pid = status()
    if not alive or pid is None:
        raise DaemonError("No daemon is currently running.")

    if sys.platform == "win32":
        # CTRL_BREAK_EVENT lets the process handle shutdown gracefully;
        # fall back to hard kill if it doesn't exit.
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except (OSError, AttributeError):
            os.kill(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGTERM)

    # Best-effort PID file cleanup (daemon removes it too, but race is fine).
    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass

    return pid


def status() -> tuple[bool, int | None]:
    """Return (alive, pid).  alive is False if no PID file or process gone."""
    if not _PID_FILE.exists():
        return False, None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False, None

    try:
        if sys.platform == "win32":
            _alive = _win_pid_alive(pid)
        else:
            os.kill(pid, 0)  # signal 0: check existence only
            _alive = True
    except ProcessLookupError:
        return False, pid
    except PermissionError:
        # Process exists but we lack permission to signal it — treat as alive.
        return True, pid
    return _alive, pid


def _win_pid_alive(pid: int) -> bool:
    """Return True if *pid* refers to a running process on Windows.

    Uses OpenProcess + GetExitCodeProcess via ctypes because
    os.kill(pid, 0) is unreliable on Windows (raises WinError 11).
    """
    import ctypes
    import ctypes.wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _setup_daemon_process() -> None:
    """Redirect stdio, change directory, and set up logging."""
    os.chdir("/")
    os.umask(0o022)

    # Redirect stdin → /dev/null
    devnull_fd = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull_fd, sys.stdin.fileno())
    os.dup2(devnull_fd, sys.stdout.fileno())
    os.close(devnull_fd)

    # Redirect stderr → log file
    log_fd = os.open(
        str(_LOG_FILE),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(log_fd)

    # Configure Python logging to write to the log file.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,
    )


def _write_pid() -> None:
    pid = os.getpid()
    _PID_FILE.write_text(str(pid))


def _run_scheduler() -> None:
    import signal as _signal

    from alarm_clock import scheduler

    def _cleanup(signum, frame):  # noqa: ARG001
        try:
            _PID_FILE.unlink()
        except FileNotFoundError:
            pass
        # Let scheduler's SIGTERM handler stop the loop.

    _signal.signal(_signal.SIGTERM, _cleanup)

    try:
        scheduler.run_loop()
    finally:
        try:
            _PID_FILE.unlink()
        except FileNotFoundError:
            pass
