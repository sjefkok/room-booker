import database as db

db._invalidate_cache()

allocs = db.get_allocations_for_week("2026-04-27", "2026-05-01")
print(f"Allocations for week 18: {len(allocs)}")
for a in allocs:
    print(f"  {a['date']} - {a['project_name']} - {a.get('room_name','?')}")

upcoming = db.get_all_upcoming_bookings()
print(f"\nUpcoming bookings: {len(upcoming)}")
for b in upcoming:
    print(f"  {b['date']} - {b['project_name']} - {b.get('room_name','?')}")
