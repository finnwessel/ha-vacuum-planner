"""Config and options flow: robot, presence, rooms and notifications."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_AWAY_DELAY,
    CONF_BATTERY,
    CONF_CALENDARS,
    CONF_DEFAULT_ABSENCE,
    CONF_DOCK_STATUS,
    CONF_ERROR_SENSOR,
    CONF_GUEST_MODE,
    CONF_MIN_WATER,
    CONF_NOTIFY,
    CONF_PROXIMITY,
    CONF_RETURN_BUFFER,
    CONF_ROOMS,
    CONF_STAGE_OVERHEAD,
    CONF_STAGE_PAUSE,
    CONF_TASK_STATUS,
    CONF_TRAVEL_TIME,
    CONF_VACUUM,
    CONF_WATER,
    CONF_WORKDAY,
    DEFAULT_OPTIONS,
    DOMAIN,
    ROOM_BLOCKERS,
    ROOM_ICON,
    ROOM_ID,
    ROOM_NAME,
)

CONF_NAME = "name"
CONF_ROOM_IDS = "room_ids"
CONF_POSITION = "position"

ENTITY_KEYS = (
    CONF_VACUUM,
    CONF_TASK_STATUS,
    CONF_ERROR_SENSOR,
    CONF_BATTERY,
    CONF_WATER,
    CONF_DOCK_STATUS,
    CONF_PROXIMITY,
    CONF_GUEST_MODE,
    CONF_WORKDAY,
    CONF_CALENDARS,
)
TUNING = {
    CONF_STAGE_PAUSE: (0, 30, "min"),
    CONF_AWAY_DELAY: (0, 30, "min"),
    CONF_RETURN_BUFFER: (0, 60, "min"),
    CONF_TRAVEL_TIME: (0, 120, "min"),
    CONF_DEFAULT_ABSENCE: (15, 600, "min"),
    CONF_STAGE_OVERHEAD: (0, 30, "min"),
    CONF_MIN_WATER: (0, 100, "%"),
}


def _entity(domain: str | list[str], multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter=selector.EntityFilterSelectorConfig(domain=domain), multiple=multiple
        )
    )


def _opt(key: str, values: dict[str, Any]) -> vol.Optional:
    if values.get(key):
        return vol.Optional(key, description={"suggested_value": values[key]})
    return vol.Optional(key)


def _entities_schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_VACUUM, default=values.get(CONF_VACUUM, vol.UNDEFINED)): _entity("vacuum"),
            _opt(CONF_TASK_STATUS, values): _entity("sensor"),
            _opt(CONF_ERROR_SENSOR, values): _entity("sensor"),
            _opt(CONF_BATTERY, values): _entity("sensor"),
            _opt(CONF_WATER, values): _entity("sensor"),
            _opt(CONF_DOCK_STATUS, values): _entity("sensor"),
            vol.Required(
                CONF_PROXIMITY, default=values.get(CONF_PROXIMITY, vol.UNDEFINED)
            ): selector.ConfigEntrySelector(selector.ConfigEntrySelectorConfig(integration="proximity")),
            _opt(CONF_GUEST_MODE, values): _entity(["input_boolean", "switch", "binary_sensor"]),
            _opt(CONF_WORKDAY, values): _entity("binary_sensor"),
            _opt(CONF_CALENDARS, values): _entity("calendar", multiple=True),
        }
    )


def _robot_rooms(hass: HomeAssistant, vacuum: str) -> dict[int, str]:
    state = hass.states.get(vacuum)
    rooms: dict[int, str] = {}
    if state is None:
        return rooms
    for raw in state.attributes.get("rooms") or state.attributes.get("segments") or []:
        try:
            rooms[int(raw["id"])] = str(raw.get("name") or f"Room {raw['id']}")
        except (KeyError, TypeError, ValueError):
            continue
    return rooms


class _RoomSteps:
    """Shared steps of config and options flow."""

    hass: HomeAssistant
    values: dict[str, Any]
    _pending: list[int]
    _rooms: list[dict[str, Any]]

    def _known_rooms(self) -> dict[int, str]:
        rooms = _robot_rooms(self.hass, self.values[CONF_VACUUM])
        for room in self.values.get(CONF_ROOMS, []):
            rooms.setdefault(int(room[ROOM_ID]), room[ROOM_NAME])
        return rooms

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        known = self._known_rooms()
        current = [str(r[ROOM_ID]) for r in self.values.get(CONF_ROOMS, [])]
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = [int(i) for i in user_input[CONF_ROOM_IDS]]
            if not ids:
                errors["base"] = "no_rooms"
            else:
                self._pending = ids
                self._rooms = []
                return await self.async_step_room()
        options = [selector.SelectOptionDict(value=str(i), label=f"{n} ({i})") for i, n in known.items()]
        schema = vol.Schema(
            {
                vol.Required(CONF_ROOM_IDS, default=current): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, multiple=True, custom_value=True)
                )
            }
        )
        return self.async_show_form(step_id="rooms", data_schema=schema, errors=errors)

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        room_id = self._pending[0]
        old = next((r for r in self.values.get(CONF_ROOMS, []) if int(r[ROOM_ID]) == room_id), {})
        if user_input is not None:
            self._rooms.append(
                {
                    ROOM_ID: room_id,
                    ROOM_NAME: user_input[ROOM_NAME],
                    ROOM_ICON: user_input.get(ROOM_ICON) or "mdi:floor-plan",
                    ROOM_BLOCKERS: user_input.get(ROOM_BLOCKERS, []),
                    CONF_POSITION: user_input[CONF_POSITION],
                }
            )
            self._pending.pop(0)
            if self._pending:
                return await self.async_step_room()
            self._rooms.sort(key=lambda r: r[CONF_POSITION])
            self.values[CONF_ROOMS] = [
                {k: v for k, v in r.items() if k != CONF_POSITION} for r in self._rooms
            ]
            return await self.async_step_notify()
        known = self._known_rooms()
        old_ids = [int(r[ROOM_ID]) for r in self.values.get(CONF_ROOMS, [])]
        position = old_ids.index(room_id) + 1 if room_id in old_ids else len(self._rooms) + 1
        schema = vol.Schema(
            {
                vol.Required(ROOM_NAME, default=old.get(ROOM_NAME, known.get(room_id, f"Room {room_id}"))): str,
                vol.Optional(ROOM_ICON, default=old.get(ROOM_ICON, "mdi:floor-plan")): selector.IconSelector(),
                vol.Optional(ROOM_BLOCKERS, default=old.get(ROOM_BLOCKERS, [])): _entity(
                    ["media_player", "binary_sensor", "input_boolean", "switch"], multiple=True
                ),
                vol.Required(CONF_POSITION, default=position): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=50, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(
            step_id="room",
            data_schema=schema,
            description_placeholders={"room": known.get(room_id, str(room_id)), "id": str(room_id)},
        )

    def _persons(self) -> list[str]:
        entry = self.hass.config_entries.async_get_entry(self.values.get(CONF_PROXIMITY, ""))
        return list(entry.data.get("tracked_entities", [])) if entry else []

    async def async_step_notify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        persons = self._persons()
        current = self.values.get(CONF_NOTIFY) or {}
        if user_input is not None or not persons:
            self.values[CONF_NOTIFY] = {p: d for p, d in (user_input or {}).items() if d}
            return await self.async_step_tuning()
        schema = vol.Schema(
            {
                _opt(person, current): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(integration="mobile_app")
                )
                for person in persons
            }
        )
        return self.async_show_form(step_id="notify", data_schema=schema)

    async def async_step_tuning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.values.update({k: int(v) for k, v in user_input.items()})
            return self._finish()
        schema = vol.Schema(
            {
                vol.Required(key, default=self.values.get(key, DEFAULT_OPTIONS[key])): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=lo, max=hi, step=1, unit_of_measurement=unit,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
                for key, (lo, hi, unit) in TUNING.items()
            }
        )  # fmt: skip
        return self.async_show_form(step_id="tuning", data_schema=schema)

    def _finish(self) -> ConfigFlowResult:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError


class VacuumPlannerConfigFlow(_RoomSteps, ConfigFlow, domain=DOMAIN):
    """Set up a planner for one robot."""

    VERSION = 1

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self._pending = []
        self._rooms = []
        self._name = "Vacuum planner"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._name = user_input.pop(CONF_NAME)
            await self.async_set_unique_id(user_input[CONF_VACUUM])
            self._abort_if_unique_id_configured()
            self.values.update(user_input)
            return await self.async_step_rooms()
        schema = vol.Schema({vol.Required(CONF_NAME, default=self._name): str}).extend(
            _entities_schema(self.values).schema
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(title=self._name, data={}, options=self.values)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VacuumPlannerOptionsFlow()


class VacuumPlannerOptionsFlow(_RoomSteps, OptionsFlow):
    """Change robot entities, rooms, notifications and tuning."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self._pending = []
        self._rooms = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self.values:
            self.values = {**DEFAULT_OPTIONS, **self.config_entry.options}
        if user_input is not None:
            for key in ENTITY_KEYS:
                self.values.pop(key, None)
            self.values.update(user_input)
            return await self.async_step_rooms()
        return self.async_show_form(step_id="init", data_schema=_entities_schema(self.values))

    def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(data=self.values)
