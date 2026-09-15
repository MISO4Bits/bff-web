from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.adapters.cotizacion_client import CotizacionClientAdapter
from app.adapters.factory import build_dependencias
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.config import Settings
from app.domain import (
    BffError,
    Conflicto,
    CotizacionInput,
    CuestionarioHabitosInput,
    DatosCreditoInput,
    NoAutorizado,
    RecursoNoEncontrado,
    RegistroInput,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient, build_breaker

IDP = "http://idp.local"
CORE = "http://core.local"
COTIZACION = "http://cotizacion.local"

ENTRADA_COTIZACION = CotizacionInput(
    datos_credito=DatosCreditoInput(
        valor_credito=Decimal("120000000"),
        plazo_meses=120,
        edad=35,
        entidad_acreedora="Banco Solventa",
        saldo_insoluto=Decimal("100000000"),
    ),
    cuestionario_habitos=CuestionarioHabitosInput(
        consume_tabaco=False,
        actividad_fisica="REGULAR",
        condiciones_preexistentes=False,
        dependientes_economicos=1,
    ),
)

DATOS = RegistroInput(
    email="ana@example.com",
    password="claveSegura12",
    tipo_documento="CC",
    numero_documento="123456",
    primer_nombre="Ana",
    primer_apellido="Ríos",
    fecha_nacimiento=date(1990, 1, 1),
    politica_version="v1",
)


def _idp() -> IdentityPlatformAdapter:
    http = ResilientHttpClient(
        IDP, breaker=build_breaker("idp", fail_max=9, reset_timeout=5), timeout=0.3, retries=0
    )
    return IdentityPlatformAdapter(http, "k")


def _core() -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE, breaker=build_breaker("core", fail_max=9, reset_timeout=5), timeout=0.3, retries=0
    )
    return CoreClientAdapter(http)


def _cotizacion() -> CotizacionClientAdapter:
    http = ResilientHttpClient(
        COTIZACION,
        breaker=build_breaker("cotizacion", fail_max=9, reset_timeout=5),
        timeout=0.3,
        retries=0,
    )
    return CotizacionClientAdapter(http)


def _cotizacion_json(**overrides) -> dict:
    base = {
        "id": "cot-1",
        "estado": "VIGENTE",
        "producto": "VIDA_HIPOTECARIO",
        "oferta": {
            "primaMensual": 45000.0,
            "primaBaseMensual": 45000.0,
            "sumaAsegurada": 100000000.0,
            "coberturaMeses": 120,
            "moneda": "COP",
            "personalizado": False,
            "fuentesNoDisponibles": ["perfilamiento"],
        },
        "vigenciaCotizacion": {
            "desde": "2026-09-13T00:00:00Z",
            "hasta": "2026-10-13T00:00:00Z",
        },
        "creadaEn": "2026-09-13T00:00:00Z",
    }
    base.update(overrides)
    return base


@respx.mock
async def test_identity_registrar_ok():
    respx.post(f"{IDP}/v1/accounts:signUp").mock(
        return_value=httpx.Response(200, json={"localId": "sub-1", "idToken": "t"})
    )
    adapter = _idp()
    try:
        assert await adapter.registrar("a@b.com", "x" * 10) == "sub-1"
    finally:
        await adapter.aclose()


@respx.mock
async def test_identity_registrar_email_existente():
    respx.post(f"{IDP}/v1/accounts:signUp").mock(
        return_value=httpx.Response(400, json={"error": {"message": "EMAIL_EXISTS"}})
    )
    adapter = _idp()
    try:
        with pytest.raises(Conflicto):
            await adapter.registrar("a@b.com", "x" * 10)
    finally:
        await adapter.aclose()


@respx.mock
async def test_identity_autenticar_malas_credenciales():
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(400, json={"error": {"message": "INVALID_PASSWORD"}})
    )
    adapter = _idp()
    try:
        with pytest.raises(NoAutorizado):
            await adapter.autenticar("a@b.com", "mala")
    finally:
        await adapter.aclose()


@respx.mock
async def test_identity_eliminar_y_error_5xx():
    respx.post(f"{IDP}/v1/accounts:delete").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{IDP}/v1/accounts:signUp").mock(return_value=httpx.Response(500, json={}))
    adapter = _idp()
    try:
        await adapter.eliminar("sub-1")
        with pytest.raises(BffError):
            await adapter.registrar("a@b.com", "x" * 10)
    finally:
        await adapter.aclose()


