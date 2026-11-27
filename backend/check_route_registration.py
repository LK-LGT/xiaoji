"""测试路由注册"""
import sys
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 `backend.app.*` 可导入。
# （顶层 `app` 会被 agent-service 的 `app` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from backend.app.main import app

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
