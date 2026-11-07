"""BITS-383 y BITS-384: consulta de mis siniestros en el BFF web contra el contrato OpenAPI."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from jsonschema import Draft202012Validator

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeSiniestros
from app.domain import (
    BffError,
    ClaimsConsola,
    Conflicto,
    DependenciaNoDisponible,
    RecursoNoEncontrado,
    RolConsola,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.security import ConsolaSessionIssuer
from app.services import SiniestrosService
from tests.conftest import REGISTRO_VALIDO

RUTA = "/v1/cuenta/siniestros"
ARCHIVO = {
    "id": "a-1",
    "clase": "FOTO",
    "capturadoEn": "2026-11-10T15:00:00Z",
    "latitud": 4.65,
    "longitud": -74.06,
    "entregadoEn": "2026-11-10T15:02:00Z",
}


def _siniestro(id_: str, reportado_en: str, **cambios) -> dict:
    base = {
        "id": id_,
        "numero": f"SIN-2026-{id_.upper()[:6]}",
        "polizaId": "poliza-1",
        "tipo": "INCENDIO_TERREMOTO",
        "automatico": False,
        "estado": "EN_ESTUDIO",
        "reportadoEn": reportado_en,
        "actualizadoEn": reportado_en,
        "fechaOcurrencia": "2026-11-10",
        "narracion": "Se incendió la cocina",
        "ultimaEntregaEn": reportado_en,
        "fechaLimite": "2026-12-10T15:02:00Z",
        "evidenciaPedida": None,
        "motivoRechazo": None,
        "pago": None,
        "archivos": [ARCHIVO],
        "evento": None,
    }
    return {**base, **cambios}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, [e.message for e in errores]


@pytest_asyncio.fixture
async def cliente_autenticado(client):
    """(client, headers, clienteId) con una sesión de cliente válida."""
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    cliente_id = registro.json()["cuenta"]["clienteId"]
    return (
        client,
        {"Authorization": f"Bearer {registro.json()['sesion']['accessToken']}"},
        cliente_id,
    )


def _con_siniestros(app) -> FakeSiniestros:
    siniestros = FakeSiniestros()
    app.state.siniestros = SiniestrosService(siniestros)
    return siniestros


# --- lista ---


async def test_la_lista_esta_vacia_si_no_hay_siniestros(app, cliente_autenticado):
    client, headers, _ = cliente_autenticado
    _con_siniestros(app)

    respuesta = await client.get(RUTA, headers=headers)

    assert respuesta.status_code == 200
    assert respuesta.json() == []
    assert respuesta.headers["Cache-Control"] == "no-store"


async def test_la_lista_trae_los_mios_del_mas_reciente_al_mas_antiguo_y_cumple_el_contrato(
    app, cliente_autenticado, openapi_spec
):
    client, headers, cliente_id = cliente_autenticado
    siniestros = _con_siniestros(app)
    siniestros.sembrar(cliente_id, _siniestro("viejo", "2026-11-01T10:00:00Z"))
    siniestros.sembrar(
        cliente_id,
        _siniestro(
            "nuevo",
            "2026-11-10T15:00:00Z",
            tipo="SISMO_PARAMETRICO",
            automatico=True,
            estado="PAGADO",
        ),
    )
    siniestros.sembrar("otro-cliente", _siniestro("ajeno", "2026-11-11T10:00:00Z"))

    respuesta = await client.get(RUTA, headers=headers)

    lista = respuesta.json()
    assert [s["id"] for s in lista] == ["nuevo", "viejo"]
    for item in lista:
        _validar(openapi_spec, "SiniestroResumen", item)
    assert lista[0]["automatico"] is True
    assert "narracion" not in lista[0]  # la lista es el resumen


# --- detalle ---


@pytest.mark.parametrize(
    "cambios",
    [
        {},
        {
            "estado": "FALTA_INFORMACION",
            "fechaLimite": None,
            "evidenciaPedida": {"detalle": "Falta el recibo", "pedidaEn": "2026-11-11T09:00:00Z"},
        },
        {
            "estado": "PAGADO",
            "fechaLimite": None,
            "pago": {
                "pagadoEn": "2026-11-12T10:20:00Z",
                "lineas": [
                    {"beneficiario": "BANCO", "valor": 180000000, "destino": "Bancolombia, abono"},
                    {"beneficiario": "CLIENTE", "valor": 40000000.5, "destino": "Cuenta …4821"},
                ],
            },
        },
        {"estado": "RECHAZADO", "fechaLimite": None, "motivoRechazo": "El evento no está cubierto"},
        {
            "tipo": "SISMO_PARAMETRICO",
            "automatico": True,
            "estado": "PAGADO",
            "fechaLimite": None,
            "archivos": [],
            "narracion": None,
            "evento": {"fecha": "2026-11-18", "municipio": "Bogotá", "magnitud": 6.1},
        },
    ],
)
async def test_el_detalle_cumple_el_contrato_en_cada_estado(
    app, cliente_autenticado, openapi_spec, cambios
):
    client, headers, cliente_id = cliente_autenticado
    siniestros = _con_siniestros(app)
    siniestros.sembrar(cliente_id, _siniestro("s-1", "2026-11-10T15:02:00Z", **cambios))

    respuesta = await client.get(f"{RUTA}/s-1", headers=headers)

    assert respuesta.status_code == 200
    _validar(openapi_spec, "Siniestro", respuesta.json())
    assert respuesta.headers["Cache-Control"] == "no-store"
    for clave, valor in cambios.items():
        assert respuesta.json()[clave] == valor


async def test_un_siniestro_ajeno_o_inexistente_es_404(app, cliente_autenticado):
    client, headers, _ = cliente_autenticado
    siniestros = _con_siniestros(app)
    siniestros.sembrar("otro-cliente", _siniestro("ajeno", "2026-11-10T15:00:00Z"))

    for siniestro_id in ("ajeno", "no-existe"):
        respuesta = await client.get(f"{RUTA}/{siniestro_id}", headers=headers)
        assert respuesta.status_code == 404
        assert respuesta.headers["content-type"].startswith("application/problem+json")


# --- tokens ---


async def test_sin_sesion_o_con_un_token_del_personal_es_401(app, client):
    _con_siniestros(app)
    personal = ConsolaSessionIssuer("test-secret").emitir(
        ClaimsConsola(uid="u", correo="admin@solventa4bits.com", rol=RolConsola.ADMIN)
    )

    assert (await client.get(RUTA)).status_code == 401
    assert (await client.get(f"{RUTA}/s-1")).status_code == 401
    cabecera = {"Authorization": f"Bearer {personal.access_token}"}
    assert (await client.get(RUTA, headers=cabecera)).status_code == 401
    assert (await client.get(f"{RUTA}/s-1", headers=cabecera)).status_code == 401


# --- adaptador de svc-core ---

CORE = "http://core.local"


def _core() -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE, breaker=build_breaker("core", fail_max=9, reset_timeout=5), timeout=0.3, retries=0
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_el_adaptador_lista_y_obtiene_desde_svc_core():
    detalle = _siniestro("s/1", "2026-11-10T15:02:00Z")
    lista = respx.get(f"{CORE}/v1/clientes/c-1/siniestros").mock(
        return_value=httpx.Response(200, json=[detalle])
    )
    uno = respx.get(f"{CORE}/v1/clientes/c-1/siniestros/s%2F1").mock(
        return_value=httpx.Response(200, json=detalle)
    )
    adaptador = _core()
    try:
        assert await adaptador.listar_siniestros("c-1") == [detalle]
        assert await adaptador.obtener_siniestro("c-1", "s/1") == detalle
    finally:
        await adaptador.aclose()

    assert (lista.call_count, uno.call_count) == (1, 1)


@pytest.mark.parametrize(
    ("estado", "esperado"),
    [
        (404, RecursoNoEncontrado),
        (409, Conflicto),
        (503, DependenciaNoDisponible),
        (418, BffError),
    ],
)
@respx.mock
async def test_el_adaptador_traduce_los_errores_de_svc_core(estado, esperado):
    respx.get(f"{CORE}/v1/clientes/c-1/siniestros/s-1").mock(
        return_value=httpx.Response(estado, json={"detail": "x", "codigo": "ALGO"})
    )
    adaptador = _core()
    try:
        with pytest.raises(esperado):
            await adaptador.obtener_siniestro("c-1", "s-1")
    finally:
        await adaptador.aclose()


@respx.mock
async def test_el_adaptador_traduce_el_error_de_la_lista():
    respx.get(f"{CORE}/v1/clientes/c-1/siniestros").mock(
        return_value=httpx.Response(404, json={"detail": "Cliente no encontrado"})
    )
    adaptador = _core()
    try:
        with pytest.raises(RecursoNoEncontrado):
            await adaptador.listar_siniestros("c-1")
    finally:
        await adaptador.aclose()


async def test_el_doble_y_el_servicio_responden_como_svc_core():
    siniestros = FakeSiniestros()
    servicio = SiniestrosService(siniestros)
    siniestros.sembrar("c-1", _siniestro("s-1", "2026-11-10T15:02:00Z"))

    assert [s["id"] for s in await servicio.listar("c-1")] == ["s-1"]
    assert (await servicio.obtener("c-1", "s-1"))["numero"].startswith("SIN-2026-")
    with pytest.raises(RecursoNoEncontrado):
        await servicio.obtener("otro", "s-1")
