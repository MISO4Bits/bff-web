"""BITS-377: bandeja de siniestros del ADMIN en el BFF web, con control de acceso por rol."""

from __future__ import annotations

import logging

import httpx
import pytest
import pytest_asyncio
import respx
from jsonschema import Draft202012Validator

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeSiniestros
from app.domain import BffError, Conflicto, RecursoNoEncontrado, ReglaNegocio, SolicitudInvalida
from app.resilience import ResilientHttpClient, build_breaker
from app.services import SiniestrosBackofficeService
from tests.conftest import REGISTRO_VALIDO

ADMIN = "admin@solventa4bits.com"
SOCIO = "socio@solventa4bits.com"
CLAVE = "clave-del-personal-1"
BASE = "/v1/backoffice/siniestros"
ARCHIVO = {
    "id": "a-1",
    "clase": "FOTO",
    "capturadoEn": "2026-11-10T15:00:00Z",
    "latitud": 4.65,
    "longitud": -74.06,
    "entregadoEn": "2026-11-10T15:02:00Z",
}
TRANSFERENCIA = {
    "tipo": "TRANSFERENCIA",
    "banco": "Bancolombia",
    "tipoCuenta": "AHORROS",
    "numeroCuenta": "48210012345",
}


def _interno(id_: str, reportado_en: str, **cambios) -> dict:
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
        "fechaLimite": f"2026-12-{reportado_en[8:10]}T15:02:00Z",
        "evidenciaPedida": None,
        "motivoRechazo": None,
        "pago": None,
        "archivos": [ARCHIVO],
        "evento": None,
        "poliza": {
            "id": "poliza-1",
            "numero": "SOL-VH-2026-ABC123",
            "cobertura": "HOGAR",
            "sumaAsegurada": 150000000,
        },
        "saldoCredito": {
            "valor": 140000000,
            "entidad": "Bancolombia",
            "consultadoEn": "2026-11-10T15:02:00Z",
            "origen": "POLIZA",
        },
        "destinoPago": TRANSFERENCIA,
        "decisiones": [],
    }
    return {**base, **cambios}


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


@pytest.fixture
def siniestros(app) -> FakeSiniestros:
    fake = FakeSiniestros()
    app.state.siniestros_backoffice = SiniestrosBackofficeService(fake)
    return fake


@pytest_asyncio.fixture
async def admin(app, client, siniestros):
    app.state.deps.identity.sembrar_personal(ADMIN, CLAVE, "ADMIN", "Administrador")
    sesion = await client.post("/v1/backoffice/sesiones", json={"email": ADMIN, "password": CLAVE})
    return {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


async def _cabecera_de(app, client, correo: str, rol: str) -> dict:
    app.state.deps.identity.sembrar_personal(correo, CLAVE, rol, "Alguien")
    sesion = await client.post("/v1/backoffice/sesiones", json={"email": correo, "password": CLAVE})
    return {"Authorization": f"Bearer {sesion.json()['accessToken']}"}


# --- control de acceso ---

RUTAS = [
    ("GET", BASE, None),
    ("GET", f"{BASE}/s-1", None),
    ("GET", f"{BASE}/s-1/evidencias/a-1", None),
    ("POST", f"{BASE}/s-1/decisiones", {"decision": "RECHAZAR", "motivo": "No cubierto"}),
]


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), RUTAS)
async def test_solo_el_admin_entra_a_la_bandeja(app, client, siniestros, metodo, ruta, cuerpo):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))
    siniestros.sembrar_archivo("s-1", "a-1", b"foto", "image/jpeg")
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
    assert siniestros.decisiones == ([("s-1", {**cuerpo, "agente": ADMIN})] if cuerpo else [])


# --- bandeja ---


async def test_la_bandeja_va_por_plazo_y_cumple_el_contrato(
    client, siniestros, admin, openapi_spec
):
    siniestros.sembrar("c-2", _interno("tarde", "2026-11-20T10:00:00Z"))
    siniestros.sembrar("c-1", _interno("pronto", "2026-11-05T10:00:00Z"))
    siniestros.sembrar(
        "c-1",
        _interno(
            "pagado",
            "2026-11-25T10:00:00Z",
            estado="PAGADO",
            fechaLimite=None,
            tipo="SISMO_PARAMETRICO",
            automatico=True,
        ),
    )

    respuesta = await client.get(BASE, headers=admin)

    assert respuesta.status_code == 200
    bandeja = respuesta.json()
    assert [s["id"] for s in bandeja] == ["pronto", "tarde", "pagado"]
    for fila in bandeja:
        _validar(openapi_spec, "SiniestroInternoResumen", fila)
    assert bandeja[0]["clienteId"] == "c-1"
    assert "narracion" not in bandeja[0]  # la bandeja es el resumen


