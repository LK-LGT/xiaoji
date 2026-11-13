"""Test import in isolation"""
import sys

print("=== Testing direct route instantiation ===")
from fastapi import APIRouter

router = APIRouter(prefix="/v1/assessment", tags=["test"])

@router.post("/records/{record_id}/self-review")
async def test_endpoint():
    return {"status": "ok"}

print(f"Router created with route: {[r.path for r in router.routes]}")

print("\n=== Testing FastAPI app registration ===")
from fastapi import FastAPI

app = FastAPI()
print(f"Before include_router: {len(list(app.routes))} routes")

app.include_router(router)
print(f"After include_router: {len(list(app.routes))} routes")

routes_with_v1_assessment = [r.path for r in app.routes if hasattr(r, 'path') and '/v1/assessment' in r.path]
print(f"Routes with '/v1/assessment': {routes_with_v1_assessment}")

if '/v1/assessment/records/{record_id}/self-review' in routes_with_v1_assessment:
    print("✅ Include router works!")
else:
    print("❌ Include router failed!")
