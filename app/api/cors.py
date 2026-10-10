"""CORS del BFF: el portal web (otro origen) llama a la API desde el navegador."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings


def origenes_permitidos(settings: Settings) -> list[str]:
    return [o.strip() for o in settings.cors_origins.split(",") if o.strip()]


def configure_cors(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes_permitidos(settings),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
        # El navegador cachea el preflight: una sola petición OPTIONS por hora y ruta.
        max_age=3600,
    )
