"""Punto de entrada ASGI: ``uvicorn app.main:app``."""

from app.api.app import create_app
from app.api.cors import configure_cors
from app.config import get_settings

app = create_app()
configure_cors(app, get_settings())
