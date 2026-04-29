"""Temp script to track allocation test for Week 19."""
import database as db
import allocation as alloc
from datetime import date

def show_requests():
    requests = db.get_requests_for_week("2026-05-04")
    print(f"=== PENDING REQUESTS FOR WEEK 19 (May 4-8) ===")
    print(f"Total: {len(requests)} requests\n")
    for r in requests:
        print(f"  ID={r['id']} | {r['project_name']} | {r['requester']} | {r['team_size']}p | Days: {r['desired_days']}")
    return requests

def show_allocations():
    allocs = db.get_allocations_for_week("2026-05-04", "2026-05-08")
    print(f"\n=== ALLOCATIONS FOR WEEK 19 (May 4-8) ===")
    print(f"Total: {len(allocs)} allocations\n")
    for a in allocs:
        print(f"  {a['date']} | {a.get('room_name','?')} ({a.get('capacity','?')}p) | {a['project_name']} | {a['requester']}")
    return allocs

def check_deadline():
    monday = date(2026, 5, 4)
    before = alloc.is_before_deadline(monday)
    print(f"\nDeadline status: {'BEFORE deadline (requests open)' if before else 'AFTER deadline (allocation should run)'}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "after":
        print(">>> POST-DEADLINE CHECK <<<\n")
        check_deadline()
        show_allocations()
    else:
        print(">>> PRE-DEADLINE CHECK <<<\n")
        check_deadline()
        show_requests()
