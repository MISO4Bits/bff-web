"""BITS-378: cliente de svc-core para la venta directa del socio (contra respx) y su servicio."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeEntidadesFinancieras, FakeVentasSocio
from app.domain import (
    Conflicto,
    DependenciaNoDisponible,
    Prohibido,
    RecursoNoEncontrado,
    ReglaNegocio,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.services import CreditosHipotecariosService, VentasSocioService, vista_de_cliente

CORE = "http://core.local"


def _core(*, retries: int = 2) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=99, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_buscar_pasa_solo_los_parametros_que_llegan():
    ruta = respx.get(f"{CORE}/v1/clientes").mock(
        return_value=httpx.Response(200, json={"id": "c1"})
    )

    await _core().buscar_cliente(correo="a@b.co", tipo_documento=None, numero_documento=None)
    await _core().buscar_cliente(correo=None, tipo_documento="CC", numero_documento="1234")

    assert dict(ruta.calls[0].request.url.params) == {"correo": "a@b.co"}
    assert dict(ruta.calls[1].request.url.params) == {
        "tipoDocumento": "CC",
        "numeroDocumento": "1234",
    }


@respx.mock
@pytest.mark.parametrize(
    ("estado", "error"),
    [(400, SolicitudInvalida), (404, RecursoNoEncontrado), (503, DependenciaNoDisponible)],
)
async def test_buscar_traduce_los_errores(estado, error):
    respx.get(f"{CORE}/v1/clientes").mock(return_value=httpx.Response(estado, json={}))

    with pytest.raises(error):
        await _core(retries=0).buscar_cliente(
            correo="a@b.co", tipo_documento=None, numero_documento=None
        )


@respx.mock
async def test_crear_cliente_envia_la_clave_y_no_reintenta():
    ruta = respx.post(f"{CORE}/v1/clientes/asistidos").mock(
        return_value=httpx.Response(503, json={})
    )

    with pytest.raises(DependenciaNoDisponible):
        await _core(retries=3).crear_cliente_asistido({"email": "a@b.co"}, "clave-1")

    assert ruta.call_count == 1
    assert ruta.calls[0].request.headers["Idempotency-Key"] == "clave-1"


@respx.mock
@pytest.mark.parametrize("campo", ["correo", "documento"])
async def test_crear_cliente_en_conflicto_dice_el_campo(campo):
    respx.post(f"{CORE}/v1/clientes/asistidos").mock(
        return_value=httpx.Response(409, json={"errores": [{"campo": campo, "mensaje": "x"}]})
    )

    with pytest.raises(Conflicto) as error:
        await _core().crear_cliente_asistido({}, None)

    assert error.value.errores[0]["campo"] == campo


@respx.mock
async def test_crear_cliente_devuelve_el_cuerpo_de_svc_core():
    respx.post(f"{CORE}/v1/clientes/asistidos").mock(
        return_value=httpx.Response(201, json={"id": "c1"})
    )

    assert await _core().crear_cliente_asistido({}, None) == {"id": "c1"}


@respx.mock
async def test_el_precio_traduce_el_rango_de_edad_y_conserva_el_codigo():
    respx.post(f"{CORE}/v1/polizas/precio").mock(
        return_value=httpx.Response(422, json={"detail": "x", "codigo": "FUERA_DE_RANGO"})
    )

    with pytest.raises(ReglaNegocio) as error:
        await _core().calcular_precio({"clienteId": "c1"})

    assert error.value.codigo == "FUERA_DE_RANGO"


@respx.mock
async def test_vender_envia_la_clave_y_no_se_reintenta():
    ruta = respx.post(f"{CORE}/v1/polizas").mock(return_value=httpx.Response(503, json={}))

    with pytest.raises(DependenciaNoDisponible):
        await _core(retries=3).vender({"socioId": "s1"}, "venta-1")

    assert ruta.call_count == 1
    assert ruta.calls[0].request.headers["Idempotency-Key"] == "venta-1"
    assert json.loads(ruta.calls[0].request.content) == {"socioId": "s1"}


@respx.mock
@pytest.mark.parametrize(
    ("estado", "error"),
    [(400, SolicitudInvalida), (403, Prohibido), (404, RecursoNoEncontrado), (409, Conflicto)],
)
async def test_vender_traduce_los_errores(estado, error):
    respx.post(f"{CORE}/v1/polizas").mock(return_value=httpx.Response(estado, json={"codigo": "X"}))

    with pytest.raises(error):
        await _core().vender({}, "k")


# --- servicio ---


def test_la_vista_del_cliente_no_filtra_datos_personales():
    vista = vista_de_cliente(
        {
            "id": "c1",
            "primerNombre": "Laura",
            "segundoNombre": None,
            "primerApellido": "Gómez",
            "segundoApellido": "Pérez",
            "tipoDocumento": "CE",
            "numeroDocumento": "9876",
            "estado": "ACTIVO",
            "email": "laura@example.com",
            "telefono": "+573001112233",
            "fechaNacimiento": "1988-03-14",
        }
    )

    assert vista == {
        "id": "c1",
        "nombre": "Laura Gómez Pérez",
        "tipoDocumento": "CE",
        "ultimosDigitosDocumento": "9876",
        "estado": "ACTIVO",
    }


def _servicio() -> VentasSocioService:
    return VentasSocioService(
        FakeVentasSocio(),
        CreditosHipotecariosService(None, FakeEntidadesFinancieras()),  # type: ignore[arg-type]
    )


async def test_buscar_exige_un_criterio_completo_y_unico():
    servicio = _servicio()

    with pytest.raises(SolicitudInvalida):
        await servicio.buscar_cliente(correo=None, tipo_documento=None, numero_documento=None)
    with pytest.raises(SolicitudInvalida):
        await servicio.buscar_cliente(correo=None, tipo_documento="CC", numero_documento=None)
    with pytest.raises(SolicitudInvalida):
        await servicio.buscar_cliente(correo="a@b.co", tipo_documento="CC", numero_documento="1234")


async def test_el_socio_de_la_venta_lo_pone_el_servicio():
    servicio = _servicio()
    cliente = await servicio.crear_cliente(
        {
            "tipoDocumento": "CC",
            "numeroDocumento": "52123456",
            "primerNombre": "Laura",
            "primerApellido": "Gómez",
            "fechaNacimiento": "1988-03-14",
            "email": "l@example.com",
            "telefono": "+573101112233",
        },
        None,
    )

    poliza = await servicio.vender(
        "uid-del-token",
        {
            "clienteId": cliente["id"],
            "coberturas": ["VIDA"],
            "valorCredito": 150000000,
            "plazoRestanteMeses": 240,
            "entidadAcreedora": "Bancolombia",
            "cuotaMensual": 1000000,
            "direccion": "Calle 1",
            "municipio": "Cali",
            "socioId": "intento-de-falsificar",
        },
        "k1",
    )

    assert poliza["socioId"] == "uid-del-token"