@respx.mock
async def test_core_registrar_cliente_ok_y_conflicto():
    ruta = respx.post(f"{CORE}/clientes")
    ruta.mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "c1",
                "primerNombre": "Ana",
                "primerApellido": "Ríos",
                "email": "ana@example.com",
                "estado": "ACTIVO",
            },
        )
    )
    adapter = _core()
    try:
        cliente = await adapter.registrar_cliente("sub-1", DATOS)
        assert cliente.id == "c1"

        ruta.mock(return_value=httpx.Response(409, json={"detail": "existe"}))
        with pytest.raises(Conflicto):
            await adapter.registrar_cliente("sub-1", DATOS)
    finally:
        await adapter.aclose()


@respx.mock
async def test_core_obtener_cliente_404():
    respx.get(f"{CORE}/clientes/c1").mock(return_value=httpx.Response(404, json={}))
    adapter = _core()
    try:
        with pytest.raises(RecursoNoEncontrado):
            await adapter.obtener_cliente("c1")
    finally:
        await adapter.aclose()


@respx.mock
async def test_core_mapea_errores_de_negocio_y_no_esperados():
    adapter = _core()
    try:
        respx.post(f"{CORE}/clientes").mock(
            return_value=httpx.Response(422, json={"detail": "regla"})
        )
        with pytest.raises(SolicitudInvalida):
            await adapter.registrar_cliente("sub-1", DATOS)

        respx.post(f"{CORE}/clientes/c1/consentimientos").mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(RecursoNoEncontrado):
            await adapter.otorgar_consentimiento("c1", "OPEN_DATA", "v1", "WEB")

        respx.post(f"{CORE}/clientes/c2/consentimientos").mock(
            return_value=httpx.Response(400, json={"detail": "mal"})
        )
        with pytest.raises(SolicitudInvalida):
            await adapter.otorgar_consentimiento("c2", "OPEN_DATA", "v1", "WEB")

        respx.get(f"{CORE}/clientes/c9/consentimientos").mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(RecursoNoEncontrado):
            await adapter.listar_consentimientos("c9")

        respx.delete(f"{CORE}/clientes/c9/consentimientos/OPEN_DATA").mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(RecursoNoEncontrado):
            await adapter.revocar_consentimiento("c9", "OPEN_DATA")

        respx.get(f"{CORE}/clientes/cX").mock(return_value=httpx.Response(500, json={}))
        with pytest.raises(BffError):
            await adapter.obtener_cliente("cX")
    finally:
        await adapter.aclose()


@respx.mock
async def test_identity_registrar_400_no_email_exists():
    respx.post(f"{IDP}/v1/accounts:signUp").mock(
        return_value=httpx.Response(400, json={"error": {"message": "WEAK_PASSWORD"}})
    )
    adapter = _idp()
    try:
        with pytest.raises(SolicitudInvalida):
            await adapter.registrar("a@b.com", "x" * 10)
    finally:
        await adapter.aclose()


@respx.mock
async def test_core_consentimientos_ciclo():
    respx.post(f"{CORE}/clientes/c1/consentimientos").mock(
        return_value=httpx.Response(
            201, json={"scope": "OPEN_FINANCE", "estado": "OTORGADO", "version": 1}
        )
    )
    respx.get(f"{CORE}/clientes/c1/consentimientos").mock(
        return_value=httpx.Response(
            200,
            json=[{"scope": "OPEN_FINANCE", "estado": "OTORGADO", "version": 1}],
        )
    )
    respx.delete(f"{CORE}/clientes/c1/consentimientos/OPEN_FINANCE").mock(
        return_value=httpx.Response(204)
    )
    adapter = _core()
    try:
        vista = await adapter.otorgar_consentimiento("c1", "OPEN_FINANCE", "v1", "WEB")
        assert vista.vigente is True
        assert (await adapter.listar_consentimientos("c1"))[0].scope == "OPEN_FINANCE"
        await adapter.revocar_consentimiento("c1", "OPEN_FINANCE")
    finally:
        await adapter.aclose()


@respx.mock
async def test_core_buscar_por_identidad():
    respx.get(f"{CORE}/clientes").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c1",
                "primerNombre": "Ana",
                "primerApellido": "Ríos",
                "email": "ana@example.com",
                "estado": "ACTIVO",
            },
        )
    )
    adapter = _core()
    try:
        assert (await adapter.buscar_cliente_por_identidad("sub-1")).id == "c1"
    finally:
        await adapter.aclose()


