"""Constants for Vacuum Planner."""

from __future__ import annotations

from datetime import time
from typing import Final

DOMAIN: Final = "vacuum_planner"
STORAGE_VERSION: Final = 1

# Cleaning modes as understood by the robot (eufy room_clean clean_mode).
MODE_OFF: Final = "off"
MODE_VACUUM: Final = "vacuum"
MODE_VACUUM_MOP: Final = "vacuum_mop"
MODE_MOP: Final = "mop"
MODE_SWEEP_THEN_MOP: Final = "mopping_after_sweeping"
CLEAN_MODES: Final = (MODE_VACUUM, MODE_VACUUM_MOP, MODE_SWEEP_THEN_MOP, MODE_MOP)
PLAN_MODES: Final = (MODE_OFF, *CLEAN_MODES)

# --- config entry data / options -------------------------------------------------
CONF_VACUUM: Final = "vacuum"
CONF_TASK_STATUS: Final = "task_status_sensor"
CONF_ERROR_SENSOR: Final = "error_sensor"
CONF_BATTERY: Final = "battery_sensor"
CONF_WATER: Final = "water_sensor"
CONF_DOCK_STATUS: Final = "dock_status_sensor"
CONF_PROXIMITY: Final = "proximity_entry"
CONF_PERSONS: Final = "persons"
CONF_GUEST_MODE: Final = "guest_mode"
CONF_WORKDAY: Final = "workday_sensor"
CONF_CALENDARS: Final = "calendars"
CONF_NOTIFY: Final = "notify_devices"
CONF_ROOMS: Final = "rooms"
CONF_STAGE_PAUSE: Final = "stage_pause"
CONF_AWAY_DELAY: Final = "away_delay"
CONF_RETURN_BUFFER: Final = "return_buffer"
CONF_TRAVEL_TIME: Final = "travel_time"
CONF_DEFAULT_ABSENCE: Final = "default_absence"
CONF_STAGE_OVERHEAD: Final = "stage_overhead"
CONF_MIN_WATER: Final = "min_water"

# room config keys
ROOM_ID: Final = "id"
ROOM_NAME: Final = "name"
ROOM_ICON: Final = "icon"
ROOM_BLOCKERS: Final = "blockers"

DEFAULT_OPTIONS: Final = {
    CONF_STAGE_PAUSE: 2,
    CONF_AWAY_DELAY: 3,
    CONF_RETURN_BUFFER: 10,
    CONF_TRAVEL_TIME: 15,
    CONF_DEFAULT_ABSENCE: 90,
    CONF_STAGE_OVERHEAD: 4,
    CONF_MIN_WATER: 15,
}

# --- runtime settings (entities, persisted in the store) ----------------------------
SET_AUTO: Final = "auto_clean"
SET_MIN_DISTANCE: Final = "min_distance"
SET_HOMEWAY_RADIUS: Final = "homeway_radius"
SET_DAY_START: Final = "day_start"
SET_DAY_END: Final = "day_end"
SET_QUIET_MIDDAY: Final = "quiet_midday"
SET_QUIET_START: Final = "quiet_start"
SET_QUIET_END: Final = "quiet_end"
SET_QUIET_SUNDAYS: Final = "quiet_sundays"
SET_MIN_BATTERY: Final = "min_battery"
SET_RETRIES: Final = "retries"
SET_PREWARN: Final = "prewarn"
SET_PLAN_B_TIME: Final = "plan_b_time"
SET_PLAN_B_DAYS: Final = "plan_b_days"

# German defaults: Mittagsruhe 13–15 Uhr, Sonn- und Feiertagsruhe; no cleaning at night.
DEFAULT_SETTINGS: Final = {
    SET_AUTO: True,
    SET_MIN_DISTANCE: 1500,
    SET_HOMEWAY_RADIUS: 30000,
    SET_DAY_START: time(8, 0),
    SET_DAY_END: time(20, 0),
    SET_QUIET_MIDDAY: True,
    SET_QUIET_START: time(13, 0),
    SET_QUIET_END: time(15, 0),
    SET_QUIET_SUNDAYS: True,
    SET_MIN_BATTERY: 40,
    SET_RETRIES: 1,
    SET_PREWARN: 5,
    SET_PLAN_B_TIME: time(16, 0),
    SET_PLAN_B_DAYS: 1,
}

DEFAULT_MINUTES: Final = {"dry": 8.0, "wet": 11.0}

# --- services ----------------------------------------------------------------------
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_ROOMS: Final = "rooms"
ATTR_ROOM: Final = "room"
ATTR_MODE: Final = "mode"
ATTR_FRONT: Final = "front"
ATTR_POSITION: Final = "position"
ATTR_SKIP_TODAY: Final = "skip_today"
ATTR_UNTIL: Final = "until"
ATTR_DAYS: Final = "days"
ATTR_TIMESTAMP: Final = "timestamp"
ATTR_INTERVAL: Final = "interval"
ATTR_FORCE: Final = "force"
ATTR_RETURN_HOME: Final = "return_home"

SERVICE_ENQUEUE: Final = "enqueue"
SERVICE_DEQUEUE: Final = "dequeue"
SERVICE_MOVE: Final = "move"
SERVICE_CLEAR_QUEUE: Final = "clear_queue"
SERVICE_ENQUEUE_DUE: Final = "enqueue_due"
SERVICE_START: Final = "start"
SERVICE_STOP: Final = "stop"
SERVICE_CLEAN_NOW: Final = "clean_now"
SERVICE_PAUSE_ROOM: Final = "pause_room"
SERVICE_RESUME_ROOM: Final = "resume_room"
SERVICE_SET_PLAN: Final = "set_plan"
SERVICE_SET_LAST_CLEANED: Final = "set_last_cleaned"
SERVICE_SKIP_TODAY: Final = "skip_today"
ATTR_SKIP: Final = "skip"

# Notification actions (mobile_app_notification_action).
ACTION_PREFIX: Final = "VACUUM_PLANNER_"
ACTION_SKIP_TODAY: Final = f"{ACTION_PREFIX}SKIP_TODAY"
ACTION_START_NOW: Final = f"{ACTION_PREFIX}START_NOW"
ACTION_RETURN: Final = f"{ACTION_PREFIX}RETURN"
ACTION_CONTINUE: Final = f"{ACTION_PREFIX}CONTINUE"
ACTION_PLAN_B_NOW: Final = f"{ACTION_PREFIX}PLAN_B_NOW"
ACTION_PLAN_B_LATER: Final = f"{ACTION_PREFIX}PLAN_B_LATER"

TAG_PREWARN: Final = "vacuum_planner_prewarn"
TAG_RETURN: Final = "vacuum_planner_return"
TAG_PLAN_B: Final = "vacuum_planner_plan_b"
TAG_ERROR: Final = "vacuum_planner_error"
TAG_SKIPPED: Final = "vacuum_planner_skipped"

EVENT_JOB_FINISHED: Final = f"{DOMAIN}_job_finished"
