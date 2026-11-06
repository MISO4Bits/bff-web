"""BITS-375: sesión del personal (emisor, adaptadores y servicio)."""

from __future__ import annotations

import json
import time
from datetime import date

import httpx
import jwt
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.adapters.factory import build_dependencias
from app.adapters.fakes import FakeCuentasInternas, FakeIdentityProvider
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.config import Settings
from app.consola import ConsolaService
from app.domain import (
    BffError,
    ClaimsConsola,
    Conflicto,
    DependenciaNoDisponible,
    NoAutorizado,
    Prohibido,
    RolConsola,
    SolicitudInvalida,
    es_correo_interno,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.security import ConsolaSessionIssuer, SessionIssuer

IDP = "http://idp.local"
CORE = "http://core.local"
SECRETO = "secreto-de-prueba"
CLAVE = "clave-del-personal-1"

ADMIN = ClaimsConsola(uid="u-1", correo="admin@solventa4bits.com", rol=RolConsola.ADMIN)
SOCIO = ClaimsConsola(
    uid="u-2", correo="socio@solventa4bits.com", rol=RolConsola.SOCIO, nombre="Aliado"
)


# --- dominio ---


@pytest.mark.parametrize(
    ("correo", "esperado"),
    [
        ("ana@solventa4bits.com", True),
        ("ANA@Solventa4Bits.COM", True),
        ("ana@gmail.com", False),
        ("ana@solventa4bits.com.co", False),
        ("ana@otro.solventa4bits.com", False),
        ("@solventa4bits.com", False),
        ("solventa4bits.com", False),
    ],
)
def test_es_correo_interno(correo, esperado):
    assert es_correo_interno(correo) is esperado


# --- emisor de sesión ---


def test_emite_y_verifica_la_sesion_de_consola():
    emisor = ConsolaSessionIssuer(SECRETO)

    sesion = emisor.emitir(SOCIO)

    assert sesion.rol is RolConsola.SOCIO
    assert sesion.nombre == "Aliado"
    assert sesion.expires_in == 900
    assert emisor.verificar(sesion.access_token) == SOCIO


def test_el_refresco_conserva_los_claims_y_emite_tokens_nuevos():
    emisor = ConsolaSessionIssuer(SECRETO)
    inicial = emisor.emitir(ADMIN)

    renovada = emisor.refrescar(inicial.refresh_token)

    assert emisor.verificar(renovada.access_token) == ADMIN
    assert renovada.rol is RolConsola.ADMIN


def test_el_token_de_acceso_no_sirve_para_refrescar_ni_al_reves():
    emisor = ConsolaSessionIssuer(SECRETO)
    sesion = emisor.emitir(ADMIN)

    with pytest.raises(NoAutorizado, match="tipo"):
        emisor.refrescar(sesion.access_token)
    with pytest.raises(NoAutorizado, match="tipo"):
        emisor.verificar(sesion.refresh_token)


def test_token_expirado_manipulado_o_de_otra_clave():
    emisor = ConsolaSessionIssuer(SECRETO, ttl_seconds=-10)
    expirado = emisor.emitir(ADMIN).access_token
    valido = ConsolaSessionIssuer(SECRETO).emitir(ADMIN).access_token
    ajeno = ConsolaSessionIssuer("otra-clave-distinta-de-32-bytes-xx").emitir(ADMIN).access_token

    with pytest.raises(NoAutorizado, match="expirado"):
        ConsolaSessionIssuer(SECRETO).verificar(expirado)
    with pytest.raises(NoAutorizado, match="inválido"):
        ConsolaSessionIssuer(SECRETO).verificar(valido[:-3] + "abc")
    with pytest.raises(NoAutorizado, match="inválido"):
        ConsolaSessionIssuer(SECRETO).verificar(ajeno)


def test_un_token_de_cliente_no_sirve_como_sesion_de_consola_ni_al_reves():
    cliente = SessionIssuer(SECRETO).emitir(
        sub="s", cliente_id="c", email="ana@example.com", fecha_nacimiento=date(1990, 1, 1)
    )
    personal = ConsolaSessionIssuer(SECRETO).emitir(ADMIN)

    with pytest.raises(NoAutorizado):
        ConsolaSessionIssuer(SECRETO).verificar(cliente.access_token)
    with pytest.raises(NoAutorizado):
        SessionIssuer(SECRETO).verificar(personal.access_token)


@pytest.mark.parametrize("cambio", [{"rol": "HACKER"}, {"rol": None}, {"sub": None}])
def test_un_token_firmado_con_claims_invalidos_se_rechaza(cambio):
    ahora = int(time.time())
    claims = {
        "sub": "u-1",
        "correo": "a@solventa4bits.com",
        "rol": "ADMIN",
        "typ": "consola",
        "iat": ahora,
        "exp": ahora + 60,
    }
    claims.update(cambio)
    claims = {k: v for k, v in claims.items() if v is not None}
    token = jwt.encode(claims, SECRETO, algorithm="HS256")

    with pytest.raises(NoAutorizado, match="inválido"):
        ConsolaSessionIssuer(SECRETO).verificar(token)


# --- adaptador de Identity Platform ---


def _idp() -> IdentityPlatformAdapter:
    http = ResilientHttpClient(
        IDP, breaker=build_breaker("idp", fail_max=9, reset_timeout=5), timeout=0.3, retries=0
    )
    return IdentityPlatformAdapter(http, "k")


def _id_token(**claims) -> str:
    return jwt.encode(claims, "no-importa-la-firma-no-se-verifica-xx", algorithm="HS256")


@respx.mock
async def test_autenticar_personal_lee_rol_y_nombre_del_id_token():
    ruta = respx.post(f"{IDP}/v1/accounts:signInWithPassword")
    token = _id_token(email="ana@solventa4bits.com", rol="ADMIN", name="Ana")
    ruta.mock(return_value=httpx.Response(200, json={"localId": "uid-1", "idToken": token}))
    adaptador = _idp()
    try:
        identidad = await adaptador.autenticar_personal("ana@solventa4bits.com", CLAVE)
    finally:
        await adaptador.aclose()

    assert identidad.uid == "uid-1"
    assert identidad.rol == "ADMIN"
    assert identidad.nombre == "Ana"
    assert json.loads(ruta.calls.last.request.content)["password"] == CLAVE


@respx.mock
async def test_autenticar_personal_sin_claim_de_rol_devuelve_rol_nulo():
    token = _id_token(email="ana@solventa4bits.com")
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(
            200, json={"localId": "uid-1", "idToken": token, "displayName": "Ana"}
        )
    )
    adaptador = _idp()
    try:
        identidad = await adaptador.autenticar_personal("ana@solventa4bits.com", CLAVE)
    finally:
        await adaptador.aclose()

    assert identidad.rol is None
    assert identidad.nombre == "Ana"


