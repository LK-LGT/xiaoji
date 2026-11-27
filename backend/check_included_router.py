import sys
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 `backend.app.*` 可导入。
# （顶层 `app` 会被 agent-service 的 `app` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from backend.app.main import app
from backend.app.routes.self_review import router as sr_router

print("=== Checking _IncludedRouter ===")
for route_obj in app.routes:
    if hasattr(route_obj, 'routes'):
        print(f"IncludedRouter found with {len(route_obj.routes)} routes")
        for sub_route in route_obj.routes:
            if hasattr(sub_route, 'path'):
                print(f"  - {sub_route.path}")

print("\n=== Direct sr_router routes ===")
print([r.path for r in sr_router.routes if hasattr(r, 'path')])

print("\n=== App routes (toplevel) ===")
toplevel = [r.path for r in app.routes if hasattr(r, 'path') and 'assessment' in r.path]
print(toplevel)
