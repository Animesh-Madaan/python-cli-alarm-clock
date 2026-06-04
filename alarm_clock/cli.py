"""
cli.py — Click-based CLI entry point for the alarm clock.

Commands:
  alarm add <label> <HH:MM> [--daily]
  alarm list
  alarm remove <id-prefix>
  alarm snooze <id-prefix> [--minutes 5]
  alarm start
  alarm stop
  alarm status
"""

from __future__ import annotations

import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from alarm_clock import daemon as _daemon
from alarm_clock.store import Alarm, AlarmStore, compute_next_fire, new_alarm_id

console = Console()
store = AlarmStore()

_LOG_FILE = Path.home() / ".alarm-clock" / "alarm.log"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(time_str: str) -> str:
    """Validate and normalise a time string to HH:MM.  Exits on bad input."""
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        return dt.strftime("%H:%M")
    except ValueError:
        console.print(f"[red]Invalid time '{time_str}'. Expected HH:MM (24-hour).[/red]")
        sys.exit(1)


def _alarm_table(alarms: list[Alarm]) -> Table:
    table = Table(title="Alarms", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Label", style="white")
    table.add_column("Time", style="yellow", no_wrap=True)
    table.add_column("Recurrence", style="blue")
    table.add_column("Next Fire", style="magenta", no_wrap=True)
    table.add_column("Enabled", justify="center")

    for a in alarms:
        enabled_mark = "[green]✓[/green]" if a.enabled else "[dim]✗[/dim]"
        table.add_row(
            a.id,
            a.label,
            a.time,
            a.recurrence,
            a.next_fire.strftime("%Y-%m-%d %H:%M"),
            enabled_mark,
        )
    return table


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def main() -> None:
    """Python CLI alarm clock."""


# ---------------------------------------------------------------------------
# alarm add
# ---------------------------------------------------------------------------

@main.command("add")
@click.argument("label")
@click.argument("time")
@click.option("--daily", is_flag=True, default=False, help="Repeat every day.")
def cmd_add(label: str, time: str, daily: bool) -> None:
    """Add a new alarm.

    TIME must be in 24-hour HH:MM format (e.g. 07:30).
    """
    time = _parse_time(time)
    recurrence = "daily" if daily else "once"
    alarm = Alarm(
        id=new_alarm_id(),
        label=label,
        time=time,
        recurrence=recurrence,
        enabled=True,
        next_fire=compute_next_fire(time),
    )
    store.add(alarm)
    console.print(
        f"[green]Alarm added[/green] — id=[cyan]{alarm.id}[/cyan] "
        f"'{alarm.label}' at {alarm.time} ({recurrence}), "
        f"fires {alarm.next_fire.strftime('%Y-%m-%d %H:%M')}"
    )


# ---------------------------------------------------------------------------
# alarm list
# ---------------------------------------------------------------------------

@main.command("list")
def cmd_list() -> None:
    """List all alarms."""
    alarms = store.load()
    if not alarms:
        console.print("[dim]No alarms configured.[/dim]")
        return
    console.print(_alarm_table(alarms))


# ---------------------------------------------------------------------------
# alarm remove
# ---------------------------------------------------------------------------

@main.command("remove")
@click.argument("id_prefix")
def cmd_remove(id_prefix: str) -> None:
    """Remove an alarm by its ID (or unique prefix)."""
    try:
        removed = store.remove_by_prefix(id_prefix)
        console.print(f"[green]Removed[/green] alarm [cyan]{removed.id}[/cyan] '{removed.label}'.")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# alarm snooze
# ---------------------------------------------------------------------------

@main.command("snooze")
@click.argument("id_prefix")
@click.option("--minutes", "-m", default=5, show_default=True, help="Snooze duration in minutes.")
def cmd_snooze(id_prefix: str, minutes: int) -> None:
    """Delay an alarm by N minutes (also re-enables it if disabled)."""
    try:
        alarm = store.get_by_prefix(id_prefix)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    alarm.next_fire = datetime.now() + timedelta(minutes=minutes)
    alarm.enabled = True
    store.update(alarm)
    console.print(
        f"[green]Snoozed[/green] alarm [cyan]{alarm.id}[/cyan] '{alarm.label}' — "
        f"next fire {alarm.next_fire.strftime('%Y-%m-%d %H:%M')}."
    )


# ---------------------------------------------------------------------------
# alarm start
# ---------------------------------------------------------------------------

@main.command("start")
def cmd_start() -> None:
    """Start the alarm daemon in the background."""
    try:
        pid = _daemon.start()
        console.print(f"[green]Daemon started[/green] (PID {pid}).")
    except _daemon.DaemonError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# alarm stop
# ---------------------------------------------------------------------------

@main.command("stop")
def cmd_stop() -> None:
    """Stop the running alarm daemon."""
    try:
        pid = _daemon.stop()
        console.print(f"[green]Daemon stopped[/green] (was PID {pid}).")
    except _daemon.DaemonError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# alarm status
# ---------------------------------------------------------------------------

@main.command("status")
def cmd_status() -> None:
    """Show daemon status and all alarms."""
    alive, pid = _daemon.status()
    if alive:
        console.print(f"[green]Daemon running[/green] (PID {pid}).")
    else:
        console.print("[yellow]Daemon not running.[/yellow]")

    alarms = store.load()
    if alarms:
        console.print()
        console.print(_alarm_table(alarms))
    else:
        console.print("[dim]No alarms configured.[/dim]")


# ---------------------------------------------------------------------------
# alarm in
# ---------------------------------------------------------------------------

@main.command("in")
@click.argument("minutes", type=int)
@click.argument("label", default="")
@click.option("--daily", is_flag=True, default=False, help="Repeat every day.")
def cmd_in(minutes: int, label: str, daily: bool) -> None:
    """Set an alarm N minutes from now.

    MINUTES is how many minutes from now to fire.\n
    LABEL is an optional description (default: "Alarm in N min").
    """
    if minutes <= 0:
        console.print("[red]MINUTES must be a positive integer.[/red]")
        sys.exit(1)
    if not label:
        label = f"Alarm in {minutes} min"
    fire_at = datetime.now() + timedelta(minutes=minutes)
    time_str = fire_at.strftime("%H:%M")
    recurrence = "daily" if daily else "once"
    alarm = Alarm(
        id=new_alarm_id(),
        label=label,
        time=time_str,
        recurrence=recurrence,
        enabled=True,
        next_fire=fire_at.replace(second=0, microsecond=0),
    )
    store.add(alarm)
    console.print(
        f"[green]Alarm added[/green] — id=[cyan]{alarm.id}[/cyan] "
        f"'{alarm.label}' fires at [yellow]{time_str}[/yellow] "
        f"({minutes} min from now)"
    )


# ---------------------------------------------------------------------------
# alarm time
# ---------------------------------------------------------------------------

@main.command("time")
def cmd_time() -> None:
    """Show the current local time and time until next alarm."""
    now = datetime.now()
    console.print(f"\n  [bold cyan]{now.strftime('%H:%M')}[/bold cyan]  "
                  f"[dim]{now.strftime('%A, %d %B %Y')}[/dim]\n")

    alarms = store.load()
    enabled = [a for a in alarms if a.enabled]
    if not enabled:
        console.print("[dim]No enabled alarms.[/dim]")
        return

    next_alarm = min(enabled, key=lambda a: a.next_fire)
    delta = next_alarm.next_fire - now
    total_sec = int(delta.total_seconds())
    if total_sec <= 0:
        countdown = "firing soon"
    elif total_sec < 60:
        countdown = f"{total_sec}s"
    elif total_sec < 3600:
        countdown = f"{total_sec // 60}m {total_sec % 60}s"
    else:
        h, rem = divmod(total_sec, 3600)
        countdown = f"{h}h {rem // 60}m"

    console.print(
        f"  Next alarm: [white]{next_alarm.label}[/white] "
        f"[dim]({next_alarm.time})[/dim] — "
        f"[yellow]in {countdown}[/yellow]\n"
    )


# ---------------------------------------------------------------------------
# alarm enable / disable
# ---------------------------------------------------------------------------

@main.command("enable")
@click.argument("id_prefix")
def cmd_enable(id_prefix: str) -> None:
    """Enable a disabled alarm (recomputes next fire time)."""
    try:
        alarm = store.set_enabled(id_prefix, True)
        console.print(
            f"[green]Enabled[/green] alarm [cyan]{alarm.id}[/cyan] '{alarm.label}' — "
            f"next fire {alarm.next_fire.strftime('%Y-%m-%d %H:%M')}."
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


@main.command("disable")
@click.argument("id_prefix")
def cmd_disable(id_prefix: str) -> None:
    """Disable an alarm without deleting it."""
    try:
        alarm = store.set_enabled(id_prefix, False)
        console.print(
            f"[yellow]Disabled[/yellow] alarm [cyan]{alarm.id}[/cyan] '{alarm.label}'."
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# alarm edit
# ---------------------------------------------------------------------------

@main.command("edit")
@click.argument("id_prefix")
@click.option("--label", "new_label", default=None, help="New label.")
@click.option("--time", "new_time", default=None, help="New time in HH:MM format.")
@click.option("--daily", "new_recurrence", flag_value="daily", default=None,
              help="Set recurrence to daily.")
@click.option("--once", "new_recurrence", flag_value="once",
              help="Set recurrence to once.")
def cmd_edit(id_prefix: str, new_label: str | None, new_time: str | None,
             new_recurrence: str | None) -> None:
    """Edit an existing alarm's label, time, or recurrence."""
    if not any([new_label, new_time, new_recurrence]):
        console.print("[red]Provide at least one of --label, --time, --daily/--once.[/red]")
        sys.exit(1)
    try:
        alarm = store.get_by_prefix(id_prefix)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if new_label:
        alarm.label = new_label
    if new_time:
        new_time = _parse_time(new_time)
        alarm.time = new_time
        alarm.next_fire = compute_next_fire(new_time)
    if new_recurrence:
        alarm.recurrence = new_recurrence

    store.update(alarm)
    console.print(
        f"[green]Updated[/green] alarm [cyan]{alarm.id}[/cyan]: "
        f"'{alarm.label}' at {alarm.time} ({alarm.recurrence}), "
        f"next fire {alarm.next_fire.strftime('%Y-%m-%d %H:%M')}."
    )


# ---------------------------------------------------------------------------
# alarm log
# ---------------------------------------------------------------------------

@main.command("log")
@click.option("--lines", "-n", default=20, show_default=True,
              help="Number of lines to show.")
@click.option("--follow", "-f", is_flag=True, default=False,
              help="Follow log output in real time (Ctrl-C to stop).")
def cmd_log(lines: int, follow: bool) -> None:
    """Show the daemon log file."""
    if not _LOG_FILE.exists():
        console.print("[yellow]No log file found. Has the daemon been started?[/yellow]")
        sys.exit(1)

    def _tail(n: int) -> list[str]:
        with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-n:]

    def _print_lines(log_lines: list[str]) -> None:
        for line in log_lines:
            text = line.rstrip()
            if " ERROR " in text:
                console.print(f"[red]{text}[/red]")
            elif " WARNING " in text:
                console.print(f"[yellow]{text}[/yellow]")
            elif " INFO " in text:
                console.print(f"[dim]{text}[/dim]")
            else:
                console.print(text)

    _print_lines(_tail(lines))

    if follow:
        console.print("[dim]--- following (Ctrl-C to stop) ---[/dim]")
        last_size = _LOG_FILE.stat().st_size
        try:
            while True:
                _time.sleep(0.5)
                current_size = _LOG_FILE.stat().st_size
                if current_size > last_size:
                    with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(last_size)
                        new_lines = fh.readlines()
                    _print_lines(new_lines)
                    last_size = current_size
        except KeyboardInterrupt:
            pass