async def test_la_bandeja_filtra_y_rechaza_valores_invalidos(client, siniestros, admin):
    siniestros.sembrar("c-1", _interno("uno", "2026-11-05T10:00:00Z"))
    siniestros.sembrar(
        "c-1",
        _interno(
            "auto", "2026-11-06T10:00:00Z", estado="PAGADO", fechaLimite=None, automatico=True
        ),
    )

    en_estudio = await client.get(BASE, params={"estado": "EN_ESTUDIO"}, headers=admin)
    automaticos = await client.get(BASE, params={"automatico": "true"}, headers=admin)
    manuales = await client.get(BASE, params={"automatico": "false"}, headers=admin)
    invalido = await client.get(BASE, params={"estado": "INVENTADO"}, headers=admin)

    assert [s["id"] for s in en_estudio.json()] == ["uno"]
    assert [s["id"] for s in automaticos.json()] == ["auto"]
    assert [s["id"] for s in manuales.json()] == ["uno"]
    assert invalido.status_code == 400


# --- detalle y archivo ---


@pytest.mark.parametrize(
    "cambios",
    [
        {},
        {
            "estado": "FALTA_INFORMACION",
            "fechaLimite": None,
            "evidenciaPedida": {"detalle": "Falta el recibo", "pedidaEn": "2026-11-11T09:00:00Z"},
            "decisiones": [
                {
                    "agente": ADMIN,
                    "decision": "PEDIR_EVIDENCIA",
                    "fecha": "2026-11-11T09:00:00Z",
                    "detalle": "Falta el recibo",
                }
            ],
        },
        {
            "tipo": "INCAPACIDAD_TOTAL",
            "destinoPago": None,
            "poliza": {"id": "p", "numero": None, "cobertura": "VIDA", "sumaAsegurada": 140000000},
        },
        {
            "destinoPago": {
                "tipo": "CHEQUE",
                "oficina": {"id": "o", "banco": "BBVA", "nombre": "N", "direccion": "D"},
            }
        },
    ],
)
async def test_el_detalle_del_admin_cumple_el_contrato(
    client, siniestros, admin, openapi_spec, cambios
):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z", **cambios))

    respuesta = await client.get(f"{BASE}/s-1", headers=admin)

    assert respuesta.status_code == 200
    _validar(openapi_spec, "SiniestroInterno", respuesta.json())
    assert respuesta.json()["clienteId"] == "c-1"


async def test_el_detalle_de_un_siniestro_que_no_existe_es_404(client, siniestros, admin):
    respuesta = await client.get(f"{BASE}/no-existe", headers=admin)

    assert respuesta.status_code == 404
    assert respuesta.headers["content-type"].startswith("application/problem+json")


async def test_el_archivo_llega_tal_cual_con_su_tipo_y_sin_cache(client, siniestros, admin):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))
    siniestros.sembrar_archivo("s-1", "a-1", b"\xff\xd8\xff los bytes", "image/jpeg")

    respuesta = await client.get(f"{BASE}/s-1/evidencias/a-1", headers=admin)

    assert respuesta.status_code == 200
    assert respuesta.content == b"\xff\xd8\xff los bytes"
    assert respuesta.headers["content-type"] == "image/jpeg"
    assert respuesta.headers["cache-control"] == "no-store"
    assert respuesta.headers["content-disposition"] == "inline"


async def test_un_archivo_o_un_siniestro_inexistente_es_404(client, siniestros, admin):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    assert (await client.get(f"{BASE}/s-1/evidencias/no-existe", headers=admin)).status_code == 404
    assert (await client.get(f"{BASE}/no-existe/evidencias/a-1", headers=admin)).status_code == 404


# --- decisiones ---


async def test_el_agente_es_el_correo_de_la_sesion_y_no_se_puede_escribir_en_el_cuerpo(
    client, siniestros, admin
):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    suplantado = await client.post(
        f"{BASE}/s-1/decisiones",
        headers=admin,
        json={"decision": "RECHAZAR", "motivo": "No cubierto", "agente": "otro@solventa4bits.com"},
    )
    verdadero = await client.post(
        f"{BASE}/s-1/decisiones",
        headers=admin,
        json={"decision": "RECHAZAR", "motivo": "No cubierto"},
    )

    assert suplantado.status_code == 400
    assert verdadero.status_code == 200
    assert [d["agente"] for d in verdadero.json()["decisiones"]] == [ADMIN]
    assert siniestros.decisiones == [
        ("s-1", {"decision": "RECHAZAR", "motivo": "No cubierto", "agente": ADMIN})
    ]


