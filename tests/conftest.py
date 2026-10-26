"""Fixtures for Home Assistant tests."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components in every test."""
    return


@pytest.fixture(autouse=True)
async def unload_planners(hass: HomeAssistant):
    """Unload every planner after a test so its timers are cancelled."""
    yield
    for entry in hass.config_entries.async_entries("vacuum_planner"):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
