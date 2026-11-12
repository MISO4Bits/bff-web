"""BITS-380: ``GET /v1/socio/polizas``, lo que vendió cada socio, con aislamiento entre socios."""

from __future__ import annotations

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator

from app import services
from app.adapters.core_client import CoreClientAdapter
from app.domain import BffError, DependenciaNoDisponible
from app.resilience import ResilientHttpClient, build_breaker
from tests.integration.test_socio_ventas_api import (
    ADMIN,
    CLAVE,
    CLIENTE,
    OTRO_SOCIO,
    SOCIO,
    VENTA,
)

RUTA = "/v1/socio/polizas"


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


@pytest.fixture
def identidad(app):
    proveedor = app.state.deps.identity
    proveedor.sembrar_personal(ADMIN, CLAVE, "ADMIN", "Administrador")
    proveedor.sembrar_personal(SOCIO, CLAVE, "SOCIO", "Aliado")
    proveedor.sembrar_personal(OTRO_SOCIO, CLAVE, "SOCIO", "Otro aliado")
    return proveedor


async def _cabecera(client, correo) -> dict:
    respuesta = await client.post(
        "/v1/backoffice/sesiones", json={"email": correo, "password": CLAVE}
    )
    assert respuesta.status_code == 200
    return {"Authorization": f"Bearer {respuesta.json()['accessToken']}"}


async def _vender(client, cabecera, n: int, **cliente) -> dict:
    """Da de alta un cliente distinto y le vende una póliza; devuelve la póliza."""
    datos = {
        **CLIENTE,
        "numeroDocumento": f"5212{n:04d}",
        "email": f"cliente{n}@example.com",
        **cliente,
    }
    creado = await client.post("/v1/socio/clientes", json=datos, headers=cabecera)
    assert creado.status_code == 201, creado.text
    venta = await client.post(
        "/v1/socio/ventas",
        json={"clienteId": creado.json()["id"], **VENTA},
        headers={**cabecera, "Idempotency-Key": f"venta-{n}-{cabecera['Authorization'][-8:]}"},
    )
    assert venta.status_code == 201, venta.text
    return venta.json()


# --- aislamiento ---


async def test_cada_socio_recibe_solo_lo_que_vendio(client, identidad, openapi_spec):
    a, b = await _cabecera(client, SOCIO), await _cabecera(client, OTRO_SOCIO)
    de_a = [await _vender(client, a, n) for n in (1, 2)]
    de_b = [await _vender(client, b, n) for n in (3,)]

    lista_a = await client.get(RUTA, headers=a)
    lista_b = await client.get(RUTA, headers=b)

    assert lista_a.status_code == lista_b.status_code == 200
    _validar(openapi_spec, "PolizasDelSocio", lista_a.json())
    assert {p["polizaId"] for p in lista_a.json()["items"]} == {p["polizaId"] for p in de_a}
    assert {p["polizaId"] for p in lista_b.json()["items"]} == {p["polizaId"] for p in de_b}


async def test_el_socio_no_puede_pedir_las_de_otro_ni_por_parametro(client, identidad):
    a, b = await _cabecera(client, SOCIO), await _cabecera(client, OTRO_SOCIO)
    await _vender(client, a, 1)
    del_otro = await _vender(client, b, 2)
    uid_del_otro = del_otro["socioId"]

    por_parametro = await client.get(RUTA, params={"socioId": uid_del_otro}, headers=a)

    assert por_parametro.status_code == 200
    ids = {p["polizaId"] for p in por_parametro.json()["items"]}
    assert del_otro["polizaId"] not in ids and len(ids) == 1


async def test_un_socio_sin_ventas_recibe_la_lista_vacia_con_la_hora_de_corte(
    client, identidad, openapi_spec
):
    await _vender(client, await _cabecera(client, OTRO_SOCIO), 1)

    respuesta = await client.get(RUTA, headers=await _cabecera(client, SOCIO))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "PolizasDelSocio", cuerpo)
    assert cuerpo["items"] == [] and cuerpo["incompleta"] is False
    assert cuerpo["consultadoEn"]
    assert respuesta.headers["cache-control"] == "no-store"


# --- contenido ---


async def test_cada_fila_trae_lo_que_pide_la_historia_y_no_filtra_datos_personales(
    client, identidad, openapi_spec
):
    socio = await _cabecera(client, SOCIO)
    poliza = await _vender(client, socio, 1)

    respuesta = await client.get(RUTA, headers=socio)

    [fila] = respuesta.json()["items"]
    _validar(openapi_spec, "PolizaVendida", fila)
    assert fila["polizaId"] == poliza["polizaId"]
    assert fila["estado"] == "EMITIDA"
    assert fila["emitidaEn"] and fila["creadaEn"]
    assert fila["primaTotalMensual"] == poliza["primaTotalMensual"]
    assert fila["cliente"]["nombre"] == "Laura María Gómez"
    assert fila["cliente"]["tipoDocumento"] == "CC"
    assert fila["cliente"]["ultimosDigitosDocumento"] == "0001"
    assert set(fila) == {
        "polizaId",
        "numero",
        "estado",
        "emitidaEn",
        "creadaEn",
        "primaTotalMensual",
        "cliente",
    }
    for dato in (
        "52120001",
        "cliente1@example.com",
        CLIENTE["telefono"],
        CLIENTE["fechaNacimiento"],
    ):
        assert dato not in respuesta.text


