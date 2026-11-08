"""BITS-376: solicitudes en revisión del ADMIN en el BFF web, con control de acceso por rol."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import pytest_asyncio
import respx
from jsonschema import Draft202012Validator

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeSolicitudes
from app.domain import BffError, Conflicto, RecursoNoEncontrado, ReglaNegocio, SolicitudInvalida
from app.resilience import ResilientHttpClient, build_breaker
from app.services import SolicitudesBackofficeService
from tests.conftest import REGISTRO_VALIDO

ADMIN = "admin@solventa4bits.com"
SOCIO = "socio@solventa4bits.com"
CLAVE = "clave-del-personal-1"
BASE = "/v1/backoffice/solicitudes"
P1 = "11111111-1111-4111-8111-111111111111"
FRENTE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SELFIE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
AJENA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

CLIENTE = {
    "id": "c-1",
    "tipoDocumento": "CC",
    "numeroDocumento": "1020340001",
    "primerNombre": "Sofía",
    "segundoNombre": None,
    "primerApellido": "Restrepo",
    "segundoApellido": "Gil",
    "email": "sofia@example.com",
    "telefono": "+573001112233",
    "fechaNacimiento": "1990-05-02",
}


def _pendiente(codigo: str) -> dict:
    return {
        "codigo": codigo,
        "tipo": "AGENTE",
        "mensaje": "Un agente está revisando.",
        "detalle": None,
    }


def _solicitud(poliza_id: str, creada_en: str, **cambios) -> dict:
    base = {
        "polizaId": poliza_id,
        "clienteId": "c-1",
        "numero": None,
        "estado": "PENDIENTE",
        "producto": "VIDA_HIPOTECARIO",
        "creadaEn": creada_en,
        "emitidaEn": None,
        "solicitudVigenteHasta": "2099-01-01T00:00:00Z",
        "vigencia": None,
        "coberturas": [
            {
                "codigo": "VIDA",
                "obligatoria": True,
                "sumaAsegurada": 150000000,
                "primaNeta": 49100,
                "iva": 0,
                "primaTotal": 49100,
            }
        ],
        "primaTotalMensual": 49100,
        "moneda": "COP",
        "datosCredito": {
            "valorCredito": 380000000,
            "plazoRestanteMeses": 180,
            "entidadAcreedora": "Bancolombia",
            "saldoInsoluto": 320000000,
        },
        "origenDatos": "DECLARADO",
        "cotizacionCreadaEn": creada_en,
        "pendientes": [_pendiente("EVIDENCIA_EN_REVISION"), _pendiente("CREDITO_NO_VERIFICADO")],
        "documento": {"estado": "NO_DISPONIBLE"},
        "documentosAceptados": [],
        "evidencias": [
            {"tipo": "cedula_frente", "estado": "REVISION_MANUAL", "evidenciaId": FRENTE},
            {"tipo": "selfie", "estado": "REVISION_MANUAL", "evidenciaId": SELFIE},
        ],
        "decisiones": [],
    }
    return {**base, **cambios}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


@pytest.fixture
def solicitudes(app) -> FakeSolicitudes:
    fake = FakeSolicitudes()
    app.state.solicitudes_backoffice = SolicitudesBackofficeService(fake)
    return fake


@pytest_asyncio.fixture
async def admin(app, client, solicitudes):
    app.state.deps.identity.sembrar_personal(ADMIN, CLAVE, "ADMIN", "Administrador")
    sesion = await client.post("/v1/backoffice/sesiones", json={"email": ADMIN, "password": CLAVE})
    return {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


async def _cabecera_de(app, client, correo: str, rol: str) -> dict:
    app.state.deps.identity.sembrar_personal(correo, CLAVE, rol, "Alguien")
    sesion = await client.post("/v1/backoffice/sesiones", json={"email": correo, "password": CLAVE})
    return {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


def _sembrar(solicitudes: FakeSolicitudes, poliza_id: str = P1, **cambios) -> dict:
    solicitud = solicitudes.sembrar(
        _solicitud(poliza_id, "2026-11-10T15:00:00Z", **cambios), CLIENTE
    )
    solicitudes.sembrar_imagen(FRENTE, b"frente", "image/jpeg")
    solicitudes.sembrar_imagen(SELFIE, b"selfie", "image/png")
    solicitudes.sembrar_imagen(AJENA, b"ajena", "image/jpeg")
    return solicitud


# --- control de acceso ---

RUTAS = [
    ("GET", BASE, None),
    ("GET", f"{BASE}/{P1}", None),
    ("GET", f"{BASE}/{P1}/evidencias/{FRENTE}", None),
    (
        "POST",
        f"{BASE}/{P1}/decisiones",
        {"decision": "APROBAR", "pendientes": ["EVIDENCIA_EN_REVISION"]},
    ),
]


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), RUTAS)
async def test_solo_el_admin_entra_a_las_solicitudes(
    app, client, solicitudes, metodo, ruta, cuerpo
):
    _sembrar(solicitudes)
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    de_cliente = {"Authorization": f"Bearer {registro.json()['sesion']['accessToken']}"}
    de_socio = await _cabecera_de(app, client, SOCIO, "SOCIO")
    de_admin = await _cabecera_de(app, client, ADMIN, "ADMIN")

    async def llamar(cabecera):
        return await client.request(metodo, ruta, headers=cabecera, json=cuerpo)

    assert (await llamar({})).status_code == 401
    assert (await llamar(de_cliente)).status_code == 401
    prohibido = await llamar(de_socio)
    assert prohibido.status_code == 403
    assert prohibido.json()["codigo"] == "ROL_NO_PERMITIDO"
    assert (await llamar(de_admin)).status_code == 200
    assert solicitudes.decisiones == (
        [(P1, {"decision": "APROBAR", "agente": ADMIN, "pendientes": cuerpo["pendientes"]})]
        if cuerpo
        else []
    )


# --- bandeja ---


async def test_la_bandeja_va_de_la_mas_antigua_y_cumple_el_contrato(
    client, solicitudes, admin, openapi_spec
):
    solicitudes.sembrar(_solicitud("reciente", "2026-11-12T10:00:00Z"), CLIENTE)
    solicitudes.sembrar(_solicitud("antigua", "2026-11-01T10:00:00Z"), CLIENTE)

    respuesta = await client.get(BASE, headers=admin)

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    _validar(openapi_spec, "PaginaSolicitudes", pagina)
    assert [s["polizaId"] for s in pagina["items"]] == ["antigua", "reciente"]
    assert pagina["siguienteCursor"] is None
    assert "cliente" not in pagina["items"][0]  # la bandeja es el resumen


async def test_la_bandeja_solo_trae_lo_que_espera_a_una_persona(client, solicitudes, admin):
    solicitudes.sembrar(_solicitud("en-revision", "2026-11-01T10:00:00Z"), CLIENTE)
    solicitudes.sembrar(
        _solicitud(
            "del-usuario",
            "2026-11-02T10:00:00Z",
            pendientes=[{**_pendiente("EVIDENCIA_FALTANTE"), "tipo": "USUARIO"}],
        ),
        CLIENTE,
    )
    solicitudes.sembrar(
        _solicitud("emitida", "2026-11-03T10:00:00Z", estado="EMITIDA", pendientes=[]), CLIENTE
    )

    respuesta = await client.get(BASE, headers=admin)

    assert [s["polizaId"] for s in respuesta.json()["items"]] == ["en-revision"]


async def test_la_bandeja_pagina_con_limite_y_cursor(client, solicitudes, admin):
    for dia in (1, 2, 3):
        solicitudes.sembrar(_solicitud(f"p{dia}", f"2026-11-0{dia}T10:00:00Z"), CLIENTE)

    primera = (await client.get(BASE, params={"limite": 2}, headers=admin)).json()
    segunda = (
        await client.get(
            BASE, params={"limite": 2, "cursor": primera["siguienteCursor"]}, headers=admin
        )
    ).json()

    assert [s["polizaId"] for s in primera["items"]] == ["p1", "p2"]
    assert [s["polizaId"] for s in segunda["items"]] == ["p3"]
    assert segunda["siguienteCursor"] is None


@pytest.mark.parametrize("limite", ["0", "101", "x"])
async def test_un_limite_invalido_es_400(client, solicitudes, admin, limite):
    respuesta = await client.get(BASE, params={"limite": limite}, headers=admin)

    assert respuesta.status_code == 400


# --- detalle ---


async def test_el_detalle_trae_al_cliente_sin_correo_ni_telefono_y_cumple_el_contrato(
    client, solicitudes, admin, openapi_spec
):
    _sembrar(solicitudes)

    respuesta = await client.get(f"{BASE}/{P1}", headers=admin)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "SolicitudDetalle", cuerpo)
    assert cuerpo["cliente"] == {
        "id": "c-1",
        "nombre": "Sofía Restrepo Gil",
        "tipoDocumento": "CC",
        "numeroDocumento": "1020340001",
    }
    assert [e["evidenciaId"] for e in cuerpo["evidencias"]] == [FRENTE, SELFIE]
    assert "sofia@example.com" not in respuesta.text
    assert "+573001112233" not in respuesta.text


async def test_el_detalle_de_una_solicitud_que_no_existe_es_404(client, solicitudes, admin):
    respuesta = await client.get(f"{BASE}/no-existe", headers=admin)

    assert respuesta.status_code == 404


async def test_si_no_se_alcanza_al_cliente_el_detalle_no_sale_a_medias(client, solicitudes, admin):
    solicitudes.sembrar(_solicitud(P1, "2026-11-10T15:00:00Z"), CLIENTE)
    solicitudes._clientes.clear()

    respuesta = await client.get(f"{BASE}/{P1}", headers=admin)

    assert respuesta.status_code == 404
    assert "polizaId" not in respuesta.text


# --- imagen ---


async def test_la_imagen_sale_con_su_tipo_y_sin_cache(client, solicitudes, admin):
    _sembrar(solicitudes)

    frente = await client.get(f"{BASE}/{P1}/evidencias/{FRENTE}", headers=admin)
    selfie = await client.get(f"{BASE}/{P1}/evidencias/{SELFIE}", headers=admin)

    assert frente.status_code == 200
    assert frente.content == b"frente"
    assert frente.headers["content-type"] == "image/jpeg"
    assert frente.headers["cache-control"] == "no-store"
    assert frente.headers["content-disposition"] == "inline"
    assert selfie.headers["content-type"] == "image/png"


async def test_una_imagen_que_no_es_de_la_solicitud_es_404_aunque_exista(
    client, solicitudes, admin
):
    _sembrar(solicitudes)

    respuesta = await client.get(f"{BASE}/{P1}/evidencias/{AJENA}", headers=admin)

    assert respuesta.status_code == 404
    assert b"ajena" not in respuesta.content


# --- decisiones ---


async def test_aprobar_un_pendiente_deja_el_otro_y_queda_a_nombre_de_la_sesion(
    client, solicitudes, admin, openapi_spec
):
    _sembrar(solicitudes)

    respuesta = await client.post(
        f"{BASE}/{P1}/decisiones",
        headers=admin,
        json={"decision": "APROBAR", "pendientes": ["EVIDENCIA_EN_REVISION"]},
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Solicitud", cuerpo)
    assert cuerpo["estado"] == "PENDIENTE"
    assert [p["codigo"] for p in cuerpo["pendientes"]] == ["CREDITO_NO_VERIFICADO"]
    assert cuerpo["decisiones"][0]["agente"] == ADMIN
    assert cuerpo["decisiones"][0]["pendientes"] == ["EVIDENCIA_EN_REVISION"]


async def test_aprobar_el_ultimo_pendiente_emite_la_poliza(client, solicitudes, admin):
    _sembrar(solicitudes)

    respuesta = await client.post(
        f"{BASE}/{P1}/decisiones",
        headers=admin,
        json={
            "decision": "APROBAR",
            "pendientes": ["EVIDENCIA_EN_REVISION", "CREDITO_NO_VERIFICADO"],
        },
    )

    assert respuesta.json()["estado"] == "EMITIDA"
    assert respuesta.json()["numero"]
    assert (await client.get(BASE, headers=admin)).json()["items"] == []


async def test_rechazar_lleva_solo_el_motivo_y_el_cliente_ve_su_texto(
    client, solicitudes, admin, openapi_spec
):
    _sembrar(solicitudes)

    respuesta = await client.post(
        f"{BASE}/{P1}/decisiones",
        headers=admin,
        json={"decision": "RECHAZAR", "motivo": "EVIDENCIA_EN_REVISION"},
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Solicitud", cuerpo)
    assert cuerpo["estado"] == "RECHAZADA"
    assert [m["codigo"] for m in cuerpo["rechazo"]["motivos"]] == ["EVIDENCIA_EN_REVISION"]
    assert solicitudes.decisiones == [
        (
            P1,
            {
                "decision": "RECHAZAR",
                "agente": ADMIN,
                "pendientes": ["EVIDENCIA_EN_REVISION"],
            },
        )
    ]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"decision": "APROBAR"},
        {"decision": "APROBAR", "pendientes": []},
        {"decision": "APROBAR", "pendientes": ["EVIDENCIA_EN_REVISION", "EVIDENCIA_EN_REVISION"]},
        {"decision": "APROBAR", "pendientes": ["EVIDENCIA_FALTANTE"]},
        {
            "decision": "APROBAR",
            "pendientes": ["EVIDENCIA_EN_REVISION"],
            "motivo": "CREDITO_NO_VERIFICADO",
        },
        {"decision": "RECHAZAR"},
        {"decision": "RECHAZAR", "motivo": "EVIDENCIA_FALTANTE"},
        {
            "decision": "RECHAZAR",
            "motivo": "EVIDENCIA_EN_REVISION",
            "pendientes": ["CREDITO_NO_VERIFICADO"],
        },
        {"decision": "RECHAZAR", "pendientes": ["EVIDENCIA_EN_REVISION", "CREDITO_NO_VERIFICADO"]},
        {"decision": "RECHAZAR", "motivo": "EVIDENCIA_EN_REVISION", "nota": "no me convence"},
        {
            "decision": "APROBAR",
            "pendientes": ["EVIDENCIA_EN_REVISION"],
            "agente": "otra@persona.co",
        },
        {"decision": "ANULAR", "motivo": "EVIDENCIA_EN_REVISION"},
    ],
)
async def test_cuerpos_invalidos_son_400_y_no_llegan_a_svc_core(client, solicitudes, admin, cuerpo):
    _sembrar(solicitudes)

    respuesta = await client.post(f"{BASE}/{P1}/decisiones", headers=admin, json=cuerpo)

    assert respuesta.status_code == 400
    assert solicitudes.decisiones == []


async def test_una_solicitud_ya_decidida_es_409(client, solicitudes, admin):
    _sembrar(solicitudes)
    cuerpo = {"decision": "RECHAZAR", "motivo": "EVIDENCIA_EN_REVISION"}
    await client.post(f"{BASE}/{P1}/decisiones", headers=admin, json=cuerpo)

    respuesta = await client.post(f"{BASE}/{P1}/decisiones", headers=admin, json=cuerpo)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "POLIZA_NO_PENDIENTE"


async def test_una_solicitud_vencida_es_409(client, solicitudes, admin):
    _sembrar(solicitudes, solicitudVigenteHasta="2020-01-01T00:00:00Z")

    respuesta = await client.post(
        f"{BASE}/{P1}/decisiones",
        headers=admin,
        json={"decision": "RECHAZAR", "motivo": "EVIDENCIA_EN_REVISION"},
    )

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "SOLICITUD_VENCIDA"


async def test_un_pendiente_que_la_solicitud_no_tiene_es_422(client, solicitudes, admin):
    _sembrar(solicitudes, pendientes=[_pendiente("CREDITO_NO_VERIFICADO")])

    respuesta = await client.post(
        f"{BASE}/{P1}/decisiones",
        headers=admin,
        json={"decision": "APROBAR", "pendientes": ["EVIDENCIA_EN_REVISION"]},
    )

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "PENDIENTE_NO_APLICA"


async def test_decidir_una_solicitud_que_no_existe_es_404(client, solicitudes, admin):
    respuesta = await client.post(
        f"{BASE}/no-existe/decisiones",
        headers=admin,
        json={"decision": "RECHAZAR", "motivo": "EVIDENCIA_EN_REVISION"},
    )

    assert respuesta.status_code == 404


async def test_el_log_no_incluye_el_correo_del_agente_ni_el_del_cliente(
    client, solicitudes, admin, caplog
):
    _sembrar(solicitudes)

    with caplog.at_level(logging.INFO):
        await client.get(f"{BASE}/{P1}", headers=admin)
        await client.post(
            f"{BASE}/{P1}/decisiones",
            headers=admin,
            json={"decision": "RECHAZAR", "motivo": "EVIDENCIA_EN_REVISION"},
        )

    assert ADMIN not in caplog.text
    assert "sofia@example.com" not in caplog.text


# --- adaptador de svc-core ---

CORE = "http://core.local"


def _core(retries: int = 2) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=9, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_el_adaptador_pide_la_bandeja_de_agente_de_la_mas_antigua():
    ruta = respx.get(f"{CORE}/v1/polizas").mock(
        return_value=httpx.Response(200, json={"items": [], "siguienteCursor": None})
    )
    adaptador = _core()
    try:
        await adaptador.listar_solicitudes(10, None)
        await adaptador.listar_solicitudes(10, "abc")
    finally:
        await adaptador.aclose()

    assert dict(ruta.calls[0].request.url.params) == {
        "estado": "PENDIENTE",
        "tipoPendiente": "AGENTE",
        "orden": "creadaEn",
        "limite": "10",
    }
    assert ruta.calls[1].request.url.params["cursor"] == "abc"


@respx.mock
async def test_el_adaptador_lee_la_solicitud_el_cliente_y_la_imagen():
    respx.get(f"{CORE}/v1/polizas/p-1").mock(
        return_value=httpx.Response(200, json={"polizaId": "p-1"})
    )
    respx.get(f"{CORE}/v1/clientes/c-1").mock(return_value=httpx.Response(200, json={"id": "c-1"}))
    respx.get(f"{CORE}/v1/evidencias-identidad/e%2F1/imagen").mock(
        return_value=httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
    )
    respx.get(f"{CORE}/v1/evidencias-identidad/sin-tipo/imagen").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    adaptador = _core()
    try:
        poliza = await adaptador.obtener_solicitud("p-1")
        cliente = await adaptador.obtener_cliente_de_solicitud("c-1")
        contenido, tipo = await adaptador.imagen_de_evidencia("e/1")
        _, sin_tipo = await adaptador.imagen_de_evidencia("sin-tipo")
    finally:
        await adaptador.aclose()

    assert poliza == {"polizaId": "p-1"}
    assert cliente == {"id": "c-1"}
    assert (contenido, tipo) == (b"png", "image/png")
    assert sin_tipo.startswith(("application/octet-stream", "text/"))


@respx.mock
async def test_la_decision_va_como_json_y_no_se_reintenta():
    ruta = respx.post(f"{CORE}/v1/polizas/p-1/decisiones").mock(
        return_value=httpx.Response(200, json={"polizaId": "p-1"})
    )
    adaptador = _core(retries=3)
    cuerpo = {"decision": "APROBAR", "agente": ADMIN, "pendientes": ["EVIDENCIA_EN_REVISION"]}
    try:
        resultado = await adaptador.decidir_solicitud("p-1", cuerpo)
    finally:
        await adaptador.aclose()

    assert resultado == {"polizaId": "p-1"}
    assert ruta.call_count == 1
    assert json.loads(ruta.calls.last.request.content) == cuerpo


@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado"),
    [
        (409, {"detail": "venció", "codigo": "SOLICITUD_VENCIDA"}, Conflicto),
        (422, {"detail": "no aplica", "codigo": "PENDIENTE_NO_APLICA"}, ReglaNegocio),
        (404, {"detail": "no existe"}, RecursoNoEncontrado),
        (400, {"detail": "falta", "errores": [{"campo": "x", "mensaje": "y"}]}, SolicitudInvalida),
        (418, {}, BffError),
    ],
)
@respx.mock
async def test_la_decision_traduce_los_errores_conservando_el_codigo(estado, cuerpo, esperado):
    respx.post(f"{CORE}/v1/polizas/p-1/decisiones").mock(
        return_value=httpx.Response(estado, json=cuerpo)
    )
    adaptador = _core()
    try:
        with pytest.raises(esperado) as error:
            await adaptador.decidir_solicitud("p-1", {"decision": "APROBAR", "agente": ADMIN})
    finally:
        await adaptador.aclose()

    assert getattr(error.value, "codigo", None) == cuerpo.get("codigo")
