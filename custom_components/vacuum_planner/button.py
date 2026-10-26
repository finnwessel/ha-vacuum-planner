"""Buttons: start the queue now, stop, take over due rooms."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VacuumPlannerConfigEntry
from .entity import PlannerEntity

BUTTONS = (
    ButtonEntityDescription(key="start", translation_key="start"),
    ButtonEntityDescription(key="stop", translation_key="stop"),
    ButtonEntityDescription(key="enqueue_due", translation_key="enqueue_due"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(PlannerButton(entry.runtime_data, d) for d in BUTTONS)


class PlannerButton(PlannerEntity, ButtonEntity):
    async def async_press(self) -> None:
        key = self.entity_description.key
        if key == "start":
            self.planner.start(force=True)
        elif key == "stop":
            await self.planner.async_stop(return_home=True)
        else:
            self.planner.enqueue_due()
