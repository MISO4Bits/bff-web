from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.adapters.factory import build_dependencias
from app.api.errors import install_error_handlers
from app.api.routes import router
from app.config import Settings, get_settings
from app.services import OnboardingService

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "openapi.yaml"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    deps = build_dependencias(settings)
    service = OnboardingService(deps.identity, deps.core, deps.sessions)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await deps.aclose()

    app = FastAPI(title="BFF Web — Onboarding", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.deps = deps
    app.state.service = service
    app.state.sessions = deps.sessions

    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "service": settings.service_name}

    if SPEC_PATH.exists():

        @app.get("/openapi.yaml", include_in_schema=False)
        async def openapi_yaml() -> FileResponse:
            return FileResponse(SPEC_PATH, media_type="application/yaml")

    return app
