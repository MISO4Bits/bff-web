"""Punto de entrada ASGI: ``uvicorn app.main:app``."""

from fastapi.middleware.cors import CORSMiddleware

from app.api.app import create_app

app = create_app()



app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)