import streamlit as st
import pandas as pd
import base64
import os
from datetime import date, datetime, timedelta

import database as db
import allocation as alloc

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="KPMG Room Booker",
    page_icon="🏢",
    layout="wide",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

DAY_NAMES_NL = {0: "Maandag", 1: "Dinsdag", 2: "Woensdag", 3: "Donderdag", 4: "Vrijdag"}
DAY_ABBR_NL = {0: "Ma", 1: "Di", 2: "Wo", 3: "Do", 4: "Vr"}


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_label(monday: date) -> str:
    friday = monday + timedelta(days=4)
    week_num = monday.isocalendar()[1]
    return f"Week {week_num}: {monday.strftime('%d %b')} – {friday.strftime('%d %b %Y')}"


def _get_week_range(monday: date) -> tuple[str, str]:
    return monday.isoformat(), (monday + timedelta(days=4)).isoformat()



# ── Sidebar navigation ───────────────────────────────────────────────────────

st.sidebar.title("🏢 KPMG Room Booker")
st.sidebar.markdown("---")
page = st.sidebar.radio(
    "Navigatie",
    ["📋 Aanvragen", "📅 Weekoverzicht", "📌 Mijn Boekingen"],
)

st.sidebar.markdown("---")
st.sidebar.caption("Deals Strategy — Kamerallocatie Tool")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Aanvragen (Requests / Direct Booking)
# ══════════════════════════════════════════════════════════════════════════════

