"""Tests for scheduler.py — get_alarms_to_fire and post-fire mutations."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alarm_clock.scheduler import get_alarms_to_fire, _tick
from alarm_clock.store import Alarm, AlarmStore, new_alarm_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alarm(*, enabled=True, recurrence="once", offset_seconds=-1, **kwargs) -> Alarm:
    """Create an alarm whose next_fire is *offset_seconds* from now."""
    defaults = dict(
        id=new_alarm_id(),
        label="Test",
        time="08:00",
        recurrence=recurrence,
        enabled=enabled,
        next_fire=datetime.now() + timedelta(seconds=offset_seconds),
    )
    defaults.update(kwargs)
    return Alarm(**defaults)


# ---------------------------------------------------------------------------
# get_alarms_to_fire
# ---------------------------------------------------------------------------

class TestGetAlarmsToFire:
    def test_fires_past_due(self):
        alarm = _alarm(offset_seconds=-60)
        result = get_alarms_to_fire([alarm])
        assert alarm in result

    def test_ignores_future(self):
        alarm = _alarm(offset_seconds=3600)
        result = get_alarms_to_fire([alarm])
        assert result == []

    def test_ignores_disabled(self):
        alarm = _alarm(enabled=False, offset_seconds=-60)
        result = get_alarms_to_fire([alarm])
        assert result == []

    def test_fires_exactly_at_now(self):
        now = datetime.now()
        alarm = _alarm(next_fire=now, offset_seconds=0)
        alarm.next_fire = now  # ensure exact equality
        result = get_alarms_to_fire([alarm], now=now)
        assert alarm in result

    def test_custom_now(self):
        future = datetime.now() + timedelta(hours=1)
        alarm = _alarm(offset_seconds=1800)  # 30 min in future
        # Pass a "now" far enough ahead
        result = get_alarms_to_fire([alarm], now=future)
        assert alarm in result

    def test_multiple_mixed(self):
        past = _alarm(offset_seconds=-10)
        future = _alarm(offset_seconds=3600)
        disabled = _alarm(enabled=False, offset_seconds=-10)
        result = get_alarms_to_fire([past, future, disabled])
        assert result == [past]


# ---------------------------------------------------------------------------
# _tick — post-fire mutations
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path: Path) -> AlarmStore:
    return AlarmStore(path=tmp_path / "alarms.json")


class TestTick:
    def _patch_store(self, store):
        """Patch scheduler's module-level _store with our tmp_store."""
        import alarm_clock.scheduler as sched
        return patch.object(sched, "_store", store)

    def test_once_alarm_disabled_after_fire(self, tmp_store):
        alarm = _alarm(recurrence="once", offset_seconds=-5)
        tmp_store.add(alarm)

        with self._patch_store(tmp_store), \
             patch("alarm_clock.notify.fire") as mock_fire:
            _tick()

        mock_fire.assert_called_once()
        updated = tmp_store.load()[0]
        assert updated.enabled is False

    def test_daily_alarm_next_fire_advanced(self, tmp_store):
        alarm = _alarm(recurrence="daily", offset_seconds=-5)
        # Strip microseconds: the store serialises to-second precision only.
        original_fire = alarm.next_fire.replace(microsecond=0)
        alarm.next_fire = original_fire
        tmp_store.add(alarm)

        with self._patch_store(tmp_store), \
             patch("alarm_clock.notify.fire"):
            _tick()

        updated = tmp_store.load()[0]
        assert updated.enabled is True
        assert updated.next_fire == original_fire + timedelta(days=1)

    def test_notify_called_for_each_fired_alarm(self, tmp_store):
        alarms = [_alarm(recurrence="once", offset_seconds=-i) for i in range(1, 4)]
        tmp_store.save(alarms)

        with self._patch_store(tmp_store), \
             patch("alarm_clock.notify.fire") as mock_fire:
            _tick()

        assert mock_fire.call_count == 3

    def test_future_alarm_not_fired(self, tmp_store):
        alarm = _alarm(offset_seconds=3600)
        tmp_store.add(alarm)

        with self._patch_store(tmp_store), \
             patch("alarm_clock.notify.fire") as mock_fire:
            _tick()

        mock_fire.assert_not_called()
        updated = tmp_store.load()[0]
        assert updated.enabled is True

    def test_notify_error_does_not_abort_other_alarms(self, tmp_store):
        a1 = _alarm(recurrence="once", offset_seconds=-1)
        a2 = _alarm(recurrence="once", offset_seconds=-2)
        tmp_store.save([a1, a2])

        call_count = 0

        def flaky_fire(alarm):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")

        with self._patch_store(tmp_store), \
             patch("alarm_clock.notify.fire", side_effect=flaky_fire):
            _tick()

        assert call_count == 2
