"""Debug route registration"""
import sys
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 ``backend.app.*`` 可导入。
# （顶层 ``app`` 会被 agent-service 的 ``app`` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    print("Step 1: Import self_review module...")
    from backend.app.routes import self_review
    print(f"  OK: {self_review.router}")
    print(f"  Routes in self_review.router: {[r.path for r in self_review.router.routes if hasattr(r, 'path')]}")
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("\nStep 2: Import main app...")
    from backend.app.main import app
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
