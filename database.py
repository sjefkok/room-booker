import gspread
from google.oauth2.service_account import Credentials
import streamlit as st
from datetime import datetime, date, timedelta
import time

# ── Google Sheets Connection ──────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# In-memory cache to avoid hitting API rate limits
_cache = {}
_cache_ttl = 15  # seconds


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
    "request_archive": ["id", "project_name", "requester", "team_size", "week_start", "desired_days", "num_days", "created_at"],
}


def _api_call(fn, *args, **kwargs):
    """Wrap any gspread call with retry logic for rate limits."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            if e.response.status_code == 429 and attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s
                continue
            raise


@st.cache_resource
def _get_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return gspread.authorize(creds)


@st.cache_resource
def _get_spreadsheet():
    client = _get_client()
    return client.open_by_key(st.secrets["spreadsheet_id"])


# Cache worksheet objects to avoid repeated lookups
_ws_cache = {}

def _get_worksheet(name: str):
    if name in _ws_cache:
        return _ws_cache[name]
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(SHEET_HEADERS[name]))
        ws.append_row(SHEET_HEADERS[name])
    _ws_cache[name] = ws
    return ws


def _all_records(ws_name: str) -> list[dict]:
    """Read all rows from a worksheet as list of dicts. Cached for _cache_ttl seconds."""
    now = time.time()
    if ws_name in _cache and (now - _cache[ws_name]["ts"]) < _cache_ttl:
        return _cache[ws_name]["data"]

    ws = _get_worksheet(ws_name)
    rows = _api_call(ws.get_all_records)
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


# ── Rooms ─────────────────────────────────────────────────────────────────────

def get_all_rooms():
    records = _all_records("rooms")
    return sorted(records, key=lambda r: (r.get("floor", ""), r.get("name", "")))


# ── Requests CRUD ─────────────────────────────────────────────────────────────

def create_request(project_name: str, requester: str, team_size: int,
                   week_start: str, desired_days: list[str]):
    ws = _get_worksheet("requests")
    new_id = _next_id("requests")
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _api_call(ws.append_row, [
        new_id, project_name, requester, team_size,
        week_start, ",".join(desired_days),
        created_at,
    ])
    _invalidate_cache("requests")
    # Archive a copy for administration / facilities reporting
    _archive_request(project_name, requester, team_size, week_start,
                     desired_days, created_at)


def _archive_request(project_name: str, requester: str, team_size: int,
                     week_start: str, desired_days: list[str], created_at: str):
    # Remove any existing archive entry for same project + week (prevents duplicates on resubmit)
    _delete_archive_entry(project_name, week_start)
    ws = _get_worksheet("request_archive")
    new_id = _next_id("request_archive")
    _api_call(ws.append_row, [
        new_id, project_name, requester, team_size,
        week_start, ",".join(desired_days), len(desired_days),
        created_at,
    ])
    _invalidate_cache("request_archive")


def _delete_archive_entry(project_name: str, week_start: str):
    """Delete archive entry matching project_name + week_start (case-insensitive)."""
    ws = _get_worksheet("request_archive")
    rows = _api_call(ws.get_all_values)
    pn_lower = project_name.strip().lower()
    for i in range(len(rows) - 1, 0, -1):  # reverse to avoid index shift
        if rows[i][1].strip().lower() == pn_lower and rows[i][4] == week_start:
            _api_call(ws.delete_rows, i + 1)
    _invalidate_cache("request_archive")


def get_requests_for_week(week_start: str):
    records = _all_records("requests")
    return [r for r in records if r.get("week_start") == week_start]


def delete_request(request_id: int, keep_archive: bool = False):
    # Look up project_name + week_start before deleting so we can clean archive too
    records = _all_records("requests")
    match = next((r for r in records if r.get("id") == request_id), None)
    row_idx = _find_row_index("requests", request_id)
    if row_idx:
        ws = _get_worksheet("requests")
        _api_call(ws.delete_rows, row_idx)
        _invalidate_cache("requests")
        # Also remove from archive so distribution table stays accurate
        # (skip when allocation consumes the request — archive needed for history)
        if match and not keep_archive:
            _delete_archive_entry(match["project_name"], match["week_start"])


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
    a = dict(a)  # copy to avoid mutating cache
    room = rooms_by_id.get(a.get("room_id"))
    a["room_name"] = room["name"] if room else "?"
    a["capacity"] = room["capacity"] if room else ""
    a["floor"] = room["floor"] if room else ""
    return a


def get_allocations_for_week(week_start: str, week_end: str):
    records = _all_records("allocations")
    rooms = {r["id"]: r for r in get_all_rooms()}
    result = [r for r in records if week_start <= r.get("date", "") <= week_end]
    # Deduplicate by (room_id, date) — keep first occurrence only
    seen = set()
    deduped = []
    for r in sorted(result, key=lambda r: (r.get("date", ""), r.get("room_id", 0))):
        key = (r.get("room_id"), r.get("date"))
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return [_enrich_allocation(r, rooms) for r in deduped]


def cancel_allocation(allocation_id: int):
    # Look up the allocation before deleting so we can clean archive if needed
    records = _all_records("allocations")
    match = next((r for r in records if r.get("id") == allocation_id), None)
    row_idx = _find_row_index("allocations", allocation_id)
    if row_idx:
        ws = _get_worksheet("allocations")
        _api_call(ws.delete_rows, row_idx)
        _invalidate_cache("allocations")
        # If no allocations remain for this project+week, remove archive entry
        if match:
            pn = match.get("project_name", "")
            d = match.get("date", "")
            if d:
                monday = date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())
                week_start = monday.isoformat()
                week_end = (monday + timedelta(days=4)).isoformat()
                remaining = [r for r in _all_records("allocations")
                             if r.get("project_name") == pn
                             and week_start <= r.get("date", "") <= week_end]
                if not remaining:
                    _delete_archive_entry(pn, week_start)


# ── Direct Bookings ───────────────────────────────────────────────────────────

def _enrich_direct(d: dict, rooms_by_id: dict) -> dict:
    d = dict(d)  # copy to avoid mutating cache
    room = rooms_by_id.get(d.get("room_id"))
    d["room_name"] = room["name"] if room else "?"
    d["capacity"] = room["capacity"] if room else ""
    d["floor"] = room["floor"] if room else ""
    return d


def get_direct_bookings_for_week(week_start: str, week_end: str):
    records = _all_records("direct_bookings")
    rooms = {r["id"]: r for r in get_all_rooms()}
    result = [r for r in records if week_start <= r.get("date", "") <= week_end]
    return [_enrich_direct(r, rooms) for r in sorted(result, key=lambda r: (r.get("date", ""), r.get("room_id", 0)))]


def cancel_direct_booking(booking_id: int):
    row_idx = _find_row_index("direct_bookings", booking_id)
    if row_idx:
        ws = _get_worksheet("direct_bookings")
        ws.delete_rows(row_idx)
        _invalidate_cache("direct_bookings")


# ── Queries ───────────────────────────────────────────────────────────────────

def get_booked_room_ids_for_date(date_str: str):
    alloc_records = _all_records("allocations")
    direct_records = _all_records("direct_bookings")
    alloc_ids = {r["room_id"] for r in alloc_records if r.get("date") == date_str}
    direct_ids = {r["room_id"] for r in direct_records if r.get("date") == date_str}
    return alloc_ids | direct_ids


def get_week_fairness(week_start: str, week_end: str):
    allocs = _all_records("allocations")
    directs = _all_records("direct_bookings")
    archive = _all_records("request_archive")
    requests = _all_records("requests")
    allocated = {}
    seen_alloc = set()
    for r in allocs:
        if week_start <= r.get("date", "") <= week_end:
            key = (r.get("room_id"), r.get("date"))
            if key in seen_alloc:
                continue
            seen_alloc.add(key)
            pn = r.get("project_name", "")
            allocated[pn] = allocated.get(pn, 0) + 1
    for r in directs:
        if week_start <= r.get("date", "") <= week_end:
            pn = r.get("project_name", "")
            allocated[pn] = allocated.get(pn, 0) + 1
    requested = {}
    # Primary source: request archive
    for r in archive:
        if r.get("week_start") == week_start:
            pn = r.get("project_name", "")
            num = r.get("num_days", 0)
            try:
                num = int(num)
            except (ValueError, TypeError):
                num = 0
            requested[pn] = requested.get(pn, 0) + num
    # Fallback: live requests (for projects not yet in archive)
    for r in requests:
        if r.get("week_start") == week_start:
            pn = r.get("project_name", "")
            if pn not in requested:
                dd = r.get("desired_days", "")
                requested[pn] = len(dd.split(",")) if dd else 0
    all_projects = set(allocated) | set(requested)
    return {pn: {"requested": requested.get(pn, 0),
                 "allocated": allocated.get(pn, 0)} for pn in all_projects}


def get_all_upcoming_bookings():
    today = date.today().isoformat()
    rooms = {r["id"]: r for r in get_all_rooms()}
    results = []
    seen = set()

    for a in _all_records("allocations"):
        if a.get("date", "") >= today:
            key = (a.get("room_id"), a.get("date"))
            if key in seen:
                continue
            seen.add(key)
            enriched = _enrich_allocation(a, rooms)
            enriched["source"] = "allocation"
            results.append(enriched)

    for d in _all_records("direct_bookings"):
        if d.get("date", "") >= today:
            enriched = _enrich_direct(d, rooms)
            enriched["source"] = "direct"
            results.append(enriched)

    return sorted(results, key=lambda r: r.get("date", ""))


# ── Init on import ────────────────────────────────────────────────────────────

init_db()
seed_rooms()
