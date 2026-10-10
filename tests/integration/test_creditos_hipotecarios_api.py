"""GET /v1/creditos-hipotecarios: modo fake y modo http (Perfilamiento y Productos mockeados)."""

from __future__ import annotations

import httpx
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.config import Settings

PERFILAMIENTO = "http://perfilamiento.test"
PRODUCTOS = "http://productos.test"

ENTIDADES = [
    {"id": "bancolombia", "nombre": "Bancolombia", "alias": ["BANCOLOMBIA S.A."]},
    {"id": "davivienda", "nombre": "Davivienda", "alias": ["DAVIVIENDA S.A."]},
]


# --- modo fake (standalone) ---


async def test_exige_sesion(client):
    resp = await client.get("/v1/creditos-hipotecarios")

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_devuelve_la_hipoteca_y_los_bancos_del_mercado(cliente_autenticado):
    client, headers = cliente_autenticado

    resp = await client.get("/v1/creditos-hipotecarios", headers=headers)

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    cuerpo = resp.json()
    assert cuerpo["estado"] == "DISPONIBLE"
    assert cuerpo["origen"] == "OPEN_FINANCE"
    assert cuerpo["fechaConsulta"]
    assert cuerpo["creditos"] == [
        {
            "entidadId": "bbva-colombia",
            "entidadNombre": "BBVA Colombia",
            "valorCredito": 200000000,
            "saldoInsoluto": 160000000,
            "plazoRestanteMeses": 180,
            "cuotaMensual": 2100000,
        }
    ]
    assert [e["id"] for e in cuerpo["entidades"]] == [
        "bancolombia",
        "davivienda",
        "banco-de-bogota",
        "bbva-colombia",
    ]
    assert set(cuerpo["entidades"][0]) == {"id", "nombre"}  # el alias es asunto interno


async def test_el_mercado_es_opcional_y_solo_acepta_colombia(cliente_autenticado):
    client, headers = cliente_autenticado

    assert (
        await client.get("/v1/creditos-hipotecarios", params={"mercado": "CO"}, headers=headers)
    ).status_code == 200
    invalido = await client.get(
        "/v1/creditos-hipotecarios", params={"mercado": "MX"}, headers=headers
    )
    assert invalido.status_code == 400
    assert invalido.json()["errores"]


# --- modo http: el BFF habla con svc-perfilamiento y svc-productos ---


@pytest_asyncio.fixture
async def http_client():
    settings = Settings(
        adapters="http",
        perfilamiento_base_url=PERFILAMIENTO,
        productos_base_url=PRODUCTOS,
        session_secret="t",
        http_retries=1,
    )
    app = create_app(settings)
    token = app.state.sessions.emitir(sub="s", cliente_id="cli-1", email="a@b.com").access_token
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


def _perfilamiento(**cuerpo) -> dict:
    return {"estado": "DISPONIBLE", "hipotecas": [], "origen": "OPEN_FINANCE", **cuerpo}


HIPOTECA = {
    "entidadAcreedora": "BANCOLOMBIA S.A.",
    "valorCredito": 380000000,
    "saldoInsoluto": 320000000,
    "plazoRestanteMeses": 180,
    "cuotaMensual": 3100000,
}


@respx.mock
async def test_lee_las_hipotecas_del_cliente_de_la_sesion_y_no_de_la_peticion(http_client):
    ruta = respx.get(f"{PERFILAMIENTO}/clientes/cli-1/creditos-hipotecarios").mock(
        return_value=httpx.Response(
            200, json=_perfilamiento(hipotecas=[HIPOTECA], fechaConsulta="2026-10-09T12:00:00Z")
        )
    )
    respx.get(f"{PRODUCTOS}/entidades-financieras").mock(
        return_value=httpx.Response(200, json=ENTIDADES)
    )

    resp = await http_client.get("/v1/creditos-hipotecarios")

    assert resp.status_code == 200
    assert ruta.called
    cuerpo = resp.json()
    assert cuerpo["creditos"][0]["entidadId"] == "bancolombia"
    assert cuerpo["creditos"][0]["saldoInsoluto"] == 320000000
    assert cuerpo["fechaConsulta"] == "2026-10-09T12:00:00Z"
    assert cuerpo["entidades"] == [
        {"id": "bancolombia", "nombre": "Bancolombia"},
        {"id": "davivienda", "nombre": "Davivienda"},
    ]


@respx.mock
async def test_sin_consentimiento_responde_la_lista_de_bancos(http_client):
    respx.get(f"{PERFILAMIENTO}/clientes/cli-1/creditos-hipotecarios").mock(
        return_value=httpx.Response(200, json=_perfilamiento(estado="SIN_CONSENTIMIENTO"))
    )
    respx.get(f"{PRODUCTOS}/entidades-financieras").mock(
        return_value=httpx.Response(200, json=ENTIDADES)
    )

    resp = await http_client.get("/v1/creditos-hipotecarios")

    assert resp.json()["estado"] == "SIN_CONSENTIMIENTO"
    assert resp.json()["creditos"] == []
    assert "fechaConsulta" not in resp.json()
    assert len(resp.json()["entidades"]) == 2


@respx.mock
async def test_si_perfilamiento_cae_el_cliente_ve_no_disponible_y_puede_escribir_los_datos(
    http_client,
):
    respx.get(f"{PERFILAMIENTO}/clientes/cli-1/creditos-hipotecarios").mock(
        return_value=httpx.Response(503)
    )
    respx.get(f"{PRODUCTOS}/entidades-financieras").mock(
        return_value=httpx.Response(200, json=ENTIDADES)
    )

    resp = await http_client.get("/v1/creditos-hipotecarios")

    assert resp.status_code == 200
    assert resp.json()["estado"] == "NO_DISPONIBLE"
    assert len(resp.json()["entidades"]) == 2


@respx.mock
async def test_si_ninguno_de_los_dos_responde_es_503(http_client):
    respx.get(f"{PERFILAMIENTO}/clientes/cli-1/creditos-hipotecarios").mock(
        return_value=httpx.Response(503)
    )
    respx.get(f"{PRODUCTOS}/entidades-financieras").mock(return_value=httpx.Response(503))

    resp = await http_client.get("/v1/creditos-hipotecarios")

    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")


@respx.mock
async def test_error_de_perfilamiento_distinto_de_503_tambien_degrada(http_client):
    respx.get(f"{PERFILAMIENTO}/clientes/cli-1/creditos-hipotecarios").mock(
        return_value=httpx.Response(500)
    )
    respx.get(f"{PRODUCTOS}/entidades-financieras").mock(
        return_value=httpx.Response(200, json=ENTIDADES)
    )

    resp = await http_client.get("/v1/creditos-hipotecarios")

    assert resp.json()["estado"] == "NO_DISPONIBLE"


@respx.mock
async def test_respuesta_invalida_de_perfilamiento_degrada(http_client):
    respx.get(f"{PERFILAMIENTO}/clientes/cli-1/creditos-hipotecarios").mock(
        return_value=httpx.Response(400, json={"detail": "x"})
    )
    respx.get(f"{PRODUCTOS}/entidades-financieras").mock(
        return_value=httpx.Response(200, json=ENTIDADES)
    )

    resp = await http_client.get("/v1/creditos-hipotecarios")

    assert resp.json()["estado"] == "NO_DISPONIBLE"