@respx.mock
async def test_autenticar_personal_con_credenciales_invalidas():
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(400, json={"error": {"message": "INVALID_PASSWORD"}})
    )
    adaptador = _idp()
    try:
        with pytest.raises(NoAutorizado):
            await adaptador.autenticar_personal("ana@solventa4bits.com", "mala")
    finally:
        await adaptador.aclose()


@pytest.mark.parametrize("cuerpo", [{"localId": "u"}, {"localId": "u", "idToken": "no-es-un-jwt"}])
@respx.mock
async def test_autenticar_personal_con_token_ilegible(cuerpo):
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(200, json=cuerpo)
    )
    adaptador = _idp()
    try:
        with pytest.raises(BffError, match="token"):
            await adaptador.autenticar_personal("ana@solventa4bits.com", CLAVE)
    finally:
        await adaptador.aclose()


@respx.mock
async def test_autenticar_personal_con_el_proveedor_caido():
    respx.post(f"{IDP}/v1/accounts:signInWithPassword").mock(
        return_value=httpx.Response(500, json={})
    )
    adaptador = _idp()
    try:
        with pytest.raises(BffError):
            await adaptador.autenticar_personal("ana@solventa4bits.com", CLAVE)
    finally:
        await adaptador.aclose()


# --- adaptador de svc-core ---


def _core() -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE, breaker=build_breaker("core", fail_max=9, reset_timeout=5), timeout=0.3, retries=2
    )
    return CoreClientAdapter(http)


@respx.mock
async def test_crear_usuario_interno_envia_el_cuerpo_y_lee_la_respuesta():
    ruta = respx.post(f"{CORE}/v1/usuarios-internos").mock(
        return_value=httpx.Response(
            201, json={"uid": "uid-9", "correo": "socio@solventa4bits.com", "rol": "SOCIO"}
        )
    )
    adaptador = _core()
    try:
        usuario = await adaptador.crear_usuario_interno(
            "socio@solventa4bits.com", CLAVE, RolConsola.SOCIO, "Aliado"
        )
    finally:
        await adaptador.aclose()

    assert (usuario.uid, usuario.rol) == ("uid-9", RolConsola.SOCIO)
    assert json.loads(ruta.calls.last.request.content) == {
        "correo": "socio@solventa4bits.com",
        "contrasena": CLAVE,
        "rol": "SOCIO",
        "nombre": "Aliado",
    }


