"""Integración del BFF con adaptadores HTTP reales (Identity Platform + svc-core mockeados)."""

from __future__ import annotations

import httpx
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.config import Settings
from tests.conftest import REGISTRO_VALIDO

IDP = "http://idp.test"
CORE = "http://core.test"


@pytest_asyncio.fixture
async def http_client():
    settings = Settings(
        adapters="http",
        identity_base_url=IDP,
        core_base_url=CORE,
        session_secret="test-secret",
        http_retries=1,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@respx.mock
async def test_registro_atraviesa_identity_y_core(http_client):
    respx.post(f"{IDP}/v1/accounts:signUp").mock(
        return_value=httpx.Response(200, json={"localId": "sub-9", "idToken": "t"})
    )
    respx.post(f"{CORE}/clientes").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "c-9",
                "primerNombre": "Ana",
                "primerApellido": "Ríos",
                "email": REGISTRO_VALIDO["email"],
                "estado": "ACTIVO",
            },
        )
    )

    resp = await http_client.post("/v1/registro", json=REGISTRO_VALIDO)

    assert resp.status_code == 201
    assert resp.json()["cuenta"]["clienteId"] == "c-9"


@respx.mock
async def test_registro_compensa_si_core_devuelve_409(http_client):
    respx.post(f"{IDP}/v1/accounts:signUp").mock(
        return_value=httpx.Response(200, json={"localId": "sub-9"})
    )
    respx.post(f"{CORE}/clientes").mock(
        return_value=httpx.Response(409, json={"detail": "documento repetido"})
    )
    borrado = respx.post(f"{IDP}/v1/accounts:delete").mock(
        return_value=httpx.Response(200, json={})
    )

    resp = await http_client.post("/v1/registro", json=REGISTRO_VALIDO)

    assert resp.status_code == 409
    assert borrado.called, "el BFF debe revertir la credencial creada"


@respx.mock
async def test_registro_devuelve_503_si_core_no_responde(http_client):
    respx.post(f"{IDP}/v1/accounts:signUp").mock(
        return_value=httpx.Response(200, json={"localId": "sub-9"})
    )
    respx.post(f"{CORE}/clientes").mock(return_value=httpx.Response(503))
    respx.post(f"{IDP}/v1/accounts:delete").mock(return_value=httpx.Response(200, json={}))

    resp = await http_client.post("/v1/registro", json=REGISTRO_VALIDO)

    assert resp.status_code == 503
