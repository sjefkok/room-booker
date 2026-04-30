import streamlit as st
import pandas as pd
import base64
import os
from datetime import date, datetime, timedelta

import database as db
import allocation as alloc

# ── Auto-allocate past-deadline weeks ─────────────────────────────────────────

def _auto_allocate():
    """Run allocation for any week where the deadline has passed but requests
    are still pending (i.e. not yet allocated)."""
    for monday in alloc.get_available_weeks():
        if not alloc.is_after_deadline(monday):
            continue
        week_start = monday.isoformat()
        # Force fresh read from sheet (bypass cache) to avoid race conditions
        db._invalidate_cache("requests")
        requests = db.get_requests_for_week(week_start)
        if not requests:
            continue
        # Keep a copy of requests, delete originals, then allocate using the copy.
        # This prevents double allocation from concurrent page loads.
        req_copies = [dict(r) for r in requests]
        for req in requests:
            db.delete_request(req["id"], keep_archive=True)
        result = alloc.run_allocation(week_start, prefetched_requests=req_copies)
        # Show allocation result to user
        st.toast(f"✅ Auto-allocated Week {monday.isocalendar()[1]}: "
                 f"{len(result['allocations'])} room(s) assigned, "
                 f"{len(result['unmet'])} unmet.")

_auto_allocate()

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Room Booker",
    page_icon="🏢",
    layout="wide",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

DAY_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
DAY_ABBR = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_label(monday: date) -> str:
    friday = monday + timedelta(days=4)
    week_num = monday.isocalendar()[1]
    return f"Week {week_num}: {monday.strftime('%d %b')} – {friday.strftime('%d %b %Y')}"


def _get_week_range(monday: date) -> tuple[str, str]:
    return monday.isoformat(), (monday + timedelta(days=4)).isoformat()



# ── Sidebar navigation ───────────────────────────────────────────────────────

st.sidebar.title("🏢 Strategy — Room Booker")
st.sidebar.markdown("---")
page = st.sidebar.radio(
    "Navigation",
    ["📋 New Request", "📅 Week Overview", "📌 Manage Bookings", "🔒 Admin"],
)

st.sidebar.markdown("---")
st.sidebar.caption("Deals Strategy — Room Allocation Tool")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Aanvragen (Requests / Direct Booking)
# ══════════════════════════════════════════════════════════════════════════════

