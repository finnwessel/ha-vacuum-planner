"""Runtime controller: watches the house and the robot and runs the queue."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfLength
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    ACTION_CONTINUE,
    ACTION_PLAN_B_LATER,
    ACTION_PLAN_B_NOW,
    ACTION_PREFIX,
    ACTION_RETURN,
    ACTION_SKIP_TODAY,
    ACTION_START_NOW,
    CLEAN_MODES,
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
    DEFAULT_SETTINGS,
    DOMAIN,
    EVENT_JOB_FINISHED,
    MODE_OFF,
    MODE_VACUUM,
    MODE_VACUUM_MOP,
    PUSH_URGENT,
    ROOM_BLOCKERS,
    ROOM_ICON,
    ROOM_ID,
    ROOM_NAME,
    SET_AUTO,
    SET_DAY_END,
    SET_DAY_START,
    SET_HOMEWAY_RADIUS,
    SET_MIN_BATTERY,
    SET_MIN_DISTANCE,
    SET_PLAN_B_DAYS,
    SET_PLAN_B_TIME,
    SET_PREWARN,
    SET_QUIET_END,
    SET_QUIET_MIDDAY,
    SET_QUIET_START,
    SET_QUIET_SUNDAYS,
    SET_RETRIES,
    STORAGE_VERSION,
    TAG_ERROR,
    TAG_PLAN_B,
    TAG_PREWARN,
    TAG_RETURN,
)
from .messages import join_names, text
from .model import (
    SKIP_FAILED,
    SKIP_PAUSED,
    SKIP_TODAY,
    SOURCE_MANUAL,
    SOURCE_PLAN,
    Queue,
    RoomPlan,
    Stage,
    build_stages,
    covers,
    learn_durations,
    plan_stage,
    queue_minutes,
    sync_due,
)
from .schedule import AbsenceModel, Window, absences_from_history, calendar_return

_LOGGER = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_PREWARN = "prewarn"
STATE_STARTING = "starting"
STATE_CLEANING = "cleaning"
STATE_BETWEEN = "between_stages"
STATE_ERROR = "error"
STATES = [
    STATE_IDLE,
    STATE_WAITING,
    STATE_PREWARN,
    STATE_STARTING,
    STATE_CLEANING,
    STATE_BETWEEN,
    STATE_ERROR,
]

ERROR_EMPTY = {"", "none", "None", "no_error", STATE_UNKNOWN, STATE_UNAVAILABLE}
BUSY_STATES = {"on", "playing"}
DOCK_BUSY = {"Washing", "Emptying", "washing", "emptying"}
BUSY_GRACE = timedelta(minutes=5)
START_TIMEOUT = timedelta(minutes=3)
PAUSE_LIMIT = timedelta(minutes=15)
ERROR_DELAY = timedelta(seconds=30)
RETURN_TIMEOUT = timedelta(minutes=3)
PLAN_B_VALID = timedelta(hours=3)
CALENDAR_REFRESH = timedelta(minutes=10)
SAVE_DELAY = 5


@dataclass
class Job:
    """One robot job (stage) that the planner started and follows."""

    room_ids: list[int]
    mode: str
    sent: datetime
    estimate: float
    forced: bool = False
    single: bool = False
    attempt: int = 1
    cleaning_since: datetime | None = None
    paused_since: datetime | None = None
    interrupted: bool = False
    aborted: bool = False
    return_asked: datetime | None = None
    return_person: str | None = None
    keep_going: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rooms": self.room_ids,
            "mode": self.mode,
            "started": (self.cleaning_since or self.sent).isoformat(),
            "estimate": round(self.estimate),
            "finish": (
                (self.cleaning_since or self.sent) + timedelta(minutes=self.estimate)
            ).isoformat(),
            "forced": self.forced,
            "single": self.single,
            "attempt": self.attempt,
            "return_asked": self.return_asked is not None,
        }


def _time(value: Any, default: time) -> time:
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        return default


def _float(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class VacuumPlanner:
    """Owns plan, queue and settings and drives the robot."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opt: dict[str, Any] = {**DEFAULT_OPTIONS, **entry.data, **entry.options}
        self.room_cfg: dict[int, dict[str, Any]] = {
            int(room[ROOM_ID]): room for room in self.opt.get(CONF_ROOMS, [])
        }
        self.order: list[int] = list(self.room_cfg)
        self.rooms: dict[int, RoomPlan] = {rid: RoomPlan(rid) for rid in self.room_cfg}
        self.queue = Queue()
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.absence = AbsenceModel()
        self.stats: dict[str, Any] = {
            "jobs": 0,
            "jobs_interrupted": 0,
            "errors": 0,
            "returns_home": 0,
            "minutes": 0.0,
            "last_job": None,
        }
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()
        self.language = hass.config.language or "en"

        # runtime state
        self.state = STATE_IDLE
        self.reason: str | None = "queue_empty"
        self.reason_data: dict[str, Any] = {}
        self.session_active = False
        self.session_forced = False
        self.job: Job | None = None
        self.between_since: datetime | None = None
        self.prewarn_until: datetime | None = None
        self.skip_auto_date: date | None = None
        self.absence_start: datetime | None = None
        self.expected_return: datetime | None = None
        self.return_source: str | None = None
        self.return_samples = 0
        self.budget: float | None = None
        self.next_stage: Stage | None = None
        self._away_since: datetime | None = None
        self._busy_last: dict[int, datetime] = {}
        self._error_since: datetime | None = None
        self._error_reported = False
        self._day: date | None = None
        self._plan_b_day: date | None = None
        self._plan_b_until: datetime | None = None
        self._calendar_events: list[tuple[datetime, datetime]] = []
        self._calendar_fetched: datetime | None = None

        # resolved entities
        self.persons: list[str] = []
        self._distance: dict[str, str] = {}
        self._direction: dict[str, str] = {}
        self._nearest: str | None = None

    # ------------------------------------------------------------------ lifecycle --
    async def async_start(self) -> None:
        await self._async_load()
        self._resolve_presence_entities()
        if not self.absence.sessions:
            await self._async_seed_absences()
        watched = [
            self.opt[CONF_VACUUM],
            *self._optional(CONF_TASK_STATUS, CONF_ERROR_SENSOR, CONF_BATTERY, CONF_WATER),
            *self._optional(CONF_DOCK_STATUS, CONF_GUEST_MODE, CONF_WORKDAY),
            *self.persons,
            *self._distance.values(),
            *self._direction.values(),
            *([self._nearest] if self._nearest else []),
            *{b for cfg in self.room_cfg.values() for b in cfg.get(ROOM_BLOCKERS, [])},
        ]
        for unsub in (
            async_track_state_change_event(self.hass, list(dict.fromkeys(watched)), self._on_state),
            async_track_time_interval(self.hass, self._on_tick, timedelta(minutes=1)),
            self.hass.bus.async_listen("mobile_app_notification_action", self._on_action),
        ):
            self.entry.async_on_unload(unsub)
        self._schedule()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._data())

    def _optional(self, *keys: str) -> list[str]:
        return [self.opt[k] for k in keys if self.opt.get(k)]

    # ---------------------------------------------------------------- persistence --
    async def _async_load(self) -> None:
        data = await self._store.async_load() or {}
        for raw in data.get("rooms", []):
            try:
                room = RoomPlan.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                continue
            if room.room_id in self.room_cfg:
                self.rooms[room.room_id] = room
        self.queue = Queue.from_list(data.get("queue"))
        self.queue.items = [i for i in self.queue.items if i.room_id in self.room_cfg]
        stored = data.get("settings", {})
        for key, default in DEFAULT_SETTINGS.items():
            if key not in stored:
                continue
            if isinstance(default, time):
                self.settings[key] = _time(stored[key], default)
            elif isinstance(default, bool):
                self.settings[key] = bool(stored[key])
            else:
                self.settings[key] = stored[key]
        self.absence = AbsenceModel.from_list(data.get("absences"))
        self.stats.update(data.get("stats", {}))
        if data.get("absence_start"):
            self.absence_start = datetime.fromisoformat(data["absence_start"])
        if data.get("skip_auto_date"):
            self.skip_auto_date = date.fromisoformat(data["skip_auto_date"])

    def _data(self) -> dict[str, Any]:
        settings = {
            k: (v.isoformat() if isinstance(v, time) else v) for k, v in self.settings.items()
        }
        return {
            "rooms": [room.to_dict() for room in self.rooms.values()],
            "queue": self.queue.to_list(),
            "settings": settings,
            "absences": self.absence.to_list(),
            "stats": self.stats,
            "absence_start": self.absence_start.isoformat() if self.absence_start else None,
            "skip_auto_date": self.skip_auto_date.isoformat() if self.skip_auto_date else None,
        }

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    # ------------------------------------------------------------------ listeners --
    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        def remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return remove

    @callback
    def _notify_listeners(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def _schedule(self) -> None:
        self.hass.async_create_task(self._async_evaluate(), eager_start=True)

    @callback
    def changed(self) -> None:
        """Something was changed by the user (entity/service): save and re-plan."""
        self._save()
        self._schedule()

    # ------------------------------------------------------------------- presence --
    def _resolve_presence_entities(self) -> None:
        entry = self.hass.config_entries.async_get_entry(self.opt.get(CONF_PROXIMITY) or "")
        if entry is None:
            _LOGGER.warning("Proximity entry not found – presence is based on persons only")
            return
        ent_reg = er.async_get(self.hass)
        tracked = list(entry.data.get("tracked_entities", []))
        self.persons = tracked
        by_unique = {
            e.unique_id: e.entity_id
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        }
        self._nearest = by_unique.get(f"{entry.entry_id}_dist_to_zone")
        for person in tracked:
            reg = ent_reg.async_get(person)
            ident = reg.id if reg else person
            dist = by_unique.get(f"{entry.entry_id}_{ident}_dist_to_zone")
            direction = by_unique.get(f"{entry.entry_id}_{ident}_dir_of_travel")
            if dist:
                self._distance[person] = dist
            if direction:
                self._direction[person] = direction

    def _meters(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        value = _float(state)
        if value is None:
            return None
        unit = state.attributes.get("unit_of_measurement")
        factor = {
            UnitOfLength.KILOMETERS: 1000,
            UnitOfLength.MILES: 1609.34,
            UnitOfLength.FEET: 0.3048,
            UnitOfLength.YARDS: 0.9144,
        }.get(unit, 1)
        return value * factor

    def _is_home(self, person: str) -> bool:
        state = self.hass.states.get(person)
        return state is not None and state.state == STATE_HOME

    def somebody_home(self) -> bool:
        return any(self._is_home(p) for p in self.persons)

    def _away_now(self) -> bool:
        if not self.persons or self.somebody_home():
            return False
        nearest = self._meters(self._nearest)
        if nearest is not None and nearest <= self.settings[SET_MIN_DISTANCE]:
            return False
        radius = self.settings[SET_HOMEWAY_RADIUS]
        for person in self.persons:
            direction = self.hass.states.get(self._direction.get(person, ""))
            dist = self._meters(self._distance.get(person))
            if direction and direction.state == "towards" and dist is not None and dist < radius:
                return False
        return True

    def away_ok(self, now: datetime) -> bool:
        if self._away_since is None:
            return False
        return now - self._away_since >= timedelta(minutes=self.opt[CONF_AWAY_DELAY])

    def holiday_today(self) -> bool:
        if not self.opt.get(CONF_WORKDAY):
            return False
        state = self.hass.states.get(self.opt[CONF_WORKDAY])
        return state is not None and state.state == "off" and dt_util.now().weekday() < 5

    def _workday(self, when: datetime) -> bool:
        if when.date() == dt_util.now().date():
            return when.weekday() < 5 and not self.holiday_today()
        return when.weekday() < 5

    def window(self) -> Window:
        s = self.settings
        return Window(
            s[SET_DAY_START], s[SET_DAY_END], s[SET_QUIET_MIDDAY],
            s[SET_QUIET_START], s[SET_QUIET_END], s[SET_QUIET_SUNDAYS],
        )  # fmt: skip

    def _guest(self) -> bool:
        if not self.opt.get(CONF_GUEST_MODE):
            return False
        state = self.hass.states.get(self.opt[CONF_GUEST_MODE])
        return state is not None and state.state == "on"

    # ------------------------------------------------------------ expected return --
    async def _async_seed_absences(self) -> None:
        """Learn past absences from the recorder (first start only)."""
        if not self.persons:
            return
        try:
            from homeassistant.components.recorder import get_instance, history

            start = dt_util.utcnow() - timedelta(days=14)
            states = await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(
                    self.hass,
                    start,
                    None,
                    self.persons,
                    significant_changes_only=False,
                    minimal_response=True,
                    no_attributes=True,
                )
            )
        except Exception:  # noqa: BLE001 – recorder optional
            _LOGGER.debug("Could not read person history", exc_info=True)
            return
        events: list[tuple[datetime, str, bool]] = []
        for person, rows in states.items():
            for row in rows:
                when = getattr(row, "last_changed", None) or dt_util.parse_datetime(
                    row["last_changed"]
                )
                st = row.state if hasattr(row, "state") else row["state"]
                if st in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                    continue
                events.append((dt_util.as_local(when), person, st == STATE_HOME))
        events.sort(key=lambda e: e[0])
        home: dict[str, bool] = {}
        changes: list[tuple[datetime, bool]] = []
        last: bool | None = None
        for when, person, is_home in events:
            home[person] = is_home
            any_home = any(home.values())
            if any_home != last:
                changes.append((when, any_home))
                last = any_home
        for start_, end, workday in absences_from_history(changes, lambda d: d.weekday() < 5):
            self.absence.add(start_, end, workday)
        _LOGGER.debug("Seeded %s absences from history", len(self.absence.sessions))
        self._save()

    @callback
    def _track_absence(self, now: datetime) -> None:
        if not self.persons:
            return
        if self.somebody_home():
            if self.absence_start is not None:
                self.absence.add(self.absence_start, now, self._workday(self.absence_start))
                self.absence_start = None
                self._save()
        elif self.absence_start is None:
            self.absence_start = now
            self._calendar_fetched = None
            self._save()
        if self._away_now():
            if self._away_since is None:
                self._away_since = now
        else:
            self._away_since = None

    async def _async_update_return(self, now: datetime) -> None:
        if self.absence_start is None:
            self.expected_return = None
            self.return_source = None
            return
        expected, source, samples = self.absence.predict(
            self.absence_start, now, self._workday(self.absence_start),
            self.opt[CONF_DEFAULT_ABSENCE],
        )  # fmt: skip
        cal = await self._async_calendar_return(now)
        if cal is not None and cal > now:
            expected, source = cal, "calendar"
        self.expected_return, self.return_source, self.return_samples = expected, source, samples

    async def _async_calendar_return(self, now: datetime) -> datetime | None:
        calendars = self.opt.get(CONF_CALENDARS) or []
        if not calendars:
            return None
        if self._calendar_fetched is None or now - self._calendar_fetched > CALENDAR_REFRESH:
            self._calendar_fetched = now
            events: list[tuple[datetime, datetime]] = []
            try:
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        "entity_id": calendars,
                        "start_date_time": (now - timedelta(hours=12)).isoformat(),
                        "end_date_time": (now + timedelta(hours=16)).isoformat(),
                    },
                    blocking=True,
                    return_response=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Calendar lookup failed", exc_info=True)
                response = {}
            for result in (response or {}).values():
                for event in result.get("events", []):
                    start, end = str(event.get("start", "")), str(event.get("end", ""))
                    if len(start) <= 10 or len(end) <= 10:  # all-day
                        continue
                    s, e = dt_util.parse_datetime(start), dt_util.parse_datetime(end)
                    if s and e:
                        events.append((dt_util.as_local(s), dt_util.as_local(e)))
            self._calendar_events = events
        return calendar_return(now, self._calendar_events, self.opt[CONF_TRAVEL_TIME])

    # -------------------------------------------------------------------- robot --
    def vacuum_state(self) -> str | None:
        state = self.hass.states.get(self.opt[CONF_VACUUM])
        return state.state if state else None

    def battery(self) -> float | None:
        if self.opt.get(CONF_BATTERY):
            return _float(self.hass.states.get(self.opt[CONF_BATTERY]))
        state = self.hass.states.get(self.opt[CONF_VACUUM])
        level = state.attributes.get("battery_level") if state else None
        return float(level) if isinstance(level, int | float) else None

    def _water(self) -> float | None:
        if not self.opt.get(CONF_WATER):
            return None
        return _float(self.hass.states.get(self.opt[CONF_WATER]))

    def _dock_busy(self) -> bool:
        if not self.opt.get(CONF_DOCK_STATUS):
            return False
        state = self.hass.states.get(self.opt[CONF_DOCK_STATUS])
        return state is not None and state.state in DOCK_BUSY

    def error_message(self) -> str | None:
        vac = self.hass.states.get(self.opt[CONF_VACUUM])
        msg = None
        if self.opt.get(CONF_ERROR_SENSOR):
            err = self.hass.states.get(self.opt[CONF_ERROR_SENSOR])
            if err is not None and err.state not in ERROR_EMPTY:
                msg = err.state
        if msg is None and vac is not None and vac.state == "error":
            msg = vac.attributes.get("error_message") or text(self.language, "unknown_error")
        return msg

    async def _async_send_clean(self, stage_rooms: list[int], mode: str) -> None:
        await self.hass.services.async_call(
            "vacuum",
            "send_command",
            {
                "entity_id": self.opt[CONF_VACUUM],
                "command": "room_clean",
                "params": {"rooms": [{"id": rid, "clean_mode": mode} for rid in stage_rooms]},
            },
            blocking=True,
        )

    async def _async_return_to_base(self) -> None:
        await self.hass.services.async_call(
            "vacuum", "return_to_base", {"entity_id": self.opt[CONF_VACUUM]}, blocking=True
        )

    # -------------------------------------------------------------------- rooms --
    def room_name(self, room_id: int) -> str:
        cfg = self.room_cfg.get(room_id)
        return cfg[ROOM_NAME] if cfg else f"Room {room_id}"

    def names(self, room_ids: list[int]) -> str:
        return join_names([self.room_name(r) for r in room_ids], self.language)

    def resolve_rooms(self, values: list[Any]) -> list[int]:
        """Room ids from ids or names (case-insensitive, aliases by prefix)."""
        result = []
        for value in values:
            if isinstance(value, int) or str(value).strip().isdigit():
                rid = int(value)
                if rid in self.room_cfg:
                    result.append(rid)
                    continue
                raise ValueError(f"unknown room {value}")
            name = str(value).strip().lower()
            if name in ("all", "alle"):
                result.extend(self.order)
                continue
            match = [
                rid
                for rid, cfg in self.room_cfg.items()
                if cfg[ROOM_NAME].lower() == name or slugify(cfg[ROOM_NAME]) == slugify(name)
            ]
            if not match:
                raise ValueError(f"unknown room {value}")
            result.extend(match)
        return list(dict.fromkeys(result))

    def _blocked_rooms(self, now: datetime) -> dict[int, str]:
        blocked: dict[int, str] = {}
        for rid, cfg in self.room_cfg.items():
            busy = [
                b
                for b in cfg.get(ROOM_BLOCKERS, [])
                if (st := self.hass.states.get(b)) is not None and st.state in BUSY_STATES
            ]
            if busy:
                self._busy_last[rid] = now
                blocked[rid] = "busy"
            elif rid in self._busy_last and now - self._busy_last[rid] < BUSY_GRACE:
                blocked[rid] = "busy"
            if self.rooms[rid].is_skipped(now):
                blocked[rid] = self.rooms[rid].skip_reason or SKIP_PAUSED
        return blocked

    def _overhead(self) -> float:
        return float(self.opt[CONF_STAGE_OVERHEAD])

    @callback
    def _sync_plan(self, now: datetime) -> bool:
        """Keep the plan entries of the queue in line with the plan."""
        changed = False
        for room in self.rooms.values():
            if room.expire_skip(now):
                changed = True
        running = set(self.job.room_ids) if self.job else set()
        for item in list(self.queue.items):
            room = self.rooms.get(item.room_id)
            if (
                item.source == SOURCE_PLAN
                and item.room_id not in running
                and room is not None
                and (room.mode == MODE_OFF or (not room.is_due(now) and not room.is_skipped(now)))
            ):
                self.queue.remove(item.room_id)
                changed = True
            elif (
                item.source == SOURCE_PLAN
                and item.room_id not in running
                and room is not None
                and room.mode in CLEAN_MODES
                and item.mode != room.mode
            ):
                item.mode = room.mode  # plan entries follow the plan
                changed = True
        if sync_due(self.queue, self.rooms, now):
            changed = True
        if changed:
            self._save()
        return changed

    # ---------------------------------------------------------------- evaluation --
    async def _async_evaluate(self) -> None:
        async with self._lock:
            try:
                await self._async_evaluate_locked()
            finally:
                self._notify_listeners()

    async def _async_evaluate_locked(self) -> None:
        now = dt_util.now()
        if self._day != now.date():
            self._day = now.date()
        self._sync_plan(now)
        self._track_absence(now)
        await self._async_update_return(now)
        vac = self.vacuum_state()

        await self._async_check_error(now)
        if self.state == STATE_ERROR:
            if vac == "docked" and self.error_message() is None:
                self._set(STATE_IDLE, None)
            else:
                return

        if self.job is not None:
            await self._async_supervise(now, vac)
            return

        await self._async_plan_b(now)
        await self._async_decide(now, vac)

    @callback
    def _set(self, state: str, reason: str | None, **data: Any) -> None:
        self.state = state
        self.reason = reason
        self.reason_data = data

    def _budget(self, now: datetime) -> float | None:
        if self.session_forced:
            return None
        budget = self.window().minutes_left(now, self.holiday_today())
        if self.expected_return is not None:
            left = (self.expected_return - now).total_seconds() / 60
            budget = min(budget, left - self.opt[CONF_RETURN_BUFFER])
        return max(budget, 0.0)

    async def _async_decide(self, now: datetime, vac: str | None) -> None:
        self.budget = self._budget(now)
        blocked = self._blocked_rooms(now)
        self.next_stage = plan_stage(
            self.queue, self.rooms, self.order, now, self.budget, self._overhead(), set(blocked)
        )
        if not self.queue:
            await self._async_end_session()
            self._set(STATE_IDLE, "queue_empty")
            return

        if not self.session_forced:
            reason = self._auto_blocker(now)
            if reason is not None:
                if reason[0] == "someone_home":
                    await self._async_end_session()
                await self._async_cancel_prewarn()
                self._set(STATE_WAITING, reason[0], **reason[1])
                return

        if vac != "docked":
            self._set(STATE_WAITING, "robot_busy", vacuum=vac)
            return
        battery = self.battery()
        if battery is not None and battery < self.settings[SET_MIN_BATTERY]:
            self._set(STATE_WAITING, "charging", battery=battery)
            return
        if self.between_since is not None:
            pause = timedelta(minutes=self.opt[CONF_STAGE_PAUSE])
            if now - self.between_since < pause or self._dock_busy():
                self._set(STATE_BETWEEN, "dock", until=(self.between_since + pause).isoformat())
                return
            self.between_since = None

        stage = self.next_stage
        if stage is None:
            if all(item.room_id in blocked for item in self.queue.items):
                self._set(STATE_WAITING, "rooms_blocked", rooms=sorted(blocked))
            else:
                self._set(STATE_WAITING, "no_time", budget=round(self.budget or 0))
            return
        if stage.mode != MODE_VACUUM:
            water = self._water()
            if water is not None and water < self.opt[CONF_MIN_WATER]:
                self._set(STATE_WAITING, "water_low", water=water)
                return

        if not self.session_active and not self.session_forced:
            prewarn = int(self.settings[SET_PREWARN])
            if prewarn > 0:
                if self.prewarn_until is None:
                    self.prewarn_until = now + timedelta(minutes=prewarn)
                    await self._async_send_prewarn(stage, prewarn, now)
                if now < self.prewarn_until:
                    self._set(STATE_PREWARN, "prewarn", until=self.prewarn_until.isoformat())
                    return
        # The prewarn is over: remove it so a late delivery can't show stale times and buttons.
        await self._async_cancel_prewarn()
        self.session_active = True
        await self._async_start_job(stage, now, forced=self.session_forced)

    def _auto_blocker(self, now: datetime) -> tuple[str, dict[str, Any]] | None:
        if not self.settings[SET_AUTO]:
            return "auto_off", {}
        if self._guest():
            return "guest_mode", {}
        if self.skip_auto_date == now.date():
            return "skipped_today", {}
        if not self.away_ok(now):
            return "someone_home", {}
        window = self.window()
        block = window.blocked_reason(now, self.holiday_today())
        if block is not None:
            nxt = window.next_allowed(now, self.holiday_today())
            return f"quiet_{block}", {"until": nxt.isoformat() if nxt else None}
        return None

    async def _async_end_session(self) -> None:
        self.session_active = False
        self.session_forced = False
        self.between_since = None

    async def _async_start_job(
        self, stage: Stage, now: datetime, forced: bool = False, single: bool = False
    ) -> None:
        self.job = Job(stage.room_ids, stage.mode, now, stage.minutes, forced=forced, single=single)
        self._set(STATE_STARTING, None)
        _LOGGER.info("Starting %s for rooms %s", stage.mode, stage.room_ids)
        try:
            await self._async_send_clean(stage.room_ids, stage.mode)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not send room_clean: %s", err)

    # -------------------------------------------------------------- job handling --
    async def _async_supervise(self, now: datetime, vac: str | None) -> None:
        job = self.job
        assert job is not None
        if job.cleaning_since is None:
            if vac == "cleaning":
                job.cleaning_since = now
            elif now - job.sent >= START_TIMEOUT:
                if job.attempt < 2:
                    job.attempt += 1
                    job.sent = now
                    _LOGGER.info("Robot did not start – sending again")
                    try:
                        await self._async_send_clean(job.room_ids, job.mode)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Could not send room_clean: %s", err)
                else:
                    await self._async_job_failed(now, "no_start")
                    return
            self._set(STATE_STARTING, None)
            return
        if job.paused_since is not None and now - job.paused_since > PAUSE_LIMIT:
            job.interrupted = True
        if job.return_asked is not None and not job.keep_going:
            if now - job.return_asked >= RETURN_TIMEOUT:
                await self._async_abort_job(returning=True)
                return
        self._set(STATE_CLEANING, "returning_soon" if job.return_asked else None)

    @callback
    def _on_vacuum_change(self, new: str | None, now: datetime) -> None:
        job = self.job
        if job is None:
            return
        if new == "cleaning":
            if job.cleaning_since is None:
                job.cleaning_since = now
            job.paused_since = None
        elif new == "paused":
            job.paused_since = job.paused_since or now
        elif new == "idle" and job.cleaning_since is not None:
            job.interrupted = True

    async def _async_task_completed(self, now: datetime) -> None:
        job = self.job
        if job is None or job.cleaning_since is None:
            return
        self.job = None
        await self._async_clear(TAG_RETURN, self.persons)
        minutes = (now - job.cleaning_since).total_seconds() / 60
        if job.interrupted or job.aborted:
            self.stats["jobs_interrupted"] += 1
            self._set(STATE_IDLE, None)
            self._save()
            return
        credited = []
        for rid in job.room_ids:
            room = self.rooms.get(rid)
            if room is None:
                continue
            room.consecutive_failures = 0
            if covers(job.mode, room.mode):
                room.mark_cleaned(now)
                credited.append(rid)
        self.queue.complete(job.room_ids, job.mode)
        ratio = learn_durations(self.rooms, job.room_ids, job.mode, minutes, self._overhead())
        self.stats["jobs"] += 1
        self.stats["minutes"] = round(self.stats["minutes"] + minutes, 1)
        self.stats["last_job"] = {
            "rooms": job.room_ids,
            "mode": job.mode,
            "end": now.isoformat(),
            "minutes": round(minutes, 1),
            "estimate": round(job.estimate, 1),
            "ratio": ratio,
        }
        self.between_since = now
        self._set(STATE_BETWEEN, "dock")
        self.hass.bus.async_fire(
            EVENT_JOB_FINISHED,
            {
                "rooms": job.room_ids,
                "names": [self.room_name(r) for r in job.room_ids],
                "mode": job.mode,
                "minutes": round(minutes, 1),
                "credited": credited,
                "queue_left": len(self.queue),
            },
        )
        self._save()

    async def _async_abort_job(self, returning: bool) -> None:
        job = self.job
        if job is None:
            return
        job.aborted = True
        self.stats["returns_home"] += 1 if returning else 0
        await self._async_end_session()
        await self._async_clear(TAG_RETURN, self.persons)
        try:
            await self._async_return_to_base()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("return_to_base failed: %s", err)
        self.job = None
        self._set(STATE_IDLE, "aborted")
        self._save()

    async def _async_check_error(self, now: datetime) -> None:
        msg = self.error_message()
        if msg is None:
            self._error_since = None
            self._error_reported = False
            return
        if self._error_since is None:
            self._error_since = now
        if self._error_reported or now - self._error_since < ERROR_DELAY:
            return
        self._error_reported = True
        await self._async_job_failed(now, msg)

    async def _async_job_failed(self, now: datetime, reason: str) -> None:
        job = self.job
        skipped: list[int] = []
        if job is not None:
            for rid in job.room_ids:
                room = self.rooms.get(rid)
                if room is None:
                    continue
                room.mark_failed(now, reason)
                if room.consecutive_failures > int(self.settings[SET_RETRIES]):
                    room.skip_until = dt_util.start_of_local_day(now) + timedelta(days=1)
                    room.skip_reason = SKIP_FAILED
                    skipped.append(rid)
        self.job = None
        self.stats["errors"] += 1
        await self._async_end_session()
        message_error = (
            text(self.language, "no_start_message", rooms=self.names(job.room_ids))
            if reason == "no_start" and job
            else reason
        )
        extra = text(self.language, "error_skipped", rooms=self.names(skipped)) if skipped else ""
        battery = self.battery()
        await self._async_push(
            self.persons,
            text(self.language, "error_title"),
            text(
                self.language,
                "error_message",
                error=message_error,
                battery=int(battery) if battery is not None else "?",
                skipped=extra,
            ),
            TAG_ERROR,
        )
        if reason == "no_start":
            self._set(STATE_IDLE, "no_start")
        else:
            self._set(STATE_ERROR, "error", message=reason)
        self._save()

    # ------------------------------------------------------------ return home --
    async def _async_person_arriving(self, person: str, now: datetime) -> None:
        job = self.job
        if any(self._is_home(p) for p in self.persons if p != person):
            return  # somebody else let it run on purpose
        if job is None:
            if self.session_active and not self.session_forced:
                await self._async_end_session()
            await self._async_cancel_prewarn()
            return
        if job.return_asked is not None or job.keep_going:
            return
        job.return_asked = now
        job.return_person = person
        await self._async_push(
            [person],
            text(self.language, "return_title"),
            text(self.language, "return_message", rooms=self.names(job.room_ids)),
            TAG_RETURN,
            [
                (ACTION_RETURN, text(self.language, "return")),
                (ACTION_CONTINUE, text(self.language, "continue")),
            ],
        )

    # ------------------------------------------------------------ prewarn, plan B --
    async def _async_send_prewarn(self, stage: Stage, minutes: int, now: datetime) -> None:
        rest_ids = [i.room_id for i in self.queue.items if i.room_id not in stage.room_ids]
        rest = text(self.language, "prewarn_rest", rooms=self.names(rest_ids)) if rest_ids else ""
        finish = now + timedelta(minutes=minutes + stage.minutes)
        await self._async_push(
            [p for p in self.persons if not self._is_home(p)],
            text(self.language, "prewarn_title"),
            text(
                self.language,
                "prewarn_message",
                minutes=minutes,
                rooms=self.names(stage.room_ids),
                duration=round(stage.minutes),
                finish=dt_util.as_local(finish).strftime("%H:%M"),
                rest=rest,
            ),
            TAG_PREWARN,
            [
                (ACTION_SKIP_TODAY, text(self.language, "skip_today")),
                (ACTION_START_NOW, text(self.language, "start_now")),
            ],
        )

    async def _async_cancel_prewarn(self) -> None:
        if self.prewarn_until is not None:
            self.prewarn_until = None
            await self._async_clear(TAG_PREWARN, self.persons)

    async def _async_plan_b(self, now: datetime) -> None:
        if self._plan_b_until is not None and now > self._plan_b_until:
            self._plan_b_until = None
            await self._async_clear(TAG_PLAN_B, self.persons)
        days = int(self.settings[SET_PLAN_B_DAYS])
        at: time = self.settings[SET_PLAN_B_TIME]
        ask_at = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if days <= 0 or self._plan_b_day == now.date() or not ask_at <= now < ask_at + timedelta(minutes=5):
            return
        self._plan_b_day = now.date()
        if self.session_active or self._guest() or not self.queue or not self.settings[SET_AUTO]:
            return
        overdue = [
            r.room_id
            for r in self.rooms.values()
            if r.room_id in self.queue and (r.overdue_days(now) or 0) >= days
        ]
        if not overdue:
            return
        self._plan_b_until = now + PLAN_B_VALID
        await self._async_push(
            self.persons,
            text(self.language, "plan_b_title"),
            text(
                self.language,
                "plan_b_message",
                rooms=self.names([i.room_id for i in self.queue.items]),
            ),
            TAG_PLAN_B,
            [
                (ACTION_PLAN_B_NOW, text(self.language, "plan_b_now")),
                (ACTION_PLAN_B_LATER, text(self.language, "plan_b_later")),
            ],
        )

    # ------------------------------------------------------------- notifications --
    def notify_service(self, person: str) -> str | None:
        device_id = (self.opt.get(CONF_NOTIFY) or {}).get(person)
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return None
        entry_ids = [getattr(device, "config_entry_id", None)] or []
        if not entry_ids[0]:
            entry_ids = list(device.config_entries)
        for entry_id in entry_ids:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is not None and entry.domain == "mobile_app":
                name = entry.data.get("device_name") or device.name or ""
                service = f"mobile_app_{slugify(name)}"
                if self.hass.services.has_service("notify", service):
                    return service
        return None

    async def _async_push(
        self,
        persons: list[str],
        title: str,
        message: str,
        tag: str,
        actions: list[tuple[str, str]] | None = None,
    ) -> None:
        data: dict[str, Any] = {"tag": tag}
        if actions:
            data["actions"] = [{"action": a, "title": t} for a, t in actions]
            # Pushes that ask for a decision are time-critical: deliver them right away
            # instead of letting Android Doze (FCM normal priority) or iOS batch them.
            data.update(PUSH_URGENT)
        for person in persons:
            service = self.notify_service(person)
            if service is None:
                continue
            try:
                await self.hass.services.async_call(
                    "notify", service, {"title": title, "message": message, "data": data}
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Notification to %s failed: %s", person, err)

    async def _async_clear(self, tag: str, persons: list[str]) -> None:
        for person in persons:
            service = self.notify_service(person)
            if service is None:
                continue
            try:
                await self.hass.services.async_call(
                    "notify", service, {"message": "clear_notification", "data": {"tag": tag}}
                )
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------------- events --
    @callback
    def _on_state(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        now = dt_util.now()
        if entity_id == self.opt[CONF_VACUUM]:
            self._on_vacuum_change(new.state if new else None, now)
        elif entity_id == self.opt.get(CONF_TASK_STATUS):
            if new is not None and new.state == "Completed":
                self.hass.async_create_task(self._async_locked(self._async_task_completed(now)))
                return
        else:
            person = self._arriving(entity_id, old, new)
            if person is not None:
                self.hass.async_create_task(
                    self._async_locked(self._async_person_arriving(person, now))
                )
        self._schedule()

    def _arriving(self, entity_id: str, old: State | None, new: State | None) -> str | None:
        if new is None:
            return None
        if entity_id in self.persons:
            if new.state == STATE_HOME and (old is None or old.state != STATE_HOME):
                return entity_id
            return None
        for person, dist_entity in self._distance.items():
            if dist_entity != entity_id:
                continue
            limit = self.settings[SET_MIN_DISTANCE]
            before, after = _float(old), self._meters(entity_id)
            if before is not None and old is not None:
                unit = old.attributes.get("unit_of_measurement")
                before = before * (1000 if unit == UnitOfLength.KILOMETERS else 1)
            if after is not None and after < limit and (before is None or before >= limit):
                return person
        return None

    async def _async_locked(self, coro) -> None:
        async with self._lock:
            try:
                await coro
            finally:
                self._notify_listeners()
        self._schedule()

    @callback
    def _on_tick(self, now: datetime) -> None:
        self._schedule()

    @callback
    def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        if not action.startswith(ACTION_PREFIX):
            return
        self.hass.async_create_task(self._async_locked(self._async_handle_action(action)))

    async def _async_handle_action(self, action: str) -> None:
        now = dt_util.now()
        if action == ACTION_SKIP_TODAY:
            self.skip_auto_date = now.date()
            await self._async_cancel_prewarn()
            self._save()
        elif action == ACTION_START_NOW:
            if self.prewarn_until is not None:
                self.prewarn_until = now
        elif action == ACTION_RETURN:
            await self._async_abort_job(returning=True)
        elif action == ACTION_CONTINUE:
            if self.job is not None:
                self.job.keep_going = True
            await self._async_clear(TAG_RETURN, self.persons)
        elif action in (ACTION_PLAN_B_NOW, ACTION_PLAN_B_LATER):
            self._plan_b_until = None
            await self._async_clear(TAG_PLAN_B, self.persons)
            if action == ACTION_PLAN_B_NOW:
                self.session_forced = True
                self.session_active = True

    # ------------------------------------------------------------- user actions --
    def _check_mode(self, mode: str | None) -> None:
        if mode is not None and mode not in CLEAN_MODES:
            raise ValueError(f"invalid mode {mode}")

    @callback
    def enqueue(self, room_ids: list[int], mode: str | None, front: bool = False) -> None:
        self._check_mode(mode)
        now = dt_util.now()
        for rid in reversed(room_ids) if front else room_ids:
            room = self.rooms[rid]
            use = mode or (room.mode if room.mode != MODE_OFF else MODE_VACUUM_MOP)
            self.queue.add(rid, use, SOURCE_MANUAL, now, front=front)
            if mode is not None:
                self.queue.get(rid).mode = mode  # an explicit mode replaces the entry's mode
            if room.skip_reason == SKIP_TODAY:
                room.skip_until = None
                room.skip_reason = None
        self.changed()

    @callback
    def dequeue(self, room_ids: list[int], skip_today: bool = True) -> None:
        now = dt_util.now()
        for rid in room_ids:
            item = self.queue.remove(rid)
            room = self.rooms[rid]
            if item is not None and skip_today and room.is_due(now):
                room.skip_until = dt_util.start_of_local_day(now) + timedelta(days=1)
                room.skip_reason = SKIP_TODAY
        self.changed()

    @callback
    def move(self, room_id: int, position: int) -> None:
        self.queue.move(room_id, position)
        self.changed()

    @callback
    def clear_queue(self) -> None:
        now = dt_util.now()
        for item in list(self.queue.items):
            if self.job is not None and item.room_id in self.job.room_ids:
                continue
            self.queue.remove(item.room_id)
            room = self.rooms[item.room_id]
            if room.is_due(now):
                room.skip_until = dt_util.start_of_local_day(now) + timedelta(days=1)
                room.skip_reason = SKIP_TODAY
        self.changed()

    @callback
    def enqueue_due(self) -> None:
        now = dt_util.now()
        for room in self.rooms.values():
            if room.skip_reason == SKIP_TODAY:
                room.skip_until = None
                room.skip_reason = None
        sync_due(self.queue, self.rooms, now)
        self.changed()

    async def async_skip_today(self, skip: bool) -> None:
        async with self._lock:
            self.skip_auto_date = dt_util.now().date() if skip else None
            await self._async_cancel_prewarn()
            self._save()
            self._notify_listeners()
        self._schedule()

    @callback
    def start(self, force: bool = True) -> None:
        self.session_active = True
        self.session_forced = force
        self.prewarn_until = None
        self.skip_auto_date = None
        self.between_since = None
        self.changed()

    async def async_stop(self, return_home: bool = True) -> None:
        async with self._lock:
            await self._async_end_session()
            await self._async_cancel_prewarn()
            if self.job is not None and return_home:
                await self._async_abort_job(returning=False)
            self._set(STATE_IDLE, "stopped")
            self._notify_listeners()
        self._schedule()

    async def async_clean_now(self, room_ids: list[int], mode: str) -> None:
        self._check_mode(mode)
        async with self._lock:
            if self.job is not None or self.vacuum_state() != "docked":
                raise RuntimeError("busy")
            minutes = self._overhead() + sum(self.rooms[r].estimate(mode) for r in room_ids)
            ordered = sorted(room_ids, key=lambda r: self.order.index(r))
            await self._async_start_job(
                Stage(mode, ordered, minutes), dt_util.now(), forced=True, single=True
            )
            self._notify_listeners()

    @callback
    def pause_room(self, room_ids: list[int], until: datetime) -> None:
        for rid in room_ids:
            self.rooms[rid].skip_until = until
            self.rooms[rid].skip_reason = SKIP_PAUSED
        self.changed()

    @callback
    def resume_room(self, room_ids: list[int]) -> None:
        for rid in room_ids:
            self.rooms[rid].skip_until = None
            self.rooms[rid].skip_reason = None
        self.changed()

    @callback
    def set_plan(self, room_id: int, mode: str | None, interval: int | None) -> None:
        room = self.rooms[room_id]
        if mode is not None:
            room.mode = mode
        if interval is not None:
            room.interval = max(1, int(interval))
        self.changed()

    @callback
    def set_last_cleaned(self, room_ids: list[int], when: datetime) -> None:
        for rid in room_ids:
            self.rooms[rid].last_cleaned = when
        self.changed()

    @callback
    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        self.changed()

    # -------------------------------------------------------------------- views --
    def room_view(self, rid: int, now: datetime) -> dict[str, Any]:
        room = self.rooms[rid]
        cfg = self.room_cfg[rid]
        due = room.next_due()
        return {
            "id": rid,
            "name": cfg[ROOM_NAME],
            "icon": cfg.get(ROOM_ICON) or "mdi:floor-plan",
            "mode": room.mode,
            "interval": room.interval,
            "last_cleaned": room.last_cleaned.isoformat() if room.last_cleaned else None,
            "next_due": None if due is None else (None if due.year == 1 else due.isoformat()),
            "due": room.is_due(now),
            "overdue_days": room.overdue_days(now),
            "skip_until": room.skip_until.isoformat() if room.skip_until else None,
            "skip_reason": room.skip_reason if room.is_skipped(now) else None,
            "minutes": {k: round(v, 1) for k, v in room.minutes.items()},
            "cleaned": room.cleaned,
            "failed": room.failed,
            "consecutive_failures": room.consecutive_failures,
            "last_failure": room.last_failure_reason,
        }

    def queue_view(self, now: datetime) -> dict[str, Any]:
        blocked = self._blocked_rooms(now)
        overhead = self._overhead()
        pause = float(self.opt[CONF_STAGE_PAUSE])
        stages = build_stages(self.queue, self.rooms, self.order, overhead)
        total = queue_minutes(self.queue, self.rooms, self.order, overhead, pause)
        running = set(self.job.room_ids) if self.job else set()
        items = []
        for item in self.queue.items:
            room = self.rooms[item.room_id]
            items.append(
                {
                    "id": item.room_id,
                    "name": self.room_name(item.room_id),
                    "mode": item.mode,
                    "source": item.source,
                    "minutes": round(room.estimate(item.mode), 1),
                    "blocked": blocked.get(item.room_id),
                    "running": item.room_id in running,
                    "next": bool(self.next_stage and item.room_id in self.next_stage.room_ids),
                }
            )
        start = now
        if self.job is not None:
            start = (self.job.cleaning_since or self.job.sent) + timedelta(
                minutes=self.job.estimate
            )
            start = max(start, now)
            total -= sum(s.minutes for s in stages if set(s.room_ids) <= running)
        return {
            "items": items,
            "stages": [
                {"mode": s.mode, "rooms": s.room_ids, "minutes": round(s.minutes), "alone": s.isolated}
                for s in stages
            ],
            "total_minutes": round(total),
            "finish": (start + timedelta(minutes=max(total, 0))).isoformat() if items else None,
            "next_stage": (
                {"mode": self.next_stage.mode, "rooms": self.next_stage.room_ids,
                 "minutes": round(self.next_stage.minutes)}
                if self.next_stage else None
            ),  # fmt: skip
        }
