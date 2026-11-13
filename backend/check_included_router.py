from app.routes.self_review import router as sr_router
from app.main import app

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
