"""Debug route registration"""
import sys

try:
    print("Step 1: Import self_review module...")
    from app.routes import self_review
    print(f"  OK: {self_review.router}")
    print(f"  Routes in self_review.router: {[r.path for r in self_review.router.routes if hasattr(r, 'path')]}")
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("\nStep 2: Import main app...")
    from app.main import app
    print(f"  OK: app={app}")
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\nStep 3: Check registered routes...")
sr_routes = [r.path for r in app.routes if hasattr(r, 'path') and 'self-review' in r.path]
print(f"  Self-review routes in app: {sr_routes}")

print("\nStep 4: Check if new route is there...")
if '/v1/assessment/records/{record_id}/self-review' in sr_routes:
    print("  ✅ Route registered!")
else:
    print("  ❌ Route NOT registered")
    print(f"  All routes: {[r.path for r in app.routes if hasattr(r, 'path')][:20]}")
