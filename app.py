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
        requests = db.get_requests_for_week(week_start)
        if not requests:
            continue
        # Run allocation and then delete the processed requests
        alloc.run_allocation(week_start)
        for req in requests:
            db.delete_request(req["id"])

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
    ["📋 New Request", "📅 Week Overview", "📌 Manage Bookings"],
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

        deadline_thu = selected_monday - timedelta(days=4)
        st.info(
            f"⏰ **Request phase** — Deadline: Thursday {deadline_thu.strftime('%d %b %Y')} at 17:00. "
            f"After the deadline, the allocation will be finalized automatically."
        )

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
        if allocs or directs:
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
            matrix[a["room_name"]][day_abbr] = f"📁 {a['project_name']}"

        # Fill with direct bookings
        directs = db.get_direct_bookings_for_week(week_start, week_end)
        for b in directs:
            d = date.fromisoformat(b["date"])
            day_abbr = DAY_ABBR[d.weekday()]
            matrix[b["room_name"]][day_abbr] = f"📁 {b['project_name']}"

        # Display
        df = pd.DataFrame(matrix).T
        df.index.name = "Room"

        # Add capacity info
        cap_map = {r["name"]: f"{r['capacity']}p" for r in rooms}
        df.insert(0, "Cap.", [cap_map.get(name, "") for name in df.index])

        # Style: color cells
        def color_cells(val):
            if val and val.startswith("📁"):
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
        </div>
        """, unsafe_allow_html=True)

        # Fairness overview (table only, no bar chart)
        st.markdown("---")
        st.subheader("📊 Distribution this week")
        fairness = db.get_week_fairness(week_start, week_end)
        if fairness:
            fair_df = pd.DataFrame(
                [{"Project": k, "Room days": v} for k, v in fairness.items()]
            )
            st.dataframe(fair_df, use_container_width=True, hide_index=True)
        else:
            st.info("No bookings this week yet.")


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
                # Cancel all button
                if st.button(f"❌ Cancel all bookings for {project_name}", key=f"cancel_all_{project_name}"):
                    for b in bookings:
                        if b["source"] == "allocation":
                            db.cancel_allocation(b["id"])
                        else:
                            db.cancel_direct_booking(b["id"])
                    st.success(f"All bookings for {project_name} cancelled.")
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
