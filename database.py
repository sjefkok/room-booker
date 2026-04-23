import gspread
from google.oauth2.service_account import Credentials
import streamlit as st
from datetime import datetime, date
import time

# ── Google Sheets Connection ──────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# In-memory cache to avoid hitting API rate limits
_cache = {}
_cache_ttl = 60  # seconds


def _invalidate_cache(ws_name: str | None = None):
    """Clear cache after a write operation."""
    if ws_name:
        _cache.pop(ws_name, None)
    else:
        _cache.clear()

SHEET_HEADERS = {
    "rooms":           ["id", "name", "capacity", "floor"],
    "requests":        ["id", "project_name", "requester", "team_size", "week_start", "desired_days", "created_at"],
    "allocations":     ["id", "request_id", "room_id", "date", "project_name", "requester", "team_size", "created_at"],
    "direct_bookings": ["id", "room_id", "date", "project_name", "requester", "team_size", "created_at"],
}


@st.cache_resource
def _get_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return gspread.authorize(creds)


def _get_spreadsheet():
    client = _get_client()
    return client.open_by_key(st.secrets["spreadsheet_id"])


def _get_worksheet(name: str):
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(SHEET_HEADERS[name]))
        ws.append_row(SHEET_HEADERS[name])
    return ws


def _all_records(ws_name: str) -> list[dict]:
    """Read all rows from a worksheet as list of dicts. Cached for _cache_ttl seconds."""
    now = time.time()
    if ws_name in _cache and (now - _cache[ws_name]["ts"]) < _cache_ttl:
        return _cache[ws_name]["data"]

    ws = _get_worksheet(ws_name)
    rows = ws.get_all_records()
    # Ensure integer fields are int
    int_fields = {"id", "capacity", "team_size", "request_id", "room_id"}
    for row in rows:
        for k in int_fields:
            if k in row and row[k] != "":
                try:
                    row[k] = int(row[k])
                except (ValueError, TypeError):
                    pass
    _cache[ws_name] = {"ts": now, "data": rows}
    return rows


def _next_id(ws_name: str) -> int:
    records = _all_records(ws_name)
    if not records:
        return 1
    return max(int(r.get("id", 0)) for r in records) + 1


def _find_row_index(ws_name: str, record_id: int) -> int | None:
    """Find the 1-based row index for a record by ID. Row 1 = header."""
    ws = _get_worksheet(ws_name)
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue  # skip header
        if row and str(row[0]) == str(record_id):
            return i + 1  # gspread is 1-indexed
    return None


# ── Init & Seed ───────────────────────────────────────────────────────────────

SEED_ROOMS = [
    ("B.08.19", 5, "B8"),
    ("B.08.21", 6, "B8"),
    ("B.08.30", 4, "B8"),
    ("B.08.34", 4, "B8"),
    ("B.09.21", 6, "B9"),
    ("B.09.28", 4, "B9"),
    ("B.09.30", 6, "B9"),
]


def init_db():
    """Ensure all worksheets exist with headers."""
    for name in SHEET_HEADERS:
        _get_worksheet(name)


def seed_rooms():
    """Add default rooms if rooms sheet is empty."""
    records = _all_records("rooms")
    existing_names = {r["name"] for r in records}
    ws = _get_worksheet("rooms")
    next_id = _next_id("rooms")
    for name, capacity, floor in SEED_ROOMS:
        if name not in existing_names:
            ws.append_row([next_id, name, capacity, floor])
            next_id += 1


# ── Rooms CRUD ────────────────────────────────────────────────────────────────

def get_all_rooms():
    records = _all_records("rooms")
    return sorted(records, key=lambda r: (r.get("floor", ""), r.get("name", "")))


def add_room(name: str, capacity: int, floor: str):
    ws = _get_worksheet("rooms")
    new_id = _next_id("rooms")
    ws.append_row([new_id, name, capacity, floor])
    _invalidate_cache("rooms")


def update_room(room_id: int, name: str, capacity: int, floor: str):
    row_idx = _find_row_index("rooms", room_id)
    if row_idx:
        ws = _get_worksheet("rooms")
        ws.update(f"A{row_idx}:D{row_idx}", [[room_id, name, capacity, floor]])
        _invalidate_cache("rooms")