async def test_pagar_incapacidad_total_deja_una_linea_al_banco(
    client, siniestros, admin, openapi_spec
):
    siniestros.sembrar(
        "c-1",
        _interno(
            "s-1",
            "2026-11-10T15:02:00Z",
            tipo="INCAPACIDAD_TOTAL",
            destinoPago=None,
            poliza={"id": "p", "numero": None, "cobertura": "VIDA", "sumaAsegurada": 140000000},
        ),
    )

    respuesta = await client.post(
        f"{BASE}/s-1/decisiones", headers=admin, json={"decision": "PAGAR"}
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "SiniestroInterno", cuerpo)
    assert cuerpo["estado"] == "PAGADO"
    assert cuerpo["fechaLimite"] is None
    assert [(x["beneficiario"], x["valor"]) for x in cuerpo["pago"]["lineas"]] == [
        ("BANCO", 140000000)
    ]


async def test_perdida_total_paga_al_banco_hasta_el_saldo_y_el_resto_al_cliente(
    client, siniestros, admin
):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    respuesta = await client.post(
        f"{BASE}/s-1/decisiones",
        headers=admin,
        json={"decision": "PAGAR", "dano": "PERDIDA_TOTAL", "valor": 150000000},
    )

    lineas = respuesta.json()["pago"]["lineas"]
    assert [(x["beneficiario"], x["valor"]) for x in lineas] == [
        ("BANCO", 140000000),
        ("CLIENTE", 10000000),
    ]


async def test_pedir_evidencia_deja_lo_pedido_y_pagar_dos_veces_deja_una_orden(
    client, siniestros, admin
):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))
    siniestros.sembrar("c-1", _interno("s-2", "2026-11-11T15:02:00Z"))

    pedido = await client.post(
        f"{BASE}/s-1/decisiones",
        headers=admin,
        json={"decision": "PEDIR_EVIDENCIA", "detalle": "  Falta el recibo  "},
    )
    primero = await client.post(
        f"{BASE}/s-2/decisiones",
        headers=admin,
        json={"decision": "PAGAR", "dano": "REPARABLE", "valor": 40000000},
    )
    segundo = await client.post(
        f"{BASE}/s-2/decisiones",
        headers=admin,
        json={"decision": "PAGAR", "dano": "PERDIDA_TOTAL", "valor": 150000000},
    )

    assert pedido.json()["estado"] == "FALTA_INFORMACION"
    assert pedido.json()["evidenciaPedida"]["detalle"] == "Falta el recibo"
    assert segundo.json()["pago"] == primero.json()["pago"]
    assert len(segundo.json()["decisiones"]) == 1


async def test_el_valor_sobre_la_suma_asegurada_es_422_con_su_codigo(
    client, siniestros, admin, openapi_spec
):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    respuesta = await client.post(
        f"{BASE}/s-1/decisiones",
        headers=admin,
        json={"decision": "PAGAR", "dano": "REPARABLE", "valor": 150000001},
    )

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "VALOR_SUPERA_SUMA_ASEGURADA"
    _validar(openapi_spec, "ProblemaSiniestro", respuesta.json())


async def test_decidir_en_falta_informacion_es_409_con_su_codigo(
    client, siniestros, admin, openapi_spec
):
    siniestros.sembrar(
        "c-1",
        _interno("s-1", "2026-11-10T15:02:00Z", estado="FALTA_INFORMACION", fechaLimite=None),
    )

    respuesta = await client.post(
        f"{BASE}/s-1/decisiones", headers=admin, json={"decision": "RECHAZAR", "motivo": "No"}
    )

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "ESTADO_NO_PERMITE"
    _validar(openapi_spec, "ProblemaSiniestro", respuesta.json())


async def test_decidir_un_siniestro_que_no_existe_es_404(client, siniestros, admin):
    respuesta = await client.post(
        f"{BASE}/no-existe/decisiones", headers=admin, json={"decision": "PAGAR"}
    )

    assert respuesta.status_code == 404


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"decision": "REGALAR"},
        {"decision": "PAGAR", "dano": "ENORME", "valor": 5},
        {"decision": "PAGAR", "dano": "REPARABLE", "valor": 0},
        {"decision": "PAGAR", "dano": "REPARABLE", "valor": -1},
        {"decision": "RECHAZAR", "motivo": "x" * 2001},
        {"decision": "RECHAZAR", "motivo": "x", "nota": "interna"},
    ],
)
async def test_cuerpos_invalidos_son_400_y_no_llegan_a_svc_core(client, siniestros, admin, cuerpo):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    respuesta = await client.post(f"{BASE}/s-1/decisiones", headers=admin, json=cuerpo)

    assert respuesta.status_code == 400
    assert siniestros.decisiones == []


