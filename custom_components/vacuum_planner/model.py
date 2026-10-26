"""Plan, queue and stage planning – free of Home Assistant imports.

Terms
-----
* **Plan**: per room a mode and an interval in days. A room is *due* when
  ``last cleaned + interval <= today``.
* **Queue**: rooms waiting to be cleaned, one entry per room. Due rooms are put
  in automatically (source ``plan``), others are added by hand (``manual``).
* **Stage**: one robot job – all chosen rooms that share a cleaning mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .const import (
    CLEAN_MODES,
    DEFAULT_MINUTES,
    MODE_MOP,
    MODE_OFF,
    MODE_SWEEP_THEN_MOP,
    MODE_VACUUM,
    MODE_VACUUM_MOP,
    PLAN_MODES,
)

SOURCE_PLAN = "plan"
SOURCE_MANUAL = "manual"

SKIP_TODAY = "skipped"
SKIP_PAUSED = "paused"
SKIP_FAILED = "failed"

# Dry before wet: vacuum first, mop-only last (needs a vacuumed floor).
STAGE_ORDER = {MODE_VACUUM: 0, MODE_VACUUM_MOP: 1, MODE_SWEEP_THEN_MOP: 2, MODE_MOP: 3}
FULL_MODES = (MODE_VACUUM_MOP, MODE_SWEEP_THEN_MOP)
LEARN_RATE = 0.3


def covers(done: str, wanted: str) -> bool:
    """Does a job in mode ``done`` fulfil a plan or queue entry in mode ``wanted``?"""
    if wanted == MODE_OFF or done == wanted:
        return True
    return done in FULL_MODES


def merge_modes(a: str, b: str) -> str:
    """Mode that fulfils both a and b (vacuum + mop → vacuum & mop)."""
    if covers(a, b):
        return a
    if covers(b, a):
        return b
    return MODE_VACUUM_MOP


def minutes_class(mode: str) -> str:
    return "dry" if mode == MODE_VACUUM else "wet"


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class RoomPlan:
    """Plan, learned duration and statistics of one room."""

    room_id: int
    mode: str = MODE_VACUUM_MOP
    interval: int = 3
    last_cleaned: datetime | None = None
    skip_until: datetime | None = None
    skip_reason: str | None = None
    minutes: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MINUTES))
    cleaned: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    last_failure: datetime | None = None
    last_failure_reason: str | None = None

    # -- plan -------------------------------------------------------------------------
    def next_due(self) -> date | None:
        if self.mode == MODE_OFF:
            return None
        if self.last_cleaned is None:
            return date.min
        return self.last_cleaned.date() + timedelta(days=self.interval)

    def is_skipped(self, now: datetime) -> bool:
        return self.skip_until is not None and self.skip_until > now

    def is_due(self, now: datetime) -> bool:
        due = self.next_due()
        return due is not None and due <= now.date() and not self.is_skipped(now)

    def overdue_days(self, now: datetime) -> int | None:
        due = self.next_due()
        if due is None:
            return None
        if due == date.min:
            return 99
        return (now.date() - due).days

    def urgency(self, now: datetime) -> float:
        """Share of the interval that has passed (≥ 1 means due)."""
        if self.mode == MODE_OFF:
            return 0.0
        if self.last_cleaned is None:
            return 99.0
        days = (now - self.last_cleaned).total_seconds() / 86400
        return days / max(self.interval, 1)

    def estimate(self, mode: str) -> float:
        minutes = self.minutes.get(minutes_class(mode), DEFAULT_MINUTES[minutes_class(mode)])
        return minutes * (1.5 if mode == MODE_SWEEP_THEN_MOP else 1.0)

    def expire_skip(self, now: datetime) -> bool:
        if self.skip_until is not None and self.skip_until <= now:
            self.skip_until = None
            self.skip_reason = None
            return True
        return False

    # -- results ------------------------------------------------------------------------
    def mark_cleaned(self, now: datetime) -> None:
        self.last_cleaned = now
        self.cleaned += 1
        self.consecutive_failures = 0
        if self.skip_reason in (SKIP_TODAY, SKIP_FAILED):
            self.skip_until = None
            self.skip_reason = None

    def mark_failed(self, now: datetime, reason: str | None) -> None:
        self.failed += 1
        self.consecutive_failures += 1
        self.last_failure = now
        self.last_failure_reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "mode": self.mode,
            "interval": self.interval,
            "last_cleaned": _iso(self.last_cleaned),
            "skip_until": _iso(self.skip_until),
            "skip_reason": self.skip_reason,
            "minutes": self.minutes,
            "cleaned": self.cleaned,
            "failed": self.failed,
            "consecutive_failures": self.consecutive_failures,
            "last_failure": _iso(self.last_failure),
            "last_failure_reason": self.last_failure_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomPlan:
        mode = data.get("mode", MODE_VACUUM_MOP)
        return cls(
            room_id=int(data["room_id"]),
            mode=mode if mode in PLAN_MODES else MODE_VACUUM_MOP,
            interval=int(data.get("interval", 3)),
            last_cleaned=_dt(data.get("last_cleaned")),
            skip_until=_dt(data.get("skip_until")),
            skip_reason=data.get("skip_reason"),
            minutes={**DEFAULT_MINUTES, **(data.get("minutes") or {})},
            cleaned=int(data.get("cleaned", 0)),
            failed=int(data.get("failed", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            last_failure=_dt(data.get("last_failure")),
            last_failure_reason=data.get("last_failure_reason"),
        )


@dataclass
class QueueItem:
    room_id: int
    mode: str
    source: str = SOURCE_MANUAL
    added: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "mode": self.mode,
            "source": self.source,
            "added": _iso(self.added),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueItem:
        return cls(
            room_id=int(data["room_id"]),
            mode=data["mode"],
            source=data.get("source", SOURCE_MANUAL),
            added=_dt(data.get("added")),
        )


class Queue:
    """Ordered rooms waiting to be cleaned – at most one entry per room."""

    def __init__(self, items: list[QueueItem] | None = None) -> None:
        self.items: list[QueueItem] = items or []

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, room_id: int) -> bool:
        return any(item.room_id == room_id for item in self.items)

    def get(self, room_id: int) -> QueueItem | None:
        return next((item for item in self.items if item.room_id == room_id), None)

    def add(
        self,
        room_id: int,
        mode: str,
        source: str,
        now: datetime,
        front: bool = False,
    ) -> bool:
        """Add a room or widen the mode of its entry. Returns True if something changed."""
        if mode not in CLEAN_MODES:
            raise ValueError(f"invalid mode {mode}")
        item = self.get(room_id)
        if item is not None:
            merged = merge_modes(item.mode, mode)
            changed = merged != item.mode
            item.mode = merged
            if source == SOURCE_MANUAL and item.source != SOURCE_MANUAL:
                item.source = SOURCE_MANUAL
                changed = True
            if front and self.items[0] is not item:
                self.items.remove(item)
                self.items.insert(0, item)
                changed = True
            return changed
        new = QueueItem(room_id, mode, source, now)
        if front:
            self.items.insert(0, new)
        else:
            self.items.append(new)
        return True

    def remove(self, room_id: int) -> QueueItem | None:
        item = self.get(room_id)
        if item is not None:
            self.items.remove(item)
        return item

    def move(self, room_id: int, position: int) -> bool:
        item = self.get(room_id)
        if item is None:
            return False
        self.items.remove(item)
        position = max(0, min(position, len(self.items)))
        self.items.insert(position, item)
        return True

    def complete(self, room_ids: list[int], mode: str) -> list[int]:
        """Drop entries fulfilled by a finished job; returns the dropped room ids."""
        done = []
        for item in list(self.items):
            if item.room_id in room_ids and covers(mode, item.mode):
                self.items.remove(item)
                done.append(item.room_id)
        return done

    def clear(self) -> None:
        self.items.clear()

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]

    @classmethod
    def from_list(cls, data: list[dict[str, Any]] | None) -> Queue:
        items = []
        seen = set()
        for raw in data or []:
            try:
                item = QueueItem.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                continue
            if item.mode in CLEAN_MODES and item.room_id not in seen:
                items.append(item)
                seen.add(item.room_id)
        return cls(items)


def sync_due(queue: Queue, rooms: dict[int, RoomPlan], now: datetime) -> list[int]:
    """Put every due room into the queue (with its plan mode). Returns added room ids."""
    added = []
    for room in rooms.values():
        if room.is_due(now) and room.room_id not in queue:
            queue.add(room.room_id, room.mode, SOURCE_PLAN, now)
            added.append(room.room_id)
        elif room.is_due(now):
            item = queue.get(room.room_id)
            if item is not None and not covers(item.mode, room.mode):
                item.mode = merge_modes(item.mode, room.mode)
    return added


@dataclass
class Stage:
    mode: str
    room_ids: list[int]
    minutes: float
    isolated: bool = False


def _route_key(order: list[int]):
    def key(room_id: int) -> int:
        return order.index(room_id) if room_id in order else len(order) + room_id

    return key


def build_stages(
    queue: Queue,
    rooms: dict[int, RoomPlan],
    order: list[int],
    overhead: float,
    exclude: set[int] | None = None,
) -> list[Stage]:
    """All stages the queue would need, in the order they run (preview, no budget)."""
    exclude = exclude or set()
    normal: dict[str, list[int]] = {}
    isolated: list[QueueItem] = []
    for item in queue.items:
        if item.room_id in exclude:
            continue
        room = rooms.get(item.room_id)
        if room is not None and room.consecutive_failures > 0:
            isolated.append(item)
            continue
        normal.setdefault(item.mode, []).append(item.room_id)
    stages = []
    for mode in sorted(normal, key=lambda m: STAGE_ORDER.get(m, 9)):
        ids = sorted(normal[mode], key=_route_key(order))
        minutes = overhead + sum(_estimate(rooms, rid, mode) for rid in ids)
        stages.append(Stage(mode, ids, minutes))
    for item in isolated:
        minutes = overhead + _estimate(rooms, item.room_id, item.mode)
        stages.append(Stage(item.mode, [item.room_id], minutes, isolated=True))
    return stages


def _estimate(rooms: dict[int, RoomPlan], room_id: int, mode: str) -> float:
    room = rooms.get(room_id)
    if room is None:
        return DEFAULT_MINUTES[minutes_class(mode)]
    return room.estimate(mode)


def _priority(item: QueueItem, rooms: dict[int, RoomPlan], now: datetime) -> tuple:
    room = rooms.get(item.room_id)
    urgency = room.urgency(now) if room else 1.0
    # manual entries first, then the most overdue rooms
    return (0 if item.source == SOURCE_MANUAL else 1, -urgency)


def plan_stage(
    queue: Queue,
    rooms: dict[int, RoomPlan],
    order: list[int],
    now: datetime,
    budget: float | None,
    overhead: float,
    blocked: set[int] | None = None,
) -> Stage | None:
    """Pick the next job that fits into ``budget`` minutes (None = no limit).

    Stages run dry → wet; within a stage the most important rooms are chosen
    first until the budget is used up, then driven in route order.
    """
    blocked = blocked or set()
    for stage in build_stages(queue, rooms, order, overhead, exclude=blocked):
        if budget is None:
            return stage
        items = [queue.get(rid) for rid in stage.room_ids]
        items.sort(key=lambda it: _priority(it, rooms, now))
        chosen: list[int] = []
        total = overhead
        for item in items:
            minutes = _estimate(rooms, item.room_id, stage.mode)
            if total + minutes <= budget:
                chosen.append(item.room_id)
                total += minutes
        if chosen:
            chosen.sort(key=_route_key(order))
            return Stage(stage.mode, chosen, total, stage.isolated)
    return None


def queue_minutes(
    queue: Queue, rooms: dict[int, RoomPlan], order: list[int], overhead: float, pause: float
) -> float:
    stages = build_stages(queue, rooms, order, overhead)
    if not stages:
        return 0.0
    return sum(stage.minutes for stage in stages) + pause * (len(stages) - 1)


def learn_durations(
    rooms: dict[int, RoomPlan],
    room_ids: list[int],
    mode: str,
    actual_minutes: float,
    overhead: float,
) -> float | None:
    """Scale the estimates of a finished job towards the measured duration.

    The robot does not report per-room times, so all rooms of the job are
    adjusted by the same ratio (bounded, exponentially smoothed).
    """
    planned = [rooms[rid] for rid in room_ids if rid in rooms]
    if not planned or actual_minutes < 3:
        return None
    estimated = overhead + sum(room.estimate(mode) for room in planned)
    ratio = max(0.5, min(2.0, actual_minutes / estimated))
    cls = minutes_class(mode)
    for room in planned:
        current = room.minutes.get(cls, DEFAULT_MINUTES[cls])
        room.minutes[cls] = round(current * (1 - LEARN_RATE) + current * ratio * LEARN_RATE, 2)
    return ratio