@respx.mock
async def test_crear_usuario_interno_sin_nombre_no_lo_envia():
    ruta = respx.post(f"{CORE}/v1/usuarios-internos").mock(
        return_value=httpx.Response(
            201, json={"uid": "u", "correo": "a@solventa4bits.com", "rol": "ADMIN"}
        )
    )
    adaptador = _core()
    try:
        await adaptador.crear_usuario_interno("a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None)
    finally:
        await adaptador.aclose()

    assert "nombre" not in json.loads(ruta.calls.last.request.content)


@respx.mock
async def test_crear_usuario_interno_traduce_los_errores_de_svc_core():
    ruta = respx.post(f"{CORE}/v1/usuarios-internos")
    adaptador = _core()
    try:
        ruta.mock(return_value=httpx.Response(409, json={"title": "La cuenta ya existe"}))
        with pytest.raises(Conflicto):
            await adaptador.crear_usuario_interno(
                "a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None
            )

        ruta.mock(
            return_value=httpx.Response(
                400,
                json={"detail": "dominio", "errores": [{"campo": "correo", "mensaje": "dominio"}]},
            )
        )
        with pytest.raises(SolicitudInvalida) as invalida:
            await adaptador.crear_usuario_interno("a@gmail.com", CLAVE, RolConsola.ADMIN, None)
        assert invalida.value.errores == [{"campo": "correo", "mensaje": "dominio"}]

        # 503 lo traduce el cliente resiliente (es transitorio): sin reintentos, de inmediato.
        ruta.mock(return_value=httpx.Response(503, json={"codigo": "IDENTIDAD_NO_DISPONIBLE"}))
        with pytest.raises(DependenciaNoDisponible):
            await adaptador.crear_usuario_interno(
                "a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None
            )

        ruta.mock(return_value=httpx.Response(418, text="no es json"))
        with pytest.raises(BffError, match="418"):
            await adaptador.crear_usuario_interno(
                "a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None
            )
    finally:
        await adaptador.aclose()


@respx.mock
async def test_crear_usuario_interno_no_reintenta_aunque_el_cliente_reintente():
    ruta = respx.post(f"{CORE}/v1/usuarios-internos").mock(
        return_value=httpx.Response(
            201, json={"uid": "u", "correo": "a@solventa4bits.com", "rol": "ADMIN"}
        )
    )
    adaptador = _core()
    try:
        await adaptador.crear_usuario_interno("a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None)
    finally:
        await adaptador.aclose()

    assert ruta.call_count == 1


# --- servicio ---


def _servicio() -> tuple[ConsolaService, FakeIdentityProvider]:
    identidad = FakeIdentityProvider()
    return (
        ConsolaService(identidad, FakeCuentasInternas(identidad), ConsolaSessionIssuer(SECRETO)),
        identidad,
    )


async def test_el_ingreso_de_un_correo_ajeno_no_consulta_a_identity_platform():
    class _NoDebeLlamarse(FakeIdentityProvider):
        async def autenticar_personal(self, email, password):
            raise AssertionError("no debía consultarse")

    identidad = _NoDebeLlamarse()
    servicio = ConsolaService(
        identidad, FakeCuentasInternas(identidad), ConsolaSessionIssuer(SECRETO)
    )

    with pytest.raises(Prohibido) as error:
        await servicio.iniciar_sesion("ana@gmail.com", CLAVE)

    assert error.value.codigo == "ACCESO_POR_PORTAL_CLIENTE"


async def test_el_ingreso_normaliza_el_correo():
    servicio, identidad = _servicio()
    identidad.sembrar_personal("ana@solventa4bits.com", CLAVE, "ADMIN", "Ana")

    sesion = await servicio.iniciar_sesion("  ANA@Solventa4Bits.com ", CLAVE)

    assert sesion.rol is RolConsola.ADMIN


@pytest.mark.parametrize("rol", [None, "", "HACKER", "BACKOFFICE"])
async def test_una_cuenta_sin_rol_valido_recibe_403(rol):
    servicio, identidad = _servicio()
    identidad.sembrar_personal("ana@solventa4bits.com", CLAVE, rol)

    with pytest.raises(Prohibido) as error:
        await servicio.iniciar_sesion("ana@solventa4bits.com", CLAVE)

    assert error.value.codigo == "CUENTA_SIN_ROL"


async def test_las_cuentas_creadas_pueden_ingresar():
    servicio, _ = _servicio()

    await servicio.crear_usuario(
        ADMIN, "Socio@Solventa4Bits.com", CLAVE, RolConsola.SOCIO, "Aliado"
    )
    sesion = await servicio.iniciar_sesion("socio@solventa4bits.com", CLAVE)

    assert (sesion.rol, sesion.nombre) == (RolConsola.SOCIO, "Aliado")


async def test_el_doble_de_cuentas_valida_dominio_nombre_y_repetidos():
    servicio, _ = _servicio()

    with pytest.raises(SolicitudInvalida):
        await servicio.crear_usuario(ADMIN, "a@gmail.com", CLAVE, RolConsola.ADMIN, None)
    with pytest.raises(SolicitudInvalida):
        await servicio.crear_usuario(ADMIN, "a@solventa4bits.com", CLAVE, RolConsola.SOCIO, " ")
    await servicio.crear_usuario(ADMIN, "a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None)
    with pytest.raises(Conflicto):
        await servicio.crear_usuario(ADMIN, "a@solventa4bits.com", CLAVE, RolConsola.ADMIN, None)


async def test_la_fabrica_construye_la_sesion_y_las_cuentas_en_ambos_modos():
    fake = build_dependencias(Settings(adapters="fake"))
    assert type(fake.cuentas_internas).__name__ == "FakeCuentasInternas"
    assert fake.consola_sessions is not None
    await fake.aclose()

    http = build_dependencias(Settings(adapters="http", consola_ttl_seconds=60))
    assert http.cuentas_internas is http.core
    assert http.consola_sessions.emitir(ADMIN).expires_in == 60
    await http.aclose()
