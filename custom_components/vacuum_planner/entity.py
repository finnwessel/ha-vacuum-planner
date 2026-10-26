"""Base entities."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, ROOM_NAME
from .planner import VacuumPlanner


class PlannerEntity(Entity):
    """Entity of the planner device that follows planner updates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, planner: VacuumPlanner, description: EntityDescription) -> None:
        self.planner = planner
        self.entity_description = description
        entry_id = planner.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=planner.entry.title,
            manufacturer="Vacuum Planner",
            model="Cleaning planner",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.planner.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class RoomEntity(PlannerEntity):
    """Entity that belongs to one room of the plan."""

    def __init__(
        self, planner: VacuumPlanner, description: EntityDescription, room_id: int
    ) -> None:
        super().__init__(planner, description)
        self.room_id = room_id
        self._attr_unique_id = f"{planner.entry.entry_id}_room{room_id}_{description.key}"
        self._attr_translation_placeholders = {"room": planner.room_cfg[room_id][ROOM_NAME]}

    @property
    def room(self):
        return self.planner.rooms[self.room_id]
