"""测试路由注册"""
from app.main import app

print("Test: Check if self-review route is registered")
routes = [r.path for r in app.routes if hasattr(r, 'path') and 'self-review' in r.path]
print(f"Routes with 'self-review': {routes}")

if '/v1/assessment/records/{record_id}/self-review' in routes:
    print("✅ SUCCESS: Route is registered!")
    exit(0)
else:
    print("❌ FAIL: Route is NOT registered")
    print(f"All assessment routes: {[r.path for r in app.routes if hasattr(r, 'path') and 'assessment' in r.path]}")
    exit(1)
