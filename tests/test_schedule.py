"""Cleaning windows, quiet hours and the expected return (no Home Assistant needed)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from custom_components.vacuum_planner.schedule import (
    BLOCK_HOLIDAY,
    BLOCK_MIDDAY,
    BLOCK_NIGHT,
    BLOCK_SUNDAY,
    AbsenceModel,
    Window,
    absences_from_history,
    calendar_return,
)

TZ = timezone(timedelta(hours=1))
MONDAY = datetime(2026, 10, 26, 9, 0, tzinfo=TZ)


def _window(**kw) -> Window:
    base = dict(
        day_start=time(8),
        day_end=time(20),
        quiet_midday=True,
        quiet_start=time(13),
        quiet_end=time(15),
        quiet_sundays=True,
    )
    base.update(kw)
    return Window(**base)


def test_german_default_quiet_times():
    w = _window()
    assert w.blocked_reason(MONDAY) is None
    assert w.blocked_reason(MONDAY.replace(hour=13, minute=30)) == BLOCK_MIDDAY
    assert w.blocked_reason(MONDAY.replace(hour=7)) == BLOCK_NIGHT
    assert w.blocked_reason(MONDAY.replace(hour=21)) == BLOCK_NIGHT
    sunday = MONDAY - timedelta(days=1)
    assert w.blocked_reason(sunday) == BLOCK_SUNDAY
    assert w.blocked_reason(MONDAY, holiday_today=True) == BLOCK_HOLIDAY


def test_minutes_left_until_midday_rest():
    w = _window()
    assert w.minutes_left(MONDAY.replace(hour=12)) == 60
    assert w.minutes_left(MONDAY.replace(hour=16)) == 240
    assert w.minutes_left(MONDAY.replace(hour=14)) == 0


def test_next_allowed_skips_rest_and_sunday():
    w = _window()
    assert w.next_allowed(MONDAY.replace(hour=13, minute=10)) == MONDAY.replace(hour=15)
    saturday_evening = MONDAY - timedelta(days=2) + timedelta(hours=12)  # Sat 21:00
    assert w.next_allowed(saturday_evening) == MONDAY.replace(hour=8)


def test_quiet_rules_can_be_disabled():
    w = _window(quiet_midday=False, quiet_sundays=False)
    assert w.blocked_reason(MONDAY.replace(hour=14)) is None
    assert w.blocked_reason(MONDAY - timedelta(days=1)) is None
    assert w.minutes_left(MONDAY.replace(hour=12)) == 480


def _model(minutes: list[float], hour: int = 8, workday: bool = True) -> AbsenceModel:
    model = AbsenceModel()
    for i, m in enumerate(minutes):
        start = MONDAY.replace(hour=hour) - timedelta(days=7 * (i + 1))
        model.add(start, start + timedelta(minutes=m), workday)
    return model


def test_prediction_uses_lower_quartile_of_similar_absences():
    model = _model([480, 500, 510, 300, 520])
    start = MONDAY.replace(hour=8, minute=15)
    expected, source, samples = model.predict(start, start, True, default=90)
    assert source == "learned"
    assert samples == 5
    assert 300 <= (expected - start).total_seconds() / 60 < 480


def test_prediction_conditions_on_elapsed_time():
    model = _model([60, 70, 480, 500])
    start = MONDAY.replace(hour=8)
    expected, source, _ = model.predict(start, start + timedelta(minutes=120), True, 90)
    assert source == "learned"
    assert expected == start + timedelta(minutes=480)


def test_prediction_without_data_falls_back():
    model = _model([60, 70])  # too few
    start = MONDAY.replace(hour=8)
    expected, source, samples = model.predict(start, start, True, default=90)
    assert (source, samples) == ("default", 0)
    assert expected == start + timedelta(minutes=90)


def test_short_absences_are_ignored_and_history_import():
    model = AbsenceModel()
    assert not model.add(MONDAY, MONDAY + timedelta(minutes=5), True)
    changes = [
        (MONDAY.replace(hour=7), True),
        (MONDAY.replace(hour=8), False),
        (MONDAY.replace(hour=17), True),
        (MONDAY.replace(hour=19), False),
    ]
    spans = absences_from_history(changes, lambda d: d.weekday() < 5)
    assert spans == [(MONDAY.replace(hour=8), MONDAY.replace(hour=17), True)]
    assert AbsenceModel.from_list(model.to_list()).sessions == model.sessions


def test_calendar_chain_plus_travel():
    now = MONDAY.replace(hour=9)
    events = [
        (MONDAY.replace(hour=8), MONDAY.replace(hour=12)),
        (MONDAY.replace(hour=12, minute=30), MONDAY.replace(hour=16)),
        (MONDAY.replace(hour=19), MONDAY.replace(hour=20)),
    ]
    assert calendar_return(now, events, 15) == MONDAY.replace(hour=16, minute=15)
    assert calendar_return(MONDAY.replace(hour=17), events, 15) is None
