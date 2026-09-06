from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from app import providers
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.routers import demo, insights, keys, openai_compat, proxy, usage
from app.routers import providers as providers_router
from app.routers import requests as requests_router

STATIC_DIR = Path(__file__).parent / "static"


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA. Real files are resolved by StaticFiles, whose
    lookup enforces path containment (traversal attempts resolve outside the
    directory and 404). Any other unmatched path returns index.html so
    client-side routes (/dashboard, /insights, ...) survive a refresh."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    providers.warm_cache()  # best-effort liveness check for any configured provider
    if settings.SEED_DEMO_DATA:
        from app.demo import seed_if_empty

        with SessionLocal() as db:
            seed_if_empty(db)
    yield


app = FastAPI(
    title="LLM Usage Tracker (Demo)",
    version=settings.VERSION,
    lifespan=lifespan,
)

if settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "version": settings.VERSION,
        "live_enabled": settings.ENABLE_LIVE,
    }


app.include_router(keys.router)
app.include_router(proxy.router)
app.include_router(openai_compat.router)
app.include_router(providers_router.router)
app.include_router(requests_router.router)
app.include_router(insights.router)
app.include_router(demo.router)
app.include_router(usage.router)


# Serve the built React SPA (only present in the production image). Mounted last
# so /api/*, /v1/*, /healthz, /docs all take precedence.
if STATIC_DIR.is_dir():
    app.mount("/", SPAStaticFiles(directory=STATIC_DIR, html=True), name="spa")
