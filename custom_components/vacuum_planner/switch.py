"""On/off settings: automatic cleaning and quiet hours."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VacuumPlannerConfigEntry
from .const import SET_AUTO, SET_QUIET_MIDDAY, SET_QUIET_SUNDAYS
from .entity import PlannerEntity

SWITCHES = (
    SwitchEntityDescription(key=SET_AUTO, translation_key=SET_AUTO),
    SwitchEntityDescription(
        key=SET_QUIET_MIDDAY, translation_key=SET_QUIET_MIDDAY, entity_category=EntityCategory.CONFIG
    ),
    SwitchEntityDescription(
        key=SET_QUIET_SUNDAYS, translation_key=SET_QUIET_SUNDAYS, entity_category=EntityCategory.CONFIG
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(SettingSwitch(entry.runtime_data, d) for d in SWITCHES)


class SettingSwitch(PlannerEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return bool(self.planner.settings[self.entity_description.key])

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.set_setting(self.entity_description.key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.set_setting(self.entity_description.key, False)
