"""BITS-101: pólizas del cliente autenticado (BFF web) contra el contrato OpenAPI."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from app.adapters.fakes import FakePolizas
from app.domain import Conflicto, DependenciaNoDisponible, ReglaNegocio, SolicitudInvalida
from app.services import PolizasService

RUTA = "/v1/cuenta/polizas"
CUERPO = {
    "coberturas": ["VIDA", "HOGAR"],
    "cotizacionCreadaEn": "2026-10-24T12:00:00.123456Z",
    "documentosAceptados": [{"tipo": "poliza", "version": "V1"}],
}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, [e.message for e in errores]


def _con_polizas(app, polizas) -> FakePolizas:
    app.state.polizas = PolizasService(polizas)
    return polizas


async def test_solicitar_responde_201_y_cumple_el_contrato(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    polizas = _con_polizas(app, FakePolizas())

    respuesta = await client.post(RUTA, headers=headers, json=CUERPO)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Poliza", cuerpo)
    assert cuerpo["estado"] == "EMITIDA"
    assert cuerpo["numero"].startswith("SOL-VH-")
    assert cuerpo["documento"] == {"estado": "DISPONIBLE"}
    assert respuesta.headers["cache-control"] == "no-store"
    # El BFF pone el clienteId del token y reenvía el cuerpo tal cual (fecha en UTC).
    [(_, cliente_id, enviado)] = polizas.llamadas
    assert cliente_id == (await client.get("/v1/cuenta", headers=headers)).json()["clienteId"]
    assert enviado["coberturas"] == ["VIDA", "HOGAR"]
    assert enviado["cotizacionCreadaEn"].startswith("2026-10-24T12:00:00.123456")
    assert enviado["documentosAceptados"] == [{"tipo": "poliza", "version": "V1"}]


async def test_solicitar_pendiente_devuelve_la_lista_de_pendientes(
    app, cliente_autenticado, openapi_spec
):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas(("CORREO_SIN_CONFIRMAR", "CREDITO_NO_VERIFICADO")))

    respuesta = await client.post(RUTA, headers=headers, json=CUERPO)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Poliza", cuerpo)
    assert cuerpo["estado"] == "PENDIENTE"
    assert cuerpo["numero"] is None
    assert [(p["codigo"], p["tipo"]) for p in cuerpo["pendientes"]] == [
        ("CORREO_SIN_CONFIRMAR", "USUARIO"),
        ("CREDITO_NO_VERIFICADO", "AGENTE"),
    ]
    assert cuerpo["documento"] == {"estado": "NO_DISPONIBLE"}


async def test_repetir_la_solicitud_responde_200(app, cliente_autenticado):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas())
    primera = await client.post(RUTA, headers=headers, json=CUERPO)

    segunda = await client.post(RUTA, headers=headers, json=CUERPO)

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert primera.json()["polizaId"] == segunda.json()["polizaId"]


async def test_listar_filtra_por_estado(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas())
    await client.post(RUTA, headers=headers, json=CUERPO)

    todas = await client.get(RUTA, headers=headers)
    emitidas = await client.get(RUTA, headers=headers, params={"estado": "EMITIDA"})
    rechazadas = await client.get(RUTA, headers=headers, params={"estado": "RECHAZADA"})

    assert [len(r.json()) for r in (todas, emitidas, rechazadas)] == [1, 1, 0]
    _validar(openapi_spec, "Poliza", todas.json()[0])
    assert todas.headers["cache-control"] == "no-store"


async def test_estado_invalido_es_400(app, cliente_autenticado):
    client, headers = cliente_autenticado

    respuesta = await client.get(RUTA, headers=headers, params={"estado": "OTRO"})

    assert respuesta.status_code == 400


async def test_obtener_una_poliza(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas())
    creada = (await client.post(RUTA, headers=headers, json=CUERPO)).json()

    respuesta = await client.get(f"{RUTA}/{creada['polizaId']}", headers=headers)

    assert respuesta.status_code == 200
    _validar(openapi_spec, "Poliza", respuesta.json())
    assert respuesta.json()["polizaId"] == creada["polizaId"]


async def test_una_poliza_ajena_o_inexistente_es_404(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas())

    respuesta = await client.get(f"{RUTA}/no-existe", headers=headers)

    assert respuesta.status_code == 404
    _validar(openapi_spec, "Problema", respuesta.json())


async def test_documento_listo_responde_200(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas())
    creada = (await client.post(RUTA, headers=headers, json=CUERPO)).json()

    respuesta = await client.get(f"{RUTA}/{creada['polizaId']}/documento", headers=headers)

    assert respuesta.status_code == 200
    _validar(openapi_spec, "DocumentoPoliza", respuesta.json())
    assert respuesta.json()["estado"] == "LISTO"
    assert respuesta.json()["url"].endswith(".pdf")


async def test_documento_generandose_responde_202_con_reintento(app, cliente_autenticado):
    client, headers = cliente_autenticado
    polizas = _con_polizas(app, FakePolizas())
    polizas.documento_listo = False
    creada = (await client.post(RUTA, headers=headers, json=CUERPO)).json()

    respuesta = await client.get(f"{RUTA}/{creada['polizaId']}/documento", headers=headers)

    assert respuesta.status_code == 202
    assert respuesta.headers["retry-after"] == "2"
    assert respuesta.json()["estado"] == "GENERANDO"


async def test_documento_de_una_pendiente_es_409_con_codigo(app, cliente_autenticado, openapi_spec):
    client, headers = cliente_autenticado
    _con_polizas(app, FakePolizas(("CORREO_SIN_CONFIRMAR",)))
    creada = (await client.post(RUTA, headers=headers, json=CUERPO)).json()

    respuesta = await client.get(f"{RUTA}/{creada['polizaId']}/documento", headers=headers)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "POLIZA_NO_EMITIDA"
    _validar(openapi_spec, "ProblemaPoliza", respuesta.json())


class _Falla:
    """Puerto que siempre falla con el error indicado (los que svc-core devuelve)."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def solicitar(self, cliente_id, cuerpo):
        raise self._error


