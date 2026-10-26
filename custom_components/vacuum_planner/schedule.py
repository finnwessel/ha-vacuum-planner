"""Allowed cleaning times and the expected return – free of Home Assistant imports.

All datetimes passed in here are timezone-aware local times.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import quantiles
from typing import Any

BLOCK_NIGHT = "night"
BLOCK_MIDDAY = "midday"
BLOCK_SUNDAY = "sunday"
BLOCK_HOLIDAY = "holiday"


@dataclass
class Window:
    """Daily cleaning window and house quiet times."""

    day_start: time
    day_end: time
    quiet_midday: bool
    quiet_start: time
    quiet_end: time
    quiet_sundays: bool

    def _at(self, day: date, t: time, tz) -> datetime:
        return datetime.combine(day, t, tzinfo=tz)

    def day_blocked(self, day: date, holiday: bool) -> str | None:
        if not self.quiet_sundays:
            return None
        if day.weekday() == 6:
            return BLOCK_SUNDAY
        if holiday:
            return BLOCK_HOLIDAY
        return None

    def intervals(self, day: date, tz, holiday: bool = False) -> list[tuple[datetime, datetime]]:
        """Allowed intervals of one day."""
        if self.day_blocked(day, holiday) or self.day_start >= self.day_end:
            return []
        start, end = self._at(day, self.day_start, tz), self._at(day, self.day_end, tz)
        if not self.quiet_midday or self.quiet_start >= self.quiet_end:
            return [(start, end)]
        q_start, q_end = self._at(day, self.quiet_start, tz), self._at(day, self.quiet_end, tz)
        result = []
        if q_start > start:
            result.append((start, min(q_start, end)))
        if q_end < end:
            result.append((max(q_end, start), end))
        return [(a, b) for a, b in result if b > a]

    def blocked_reason(self, now: datetime, holiday_today: bool = False) -> str | None:
        """Why cleaning is not allowed right now (None = allowed)."""
        reason = self.day_blocked(now.date(), holiday_today)
        if reason:
            return reason
        for start, end in self.intervals(now.date(), now.tzinfo, holiday_today):
            if start <= now < end:
                return None
        if (
            self.quiet_midday
            and self._at(now.date(), self.quiet_start, now.tzinfo)
            <= now
            < self._at(now.date(), self.quiet_end, now.tzinfo)
        ):
            return BLOCK_MIDDAY
        return BLOCK_NIGHT

    def minutes_left(self, now: datetime, holiday_today: bool = False) -> float:
        """Minutes until the current allowed interval ends (0 if not allowed now)."""
        for start, end in self.intervals(now.date(), now.tzinfo, holiday_today):
            if start <= now < end:
                return (end - now).total_seconds() / 60
        return 0.0

    def next_allowed(self, now: datetime, holiday_today: bool = False) -> datetime | None:
        """Start of the next allowed interval (now if allowed now)."""
        for offset in range(0, 9):
            day = now.date() + timedelta(days=offset)
            holiday = holiday_today if offset == 0 else False
            for start, end in self.intervals(day, now.tzinfo, holiday):
                if end > now:
                    return max(start, now)
        return None


# --- expected return -----------------------------------------------------------------


@dataclass
class Absence:
    start: datetime
    minutes: float
    workday: bool

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "minutes": self.minutes, "workday": self.workday}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Absence:
        return cls(datetime.fromisoformat(data["start"]), float(data["minutes"]), bool(data["workday"]))


def _clock_distance(a: datetime, b: datetime) -> float:
    """Minutes between two times of day (wrapping at midnight)."""
    ma = a.hour * 60 + a.minute
    mb = b.hour * 60 + b.minute
    d = abs(ma - mb)
    return min(d, 1440 - d)


class AbsenceModel:
    """Learns how long the flat stays empty, by weekday type and time of departure."""

    MAX_SESSIONS = 150
    MIN_MINUTES = 10
    MAX_MINUTES = 24 * 60

    def __init__(self, sessions: list[Absence] | None = None) -> None:
        self.sessions: list[Absence] = sessions or []

    def add(self, start: datetime, end: datetime, workday: bool) -> bool:
        minutes = (end - start).total_seconds() / 60
        if minutes < self.MIN_MINUTES or minutes > self.MAX_MINUTES:
            return False
        if any(abs((s.start - start).total_seconds()) < 60 for s in self.sessions):
            return False
        self.sessions.append(Absence(start, round(minutes, 1), workday))
        self.sessions.sort(key=lambda s: s.start)
        del self.sessions[: -self.MAX_SESSIONS]
        return True

    def _matching(self, start: datetime, workday: bool) -> list[float]:
        for window, same_type in ((90, True), (180, True), (180, False)):
            values = [
                s.minutes
                for s in self.sessions
                if (s.workday == workday or not same_type)
                and _clock_distance(s.start, start) <= window
            ]
            if len(values) >= 3:
                return values
        return []

    def predict(
        self, start: datetime, now: datetime, workday: bool, default: float
    ) -> tuple[datetime, str, int]:
        """Expected end of the absence that began at ``start``.

        Conservative: the lower quartile of comparable absences. If the flat has
        already been empty longer than that, only longer absences count.
        Returns (expected return, source, number of samples).
        """
        elapsed = (now - start).total_seconds() / 60
        values = self._matching(start, workday)
        if not values:
            minutes = max(default, elapsed + 30)
            return start + timedelta(minutes=minutes), "default", 0
        longer = sorted(v for v in values if v > elapsed + 5)
        if not longer:
            return now + timedelta(minutes=30), "overdue", len(values)
        if len(longer) >= 4:
            minutes = quantiles(longer, n=4)[0]
        else:
            minutes = longer[0]
        return start + timedelta(minutes=minutes), "learned", len(values)

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.sessions]

    @classmethod
    def from_list(cls, data: list[dict[str, Any]] | None) -> AbsenceModel:
        sessions = []
        for raw in data or []:
            try:
                sessions.append(Absence.from_dict(raw))
            except (KeyError, ValueError, TypeError):
                continue
        return cls(sessions)


def absences_from_history(
    changes: list[tuple[datetime, bool]], workday_of
) -> list[tuple[datetime, datetime, bool]]:
    """Turn (time, somebody home) changes into absences (start, end, workday)."""
    result = []
    start = None
    for when, home in sorted(changes, key=lambda c: c[0]):
        if not home and start is None:
            start = when
        elif home and start is not None:
            result.append((start, when, workday_of(start)))
            start = None
    return result


def calendar_return(
    now: datetime,
    events: list[tuple[datetime, datetime]],
    travel_minutes: float,
    lookahead: float = 60,
    gap: float = 45,
) -> datetime | None:
    """End of the appointment chain that is running now (or starts soon) plus travel time."""
    relevant = sorted(
        (s, e) for s, e in events if e > now and s <= now + timedelta(minutes=lookahead)
    )
    if not relevant:
        return None
    end = max(e for _, e in relevant)
    for s, e in sorted(events):
        if end < s <= end + timedelta(minutes=gap) and e > end:
            end = e
    return end + timedelta(minutes=travel_minutes)
