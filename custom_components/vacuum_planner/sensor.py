"""Sensors: status, queue, expected return and per-room due dates."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import VacuumPlannerConfigEntry
from .entity import PlannerEntity, RoomEntity
from .planner import STATES

STATUS = SensorEntityDescription(
    key="status",
    translation_key="status",
    device_class=SensorDeviceClass.ENUM,
    options=STATES,
)
QUEUE = SensorEntityDescription(key="queue", translation_key="queue")
RETURN = SensorEntityDescription(
    key="expected_return", translation_key="expected_return", device_class=SensorDeviceClass.TIMESTAMP
)
NEXT_DUE = SensorEntityDescription(
    key="next_due", translation_key="room_next_due", device_class=SensorDeviceClass.DATE
)
LAST_CLEANED = SensorEntityDescription(
    key="last_cleaned", translation_key="room_last_cleaned", device_class=SensorDeviceClass.TIMESTAMP
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    planner = entry.runtime_data
    entities: list[SensorEntity] = [
        StatusSensor(planner, STATUS),
        QueueSensor(planner, QUEUE),
        ReturnSensor(planner, RETURN),
    ]
    for room_id in planner.order:
        entities.append(NextDueSensor(planner, NEXT_DUE, room_id))
        entities.append(LastCleanedSensor(planner, LAST_CLEANED, room_id))
    async_add_entities(entities)


class StatusSensor(PlannerEntity, SensorEntity):
    _unrecorded_attributes = frozenset({"job", "reason_data", "stats", "absences_learned"})

    @property
    def native_value(self) -> str:
        return self.planner.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self.planner
        now = dt_util.now()
        window = p.window()
        nxt = window.next_allowed(now, p.holiday_today())
        return {
            "reason": p.reason,
            "reason_data": p.reason_data,
            "session_active": p.session_active,
            "forced": p.session_forced,
            "job": p.job.as_dict() if p.job else None,
            "away": p.away_ok(now),
            "somebody_home": p.somebody_home(),
            "absent_since": p.absence_start.isoformat() if p.absence_start else None,
            "expected_return": p.expected_return.isoformat() if p.expected_return else None,
            "return_source": p.return_source,
            "budget_minutes": None if p.budget is None else round(p.budget),
            "quiet": window.blocked_reason(now, p.holiday_today()),
            "next_allowed": nxt.isoformat() if nxt else None,
            "skipped_today": p.skip_auto_date == now.date(),
            "battery": p.battery(),
            "stats": p.stats,
            "absences_learned": len(p.absence.sessions),
        }


class QueueSensor(PlannerEntity, SensorEntity):
    """Queue length; attributes carry everything the dashboard card needs."""

    _unrecorded_attributes = frozenset({"items", "stages", "rooms", "next_stage", "finish"})

    @property
    def native_value(self) -> int:
        return len(self.planner.queue)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now()
        view = self.planner.queue_view(now)
        view["rooms"] = [self.planner.room_view(rid, now) for rid in self.planner.order]
        return view


class ReturnSensor(PlannerEntity, SensorEntity):
    @property
    def native_value(self) -> datetime | None:
        return self.planner.expected_return

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self.planner.return_source, "samples": self.planner.return_samples}


class NextDueSensor(RoomEntity, SensorEntity):
    @property
    def native_value(self) -> date | None:
        due = self.room.next_due()
        if due is None:
            return None
        return dt_util.now().date() if due.year == 1 else due

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.planner.room_view(self.room_id, dt_util.now())


class LastCleanedSensor(RoomEntity, SensorEntity):
    @property
    def native_value(self) -> datetime | None:
        return self.room.last_cleaned
