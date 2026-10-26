"""Services to work with the queue, the plan and the runner."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DAYS,
    ATTR_FORCE,
    ATTR_FRONT,
    ATTR_INTERVAL,
    ATTR_MODE,
    ATTR_POSITION,
    ATTR_RETURN_HOME,
    ATTR_ROOM,
    ATTR_ROOMS,
    ATTR_SKIP,
    ATTR_SKIP_TODAY,
    ATTR_TIMESTAMP,
    ATTR_UNTIL,
    CLEAN_MODES,
    DOMAIN,
    MODE_VACUUM_MOP,
    PLAN_MODES,
    SERVICE_CLEAN_NOW,
    SERVICE_CLEAR_QUEUE,
    SERVICE_DEQUEUE,
    SERVICE_ENQUEUE,
    SERVICE_ENQUEUE_DUE,
    SERVICE_MOVE,
    SERVICE_PAUSE_ROOM,
    SERVICE_RESUME_ROOM,
    SERVICE_SET_LAST_CLEANED,
    SERVICE_SET_PLAN,
    SERVICE_SKIP_TODAY,
    SERVICE_START,
    SERVICE_STOP,
)

if TYPE_CHECKING:
    from .planner import VacuumPlanner

ROOMS = vol.All(cv.ensure_list, [vol.Any(vol.Coerce(int), cv.string)])
ROOM = vol.Any(vol.Coerce(int), cv.string)
BASE = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}

SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_ENQUEUE: vol.Schema(
        {
            **BASE,
            vol.Required(ATTR_ROOMS): ROOMS,
            vol.Optional(ATTR_MODE): vol.In(CLEAN_MODES),
            vol.Optional(ATTR_FRONT, default=False): cv.boolean,
        }
    ),
    SERVICE_DEQUEUE: vol.Schema(
        {
            **BASE,
            vol.Required(ATTR_ROOMS): ROOMS,
            vol.Optional(ATTR_SKIP_TODAY, default=True): cv.boolean,
        }
    ),
    SERVICE_MOVE: vol.Schema(
        {**BASE, vol.Required(ATTR_ROOM): ROOM, vol.Required(ATTR_POSITION): vol.Coerce(int)}
    ),
    SERVICE_CLEAR_QUEUE: vol.Schema(BASE),
    SERVICE_ENQUEUE_DUE: vol.Schema(BASE),
    SERVICE_START: vol.Schema({**BASE, vol.Optional(ATTR_FORCE, default=True): cv.boolean}),
    SERVICE_STOP: vol.Schema({**BASE, vol.Optional(ATTR_RETURN_HOME, default=True): cv.boolean}),
    SERVICE_CLEAN_NOW: vol.Schema(
        {
            **BASE,
            vol.Required(ATTR_ROOMS): ROOMS,
            vol.Optional(ATTR_MODE, default=MODE_VACUUM_MOP): vol.In(CLEAN_MODES),
        }
    ),
    SERVICE_PAUSE_ROOM: vol.Schema(
        {
            **BASE,
            vol.Required(ATTR_ROOMS): ROOMS,
            vol.Exclusive(ATTR_UNTIL, "until"): cv.datetime,
            vol.Exclusive(ATTR_DAYS, "until"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        }
    ),
    SERVICE_RESUME_ROOM: vol.Schema({**BASE, vol.Required(ATTR_ROOMS): ROOMS}),
    SERVICE_SET_PLAN: vol.Schema(
        {
            **BASE,
            vol.Required(ATTR_ROOM): ROOM,
            vol.Optional(ATTR_MODE): vol.In(PLAN_MODES),
            vol.Optional(ATTR_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
        }
    ),
    SERVICE_SKIP_TODAY: vol.Schema({**BASE, vol.Optional(ATTR_SKIP, default=True): cv.boolean}),
    SERVICE_SET_LAST_CLEANED: vol.Schema(
        {**BASE, vol.Required(ATTR_ROOMS): ROOMS, vol.Optional(ATTR_TIMESTAMP): cv.datetime}
    ),
}


def _planner(hass: HomeAssistant, call: ServiceCall) -> VacuumPlanner:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
    ]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="pick_entry")
    return entries[0].runtime_data


def _rooms(planner: VacuumPlanner, values: list[Any]) -> list[int]:
    try:
        return planner.resolve_rooms(values)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_room",
            translation_placeholders={"room": str(err).removeprefix("unknown room ")},
        ) from err


def _local(value: datetime) -> datetime:
    return dt_util.as_local(value if value.tzinfo else value.replace(tzinfo=dt_util.get_default_time_zone()))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def handle(call: ServiceCall) -> None:
        planner = _planner(hass, call)
        data = call.data
        service = call.service
        if service == SERVICE_ENQUEUE:
            planner.enqueue(_rooms(planner, data[ATTR_ROOMS]), data.get(ATTR_MODE), data[ATTR_FRONT])
        elif service == SERVICE_DEQUEUE:
            planner.dequeue(_rooms(planner, data[ATTR_ROOMS]), data[ATTR_SKIP_TODAY])
        elif service == SERVICE_MOVE:
            planner.move(_rooms(planner, [data[ATTR_ROOM]])[0], data[ATTR_POSITION])
        elif service == SERVICE_CLEAR_QUEUE:
            planner.clear_queue()
        elif service == SERVICE_ENQUEUE_DUE:
            planner.enqueue_due()
        elif service == SERVICE_START:
            planner.start(force=data[ATTR_FORCE])
        elif service == SERVICE_STOP:
            await planner.async_stop(return_home=data[ATTR_RETURN_HOME])
        elif service == SERVICE_CLEAN_NOW:
            try:
                await planner.async_clean_now(_rooms(planner, data[ATTR_ROOMS]), data[ATTR_MODE])
            except RuntimeError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="robot_busy"
                ) from err
        elif service == SERVICE_PAUSE_ROOM:
            now = dt_util.now()
            if ATTR_UNTIL in data:
                until = _local(data[ATTR_UNTIL])
            else:
                until = dt_util.start_of_local_day(now) + timedelta(days=data.get(ATTR_DAYS, 1))
            planner.pause_room(_rooms(planner, data[ATTR_ROOMS]), until)
        elif service == SERVICE_RESUME_ROOM:
            planner.resume_room(_rooms(planner, data[ATTR_ROOMS]))
        elif service == SERVICE_SET_PLAN:
            planner.set_plan(
                _rooms(planner, [data[ATTR_ROOM]])[0], data.get(ATTR_MODE), data.get(ATTR_INTERVAL)
            )
        elif service == SERVICE_SKIP_TODAY:
            await planner.async_skip_today(data[ATTR_SKIP])
        elif service == SERVICE_SET_LAST_CLEANED:
            when = _local(data[ATTR_TIMESTAMP]) if ATTR_TIMESTAMP in data else dt_util.now()
            planner.set_last_cleaned(_rooms(planner, data[ATTR_ROOMS]), when)

    for name, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, name, handle, schema=schema)
