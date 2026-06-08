"""
Allocation engine for the Room Booker.

Implements the optimised round-based allocation algorithm:
1. Requests are collected per week (deadline: Thursday 17:00).
2. After the deadline the engine allocates rooms in rounds:
   - Round 1: every project gets at most 1 day.
   - Round 2: every project gets a 2nd day, etc.
   - Within each round, overbooked days are resolved by removing the project
     with the MOST alternative days first (maximises total fulfilled requests).
   - Room assignment: smallest room whose capacity >= team_size.
3. After allocation, remaining free rooms can be booked directly (first-come).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from collections import defaultdict

import database as db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_available_weeks() -> list[date]:
    """Returns the Monday of each bookable week (current week + 3 = 4 weeks)."""
    today = date.today()
    current_monday = _monday_of_week(today)
    return [current_monday + timedelta(weeks=w) for w in range(4)]


def week_dates(monday: date) -> list[date]:
    """Returns Mon-Fri for a given week's Monday."""
    return [monday + timedelta(days=i) for i in range(5)]


def is_before_deadline(target_monday: date) -> bool:
    """True if we're still before Friday 16:00 CEST of the week BEFORE target_monday."""
    now = datetime.now()
    # Deadline = Friday 16:00 CEST = 14:00 UTC (Streamlit Cloud runs UTC).
    deadline_friday = target_monday - timedelta(days=3)  # Monday - 3 = Friday before
    deadline = datetime(deadline_friday.year, deadline_friday.month,
                        deadline_friday.day, 14, 0, 0)
    return now < deadline


def is_after_deadline(target_monday: date) -> bool:
    return not is_before_deadline(target_monday)


# ── Best-fit room selection ──────────────────────────────────────────────────

def find_best_room(date_str: str, team_size: int) -> dict | None:
    """Find the smallest available room that fits team_size on date_str."""
    booked_ids = db.get_booked_room_ids_for_date(date_str)
    rooms = db.get_all_rooms()
    available = [r for r in rooms if r["id"] not in booked_ids and r["capacity"] >= team_size]
    if not available:
        return None
    available.sort(key=lambda r: r["capacity"])
    return available[0]


def get_available_rooms(date_str: str, team_size: int = 1) -> list[dict]:
    """All available rooms on date_str with capacity >= team_size."""
    booked_ids = db.get_booked_room_ids_for_date(date_str)
    rooms = db.get_all_rooms()
    available = [r for r in rooms if r["id"] not in booked_ids and r["capacity"] >= team_size]
    available.sort(key=lambda r: r["capacity"])
    return available


# ── Optimised round-based allocation ─────────────────────────────────────────

