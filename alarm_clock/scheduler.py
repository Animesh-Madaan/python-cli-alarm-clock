"""
scheduler.py — Background alarm-check loop.

Called by daemon.py after the double-fork.  Polls the alarm store every
*interval* seconds and fires any alarms whose next_fire has passed.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime, timedelta

from alarm_clock import notify
from alarm_clock.store import AlarmStore

logger = logging.getLogger(__name__)

_store = AlarmStore()
_running = True


def _handle_sigterm(signum, frame):  # noqa: ARG001
    global _running
    logger.info("Received SIGTERM — shutting down scheduler.")
    _running = False


def get_alarms_to_fire(alarms, now: datetime | None = None):
    """Return the subset of *alarms* that should fire right now."""
    now = now or datetime.now()
    return [a for a in alarms if a.enabled and a.next_fire <= now]


def run_loop(interval: int = 30) -> None:
    """Main scheduler loop.  Runs until SIGTERM / CTRL_BREAK_EVENT received."""
    signal.signal(signal.SIGTERM, _handle_sigterm)
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_sigterm)
    logger.info("Scheduler started (interval=%ds).", interval)

    while _running:
        _tick()
        # Sleep in small increments so SIGTERM is handled promptly.
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)

    logger.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _tick() -> None:
    try:
        alarms = _store.load()
        now = datetime.now()
        to_fire = get_alarms_to_fire(alarms, now)

        if not to_fire:
            return

        for alarm in to_fire:
            logger.info("Firing alarm %s (%s).", alarm.id, alarm.label)
            try:
                notify.fire(alarm)
            except Exception as exc:  # noqa: BLE001
                logger.error("Notification failed for %s: %s", alarm.id, exc)

            if alarm.recurrence == "daily":
                alarm.next_fire += timedelta(days=1)
            else:
                alarm.enabled = False

        _store.save(alarms)

    except Exception as exc:  # noqa: BLE001
        logger.error("Scheduler tick error: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    run_loop()