@pytest.mark.parametrize(
    ("error", "estado", "codigo"),
    [
        (Conflicto("Cambió", codigo="COTIZACION_CAMBIO"), 409, "COTIZACION_CAMBIO"),
        (Conflicto("Vencida", codigo="COTIZACION_VENCIDA"), 409, "COTIZACION_VENCIDA"),
        (ReglaNegocio("Edad", codigo="FUERA_DE_RANGO"), 422, "FUERA_DE_RANGO"),
        (
            SolicitudInvalida("Sin VIDA", errores=[{"campo": "coberturas", "mensaje": "Sin VIDA"}]),
            400,
            None,
        ),
        (DependenciaNoDisponible("caído", codigo="COTIZACION_NO_DISPONIBLE"), 503, None),
    ],
)
async def test_los_errores_de_svc_core_conservan_su_codigo(
    app, cliente_autenticado, openapi_spec, error, estado, codigo
):
    client, headers = cliente_autenticado
    app.state.polizas = PolizasService(_Falla(error))

    respuesta = await client.post(RUTA, headers=headers, json=CUERPO)

    assert respuesta.status_code == estado
    assert respuesta.headers["content-type"].startswith("application/problem+json")
    if codigo:
        assert respuesta.json()["codigo"] == codigo
        _validar(openapi_spec, "ProblemaPoliza", respuesta.json())


@pytest.mark.parametrize(
    "cambio",
    [
        {"coberturas": ["HOGAR", "OTRA"]},
        {"coberturas": []},
        {"documentosAceptados": []},
        {"documentosAceptados": [{"tipo": "poliza", "version": "uno"}]},
        {"documentosAceptados": [{"tipo": "otro", "version": "V1"}]},
        {"cotizacionCreadaEn": "2026-10-24T12:00:00"},
        {"extra": "no"},
    ],
)
async def test_un_cuerpo_invalido_es_400_y_no_llega_a_svc_core(app, cliente_autenticado, cambio):
    client, headers = cliente_autenticado
    polizas = _con_polizas(app, FakePolizas())

    respuesta = await client.post(RUTA, headers=headers, json={**CUERPO, **cambio})

    assert respuesta.status_code == 400
    assert polizas.llamadas == []


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", RUTA),
        ("get", RUTA),
        ("get", f"{RUTA}/x"),
        ("get", f"{RUTA}/x/documento"),
    ],
)
async def test_sin_sesion_es_401(client, metodo, ruta):
    kwargs = {"json": CUERPO} if metodo == "post" else {}

    respuesta = await getattr(client, metodo)(ruta, **kwargs)

    assert respuesta.status_code == 401
