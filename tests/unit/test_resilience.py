from __future__ import annotations

import httpx
import pytest
import respx

from app.domain import DependenciaNoDisponible
from app.resilience import ResilientHttpClient, build_breaker

BASE = "http://dep.local"


def _cliente(retries: int = 2, fail_max: int = 5) -> ResilientHttpClient:
    return ResilientHttpClient(
        BASE,
        breaker=build_breaker("dep", fail_max=fail_max, reset_timeout=30),
        timeout=0.2,
        retries=retries,
    )


@respx.mock
async def test_devuelve_respuesta_en_camino_feliz():
    ruta = respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(200, json={"ok": True}))
    http = _cliente()
    try:
        resp = await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert resp.status_code == 200
    assert ruta.call_count == 1


@respx.mock
async def test_reintenta_ante_503_y_luego_tiene_exito():
    ruta = respx.get(f"{BASE}/ping").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200)]
    )
    http = _cliente(retries=2)
    try:
        resp = await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert resp.status_code == 200
    assert ruta.call_count == 3


@respx.mock
async def test_agota_reintentos_y_lanza_dependencia_no_disponible():
    respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(503))
    http = _cliente(retries=1, fail_max=99)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")
    finally:
        await http.aclose()


@respx.mock
async def test_reintenta_ante_error_de_conexion():
    ruta = respx.get(f"{BASE}/ping").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200)]
    )
    http = _cliente(retries=2, fail_max=99)
    try:
        resp = await http.request("GET", "/ping")
    finally:
        await http.aclose()
    assert resp.status_code == 200
    assert ruta.call_count == 2


@respx.mock
async def test_circuito_se_abre_y_falla_rapido():
    ruta = respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(503))
    http = _cliente(retries=2, fail_max=2)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")  # abre el circuito tras 2 fallos
        llamadas_tras_apertura = ruta.call_count

        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/ping")  # circuito abierto: no toca la red
    finally:
        await http.aclose()

    assert llamadas_tras_apertura == 2
    assert ruta.call_count == 2  # la segunda petición no llamó al backend


@respx.mock
async def test_timeout_se_trata_como_transitorio():
    respx.get(f"{BASE}/lento").mock(side_effect=httpx.ReadTimeout("timeout"))
    http = _cliente(retries=1, fail_max=99)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await http.request("GET", "/lento")
    finally:
        await http.aclose()
