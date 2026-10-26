"""Per-room cleaning mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VacuumPlannerConfigEntry
from .const import PLAN_MODES
from .entity import RoomEntity

MODE = SelectEntityDescription(
    key="mode",
    translation_key="room_mode",
    options=list(PLAN_MODES),
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    planner = entry.runtime_data
    async_add_entities(RoomModeSelect(planner, MODE, rid) for rid in planner.order)


class RoomModeSelect(RoomEntity, SelectEntity):
    @property
    def current_option(self) -> str:
        return self.room.mode

    async def async_select_option(self, option: str) -> None:
        self.planner.set_plan(self.room_id, option, None)