if page == "📋 Aanvragen":
    st.title("📋 Kameraanvraag indienen")

    # Week selector
    available_weeks = alloc.get_available_weeks()
    week_options = {_week_label(m): m for m in available_weeks}
    selected_label = st.selectbox("Week", list(week_options.keys()))
    selected_monday = week_options[selected_label]

    before_deadline = alloc.is_before_deadline(selected_monday)

    if before_deadline:
        deadline_thu = selected_monday - timedelta(days=4)
        st.info(
            f"⏰ **Aanvraagfase** — Deadline: donderdag {deadline_thu.strftime('%d %b %Y')} om 17:00. "
            f"Na de deadline wordt de verdeling automatisch bekendgemaakt."
        )
    else:
        st.warning(
            "⚡ **Deadline verstreken** — Resterende vrije kamers kunnen direct geboekt worden (first-come, first-served)."
        )

    st.markdown("---")

    # ── Request form / Direct booking form ────────────────────────────────────

    col1, col2 = st.columns(2)

    with col1:
        project_name = st.text_input("Projectnaam", placeholder="bijv. Project Phoenix")
    with col2:
        requester = st.text_input("Naam reserveerder", placeholder="bijv. Jan Jansen")

    team_size = st.number_input("Teamgrootte", min_value=1, max_value=10, value=4)

    if before_deadline:
        # ── Aanvraag modus ────────────────────────────────────────────────────
        st.subheader("Gewenste dagen")
        days = alloc.week_dates(selected_monday)
        day_cols = st.columns(5)
        selected_days = []
        for i, d in enumerate(days):
            with day_cols[i]:
                if st.checkbox(DAY_NAMES_NL[i], key=f"day_{d.isoformat()}"):
                    selected_days.append(d.isoformat())

        if st.button("✅ Aanvraag indienen", type="primary", use_container_width=True):
            if not project_name or not requester:
                st.error("Vul projectnaam en reserveerder in.")
            elif not selected_days:
                st.error("Selecteer minstens één dag.")
            else:
                db.create_request(
                    project_name=project_name.strip(),
                    requester=requester.strip(),
                    team_size=team_size,
                    week_start=selected_monday.isoformat(),
                    desired_days=selected_days,
                )
                st.success(f"✅ Aanvraag ingediend voor {project_name} — "
                           f"{len(selected_days)} dag(en) aangevraagd.")
                st.rerun()

        # Show existing requests for this week
        st.markdown("---")
        st.subheader("Huidige aanvragen voor deze week")
        requests = db.get_requests_for_week(selected_monday.isoformat())
        if requests:
            for req in requests:
                days_str = ", ".join(
                    DAY_NAMES_NL[date.fromisoformat(d).weekday()]
                    for d in req["desired_days"].split(",") if d
                )
                with st.expander(f"**{req['project_name']}** — {req['requester']} ({req['team_size']}p) → {days_str}"):
                    st.write(f"**Ingediend:** {req['created_at']}")
                    if st.button("❌ Verwijder aanvraag", key=f"del_req_{req['id']}"):
                        db.delete_request(req["id"])
                        st.success("Aanvraag verwijderd.")
                        st.rerun()
        else:
            st.info("Nog geen aanvragen voor deze week.")

    else:
        # ── Direct booking modus ──────────────────────────────────────────────
        st.subheader("Direct boeken")
        days = alloc.week_dates(selected_monday)
        day_options = {DAY_NAMES_NL[d.weekday()]: d for d in days if d >= date.today()}

        if not day_options:
            st.warning("Geen boekbare dagen meer deze week.")
        else:
            selected_day_label = st.selectbox("Dag", list(day_options.keys()))
            selected_date = day_options[selected_day_label]
            date_str = selected_date.isoformat()

            available_rooms = alloc.get_available_rooms(date_str, team_size)
            if available_rooms:
                best = available_rooms[0]
                st.success(f"🎯 Aanbevolen kamer: **{best['name']}** ({best['capacity']}p, {best['floor']})")

                room_options = {f"{r['name']} ({r['capacity']}p, {r['floor']})": r for r in available_rooms}
                selected_room_label = st.selectbox("Kies kamer", list(room_options.keys()))
                selected_room = room_options[selected_room_label]

                if st.button("✅ Direct boeken", type="primary", use_container_width=True):
                    if not project_name or not requester:
                        st.error("Vul projectnaam en reserveerder in.")
                    else:
                        try:
                            db.create_direct_booking(
                                room_id=selected_room["id"],
                                date_str=date_str,
                                project_name=project_name.strip(),
                                requester=requester.strip(),
                                team_size=team_size,
                            )
                            st.success(
                                f"✅ Geboekt: **{selected_room['name']}** op "
                                f"{DAY_NAMES_NL[selected_date.weekday()]} {selected_date.strftime('%d %b')} "
                                f"voor {project_name}."
                            )
                            st.rerun()
                        except Exception:
                            st.error("Deze kamer is al geboekt op deze dag.")
            else:
                st.warning(f"Geen kamers beschikbaar op {DAY_NAMES_NL[selected_date.weekday()]} "
                           f"voor een team van {team_size} personen.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Weekoverzicht
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📅 Weekoverzicht":
    st.title("📅 Weekoverzicht")

    available_weeks = alloc.get_available_weeks()
    week_options = {_week_label(m): m for m in available_weeks}
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
            matrix[room["name"]][DAY_ABBR_NL[d.weekday()]] = ""

    # Fill with allocations
    allocations = db.get_allocations_for_week(week_start, week_end)
    for a in allocations:
        d = date.fromisoformat(a["date"])
        day_abbr = DAY_ABBR_NL[d.weekday()]
        matrix[a["room_name"]][day_abbr] = f"📁 {a['project_name']}"

    # Fill with direct bookings
    directs = db.get_direct_bookings_for_week(week_start, week_end)
    for b in directs:
        d = date.fromisoformat(b["date"])
        day_abbr = DAY_ABBR_NL[d.weekday()]
        matrix[b["room_name"]][day_abbr] = f"📁 {b['project_name']}"

    # Display
    df = pd.DataFrame(matrix).T
    df.index.name = "Kamer"

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
        <span style="background:#C8E6C9; padding:4px 12px; border-radius:4px;">🟢 Vrij</span>
        <span style="background:#FFE082; padding:4px 12px; border-radius:4px;">🟡 Bezet</span>
    </div>
    """, unsafe_allow_html=True)

    # Fairness overview
    st.markdown("---")
    st.subheader("📊 Verdeling deze week")
    fairness = db.get_week_fairness(week_start, week_end)
    if fairness:
        fair_df = pd.DataFrame(
            [{"Project": k, "Kamerdagen": v} for k, v in fairness.items()]
        )
        col1, col2 = st.columns([2, 3])
        with col1:
            st.dataframe(fair_df, use_container_width=True, hide_index=True)
        with col2:
            st.bar_chart(fair_df.set_index("Project"))
    else:
        st.info("Nog geen boekingen deze week.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Mijn Boekingen
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📌 Mijn Boekingen":
    st.title("📌 Mijn Boekingen")

    search_term = st.text_input(
        "🔍 Filter op project of naam",
        placeholder="Typ om te filteren (optioneel)...",
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
        st.success(f"{len(all_upcoming)} boeking(en) gevonden.")
        for b in all_upcoming:
                d = date.fromisoformat(b["date"])
                day_name = DAY_NAMES_NL[d.weekday()]
                source_label = "📋 Allocatie" if b["source"] == "allocation" else "⚡ Direct"
                with st.expander(
                    f"**{day_name} {d.strftime('%d %b')}** — {b['room_name']} ({b['capacity']}p) — {b['project_name']}"
                ):
                    st.write(f"**Reserveerder:** {b['requester']}")
                    st.write(f"**Teamgrootte:** {b['team_size']}p")
                    st.write(f"**Kamer:** {b['room_name']} ({b['floor']})")
                    st.write(f"**Type:** {source_label}")

                    if st.button("❌ Annuleer boeking", key=f"cancel_{b['source']}_{b['id']}"):
                        if b["source"] == "allocation":
                            db.cancel_allocation(b["id"])
                        else:
                            db.cancel_direct_booking(b["id"])
                        st.success("Boeking geannuleerd — kamer is nu vrij.")
                        st.rerun()
    else:
        st.info("Geen toekomstige boekingen gevonden." + (" Pas je filter aan." if search_term else ""))

    # Also show pending requests
    st.markdown("---")
    st.subheader("📋 Openstaande aanvragen")
    has_pending = False
    for monday in alloc.get_available_weeks():
        requests = db.get_requests_for_week(monday.isoformat())
        if search_term:
            q = search_term.strip().lower()
            requests = [r for r in requests
                        if q in r["project_name"].lower() or q in r["requester"].lower()]
        for req in requests:
            has_pending = True
            days_str = ", ".join(
                DAY_NAMES_NL[date.fromisoformat(d).weekday()]
                for d in req["desired_days"].split(",") if d
            )
            st.info(f"**{_week_label(monday)}** — {req['project_name']} ({req['requester']}, "
                    f"{req['team_size']}p) → {days_str}")
    if not has_pending:
        st.caption("Geen openstaande aanvragen.")
