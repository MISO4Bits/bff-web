from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.config import Settings

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"

REGISTRO_VALIDO = {
    "email": "ana.rios@example.com",
    "password": "unaClaveSegura1",
    "tipoDocumento": "CC",
    "numeroDocumento": "1032456789",
    "primerNombre": "Ana",
    "primerApellido": "Ríos",
    "fechaNacimiento": "1991-05-20",
    "politicaVersion": "2026-01",
    "aceptaTerminos": True,
}


@pytest.fixture(scope="session")
def openapi_spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def settings() -> Settings:
    return Settings(adapters="fake", session_secret="test-secret")


@pytest_asyncio.fixture
async def app(settings):
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest_asyncio.fixture
async def cliente_autenticado(client):
    """Devuelve (client, headers) con una sesión válida ya iniciada."""
    resp = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    assert resp.status_code == 201
    token = resp.json()["sesion"]["accessToken"]
    return client, {"Authorization": f"Bearer {token}"}
