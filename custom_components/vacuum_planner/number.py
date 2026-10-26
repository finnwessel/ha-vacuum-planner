"""Per-room interval and numeric settings."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VacuumPlannerConfigEntry
from .const import (
    SET_HOMEWAY_RADIUS,
    SET_MIN_BATTERY,
    SET_MIN_DISTANCE,
    SET_PLAN_B_DAYS,
    SET_PREWARN,
    SET_RETRIES,
)
from .entity import PlannerEntity, RoomEntity

INTERVAL = NumberEntityDescription(
    key="interval",
    translation_key="room_interval",
    native_min_value=1,
    native_max_value=30,
    native_step=1,
    native_unit_of_measurement=UnitOfTime.DAYS,
    mode=NumberMode.BOX,
    entity_category=EntityCategory.CONFIG,
)


def _setting(key: str, lo: float, hi: float, step: float, unit: str | None) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        native_min_value=lo,
        native_max_value=hi,
        native_step=step,
        native_unit_of_measurement=unit,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


SETTINGS = (
    _setting(SET_MIN_DISTANCE, 250, 20000, 250, UnitOfLength.METERS),
    _setting(SET_HOMEWAY_RADIUS, 1000, 200000, 1000, UnitOfLength.METERS),
    _setting(SET_MIN_BATTERY, 10, 100, 5, PERCENTAGE),
    _setting(SET_PREWARN, 0, 30, 1, UnitOfTime.MINUTES),
    _setting(SET_PLAN_B_DAYS, 0, 7, 1, UnitOfTime.DAYS),
    _setting(SET_RETRIES, 0, 3, 1, None),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    planner = entry.runtime_data
    entities: list[NumberEntity] = [SettingNumber(planner, d) for d in SETTINGS]
    entities.extend(RoomIntervalNumber(planner, INTERVAL, rid) for rid in planner.order)
    async_add_entities(entities)


class RoomIntervalNumber(RoomEntity, NumberEntity):
    @property
    def native_value(self) -> float:
        return self.room.interval

    async def async_set_native_value(self, value: float) -> None:
        self.planner.set_plan(self.room_id, None, int(value))


class SettingNumber(PlannerEntity, NumberEntity):
    @property
    def native_value(self) -> float:
        return self.planner.settings[self.entity_description.key]

    async def async_set_native_value(self, value: float) -> None:
        self.planner.set_setting(self.entity_description.key, int(value))