if page == "📋 New Request":
    st.title("📋 Submit Room Request")

    # Only show weeks where the deadline has NOT yet passed
    available_weeks = alloc.get_available_weeks()
    bookable_weeks = [m for m in available_weeks if alloc.is_before_deadline(m)]

    if not bookable_weeks:
        st.warning("There are no weeks open for requests at this moment. "
                   "Check back later when the next week opens up.")
    else:
        week_options = {_week_label(m): m for m in bookable_weeks}
        selected_label = st.selectbox("Week", list(week_options.keys()))
        selected_monday = week_options[selected_label]

        deadline_fri = selected_monday - timedelta(days=3)
        st.info(
            f"⏰ **Request phase** — Deadline: Friday {deadline_fri.strftime('%d %b %Y')} at 16:00. "
            f"After the deadline, the allocation will be finalized automatically."
        )
        st.warning("⚠️ Only **one request per project** per week is allowed. "
                   "If you need to change an existing request, cancel it first under **Manage Bookings**.")

        st.markdown("---")

        col1, col2 = st.columns(2)

        with col1:
            project_name = st.text_input("Project name", placeholder="e.g. Project Phoenix")
            st.caption("⚠️ Do **not** use client names — use internal project names only (confidentiality).")
        with col2:
            requester = st.text_input("Requester name", placeholder="e.g. John Smith")

        team_size = st.number_input("Team size", min_value=1, max_value=10, value=4)

        st.subheader("Preferred days")
        days = alloc.week_dates(selected_monday)
        day_cols = st.columns(5)
        selected_days = []
        for i, d in enumerate(days):
            with day_cols[i]:
                if st.checkbox(DAY_NAMES[i], key=f"day_{d.isoformat()}"):
                    selected_days.append(d.isoformat())

        if st.button("✅ Submit request", type="primary", use_container_width=True):
            if not project_name or not requester:
                st.error("Please fill in project name and requester.")
            elif not selected_days:
                st.error("Select at least one day.")
            else:
                # Check if this project already has a request for this week
                existing = db.get_requests_for_week(selected_monday.isoformat())
                duplicate = [r for r in existing if r["project_name"].strip().lower() == project_name.strip().lower()]
                if duplicate:
                    st.error(f"⚠️ A request for **{project_name}** already exists for this week. "
                             f"Only one request per project per week is allowed. "
                             f"You can cancel the existing request under **Manage Bookings**.")
                else:
                    db.create_request(
                        project_name=project_name.strip(),
                        requester=requester.strip(),
                        team_size=team_size,
                        week_start=selected_monday.isoformat(),
                        desired_days=selected_days,
                    )
                    st.balloons()
                    st.success(f"✅ Request submitted for **{project_name}** — "
                               f"{len(selected_days)} day(s) requested. "
                               f"You can view it under **Manage Bookings**.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Weekoverzicht
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📅 Week Overview":
    st.title("📅 Week Overview")

    # Only show weeks that have at least one booking
    available_weeks = alloc.get_available_weeks()
    weeks_with_bookings = []
    for monday in available_weeks:
        ws, we = _get_week_range(monday)
        allocs = db.get_allocations_for_week(ws, we)
        directs = db.get_direct_bookings_for_week(ws, we)
        blocks = db.get_room_blocks_for_week(monday.isoformat())
        if allocs or directs or blocks:
            weeks_with_bookings.append(monday)

    if not weeks_with_bookings:
        st.info("No weeks with allocated rooms yet.")
    else:
        week_options = {_week_label(m): m for m in weeks_with_bookings}
        selected_label = st.selectbox("Week", list(week_options.keys()))
        selected_monday = week_options[selected_label]
        week_start, week_end = _get_week_range(selected_monday)

        rooms = db.get_all_rooms()
        days = alloc.week_dates(selected_monday)

        # Build matrix: rows = rooms, cols = days
        matrix = {}
        for room in rooms:
            matrix[room["name"]] = {}
            for d in days:
                matrix[room["name"]][DAY_ABBR[d.weekday()]] = ""

        # Fill with allocations
        allocations = db.get_allocations_for_week(week_start, week_end)
        for a in allocations:
            d = date.fromisoformat(a["date"])
            day_abbr = DAY_ABBR[d.weekday()]
            matrix[a["room_name"]][day_abbr] = f"📁 {a['project_name']} - {a['requester']}"

        # Fill with direct bookings
        directs = db.get_direct_bookings_for_week(week_start, week_end)
        for b in directs:
            d = date.fromisoformat(b["date"])
            day_abbr = DAY_ABBR[d.weekday()]
            matrix[b["room_name"]][day_abbr] = f"📁 {b['project_name']} - {b['requester']}"

        # Fill with room blocks (reserved rooms)
        blocks = db.get_room_blocks_for_week(selected_monday.isoformat())
        rooms_by_id_map = {r["id"]: r for r in rooms}
        for block in blocks:
            room = rooms_by_id_map.get(block["room_id"])
            if not room:
                continue
            for day_str in str(block["days"]).split(","):
                day_str = day_str.strip()
                if not day_str:
                    continue
                try:
                    d = date.fromisoformat(day_str)
                    day_abbr = DAY_ABBR[d.weekday()]
                    if not matrix[room["name"]][day_abbr]:
                        matrix[room["name"]][day_abbr] = f"🔒 {block['project_name']} (Reserved)"
                except (ValueError, KeyError):
                    pass

        # Display
        df = pd.DataFrame(matrix).T
        df.index.name = "Room"

        # Add capacity info
        cap_map = {r["name"]: f"{r['capacity']}p" for r in rooms}
        df.insert(0, "Cap.", [cap_map.get(name, "") for name in df.index])

        # Style: color cells
        def color_cells(val):
            if val and "🔒" in val:
                return "background-color: #FFCDD2; color: #333; font-weight: bold;"
            elif val and val.startswith("📁"):
                return "background-color: #FFE082; color: #333; font-weight: bold;"
            elif val == "":
                return "background-color: #C8E6C9; color: #2E7D32;"
            return ""

        styled = df.style.map(color_cells, subset=[c for c in df.columns if c != "Cap."])
        st.dataframe(styled, use_container_width=True, height=320)

        st.markdown("""
        <div style="display:flex; gap:20px; margin-top:8px;">
            <span style="background:#C8E6C9; padding:4px 12px; border-radius:4px;">🟢 Available</span>
            <span style="background:#FFE082; padding:4px 12px; border-radius:4px;">🟡 Booked</span>
            <span style="background:#FFCDD2; padding:4px 12px; border-radius:4px;">🔴 Reserved</span>
        </div>
        """, unsafe_allow_html=True)

        # Fairness overview (table only, no bar chart)
        st.markdown("---")
        st.subheader("📊 Distribution this week")
        fairness = db.get_week_fairness(week_start, week_end)
        if fairness:
            fair_df = pd.DataFrame(
                [{"Project": k, "Days requested": v["requested"], "Days allocated": v["allocated"]}
                 for k, v in fairness.items()]
            )
            st.dataframe(fair_df, use_container_width=True, hide_index=True)
        else:
            st.info("No bookings this week yet.")

        # Download bookings as CSV
        all_bookings = allocations + directs
        # Include room blocks in bookings list
        for block in blocks:
            room = rooms_by_id_map.get(block["room_id"])
            if not room:
                continue
            for day_str in str(block["days"]).split(","):
                day_str = day_str.strip()
                if day_str:
                    all_bookings.append({
                        "date": day_str,
                        "room_name": room["name"],
                        "project_name": block["project_name"],
                        "requester": block["requester"],
                        "team_size": "",
                    })
        if all_bookings:
            st.markdown("---")
            # One row per project, day columns show room name
            from collections import defaultdict
            grouped = defaultdict(lambda: {"Mon": "", "Tue": "", "Wed": "", "Thu": "", "Fri": "",
                                           "requester": "", "team_size": ""})
            for b in all_bookings:
                project = b.get("project_name", "")
                d = date.fromisoformat(b["date"])
                day = DAY_ABBR[d.weekday()]
                grouped[project]["requester"] = b.get("requester", "")
                grouped[project]["team_size"] = b.get("team_size", "")
                grouped[project][day] = b.get("room_name", "")

            download_rows = []
            for project in sorted(grouped.keys()):
                info = grouped[project]
                download_rows.append({
                    "Project": project,
                    "Requester": info["requester"],
                    "Team size": info["team_size"],
                    "Mon": info["Mon"],
                    "Tue": info["Tue"],
                    "Wed": info["Wed"],
                    "Thu": info["Thu"],
                    "Fri": info["Fri"],
                })
            download_df = pd.DataFrame(download_rows)
            csv = download_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="⬇️ Download bookings as CSV",
                data=csv,
                file_name=f"room_bookings_week_{selected_monday.isocalendar()[1]}.csv",
                mime="text/csv",
            )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Mijn Boekingen
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📌 Manage Bookings":
    st.title("📌 Manage Bookings")

    search_term = st.text_input(
        "🔍 Filter by project or name",
        placeholder="Type to filter (optional)...",
    )

    # Get ALL upcoming bookings
    all_upcoming = db.get_all_upcoming_bookings()

    # Filter if search term provided
    if search_term:
        q = search_term.strip().lower()
        all_upcoming = [b for b in all_upcoming
                        if q in b.get("project_name", "").lower()
                        or q in b.get("requester", "").lower()]

    if all_upcoming:
        st.success(f"{len(all_upcoming)} booking(s) found.")

        # Group bookings by project name
        from collections import defaultdict
        grouped = defaultdict(list)
        for b in all_upcoming:
            grouped[b["project_name"]].append(b)

        for project_name, bookings in grouped.items():
            requester = bookings[0]["requester"]
            team_size = bookings[0]["team_size"]
            days_summary = ", ".join(
                f"{DAY_NAMES[date.fromisoformat(b['date']).weekday()]} {date.fromisoformat(b['date']).strftime('%d %b')}"
                for b in bookings
            )
            with st.expander(
                f"**{project_name}** — {requester} ({team_size}p) — {len(bookings)} day(s): {days_summary}"
            ):
                # Cancel all future bookings button
                future_bookings = [b for b in bookings if date.fromisoformat(b["date"]) >= date.today()]
                if future_bookings:
                    if st.button(f"❌ Cancel all future bookings for {project_name}", key=f"cancel_all_{project_name}"):
                        for b in future_bookings:
                            if b["source"] == "allocation":
                                db.cancel_allocation(b["id"])
                            else:
                                db.cancel_direct_booking(b["id"])
                        st.success(f"All future bookings for {project_name} cancelled.")
                        st.rerun()

                st.markdown("---")

                # Individual day cancellations
                for b in bookings:
                    d = date.fromisoformat(b["date"])
                    day_name = DAY_NAMES[d.weekday()]
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.write(f"**{day_name} {d.strftime('%d %b')}** — {b['room_name']} ({b['capacity']}p, {b['floor']})")
                    with col2:
                        if d >= date.today():
                            if st.button("❌", key=f"cancel_{b['source']}_{b['id']}", help=f"Cancel {day_name}"):
                                if b["source"] == "allocation":
                                    db.cancel_allocation(b["id"])
                                else:
                                    db.cancel_direct_booking(b["id"])
                                st.success(f"Booking on {day_name} {d.strftime('%d %b')} cancelled.")
                                st.rerun()
    else:
        st.info("No upcoming bookings found." + (" Adjust your filter." if search_term else ""))

    # Also show pending requests
    st.markdown("---")
    st.subheader("📋 Pending requests")

    # Collect all pending requests
    all_pending = []
    for monday in alloc.get_available_weeks():
        requests = db.get_requests_for_week(monday.isoformat())
        for req in requests:
            req["_monday"] = monday
            all_pending.append(req)

    # Text search filter for pending requests
    if all_pending:
        pending_search = st.text_input(
            "🔍 Filter pending requests",
            placeholder="Type to filter by project or name (optional)...",
            key="pending_search",
        )

        filtered_pending = all_pending
        if pending_search:
            q = pending_search.strip().lower()
            filtered_pending = [r for r in filtered_pending
                                if q in r["project_name"].lower()
                                or q in r["requester"].lower()]

        if filtered_pending:
            for req in filtered_pending:
                monday = req["_monday"]
                days_str = ", ".join(
                    DAY_NAMES[date.fromisoformat(d).weekday()]
                    for d in req["desired_days"].split(",") if d
                )
                with st.expander(
                    f"**{_week_label(monday)}** — {req['project_name']} ({req['requester']}, {req['team_size']}p) → {days_str}"
                ):
                    st.write(f"**Submitted:** {req['created_at']}")
                    if st.button("❌ Cancel request", key=f"cancel_req_{req['id']}"):
                        db.delete_request(req["id"])
                        st.success("Request cancelled.")
                        st.rerun()
        else:
            st.caption("No pending requests matching filters.")
    else:
        st.caption("No pending requests.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Admin
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🔒 Admin":
    st.title("🔒 Admin")

    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False

    if not st.session_state.admin_authenticated:
        password = st.text_input("Password", type="password")
        if st.button("🔑 Login", type="primary"):
            if password == "Selma":
                st.session_state.admin_authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    else:
        if st.sidebar.button("🚪 Logout Admin"):
            st.session_state.admin_authenticated = False
            st.rerun()

        # ── Block a Room ──────────────────────────────────────────────
        st.subheader("🔒 Block a Room")
        st.caption("Reserve a room for a confidential project. "
                   "Blocked rooms are excluded from the allocation algorithm.")

        rooms = db.get_all_rooms()
        room_options = {f"{r['name']} ({r['capacity']}p, {r['floor']})": r for r in rooms}

        col1, col2 = st.columns(2)
        with col1:
            selected_room_label = st.selectbox("Room", list(room_options.keys()))
            project_name = st.text_input("Project name", placeholder="e.g. Project Phoenix",
                                         key="admin_project")
        with col2:
            available_weeks = alloc.get_available_weeks()
            # Exclude current week — rooms already allocated
            future_weeks = [m for m in available_weeks if m > _monday_of(date.today())]
            week_options = {_week_label(m): m for m in future_weeks}
            selected_week_label = st.selectbox("Week", list(week_options.keys()),
                                               key="admin_week")
            requester = st.text_input("Requester", placeholder="e.g. John Smith",
                                      key="admin_requester")

        selected_monday = week_options[selected_week_label]
        days = alloc.week_dates(selected_monday)
        day_cols = st.columns(5)
        selected_days = []
        for i, d in enumerate(days):
            with day_cols[i]:
                if st.checkbox(DAY_NAMES[i], key=f"block_day_{d.isoformat()}"):
                    selected_days.append(d.isoformat())

        if st.button("🔒 Block Room", type="primary", use_container_width=True):
            if not project_name or not requester:
                st.error("Please fill in project name and requester.")
            elif not selected_days:
                st.error("Select at least one day.")
            else:
                selected_room = room_options[selected_room_label]
                # Check if room is already blocked for any of the selected days
                existing_blocks = db.get_room_blocks_for_week(selected_monday.isoformat())
                conflicts = []
                for block in existing_blocks:
                    if block["room_id"] == selected_room["id"]:
                        block_days = [bd.strip() for bd in str(block["days"]).split(",")]
                        for sd in selected_days:
                            if sd in block_days:
                                conflicts.append(sd)
                if conflicts:
                    conflict_names = ", ".join(
                        DAY_NAMES[date.fromisoformat(c).weekday()] for c in conflicts
                    )
                    st.error(f"Room is already blocked on: {conflict_names}")
                else:
                    db.create_room_block(
                        room_id=selected_room["id"],
                        project_name=project_name.strip(),
                        requester=requester.strip(),
                        week_start=selected_monday.isoformat(),
                        days=selected_days,
                    )
                    st.success(f"✅ Room **{selected_room['name']}** blocked for "
                               f"**{project_name}** — {len(selected_days)} day(s).")
                    st.rerun()

        # ── Current Blocks ────────────────────────────────────────────
        st.markdown("---")
        st.subheader("📋 Current Room Blocks")

        all_blocks = db.get_all_room_blocks()
        rooms_by_id = {r["id"]: r for r in rooms}

        # Filter to upcoming blocks only
        today = date.today()
        upcoming_blocks = []
        for block in all_blocks:
            try:
                ws = date.fromisoformat(block["week_start"])
                if ws + timedelta(days=4) >= today:
                    upcoming_blocks.append(block)
            except (ValueError, TypeError):
                pass

        if upcoming_blocks:
            for block in upcoming_blocks:
                room = rooms_by_id.get(block["room_id"], {})
                room_name = room.get("name", "?")
                ws = date.fromisoformat(block["week_start"])
                week_lbl = _week_label(ws)
                block_days = [bd.strip() for bd in str(block["days"]).split(",") if bd.strip()]
                day_names = ", ".join(
                    DAY_NAMES.get(date.fromisoformat(bd).weekday(), bd) for bd in block_days
                )
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.write(f"**{room_name}** — {block['project_name']} "
                             f"({block['requester']}) — {week_lbl} — {day_names}")
                with col2:
                    if st.button("❌", key=f"del_block_{block['id']}",
                                 help="Remove block"):
                        db.delete_room_block(block["id"])
                        st.success("Block removed.")
                        st.rerun()
        else:
            st.info("No room blocks set.")
