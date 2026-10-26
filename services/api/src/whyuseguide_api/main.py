from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from whyuseguide_api import errors
from whyuseguide_api.config import Settings
from whyuseguide_api.health import build_checks, run_checks
from whyuseguide_api.routers import auth, catalog


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url)
    async with httpx.AsyncClient(timeout=settings.health_timeout_s) as http:
        if not hasattr(app.state, "checks"):
            app.state.checks = build_checks(settings, engine, redis, http)
        yield
    await redis.aclose()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="WhyUseGuide API", lifespan=lifespan)
    app.state.settings = settings or Settings()
    errors.install(app)
    app.include_router(auth.router)
    app.include_router(catalog.router)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        results = await run_checks(request.app.state.checks, request.app.state.settings.health_timeout_s)
        ok = all(r["ok"] for r in results.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ok" if ok else "degraded", "checks": results},
        )

    return app


app = create_app()
