"""Diagnostics: plan, queue, learned values and the current runner state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from . import VacuumPlannerConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VacuumPlannerConfigEntry
) -> dict[str, Any]:
    planner = entry.runtime_data
    now = dt_util.now()
    return {
        "options": {k: v for k, v in entry.options.items() if k != "notify_devices"},
        "state": planner.state,
        "reason": planner.reason,
        "reason_data": planner.reason_data,
        "job": planner.job.as_dict() if planner.job else None,
        "persons": planner.persons,
        "presence_entities": {"distance": planner._distance, "direction": planner._direction},
        "queue": planner.queue_view(now),
        "rooms": [planner.room_view(rid, now) for rid in planner.order],
        "absences": planner.absence.to_list(),
        "expected_return": planner.expected_return.isoformat() if planner.expected_return else None,
        "return_source": planner.return_source,
        "stats": planner.stats,
    }
