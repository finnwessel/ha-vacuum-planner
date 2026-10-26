"""Time-of-day settings: cleaning window, midday rest, plan B question."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VacuumPlannerConfigEntry
from .const import SET_DAY_END, SET_DAY_START, SET_PLAN_B_TIME, SET_QUIET_END, SET_QUIET_START
from .entity import PlannerEntity

TIMES = tuple(
    TimeEntityDescription(key=key, translation_key=key, entity_category=EntityCategory.CONFIG)
    for key in (SET_DAY_START, SET_DAY_END, SET_QUIET_START, SET_QUIET_END, SET_PLAN_B_TIME)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(SettingTime(entry.runtime_data, d) for d in TIMES)


class SettingTime(PlannerEntity, TimeEntity):
    @property
    def native_value(self) -> time:
        return self.planner.settings[self.entity_description.key]

    async def async_set_value(self, value: time) -> None:
        self.planner.set_setting(self.entity_description.key, value.replace(second=0, microsecond=0))