async def test_el_doble_exige_lo_mismo_que_svc_core(client, siniestros, admin):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    sin_detalle = await client.post(
        f"{BASE}/s-1/decisiones", headers=admin, json={"decision": "PEDIR_EVIDENCIA"}
    )
    sin_motivo = await client.post(
        f"{BASE}/s-1/decisiones", headers=admin, json={"decision": "RECHAZAR"}
    )
    sin_dano = await client.post(
        f"{BASE}/s-1/decisiones", headers=admin, json={"decision": "PAGAR", "valor": 100}
    )

    assert (sin_detalle.status_code, sin_motivo.status_code, sin_dano.status_code) == (
        400,
        400,
        400,
    )


async def test_el_log_no_incluye_el_correo_del_agente(client, siniestros, admin, caplog):
    siniestros.sembrar("c-1", _interno("s-1", "2026-11-10T15:02:00Z"))

    with caplog.at_level(logging.INFO):
        await client.post(
            f"{BASE}/s-1/decisiones", headers=admin, json={"decision": "RECHAZAR", "motivo": "No"}
        )

    assert ADMIN not in caplog.text


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
async def test_el_adaptador_pide_la_bandeja_con_los_filtros_que_lleguen():
    ruta = respx.get(f"{CORE}/v1/siniestros").mock(return_value=httpx.Response(200, json=[]))
    adaptador = _core()
    try:
        await adaptador.bandeja_siniestros(None, None)
        await adaptador.bandeja_siniestros("EN_ESTUDIO", None)
        await adaptador.bandeja_siniestros(None, True)
        await adaptador.bandeja_siniestros("PAGADO", False)
    finally:
        await adaptador.aclose()

    consultas = [str(c.request.url.query, "utf-8") for c in ruta.calls]
    assert consultas == [
        "",
        "estado=EN_ESTUDIO",
        "automatico=true",
        "estado=PAGADO&automatico=false",
    ]


@respx.mock
async def test_el_adaptador_lee_el_detalle_y_el_archivo_con_el_id_escapado():
    detalle = _interno("s/1", "2026-11-10T15:02:00Z")
    respx.get(f"{CORE}/v1/siniestros/s%2F1").mock(return_value=httpx.Response(200, json=detalle))
    archivo = respx.get(f"{CORE}/v1/siniestros/s%2F1/evidencias/a%2F1").mock(
        return_value=httpx.Response(200, content=b"foto", headers={"content-type": "image/png"})
    )
    adaptador = _core()
    try:
        assert await adaptador.detalle_siniestro_interno("s/1") == detalle
        assert await adaptador.archivo_de_siniestro("s/1", "a/1") == (b"foto", "image/png")
    finally:
        await adaptador.aclose()

    assert archivo.call_count == 1


@respx.mock
async def test_el_adaptador_asume_un_tipo_binario_si_svc_core_no_lo_dice():
    respx.get(f"{CORE}/v1/siniestros/s-1/evidencias/a-1").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    adaptador = _core()
    try:
        _, tipo = await adaptador.archivo_de_siniestro("s-1", "a-1")
    finally:
        await adaptador.aclose()

    assert tipo.startswith(("application/octet-stream", "text/"))


@respx.mock
async def test_la_decision_va_como_json_y_no_se_reintenta():
    ruta = respx.post(f"{CORE}/v1/siniestros/s-1/decisiones").mock(
        return_value=httpx.Response(200, json=_interno("s-1", "2026-11-10T15:02:00Z"))
    )
    adaptador = _core(retries=3)
    cuerpo = {"decision": "RECHAZAR", "motivo": "No cubierto", "agente": ADMIN}
    try:
        resultado = await adaptador.decidir_siniestro("s-1", cuerpo)
    finally:
        await adaptador.aclose()

    assert resultado["id"] == "s-1"
    assert ruta.call_count == 1
    import json

    assert json.loads(ruta.calls.last.request.content) == cuerpo


@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado"),
    [
        (409, {"detail": "no admite", "codigo": "ESTADO_NO_PERMITE"}, Conflicto),
        (422, {"detail": "supera", "codigo": "VALOR_SUPERA_SUMA_ASEGURADA"}, ReglaNegocio),
        (404, {"detail": "no existe"}, RecursoNoEncontrado),
        (
            400,
            {"detail": "falta", "errores": [{"campo": "valor", "mensaje": "x"}]},
            SolicitudInvalida,
        ),
        (418, {}, BffError),
    ],
)
@respx.mock
async def test_la_decision_traduce_los_errores_conservando_el_codigo(estado, cuerpo, esperado):
    respx.post(f"{CORE}/v1/siniestros/s-1/decisiones").mock(
        return_value=httpx.Response(estado, json=cuerpo)
    )
    adaptador = _core()
    try:
        with pytest.raises(esperado) as error:
            await adaptador.decidir_siniestro("s-1", {"decision": "PAGAR", "agente": ADMIN})
    finally:
        await adaptador.aclose()

    assert getattr(error.value, "codigo", None) == cuerpo.get("codigo")
    assert getattr(error.value, "errores", None) == cuerpo.get("errores")
