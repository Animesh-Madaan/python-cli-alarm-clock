"""
notify.py — Fire alarm notifications.

Platform strategy:
  macOS:        osascript desktop notification + direct /dev/ttys* write
  Linux / WSL:  notify-send (best-effort) + direct /dev/pts/* write + wall
  Windows:      new console window with ASCII-art banner (python.exe)

All methods prepend \\a (ASCII bell) so the terminal beeps.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from alarm_clock.store import Alarm

logger = logging.getLogger(__name__)


_BELL_ART = """\
       .-.-.
  ((  (__I__)  ))
    .'_....._'.
   / /   12  \\ \\
  | |         | |
  | |9   .   3| |
  | |         | |
   \\ \\   6   / /
    '.`-...-'.'
     /'-- --'\\"""

_BELL_ART_CAT = """
      |\      _,,,---,,_
ZZZzz /,`.-'`'    -.  ;-;;,_
     |,4-  ) )-,_. ,\ (  `'-'
    '---''(_/--'  `-'\_)    """

def _build_notification_body(alarm: "Alarm") -> str:
    """Return a boxed ASCII-art notification body for *alarm*."""
    label   = alarm.label
    time_   = alarm.time
    snooze  = f"alarm snooze {alarm.id}"
    remove  = f"alarm remove {alarm.id}"

    art_lines   = _BELL_ART.splitlines()
    cat_lines   = _BELL_ART_CAT.strip("\n").splitlines()
    info_lines  = [
        f"  ALARM: {label}",
        f"",
        f"  Scheduled : {time_}",
        f"  Snooze    : {snooze}",
        f"  Remove    : {remove}",
        f"",
    ] + cat_lines

    # Pad shorter column so both sides align inside the box
    max_art  = max(len(l) for l in art_lines)
    max_info = max(len(l) for l in info_lines)
    col_w    = max_art + 2   # 2-char gutter between columns
    inner_w  = col_w + max_info + 2

    rows = max(len(art_lines), len(info_lines))
    art_lines  += [""] * (rows - len(art_lines))
    info_lines += [""] * (rows - len(info_lines))

    border_top = "╔" + "═" * (inner_w + 2) + "╗"
    title_text = " ⏰  ALARM FIRED  ⏰ "
    title_pad  = inner_w + 2 - len(title_text)
    title_row  = "║" + " " * (title_pad // 2) + title_text + " " * (title_pad - title_pad // 2) + "║"
    divider    = "╠" + "═" * (inner_w + 2) + "╣"
    border_bot = "╚" + "═" * (inner_w + 2) + "╝"

    lines = [border_top, title_row, divider]
    for art, info in zip(art_lines, info_lines):
        art_col  = art.ljust(col_w)
        info_col = info.ljust(max_info)
        lines.append(f"║ {art_col}{info_col} ║")
    lines.append(border_bot)

    return "\a\n" + "\n".join(lines) + "\n"


def fire(alarm: Alarm) -> None:
    """Notify the user that *alarm* has fired."""
    body = _build_notification_body(alarm)
    # Plain text version for desktop notification APIs that don't render boxes.
    plain = (
        f"ALARM: {alarm.label}\n"
        f"Scheduled: {alarm.time}\n"
        f"Snooze: alarm snooze {alarm.id}"
    )

    if sys.platform == "darwin":
        _osascript(alarm.label, plain)
        _write_to_macos_ttys(body)
    elif sys.platform == "win32":
        _show_alarm_console(body)
    else:
        _try_notify_send(alarm.label, plain)
        _write_to_user_ptys(body)
        _wall(body)


# ---------------------------------------------------------------------------
# macOS backends
# ---------------------------------------------------------------------------

def _osascript(summary: str, body: str) -> None:
    """Desktop notification via osascript (macOS only)."""
    # Strip the bell char — osascript doesn't need it and it may show as garbage.
    clean = body.replace("\a", "")
    script = (
        f'display notification "{clean}" '
        f'with title "ALARM: {summary}" '
        f'sound name "Glass"'
    )
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, check=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("osascript failed: %s", exc)


def _write_to_macos_ttys(message: str) -> None:
    """Write directly to /dev/ttys* owned by the current user (macOS)."""
    uid = os.getuid()
    encoded = message.encode("utf-8", errors="replace")
    wrote_any = False
    for tty in Path("/dev").glob("ttys*"):
        try:
            if tty.stat().st_uid != uid:
                continue
            fd = os.open(str(tty), os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                os.write(fd, encoded)
                wrote_any = True
            finally:
                os.close(fd)
        except OSError:
            pass
    if not wrote_any:
        logger.warning("No writable TTYs found for uid %d.", uid)


# ---------------------------------------------------------------------------
# Linux / WSL backends
# ---------------------------------------------------------------------------

def _try_notify_send(summary: str, body: str) -> None:
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(
            ["notify-send", "--urgency=critical", "--expire-time=0",
             f"ALARM: {summary}", body],
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify-send failed: %s", exc)


def _write_to_user_ptys(message: str) -> None:
    """Write *message* directly to every /dev/pts/* owned by the current user.

    This is the primary notification method under WSL and VS Code, where
    wall does not work because those terminals are not in utmp.
    """
    uid = os.getuid()
    pts_dir = Path("/dev/pts")
    if not pts_dir.exists():
        logger.warning("No /dev/pts — skipping PTY write.")
        return

    encoded = message.encode("utf-8", errors="replace")
    wrote_any = False
    for pts in pts_dir.iterdir():
        if not pts.name.isdigit():
            continue
        try:
            if pts.stat().st_uid != uid:
                continue
            fd = os.open(str(pts), os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                os.write(fd, encoded)
                wrote_any = True
            finally:
                os.close(fd)
        except OSError:
            # PTY may have closed between stat and open; skip silently.
            pass

    if not wrote_any:
        logger.warning("No writable PTYs found for uid %d.", uid)


def _wall(message: str) -> None:
    if shutil.which("wall") is None:
        return
    try:
        subprocess.run(
            ["wall"],
            input=message,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("wall failed: %s", exc)


# ---------------------------------------------------------------------------
# Windows backends
# ---------------------------------------------------------------------------

def _show_alarm_console(body: str) -> None:
    """Open a new console window that prints the ASCII-art alarm banner.

    This is the Windows equivalent of writing directly to /dev/pts/* on Linux:
    the boxed banner appears in a dedicated terminal window so the user cannot
    miss it.  The window stays open until the user presses Enter.

    The daemon runs under pythonw.exe (no console), so we explicitly launch
    python.exe (the console variant) for the display window.
    """
    import tempfile
    from pathlib import Path as _Path

    # Play the Windows "Exclamation" system sound asynchronously so it doesn't
    # block while the console window is being prepared.
    try:
        import winsound
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not play alarm sound: %s", exc)

    clean = body.replace("\a", "")
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(clean)
            tmpfile = fh.name

        # Prefer python.exe (has a console) even when the daemon runs under pythonw.exe.
        py = _Path(sys.executable)
        python_console = py.parent / "python.exe"
        if not python_console.exists():
            python_console = py

        script = (
            f"data=open({repr(tmpfile)},encoding='utf-8').read();"
            f"print(data);"
            f"input('\\nPress Enter to dismiss...');"
            f"__import__('os').unlink({repr(tmpfile)})"
        )
        subprocess.Popen(
            [str(python_console), "-c", script],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to open alarm console window: %s", exc)
