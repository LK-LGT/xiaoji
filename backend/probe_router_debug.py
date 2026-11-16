from fastapi import APIRouter, FastAPI

app = FastAPI()
r = APIRouter()

@r.post('/test')
async def t():
    pass

app.include_router(r, prefix='/v1')

print([type(route).__name__ + ':' + str(getattr(route, 'path', 'no-path')) for route in app.routes])
print()
print("All route paths:")
for route in app.routes:
    if hasattr(route, 'path'):
        print(f"  {route.path}")
    elif hasattr(route, 'routes'):
        print("  IncludedRouter with routes:")
        for sub in route.routes:
            if hasattr(sub, 'path'):
                print(f"    {sub.path}")
