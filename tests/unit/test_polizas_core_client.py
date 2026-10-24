"""BITS-101: cliente de svc-core para las pólizas (contra respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.domain import (
    BffError,
    Conflicto,
    DependenciaNoDisponible,
    Prohibido,
    RecursoNoEncontrado,
    ReglaNegocio,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient, build_breaker

CORE = "http://core.local"
POLIZAS = f"{CORE}/v1/clientes/c1/polizas"
CUERPO = {
    "coberturas": ["VIDA"],
    "cotizacionCreadaEn": "2026-10-24T12:00:00Z",
    "documentosAceptados": [{"tipo": "poliza", "version": "V1"}],
}


def _core(*, retries: int = 2) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=99, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_solicitar_envia_el_cuerpo_a_la_ruta_del_cliente_y_distingue_201_de_200():
    ruta = respx.post(POLIZAS).mock(
        side_effect=[
            httpx.Response(201, json={"polizaId": "p1"}),
            httpx.Response(200, json={"polizaId": "p1"}),
        ]
    )

    primera = await _core().solicitar("c1", CUERPO)
    segunda = await _core().solicitar("c1", CUERPO)

    assert primera == ({"polizaId": "p1"}, True)
    assert segunda == ({"polizaId": "p1"}, False)
    assert ruta.calls[0].request.content == httpx.Request("POST", POLIZAS, json=CUERPO).content


@respx.mock
async def test_solicitar_no_se_reintenta_ante_un_fallo():
    ruta = respx.post(POLIZAS).mock(return_value=httpx.Response(503))

    with pytest.raises(DependenciaNoDisponible):
        await _core(retries=3).solicitar("c1", CUERPO)

    assert ruta.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    ("estado", "error", "codigo"),
    [
        (400, SolicitudInvalida, None),
        (403, Prohibido, "CLIENTE_NO_ACTIVO"),
        (404, RecursoNoEncontrado, None),
        (409, Conflicto, "COTIZACION_CAMBIO"),
        (422, ReglaNegocio, "FUERA_DE_RANGO"),
    ],
)
async def test_los_errores_de_core_conservan_detalle_codigo_y_campos(estado, error, codigo):
    respx.post(POLIZAS).mock(
        return_value=httpx.Response(
            estado,
            json={
                "detail": "el detalle",
                "codigo": codigo,
                "errores": [{"campo": "coberturas", "mensaje": "mal"}],
            },
            headers={"content-type": "application/problem+json"},
        )
    )

    with pytest.raises(error) as capturado:
        await _core().solicitar("c1", CUERPO)

    assert capturado.value.detail == "el detalle"
    assert capturado.value.codigo == codigo
    assert capturado.value.errores == [{"campo": "coberturas", "mensaje": "mal"}]


@respx.mock
async def test_un_error_sin_cuerpo_json_igual_se_traduce():
    respx.post(POLIZAS).mock(return_value=httpx.Response(409, text="conflicto"))

    with pytest.raises(Conflicto) as capturado:
        await _core().solicitar("c1", CUERPO)

    assert capturado.value.codigo is None


@respx.mock
async def test_un_codigo_inesperado_es_error_generico():
    respx.post(POLIZAS).mock(return_value=httpx.Response(418))

    with pytest.raises(BffError, match="418"):
        await _core().solicitar("c1", CUERPO)


@respx.mock
async def test_listar_pasa_el_filtro_de_estado():
    ruta = respx.get(POLIZAS).mock(return_value=httpx.Response(200, json=[{"polizaId": "p1"}]))

    todas = await _core().listar("c1", None)
    filtradas = await _core().listar("c1", "EMITIDA")

    assert todas == filtradas == [{"polizaId": "p1"}]
    assert "estado" not in ruta.calls[0].request.url.params
    assert ruta.calls[1].request.url.params["estado"] == "EMITIDA"


@respx.mock
async def test_obtener_y_documento():
    respx.get(f"{POLIZAS}/p1").mock(return_value=httpx.Response(200, json={"polizaId": "p1"}))
    respx.get(f"{POLIZAS}/p1/documento").mock(
        side_effect=[
            httpx.Response(200, json={"estado": "LISTO", "url": "http://d"}),
            httpx.Response(202, json={"estado": "GENERANDO"}),
        ]
    )

    assert await _core().obtener("c1", "p1") == {"polizaId": "p1"}
    assert await _core().documento("c1", "p1") == ({"estado": "LISTO", "url": "http://d"}, True)
    assert await _core().documento("c1", "p1") == ({"estado": "GENERANDO"}, False)


@respx.mock
async def test_una_poliza_inexistente_es_404():
    respx.get(f"{POLIZAS}/nada").mock(return_value=httpx.Response(404, json={"detail": "no"}))

    with pytest.raises(RecursoNoEncontrado):
        await _core().obtener("c1", "nada")
