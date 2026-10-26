"""Plan, queue and stage planning (no Home Assistant needed)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.vacuum_planner.model import (
    SKIP_TODAY,
    SOURCE_MANUAL,
    SOURCE_PLAN,
    Queue,
    RoomPlan,
    build_stages,
    covers,
    learn_durations,
    merge_modes,
    plan_stage,
    queue_minutes,
    sync_due,
)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=TZ)
ORDER = [1, 4, 5, 7, 2, 6]


def _rooms() -> dict[int, RoomPlan]:
    rooms = {
        1: RoomPlan(1, "vacuum_mop", 2, NOW - timedelta(days=2)),
        4: RoomPlan(4, "vacuum_mop", 2, NOW - timedelta(days=3)),
        5: RoomPlan(5, "vacuum_mop", 3, NOW - timedelta(days=1)),
        7: RoomPlan(7, "vacuum", 3, NOW - timedelta(days=5)),
        2: RoomPlan(2, "off", 4, None),
        6: RoomPlan(6, "vacuum_mop", 4, None),
    }
    for room in rooms.values():
        room.minutes = {"dry": 8.0, "wet": 10.0}
    return rooms


def test_covers_and_merge():
    assert covers("vacuum_mop", "vacuum")
    assert covers("mopping_after_sweeping", "mop")
    assert not covers("vacuum", "vacuum_mop")
    assert not covers("mop", "vacuum")
    assert merge_modes("vacuum", "mop") == "vacuum_mop"
    assert merge_modes("vacuum", "vacuum_mop") == "vacuum_mop"
    assert merge_modes("mopping_after_sweeping", "vacuum") == "mopping_after_sweeping"


def test_due_rooms_land_in_queue_once():
    rooms = _rooms()
    queue = Queue()
    added = sync_due(queue, rooms, NOW)
    assert sorted(added) == [1, 4, 6, 7]  # 5 not yet, 2 has no plan
    assert sync_due(queue, rooms, NOW) == []
    assert all(item.source == SOURCE_PLAN for item in queue.items)


def test_skipped_room_is_not_requeued_until_skip_expires():
    rooms = _rooms()
    queue = Queue()
    rooms[1].skip_until = NOW.replace(hour=0) + timedelta(days=1)
    rooms[1].skip_reason = SKIP_TODAY
    assert 1 not in sync_due(queue, rooms, NOW)
    tomorrow = NOW + timedelta(days=1)
    assert rooms[1].expire_skip(tomorrow)
    assert 1 in sync_due(queue, rooms, tomorrow)


def test_manual_add_merges_mode_and_upgrades_source():
    rooms = _rooms()
    queue = Queue()
    sync_due(queue, rooms, NOW)
    assert queue.add(7, "mop", SOURCE_MANUAL, NOW)
    item = queue.get(7)
    assert item.mode == "vacuum_mop"
    assert item.source == SOURCE_MANUAL
    assert len([i for i in queue.items if i.room_id == 7]) == 1


def test_stages_run_dry_before_wet_in_route_order():
    rooms = _rooms()
    queue = Queue()
    queue.add(6, "vacuum_mop", SOURCE_PLAN, NOW)
    queue.add(7, "vacuum", SOURCE_PLAN, NOW)
    queue.add(1, "vacuum_mop", SOURCE_PLAN, NOW)
    queue.add(5, "mop", SOURCE_MANUAL, NOW)
    stages = build_stages(queue, rooms, ORDER, overhead=4)
    assert [(s.mode, s.room_ids) for s in stages] == [
        ("vacuum", [7]),
        ("vacuum_mop", [1, 6]),
        ("mop", [5]),
    ]
    assert stages[1].minutes == 4 + 10 + 10
    assert queue_minutes(queue, rooms, ORDER, 4, 2) == (4 + 8) + (4 + 20) + (4 + 10) + 2 * 2


def test_budget_picks_most_urgent_rooms_first():
    rooms = _rooms()
    queue = Queue()
    for rid in (1, 4, 6):
        queue.add(rid, "vacuum_mop", SOURCE_PLAN, NOW)
    # 6 never cleaned (most urgent), 4 three days on a 2-day plan, 1 exactly due
    stage = plan_stage(queue, rooms, ORDER, NOW, budget=4 + 20, overhead=4)
    assert stage.room_ids == [4, 6]
    assert plan_stage(queue, rooms, ORDER, NOW, budget=10, overhead=4) is None
    full = plan_stage(queue, rooms, ORDER, NOW, budget=None, overhead=4)
    assert full.room_ids == [1, 4, 6]


def test_manual_rooms_beat_plan_rooms_in_tight_budget():
    rooms = _rooms()
    queue = Queue()
    queue.add(6, "vacuum_mop", SOURCE_PLAN, NOW)
    queue.add(5, "vacuum_mop", SOURCE_MANUAL, NOW)
    stage = plan_stage(queue, rooms, ORDER, NOW, budget=14, overhead=4)
    assert stage.room_ids == [5]


def test_blocked_room_is_skipped_and_next_stage_used():
    rooms = _rooms()
    queue = Queue()
    queue.add(5, "vacuum", SOURCE_MANUAL, NOW)
    queue.add(1, "vacuum_mop", SOURCE_PLAN, NOW)
    stage = plan_stage(queue, rooms, ORDER, NOW, budget=None, overhead=4, blocked={5})
    assert (stage.mode, stage.room_ids) == ("vacuum_mop", [1])


def test_failed_rooms_run_alone_after_the_rest():
    rooms = _rooms()
    rooms[4].consecutive_failures = 1
    queue = Queue()
    for rid in (1, 4, 6):
        queue.add(rid, "vacuum_mop", SOURCE_PLAN, NOW)
    stages = build_stages(queue, rooms, ORDER, overhead=4)
    assert [(s.room_ids, s.isolated) for s in stages] == [([1, 6], False), ([4], True)]


def test_complete_drops_only_covered_entries():
    queue = Queue()
    queue.add(1, "vacuum_mop", SOURCE_PLAN, NOW)
    queue.add(4, "vacuum", SOURCE_PLAN, NOW)
    done = queue.complete([1, 4], "vacuum")
    assert done == [4]
    assert 1 in queue


def test_move_and_roundtrip():
    queue = Queue()
    for rid in (1, 4, 5):
        queue.add(rid, "vacuum", SOURCE_PLAN, NOW)
    assert queue.move(5, 0)
    assert [i.room_id for i in queue.items] == [5, 1, 4]
    copy = Queue.from_list(queue.to_list())
    assert [i.room_id for i in copy.items] == [5, 1, 4]
    room = RoomPlan(1, "mop", 5, NOW, cleaned=3)
    assert RoomPlan.from_dict(room.to_dict()) == room


def test_learning_moves_estimates_towards_reality():
    rooms = _rooms()
    ratio = learn_durations(rooms, [1, 4], "vacuum_mop", actual_minutes=48, overhead=4)
    assert ratio == 2.0  # bounded (48 / 24)
    assert rooms[1].minutes["wet"] == 13.0
    assert rooms[1].minutes["dry"] == 8.0
    assert learn_durations(rooms, [1], "vacuum", actual_minutes=1, overhead=4) is None


def test_mark_failed_and_cleaned():
    room = RoomPlan(1)
    room.mark_failed(NOW, "stuck")
    assert room.consecutive_failures == 1
    room.mark_cleaned(NOW)
    assert room.consecutive_failures == 0
    assert room.cleaned == 1
    assert room.overdue_days(NOW) == -3
