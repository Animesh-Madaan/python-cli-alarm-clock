"""Tests for store.py — Alarm persistence and compute_next_fire."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from alarm_clock.store import (
    Alarm,
    AlarmStore,
    compute_next_fire,
    new_alarm_id,
)


# ---------------------------------------------------------------------------
# compute_next_fire
# ---------------------------------------------------------------------------

class TestComputeNextFire:
    def test_future_time_today(self):
        """If the time is still in the future today, return today's datetime."""
        now = datetime.now()
        future = now + timedelta(hours=1)
        time_str = future.strftime("%H:%M")
        result = compute_next_fire(time_str)
        assert result.date() == now.date()
        assert result.hour == future.hour
        assert result.minute == future.minute

    def test_past_time_returns_tomorrow(self):
        """If the time already passed today, return tomorrow."""
        now = datetime.now()
        past = now - timedelta(hours=1)
        time_str = past.strftime("%H:%M")
        result = compute_next_fire(time_str)
        assert result.date() == (now + timedelta(days=1)).date()

    def test_from_dt_strictly_after(self):
        """With from_dt, result is the next occurrence strictly after from_dt."""
        base = datetime(2026, 6, 3, 10, 0, 0)
        # Same time as base → should roll to next day
        result = compute_next_fire("10:00", from_dt=base)
        assert result.date() == base.date() + timedelta(days=1)
        assert result.hour == 10
        assert result.minute == 0

    def test_from_dt_same_day_if_later(self):
        """With from_dt, if time is later today it returns same day."""
        base = datetime(2026, 6, 3, 8, 0, 0)
        result = compute_next_fire("10:00", from_dt=base)
        assert result.date() == base.date()
        assert result.hour == 10

    def test_seconds_zeroed(self):
        result = compute_next_fire("07:30")
        assert result.second == 0
        assert result.microsecond == 0


# ---------------------------------------------------------------------------
# AlarmStore — round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path: Path) -> AlarmStore:
    return AlarmStore(path=tmp_path / "alarms.json")


def _make_alarm(**kwargs) -> Alarm:
    defaults = dict(
        id=new_alarm_id(),
        label="Test",
        time="08:00",
        recurrence="once",
        enabled=True,
        next_fire=datetime(2026, 6, 4, 8, 0, 0),
    )
    defaults.update(kwargs)
    return Alarm(**defaults)


class TestAlarmStore:
    def test_load_empty(self, tmp_store):
        assert tmp_store.load() == []

    def test_add_and_load(self, tmp_store):
        alarm = _make_alarm(label="Wake up")
        tmp_store.add(alarm)
        loaded = tmp_store.load()
        assert len(loaded) == 1
        assert loaded[0].label == "Wake up"
        assert loaded[0].id == alarm.id

    def test_round_trip_preserves_fields(self, tmp_store):
        alarm = _make_alarm(
            label="Meeting",
            time="14:30",
            recurrence="daily",
            enabled=False,
            next_fire=datetime(2026, 6, 5, 14, 30, 0),
        )
        tmp_store.add(alarm)
        loaded = tmp_store.load()[0]
        assert loaded.label == "Meeting"
        assert loaded.time == "14:30"
        assert loaded.recurrence == "daily"
        assert loaded.enabled is False
        assert loaded.next_fire == datetime(2026, 6, 5, 14, 30, 0)

    def test_save_multiple(self, tmp_store):
        alarms = [_make_alarm(label=f"A{i}") for i in range(3)]
        tmp_store.save(alarms)
        loaded = tmp_store.load()
        assert len(loaded) == 3
        assert {a.label for a in loaded} == {"A0", "A1", "A2"}

    def test_remove_by_prefix(self, tmp_store):
        a1 = _make_alarm(id="aabbccdd", label="First")
        a2 = _make_alarm(id="11223344", label="Second")
        tmp_store.save([a1, a2])
        removed = tmp_store.remove_by_prefix("aabb")
        assert removed.id == "aabbccdd"
        remaining = tmp_store.load()
        assert len(remaining) == 1
        assert remaining[0].id == "11223344"

    def test_remove_full_id(self, tmp_store):
        alarm = _make_alarm(id="deadbeef")
        tmp_store.add(alarm)
        removed = tmp_store.remove_by_prefix("deadbeef")
        assert removed.id == "deadbeef"

    def test_remove_not_found(self, tmp_store):
        tmp_store.add(_make_alarm(id="aabbccdd"))
        with pytest.raises(ValueError, match="No alarm found"):
            tmp_store.remove_by_prefix("zzzz")

    def test_remove_ambiguous(self, tmp_store):
        tmp_store.save([
            _make_alarm(id="aabb1111"),
            _make_alarm(id="aabb2222"),
        ])
        with pytest.raises(ValueError, match="ambiguous"):
            tmp_store.remove_by_prefix("aabb")

    def test_update(self, tmp_store):
        alarm = _make_alarm(id="cafebabe", label="Before")
        tmp_store.add(alarm)
        alarm.label = "After"
        tmp_store.update(alarm)
        loaded = tmp_store.load()[0]
        assert loaded.label == "After"

    def test_update_not_found(self, tmp_store):
        alarm = _make_alarm(id="00000000")
        with pytest.raises(ValueError, match="not found"):
            tmp_store.update(alarm)

    def test_get_by_prefix(self, tmp_store):
        alarm = _make_alarm(id="feedface")
        tmp_store.add(alarm)
        found = tmp_store.get_by_prefix("feed")
        assert found.id == "feedface"

    def test_json_file_structure(self, tmp_store):
        """The on-disk JSON must have a top-level 'alarms' list."""
        tmp_store.add(_make_alarm())
        raw = json.loads(tmp_store._path.read_text())
        assert "alarms" in raw
        assert isinstance(raw["alarms"], list)

    def test_new_alarm_id_length(self):
        aid = new_alarm_id()
        assert len(aid) == 8
        int(aid, 16)  # must be valid hex