def run_allocation(week_start: str, prefetched_requests: list | None = None) -> dict:
    """
    Run the allocation algorithm for a given week.

    Args:
        week_start: ISO date string of the Monday.
        prefetched_requests: Optional pre-loaded requests list (to avoid
            re-reading from the sheet after they've been deleted).

    Returns:
        {
            "allocations": [{"request_id", "room_id", "date", "project_name", "requester", "team_size"}, ...],
            "unmet": [{"request_id", "project_name", "date", "reason"}, ...],
            "summary": {project_name: {"requested": int, "allocated": int}},
        }
    """
    monday = date.fromisoformat(week_start)
    week_end = (monday + timedelta(days=4)).isoformat()
    days = week_dates(monday)
    day_strs = [d.isoformat() for d in days]

    # Clear previous allocations for this week
    db.clear_allocations_for_week(week_start, week_end)

    # Load requests
    requests = prefetched_requests if prefetched_requests is not None else db.get_requests_for_week(week_start)
    rooms = db.get_all_rooms()

    if not requests:
        return {"allocations": [], "unmet": [], "summary": {}}

    # Parse requests into per-project structures
    # project_requests: list of {request_id, project_name, requester, team_size, desired_days: [str]}
    project_requests = []
    for req in requests:
        desired = [d.strip() for d in req["desired_days"].split(",") if d.strip() in day_strs]
        if desired:
            project_requests.append({
                "request_id": req["id"],
                "project_name": req["project_name"],
                "requester": req["requester"],
                "team_size": req["team_size"],
                "desired_days": desired,
                "remaining_days": list(desired),
                "allocated_days": [],
            })

    # Track room availability per day: {date_str: set of available room_ids}
    room_avail = {}
    for ds in day_strs:
        booked = db.get_booked_room_ids_for_date(ds)
        room_avail[ds] = {r["id"] for r in rooms} - booked

    rooms_by_id = {r["id"]: r for r in rooms}

    final_allocations = []
    unmet = []

    # Rounds
    max_rounds = max((len(pr["desired_days"]) for pr in project_requests), default=0)

    for round_num in range(max_rounds):
        # Collect which projects still want a day in this round
        candidates = [pr for pr in project_requests if len(pr["remaining_days"]) > 0]
        if not candidates:
            break

        # Build demand per day: {date_str: [project_request_indices]}
        demand = defaultdict(list)
        for pr in candidates:
            for d in pr["remaining_days"]:
                demand[d].append(pr)

        # Process each day
        allocated_this_round = set()  # project_names that got a room this round

        # Sort days by demand (most overbooked first) to resolve conflicts early
        sorted_days = sorted(demand.keys(), key=lambda d: len(demand[d]), reverse=True)

        for day in sorted_days:
            day_candidates = [pr for pr in demand[day]
                              if pr["project_name"] not in allocated_this_round]

            avail_room_ids = room_avail.get(day, set())
            avail_rooms = [rooms_by_id[rid] for rid in avail_room_ids]

            if not day_candidates or not avail_rooms:
                continue

            # If more candidates than rooms: remove those best served so far
            while len(day_candidates) > len(avail_rooms):
                # Fairness-weighted removal: prioritize removing the project that
                # (1) already has the highest allocation ratio (allocated/requested)
                # (2) has the most alternative remaining days (tiebreaker)
                day_candidates.sort(
                    key=lambda pr: (
                        -(len(pr["allocated_days"]) / len(pr["desired_days"])),
                        -len([d for d in pr["remaining_days"] if d != day]),
                    ),
                )
                # Remove the one already best served (highest ratio, most alternatives)
                removed = day_candidates.pop(0)

            # Allocate rooms to remaining candidates
            for pr in day_candidates:
                # Find smallest room that fits
                fitting = sorted(
                    [r for r in avail_rooms if r["capacity"] >= pr["team_size"]],
                    key=lambda r: r["capacity"],
                )
                if fitting:
                    room = fitting[0]
                    alloc = {
                        "request_id": pr["request_id"],
                        "room_id": room["id"],
                        "date": day,
                        "project_name": pr["project_name"],
                        "requester": pr["requester"],
                        "team_size": pr["team_size"],
                    }
                    final_allocations.append(alloc)
                    pr["allocated_days"].append(day)
                    pr["remaining_days"].remove(day)
                    allocated_this_round.add(pr["project_name"])
                    avail_rooms.remove(room)
                    room_avail[day].discard(room["id"])
                else:
                    # No fitting room — team too large for remaining rooms
                    unmet.append({
                        "request_id": pr["request_id"],
                        "project_name": pr["project_name"],
                        "date": day,
                        "reason": f"Geen kamer met voldoende capaciteit ({pr['team_size']}p)",
                    })
                    pr["remaining_days"].remove(day)

        # After processing all days in this round, remove days for projects that
        # already got allocated this round (enforce 1 per round)
        # This is already handled by allocated_this_round check above

    # Remaining unmet
    for pr in project_requests:
        for d in pr["remaining_days"]:
            unmet.append({
                "request_id": pr["request_id"],
                "project_name": pr["project_name"],
                "date": d,
                "reason": "Geen kamer beschikbaar (overboeking)",
            })

    # Build summary
    summary = {}
    for pr in project_requests:
        pn = pr["project_name"]
        if pn not in summary:
            summary[pn] = {"requested": 0, "allocated": 0}
        summary[pn]["requested"] += len(pr["desired_days"])
        summary[pn]["allocated"] += len(pr["allocated_days"])

    # Save to database
    if final_allocations:
        db.save_allocations(final_allocations)

    return {
        "allocations": final_allocations,
        "unmet": unmet,
        "summary": summary,
    }
