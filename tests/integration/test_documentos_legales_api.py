"""API pública de documentos legales: modo fake y modo http (svc-productos mockeado)."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.config import Settings

PRODUCTOS = "http://productos.test"
PARAMS = {"mercado": "CO", "idioma": "es-CO"}
INMUTABLE = "public, max-age=31536000, immutable"


# --- modo fake (standalone) ---


async def test_es_publico_y_lista_los_tres_documentos_sin_token(client):
    resp = await client.get("/v1/documentos-legales", params=PARAMS)
    assert resp.status_code == 200
    assert [d["tipo"] for d in resp.json()] == ["terminos", "open-data", "open-finance"]


async def test_la_respuesta_usa_los_nombres_del_contrato(client):
    (primero, *_) = (await client.get("/v1/documentos-legales", params=PARAMS)).json()
    assert set(primero) == {
        "tipo",
        "version",
        "titulo",
        "subtitulo",
        "baseLegal",
        "contenido",
        "notaPie",
    }
    assert primero["version"] == "V1"


async def test_el_idioma_es_opcional(client):
    resp = await client.get("/v1/documentos-legales", params={"mercado": "CO"})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


async def test_version_exacta_lleva_cache_inmutable(client):
    resp = await client.get("/v1/documentos-legales/open-data/versiones/V1", params=PARAMS)
    assert resp.status_code == 200
    assert resp.json()["baseLegal"] == "Ley 1581 de 2012"
    assert resp.headers["cache-control"] == INMUTABLE


async def test_version_inexistente_es_404_problem_details_sin_cache(client):
    resp = await client.get("/v1/documentos-legales/terminos/versiones/V9", params=PARAMS)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert "cache-control" not in resp.headers


@pytest.mark.parametrize(
    ("ruta", "params"),
    [
        ("/v1/documentos-legales", {}),
        ("/v1/documentos-legales", {"mercado": "MX"}),
        ("/v1/documentos-legales", {"mercado": "CO", "idioma": "espanol"}),
        ("/v1/documentos-legales/otro/versiones/V1", PARAMS),
        ("/v1/documentos-legales/terminos/versiones/1", PARAMS),
        ("/v1/documentos-legales/terminos/versiones/V0", PARAMS),
        ("/v1/documentos-legales/terminos/versiones/V1", {}),
    ],
)
async def test_parametros_invalidos_son_400_con_detalle_por_campo(client, ruta, params):
    resp = await client.get(ruta, params=params)
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["errores"]


async def test_no_hay_escritura_de_documentos_legales(client):
    for metodo in ("post", "put", "patch", "delete"):
        resp = await getattr(client, metodo)("/v1/documentos-legales", params=PARAMS)
        assert resp.status_code == 405


# --- modo http: el BFF habla con svc-productos ---


@pytest_asyncio.fixture
async def http_client():
    settings = Settings(
        adapters="http", productos_base_url=PRODUCTOS, session_secret="t", http_retries=1
    )
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _doc(**cambios) -> dict:
    base = {
        "tipo": "terminos",
        "version": "V1",
        "titulo": "Términos",
        "baseLegal": "Ley 527 de 1999",
        "contenido": "<p>x</p>",
    }
    return {**base, **cambios}


@respx.mock
async def test_el_bff_reenvia_la_lista_de_svc_productos_sin_transformarla(http_client):
    ruta = respx.get(f"{PRODUCTOS}/documentos-legales").mock(
        return_value=httpx.Response(200, json=[_doc(), _doc(tipo="open-data", subtitulo="s")])
    )
    resp = await http_client.get("/v1/documentos-legales", params=PARAMS)
    assert resp.status_code == 200
    assert resp.json() == [_doc(), _doc(tipo="open-data", subtitulo="s")]
    assert dict(ruta.calls.last.request.url.params) == PARAMS


@respx.mock
async def test_el_bff_reenvia_una_version_exacta(http_client):
    respx.get(f"{PRODUCTOS}/documentos-legales/terminos/versiones/V2").mock(
        return_value=httpx.Response(200, json=_doc(version="V2"))
    )
    resp = await http_client.get("/v1/documentos-legales/terminos/versiones/V2", params=PARAMS)
    assert resp.status_code == 200
    assert resp.json()["version"] == "V2"
    assert resp.headers["cache-control"] == INMUTABLE


@respx.mock
async def test_404_de_svc_productos_llega_como_404(http_client):
    respx.get(f"{PRODUCTOS}/documentos-legales/terminos/versiones/V9").mock(
        return_value=httpx.Response(404, json={"detail": "no existe"})
    )
    resp = await http_client.get("/v1/documentos-legales/terminos/versiones/V9", params=PARAMS)
    assert resp.status_code == 404


@respx.mock
async def test_si_svc_productos_no_responde_el_bff_devuelve_503(http_client):
    respx.get(f"{PRODUCTOS}/documentos-legales").mock(side_effect=httpx.ConnectTimeout("lento"))
    resp = await http_client.get("/v1/documentos-legales", params=PARAMS)
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