async def test_factory_fake_y_http_y_error():
    fake = build_dependencias(Settings(adapters="fake"))
    assert type(fake.identity).__name__ == "FakeIdentityProvider"
    assert type(fake.cotizacion).__name__ == "FakeCotizacion"
    await fake.aclose()

    http = build_dependencias(Settings(adapters="http"))
    assert type(http.core).__name__ == "CoreClientAdapter"
    assert type(http.cotizacion).__name__ == "CotizacionClientAdapter"
    await http.aclose()

    with pytest.raises(ValueError):
        build_dependencias(Settings(adapters="otro"))


@respx.mock
async def test_cotizacion_crear_ok_envia_cliente_id_y_decimales_como_texto():
    ruta = respx.post(f"{COTIZACION}/cotizaciones").mock(
        return_value=httpx.Response(201, json=_cotizacion_json())
    )
    adapter = _cotizacion()
    try:
        cotizacion = await adapter.crear_cotizacion("cliente-1", ENTRADA_COTIZACION)
        assert cotizacion.id == "cot-1"
        assert cotizacion.oferta.prima_mensual == 45000.0
        assert cotizacion.perfil_riesgo is None

        enviado = ruta.calls.last.request
        assert enviado.headers["x-cliente-id"] == "cliente-1"
        import json as _json

        cuerpo = _json.loads(enviado.content)
        assert cuerpo["datosCredito"]["valorCredito"] == "120000000"
        assert cuerpo["datosCredito"]["saldoInsoluto"] == "100000000"
    finally:
        await adapter.aclose()


@respx.mock
async def test_cotizacion_crear_con_perfil_riesgo():
    payload = _cotizacion_json(
        oferta={
            "primaMensual": 63000.0,
            "primaBaseMensual": 45000.0,
            "sumaAsegurada": 100000000.0,
            "coberturaMeses": 120,
            "moneda": "COP",
            "personalizado": True,
            "fuentesNoDisponibles": [],
        },
        perfilRiesgo={
            "nivelRiesgo": "ALTO",
            "factores": [
                {"descripcion": "fumador", "efecto": "NEGATIVO", "pesoRelativo": 0.4},
            ],
        },
    )
    respx.post(f"{COTIZACION}/cotizaciones").mock(return_value=httpx.Response(201, json=payload))
    adapter = _cotizacion()
    try:
        cotizacion = await adapter.crear_cotizacion("cliente-1", ENTRADA_COTIZACION)
        assert cotizacion.oferta.personalizado is True
        assert cotizacion.perfil_riesgo.nivel_riesgo == "ALTO"
        assert cotizacion.perfil_riesgo.factores[0].descripcion == "fumador"
    finally:
        await adapter.aclose()


@respx.mock
async def test_cotizacion_crear_solicitud_invalida():
    respx.post(f"{COTIZACION}/cotizaciones").mock(
        return_value=httpx.Response(422, json={"detail": "regla de negocio"})
    )
    adapter = _cotizacion()
    try:
        with pytest.raises(SolicitudInvalida):
            await adapter.crear_cotizacion("cliente-1", ENTRADA_COTIZACION)
    finally:
        await adapter.aclose()


@respx.mock
async def test_cotizacion_obtener_ok_y_404():
    respx.get(f"{COTIZACION}/cotizaciones/cot-1").mock(
        return_value=httpx.Response(200, json=_cotizacion_json())
    )
    adapter = _cotizacion()
    try:
        cotizacion = await adapter.obtener_cotizacion("cliente-1", "cot-1")
        assert cotizacion.id == "cot-1"

        respx.get(f"{COTIZACION}/cotizaciones/no-existe").mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(RecursoNoEncontrado):
            await adapter.obtener_cotizacion("cliente-1", "no-existe")
    finally:
        await adapter.aclose()


@respx.mock
async def test_cotizacion_error_5xx():
    respx.get(f"{COTIZACION}/cotizaciones/cot-1").mock(return_value=httpx.Response(500, json={}))
    adapter = _cotizacion()
    try:
        with pytest.raises(BffError):
            await adapter.obtener_cotizacion("cliente-1", "cot-1")
    finally:
        await adapter.aclose()