async def test_va_de_la_mas_reciente_a_la_mas_antigua(client, identidad):
    socio = await _cabecera(client, SOCIO)
    vendidas = [await _vender(client, socio, n) for n in (1, 2, 3)]

    respuesta = await client.get(RUTA, headers=socio)

    assert [p["polizaId"] for p in respuesta.json()["items"]] == [
        p["polizaId"] for p in reversed(vendidas)
    ]


# --- lista completa ---


async def test_recorre_todas_las_paginas_de_svc_core(client, app, identidad, monkeypatch):
    monkeypatch.setattr(services, "TAMANO_PAGINA_POLIZAS_DEL_SOCIO", 2)
    socio = await _cabecera(client, SOCIO)
    for n in range(1, 6):
        await _vender(client, socio, n)

    respuesta = await client.get(RUTA, headers=socio)

    assert len(respuesta.json()["items"]) == 5
    assert respuesta.json()["incompleta"] is False
    paginas = [c for c in app.state.deps.ventas.llamadas if c[0] == "listar_polizas_del_socio"]
    assert [c[3] for c in paginas] == [None, "2", "4"]
    assert {c[2] for c in paginas} == {2}


async def test_con_mas_polizas_que_el_tope_avisa_que_la_lista_esta_incompleta(
    client, identidad, monkeypatch
):
    monkeypatch.setattr(services, "TAMANO_PAGINA_POLIZAS_DEL_SOCIO", 2)
    monkeypatch.setattr(services, "MAX_PAGINAS_POLIZAS_DEL_SOCIO", 2)
    socio = await _cabecera(client, SOCIO)
    for n in range(1, 6):
        await _vender(client, socio, n)

    respuesta = await client.get(RUTA, headers=socio)

    assert len(respuesta.json()["items"]) == 4
    assert respuesta.json()["incompleta"] is True


async def test_si_el_cliente_ya_no_existe_la_fila_sale_sin_el(client, app, identidad, openapi_spec):
    socio = await _cabecera(client, SOCIO)
    await _vender(client, socio, 1)
    app.state.deps.ventas.clientes.clear()

    respuesta = await client.get(RUTA, headers=socio)

    [fila] = respuesta.json()["items"]
    assert fila["cliente"] is None
    _validar(openapi_spec, "PolizasDelSocio", respuesta.json())


# --- acceso ---


async def test_sin_sesion_es_401_el_admin_es_403_y_el_cliente_no_entra(client, identidad):
    assert (await client.get(RUTA)).status_code == 401

    como_admin = await client.get(RUTA, headers=await _cabecera(client, ADMIN))
    assert como_admin.status_code == 403
    assert como_admin.json()["codigo"] == "ROL_NO_PERMITIDO"

    registro = await client.post(
        "/v1/registro",
        json={
            "email": "ana.rios@example.com",
            "password": "unaClaveSegura1",
            "tipoDocumento": "CC",
            "numeroDocumento": "1032456789",
            "primerNombre": "Ana",
            "primerApellido": "Ríos",
            "fechaNacimiento": "1991-05-20",
            "telefono": "+573001234567",
            "politicaVersion": "2026-01",
            "aceptaTerminos": True,
            "autorizaTratamientoDatos": True,
            "autorizaDatosFinancieros": True,
        },
    )
    token = registro.json()["sesion"]["accessToken"]
    de_cliente = await client.get(RUTA, headers={"Authorization": f"Bearer {token}"})
    assert de_cliente.status_code == 401


async def test_el_log_no_escribe_nombres_ni_documentos(client, identidad, caplog):
    socio = await _cabecera(client, SOCIO)
    await _vender(client, socio, 1)

    with caplog.at_level("INFO"):
        await client.get(RUTA, headers=socio)

    assert "Laura" not in caplog.text and "0001" not in caplog.text
    assert "cliente1@example.com" not in caplog.text


# --- adaptador de svc-core ---

CORE = "http://core.local"


def _core(retries: int = 2) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=99, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_el_adaptador_pide_a_svc_core_las_del_socio_de_la_mas_reciente():
    ruta = respx.get(f"{CORE}/v1/polizas").mock(
        return_value=httpx.Response(200, json={"items": [], "siguienteCursor": None})
    )
    adaptador = _core()
    try:
        await adaptador.listar_polizas_del_socio("uid-1", 100, None)
        await adaptador.listar_polizas_del_socio("uid-1", 100, "cursor-1")
    finally:
        await adaptador.aclose()

    assert dict(ruta.calls[0].request.url.params) == {
        "socioId": "uid-1",
        "orden": "-creadaEn",
        "limite": "100",
    }
    assert ruta.calls[1].request.url.params["cursor"] == "cursor-1"


@respx.mock
@pytest.mark.parametrize(("estado", "error"), [(503, DependenciaNoDisponible), (418, BffError)])
async def test_el_adaptador_traduce_los_errores_de_svc_core(estado, error):
    respx.get(f"{CORE}/v1/polizas").mock(return_value=httpx.Response(estado, json={}))
    adaptador = _core(retries=0)
    try:
        with pytest.raises(error):
            await adaptador.listar_polizas_del_socio("uid-1", 100, None)
    finally:
        await adaptador.aclose()
