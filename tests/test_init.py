"""Home Assistant tests: config flow, daily queue, a run with stages, quiet hours, errors."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.vacuum_planner.const import (
    ACTION_RETURN,
    CONF_AWAY_DELAY,
    CONF_BATTERY,
    CONF_ERROR_SENSOR,
    CONF_NOTIFY,
    CONF_PROXIMITY,
    CONF_ROOMS,
    CONF_TASK_STATUS,
    CONF_VACUUM,
    DEFAULT_OPTIONS,
    DOMAIN,
)

VACUUM = "vacuum.robot"
TASK = "sensor.robot_task_status"
ERROR = "sensor.robot_error_message"
BATTERY = "sensor.robot_battery"
PERSON_A = "person.alex"
PERSON_B = "person.sam"
ROOMS = [
    {"id": 1, "name": "Kitchen", "icon": "mdi:silverware-fork-knife", "blockers": []},
    {"id": 4, "name": "Hall", "icon": "mdi:door", "blockers": []},
    {"id": 5, "name": "Living", "icon": "mdi:sofa", "blockers": ["media_player.tv"]},
]


@pytest.fixture
async def berlin(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> datetime:
    await hass.config.async_set_time_zone("Europe/Berlin")
    start = datetime(2026, 10, 26, 10, 0, tzinfo=dt_util.get_time_zone("Europe/Berlin"))
    freezer.move_to(start)
    return start


def _presence(hass: HomeAssistant) -> tuple[MockConfigEntry, dict[str, str]]:
    prox = MockConfigEntry(
        domain="proximity",
        title="Home",
        data={"zone": "zone.home", "tracked_entities": [PERSON_A, PERSON_B]},
    )
    prox.add_to_hass(hass)
    reg = er.async_get(hass)
    ids = {}
    for person, key in ((PERSON_A, "alex"), (PERSON_B, "sam")):
        entry = reg.async_get_or_create("person", "person", key, suggested_object_id=key)
        for suffix in ("dist_to_zone", "dir_of_travel"):
            sensor = reg.async_get_or_create(
                "sensor",
                "proximity",
                f"{prox.entry_id}_{entry.id}_{suffix}",
                config_entry=prox,
                suggested_object_id=f"home_{key}_{suffix}",
            )
            ids[f"{person}_{suffix}"] = sensor.entity_id
    nearest = reg.async_get_or_create(
        "sensor", "proximity", f"{prox.entry_id}_dist_to_zone", config_entry=prox,
        suggested_object_id="home_nearest",
    )  # fmt: skip
    ids["nearest"] = nearest.entity_id
    return prox, ids


def _set_home(hass: HomeAssistant, ids: dict[str, str], home: bool, distance: float = 5000) -> None:
    for person in (PERSON_A, PERSON_B):
        hass.states.async_set(person, "home" if home else "not_home")
        hass.states.async_set(
            ids[f"{person}_dist_to_zone"], "0" if home else str(distance),
            {"unit_of_measurement": "m"},
        )  # fmt: skip
        hass.states.async_set(ids[f"{person}_dir_of_travel"], "arrived" if home else "away_from")
    hass.states.async_set(ids["nearest"], "0" if home else str(distance), {"unit_of_measurement": "m"})


def _robot(hass: HomeAssistant, state: str = "docked", task: str = "Completed", error: str = "") -> None:
    hass.states.async_set(VACUUM, state, {"rooms": [{"id": r["id"], "name": r["name"]} for r in ROOMS]})
    hass.states.async_set(TASK, task)
    hass.states.async_set(ERROR, error)
    hass.states.async_set(BATTERY, "100")


async def _setup(hass: HomeAssistant, home: bool = True, **options) -> tuple[MockConfigEntry, dict]:
    prox, ids = _presence(hass)
    phone = MockConfigEntry(domain="mobile_app", data={"device_name": "Phone Alex"})
    phone.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=phone.entry_id, identifiers={("mobile_app", "alex")}, name="Phone Alex"
    )
    _robot(hass)
    _set_home(hass, ids, home)
    hass.states.async_set("media_player.tv", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Vacuum planner",
        data={},
        options={
            **DEFAULT_OPTIONS,
            CONF_VACUUM: VACUUM,
            CONF_TASK_STATUS: TASK,
            CONF_ERROR_SENSOR: ERROR,
            CONF_BATTERY: BATTERY,
            CONF_PROXIMITY: prox.entry_id,
            CONF_ROOMS: ROOMS,
            CONF_NOTIFY: {PERSON_A: device.id},
            CONF_AWAY_DELAY: 0,
            **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    planner = entry.runtime_data
    # plan: kitchen & hall due, living room cleaned yesterday
    now = dt_util.now()
    planner.set_plan(1, "vacuum_mop", 2)
    planner.set_plan(4, "vacuum", 2)
    planner.set_plan(5, "vacuum_mop", 3)
    planner.set_last_cleaned([5], now - timedelta(days=1))
    await hass.async_block_till_done()
    return entry, ids


def _queue(hass: HomeAssistant) -> list[int]:
    state = hass.states.get("sensor.vacuum_planner_queue")
    return [item["id"] for item in state.attributes["items"]]


def _status(hass: HomeAssistant) -> tuple[str, str]:
    state = hass.states.get("sensor.vacuum_planner_status")
    return state.state, state.attributes["reason"]


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_config_flow(hass: HomeAssistant) -> None:
    prox, _ = _presence(hass)
    _robot(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Robot", CONF_VACUUM: VACUUM, CONF_TASK_STATUS: TASK, CONF_PROXIMITY: prox.entry_id},
    )
    assert result["step_id"] == "rooms"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"room_ids": ["4", "1"]})
    assert result["step_id"] == "room"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Flur", "icon": "mdi:door", "blockers": [], "position": 2}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Küche", "icon": "mdi:pot", "blockers": [], "position": 1}
    )
    assert result["step_id"] == "notify"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(DEFAULT_OPTIONS))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [r["name"] for r in result["options"][CONF_ROOMS]] == ["Küche", "Flur"]


async def test_due_rooms_are_queued_every_day(
    hass: HomeAssistant, berlin: datetime, freezer: FrozenDateTimeFactory
) -> None:
    entry, _ = await _setup(hass)
    assert _queue(hass) == [1, 4]
    assert _status(hass) == ("waiting", "someone_home")

    # removing a due room skips it for today only
    await hass.services.async_call(DOMAIN, "dequeue", {"rooms": ["Kitchen"]}, blocking=True)
    await hass.async_block_till_done()
    assert _queue(hass) == [4]
    await _tick(hass, freezer, 60)
    assert _queue(hass) == [4]
    await _tick(hass, freezer, 14 * 60 + 5)  # next day 00:05
    assert _queue(hass) == [4, 1]

    # manual entries go to the front when asked; living room not due → manual
    await hass.services.async_call(DOMAIN, "enqueue", {"rooms": [5], "front": True}, blocking=True)
    await hass.async_block_till_done()
    queue = hass.states.get("sensor.vacuum_planner_queue").attributes["items"]
    assert [(i["id"], i["source"]) for i in queue][0] == (5, "manual")


async def test_run_in_stages_while_away(
    hass: HomeAssistant, berlin: datetime, freezer: FrozenDateTimeFactory
) -> None:
    send = async_mock_service(hass, "vacuum", "send_command")
    notify = async_mock_service(hass, "notify", "mobile_app_phone_alex")
    entry, ids = await _setup(hass)

    _set_home(hass, ids, home=False)
    await hass.async_block_till_done()
    assert _status(hass) == ("prewarn", "prewarn")
    assert len(notify) == 1
    assert notify[0].data["data"]["priority"] == "high"
    assert notify[0].data["data"]["ttl"] == 0
    assert not send

    await _tick(hass, freezer, 6)
    assert len(send) == 1
    # starting removes the prewarn so a late delivery can't show stale info
    assert notify[-1].data == {
        "message": "clear_notification",
        "data": {"tag": "vacuum_planner_prewarn"},
    }
    assert send[0].data["params"]["rooms"] == [{"id": 4, "clean_mode": "vacuum"}]  # dry first
    hass.states.async_set(TASK, "Cleaning")
    hass.states.async_set(VACUUM, "cleaning")
    await hass.async_block_till_done()
    assert _status(hass)[0] == "cleaning"

    await _tick(hass, freezer, 10)
    hass.states.async_set(VACUUM, "docked")
    hass.states.async_set(TASK, "Completed")
    await hass.async_block_till_done()
    assert _queue(hass) == [1]
    assert hass.states.get("sensor.vacuum_planner_hall_last_cleaned").state != "unknown"
    assert _status(hass)[0] == "between_stages"

    await _tick(hass, freezer, 3)
    assert len(send) == 2
    assert send[1].data["params"]["rooms"] == [{"id": 1, "clean_mode": "vacuum_mop"}]
    hass.states.async_set(TASK, "Cleaning")
    hass.states.async_set(VACUUM, "cleaning")
    await _tick(hass, freezer, 12)
    hass.states.async_set(VACUUM, "docked")
    hass.states.async_set(TASK, "Completed")
    await hass.async_block_till_done()
    assert _queue(hass) == []
    await _tick(hass, freezer, 3)
    assert _status(hass) == ("idle", "queue_empty")


async def test_midday_rest_blocks_start(
    hass: HomeAssistant, berlin: datetime, freezer: FrozenDateTimeFactory
) -> None:
    send = async_mock_service(hass, "vacuum", "send_command")
    freezer.move_to(berlin.replace(hour=13, minute=20))
    entry, ids = await _setup(hass)
    _set_home(hass, ids, home=False)
    await hass.async_block_till_done()
    state, reason = _status(hass)
    assert (state, reason) == ("waiting", "quiet_midday")
    assert not send
    # switching the midday rest off lets it plan again
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.vacuum_planner_midday_rest"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _status(hass)[0] == "prewarn"


async def test_return_home_sends_robot_back(
    hass: HomeAssistant, berlin: datetime, freezer: FrozenDateTimeFactory
) -> None:
    async_mock_service(hass, "vacuum", "send_command")
    back = async_mock_service(hass, "vacuum", "return_to_base")
    notify = async_mock_service(hass, "notify", "mobile_app_phone_alex")
    entry, ids = await _setup(hass)
    entry.runtime_data.set_setting("prewarn", 0)
    _set_home(hass, ids, home=False)
    await hass.async_block_till_done()
    hass.states.async_set(TASK, "Cleaning")
    hass.states.async_set(VACUUM, "cleaning")
    await hass.async_block_till_done()

    hass.states.async_set(ids[f"{PERSON_A}_dist_to_zone"], "800", {"unit_of_measurement": "m"})
    await hass.async_block_till_done()
    actions = [a["action"] for a in notify[-1].data["data"]["actions"]]
    assert ACTION_RETURN in actions
    await _tick(hass, freezer, 4)
    assert len(back) == 1
    assert _queue(hass) == [1, 4]  # nothing credited


async def test_error_counts_failure_and_isolates_room(
    hass: HomeAssistant, berlin: datetime, freezer: FrozenDateTimeFactory
) -> None:
    async_mock_service(hass, "vacuum", "send_command")
    entry, ids = await _setup(hass)
    planner = entry.runtime_data
    planner.set_setting("prewarn", 0)
    _set_home(hass, ids, home=False)
    await hass.async_block_till_done()
    hass.states.async_set(VACUUM, "cleaning")
    await hass.async_block_till_done()
    hass.states.async_set(VACUUM, "error")
    hass.states.async_set(ERROR, "Wheel stuck")
    await _tick(hass, freezer, 1)
    assert _status(hass)[0] == "error"
    assert planner.rooms[4].consecutive_failures == 1
    stages = hass.states.get("sensor.vacuum_planner_queue").attributes["stages"]
    assert stages[-1] == {"mode": "vacuum", "rooms": [4], "minutes": 12, "alone": True}

    hass.states.async_set(ERROR, "")
    hass.states.async_set(VACUUM, "docked")
    await _tick(hass, freezer, 1)
    assert _status(hass)[0] != "error"
