"""Vacuum Planner: a cleaning plan, a daily queue and a smart runner for room vacuums.

One config entry is one robot. Due rooms land in the queue every day; the
queue is cleaned in stages while nobody is home, within the allowed hours and
only as far as the expected return leaves time.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .planner import VacuumPlanner
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type VacuumPlannerConfigEntry = ConfigEntry[VacuumPlanner]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VacuumPlannerConfigEntry) -> bool:
    planner = VacuumPlanner(hass, entry)
    entry.runtime_data = planner
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await planner.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VacuumPlannerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: VacuumPlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
