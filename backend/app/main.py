from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import providers
from app.config import settings
from app.db import Base, engine
from app.routers import keys, openai_compat, proxy, usage
from app.routers import providers as providers_router
from app.routers import requests as requests_router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    providers.warm_cache()  # best-effort liveness check for any configured provider
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
app.include_router(usage.router)


# Serve the built React SPA (only present in the production image). Registered last
# so /api/*, /v1/*, /healthz, /docs all take precedence.
if STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
