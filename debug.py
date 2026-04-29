"""Check allocation data for Week 19."""
import streamlit as st
st.set_page_config(page_title="Debug")
import database as db

st.title("Debug: Week 19 data")

st.subheader("Allocations")
allocs = db.get_allocations_for_week("2026-05-04", "2026-05-08")
for a in allocs:
    st.write(f"ID={a.get('id')} | req_id={a.get('request_id')} | {a['date']} | {a.get('room_name','?')} | {a['project_name']} | {a['requester']}")
st.write(f"**Total: {len(allocs)}**")

st.subheader("Request Archive")
archive = db._all_records("request_archive")
wk19 = [r for r in archive if r.get("week_start") == "2026-05-04"]
for r in wk19:
    st.write(f"ID={r.get('id')} | {r['project_name']} | {r['requester']} | days={r.get('num_days')} | {r.get('desired_days')}")
st.write(f"**Total: {len(wk19)}**")