def delete_room(room_id: int):
    row_idx = _find_row_index("rooms", room_id)
    if row_idx:
        ws = _get_worksheet("rooms")
        ws.delete_rows(row_idx)
        _invalidate_cache("rooms")


# ── Requests CRUD ─────────────────────────────────────────────────────────────

def create_request(project_name: str, requester: str, team_size: int,
                   week_start: str, desired_days: list[str]):
    ws = _get_worksheet("requests")
    new_id = _next_id("requests")
    ws.append_row([
        new_id, project_name, requester, team_size,
        week_start, ",".join(desired_days),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ])
    _invalidate_cache("requests")


def get_requests_for_week(week_start: str):
    records = _all_records("requests")
    return [r for r in records if r.get("week_start") == week_start]


def delete_request(request_id: int):
    row_idx = _find_row_index("requests", request_id)
    if row_idx:
        ws = _get_worksheet("requests")
        ws.delete_rows(row_idx)
        _invalidate_cache("requests")


# ── Allocations CRUD ──────────────────────────────────────────────────────────

def save_allocations(allocations: list[dict]):
    ws = _get_worksheet("allocations")
    next_id = _next_id("allocations")
    rows = []
    for a in allocations:
        rows.append([
            next_id, a["request_id"], a["room_id"], a["date"],
            a["project_name"], a["requester"], a["team_size"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ])
        next_id += 1
    if rows:
        ws.append_rows(rows)
    _invalidate_cache("allocations")


def clear_allocations_for_week(week_start: str, week_end: str):
    ws = _get_worksheet("allocations")
    rows = ws.get_all_values()
    # Find rows to delete (reverse order to preserve indices)
    to_delete = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if len(row) > 3 and row[3] >= week_start and row[3] <= week_end:
            to_delete.append(i + 1)
    for idx in reversed(to_delete):
        ws.delete_rows(idx)
    _invalidate_cache("allocations")


def _enrich_allocation(a: dict, rooms_by_id: dict) -> dict:
    room = rooms_by_id.get(a.get("room_id"))
    if room:
        a["room_name"] = room["name"]
        a["capacity"] = room["capacity"]
        a["floor"] = room["floor"]
    return a


def get_allocations_for_week(week_start: str, week_end: str):
    records = _all_records("allocations")
    rooms = {r["id"]: r for r in get_all_rooms()}
    result = [r for r in records if week_start <= r.get("date", "") <= week_end]
    return [_enrich_allocation(r, rooms) for r in sorted(result, key=lambda r: (r.get("date", ""), r.get("room_id", 0)))]


def get_allocations_for_date(date_str: str):
    records = _all_records("allocations")
    rooms = {r["id"]: r for r in get_all_rooms()}
    result = [r for r in records if r.get("date") == date_str]
    return [_enrich_allocation(r, rooms) for r in sorted(result, key=lambda r: r.get("room_id", 0))]


def cancel_allocation(allocation_id: int):
    row_idx = _find_row_index("allocations", allocation_id)
    if row_idx:
        ws = _get_worksheet("allocations")
        ws.delete_rows(row_idx)
        _invalidate_cache("allocations")


# ── Direct Bookings (post-deadline) ──────────────────────────────────────────

def create_direct_booking(room_id: int, date_str: str, project_name: str,
                          requester: str, team_size: int):
    # Check if room already booked
    booked = get_booked_room_ids_for_date(date_str)
    if room_id in booked:
        raise ValueError("Kamer is al geboekt op deze dag.")
    ws = _get_worksheet("direct_bookings")
    new_id = _next_id("direct_bookings")
    ws.append_row([
        new_id, room_id, date_str, project_name, requester, team_size,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ])
    _invalidate_cache("direct_bookings")


def _enrich_direct(d: dict, rooms_by_id: dict) -> dict:
    room = rooms_by_id.get(d.get("room_id"))
    if room:
        d["room_name"] = room["name"]
        d["capacity"] = room["capacity"]
        d["floor"] = room["floor"]
    return d


def get_direct_bookings_for_week(week_start: str, week_end: str):
    records = _all_records("direct_bookings")
    rooms = {r["id"]: r for r in get_all_rooms()}
    result = [r for r in records if week_start <= r.get("date", "") <= week_end]
    return [_enrich_direct(r, rooms) for r in sorted(result, key=lambda r: (r.get("date", ""), r.get("room_id", 0)))]


def get_direct_bookings_for_date(date_str: str):
    records = _all_records("direct_bookings")
    rooms = {r["id"]: r for r in get_all_rooms()}
    result = [r for r in records if r.get("date") == date_str]
    return [_enrich_direct(r, rooms) for r in sorted(result, key=lambda r: r.get("room_id", 0))]


def cancel_direct_booking(booking_id: int):
    row_idx = _find_row_index("direct_bookings", booking_id)
    if row_idx:
        ws = _get_worksheet("direct_bookings")
        ws.delete_rows(row_idx)
        _invalidate_cache("direct_bookings")


# ── Queries ───────────────────────────────────────────────────────────────────

def get_all_bookings_for_date(date_str: str):
    allocs = get_allocations_for_date(date_str)
    directs = get_direct_bookings_for_date(date_str)
    for d in directs:
        d["source"] = "direct"
    for a in allocs:
        a["source"] = "allocation"
    return allocs + directs


def get_booked_room_ids_for_date(date_str: str):
    alloc_records = _all_records("allocations")
    direct_records = _all_records("direct_bookings")
    alloc_ids = {r["room_id"] for r in alloc_records if r.get("date") == date_str}
    direct_ids = {r["room_id"] for r in direct_records if r.get("date") == date_str}
    return alloc_ids | direct_ids


def get_week_fairness(week_start: str, week_end: str):
    allocs = _all_records("allocations")
    directs = _all_records("direct_bookings")
    counts = {}
    for r in allocs:
        if week_start <= r.get("date", "") <= week_end:
            pn = r.get("project_name", "")
            counts[pn] = counts.get(pn, 0) + 1
    for r in directs:
        if week_start <= r.get("date", "") <= week_end:
            pn = r.get("project_name", "")
            counts[pn] = counts.get(pn, 0) + 1
    return counts


def get_all_upcoming_bookings():
    today = date.today().isoformat()
    rooms = {r["id"]: r for r in get_all_rooms()}
    results = []

    for a in _all_records("allocations"):
        if a.get("date", "") >= today:
            _enrich_allocation(a, rooms)
            a["source"] = "allocation"
            results.append(a)

    for d in _all_records("direct_bookings"):
        if d.get("date", "") >= today:
            _enrich_direct(d, rooms)
            d["source"] = "direct"
            results.append(d)

    return sorted(results, key=lambda r: r.get("date", ""))


def get_bookings_by_project(project_name: str):
    today = date.today().isoformat()
    rooms = {r["id"]: r for r in get_all_rooms()}
    results = []

    for a in _all_records("allocations"):
        if a.get("project_name") == project_name and a.get("date", "") >= today:
            _enrich_allocation(a, rooms)
            a["source"] = "allocation"
            results.append(a)

    for d in _all_records("direct_bookings"):
        if d.get("project_name") == project_name and d.get("date", "") >= today:
            _enrich_direct(d, rooms)
            d["source"] = "direct"
            results.append(d)

    return sorted(results, key=lambda r: r.get("date", ""))


def get_bookings_by_requester(requester: str):
    today = date.today().isoformat()
    rooms = {r["id"]: r for r in get_all_rooms()}
    results = []

    for a in _all_records("allocations"):
        if a.get("requester") == requester and a.get("date", "") >= today:
            _enrich_allocation(a, rooms)
            a["source"] = "allocation"
            results.append(a)

    for d in _all_records("direct_bookings"):
        if d.get("requester") == requester and d.get("date", "") >= today:
            _enrich_direct(d, rooms)
            d["source"] = "direct"
            results.append(d)

    return sorted(results, key=lambda r: r.get("date", ""))


def get_occupancy_stats(week_start: str, week_end: str):
    rooms = get_all_rooms()
    allocs = _all_records("allocations")
    directs = _all_records("direct_bookings")
    result = []
    for room in rooms:
        count = 0
        for a in allocs:
            if a.get("room_id") == room["id"] and week_start <= a.get("date", "") <= week_end:
                count += 1
        for d in directs:
            if d.get("room_id") == room["id"] and week_start <= d.get("date", "") <= week_end:
                count += 1
        result.append({
            "room_name": room["name"],
            "floor": room["floor"],
            "capacity": room["capacity"],
            "booked_days": count,
            "occupancy_pct": round(count / 5 * 100),
        })
    return result


# ── Init on import ────────────────────────────────────────────────────────────

init_db()
seed_rooms()
