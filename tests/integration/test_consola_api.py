"""BITS-375: ingreso del personal y rutas protegidas por rol, de punta a punta con dobles."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from jsonschema import Draft202012Validator

from app.api.consola import backoffice_router, claims_consola_actuales, socio_router
from app.domain import ClaimsConsola, RolConsola
from app.security import ConsolaSessionIssuer
from tests.conftest import REGISTRO_VALIDO

CLAVE = "clave-del-personal-1"
ADMIN = "admin@solventa4bits.com"
SOCIO = "socio@solventa4bits.com"
USUARIOS = "/v1/backoffice/usuarios"


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


@pytest.fixture
def identidad(app):
    """El doble de Identity Platform, con un administrador y un socio ya creados."""
    proveedor = app.state.deps.identity
    proveedor.sembrar_personal(ADMIN, CLAVE, "ADMIN", "Administrador")
    proveedor.sembrar_personal(SOCIO, CLAVE, "SOCIO", "Aliado")
    return proveedor


async def _ingresar(client, correo, clave=CLAVE):
    return await client.post("/v1/backoffice/sesiones", json={"email": correo, "password": clave})


async def _cabecera(client, correo) -> dict:
    respuesta = await _ingresar(client, correo)
    assert respuesta.status_code == 200
    return {"Authorization": f"Bearer {respuesta.json()['accessToken']}"}


def _cuerpo(**cambios) -> dict:
    return {
        "correo": "nuevo@solventa4bits.com",
        "contrasena": "otra-clave-larga-1",
        "rol": "SOCIO",
        "nombre": "Nuevo aliado",
        **cambios,
    }


# --- ingreso ---


async def test_el_admin_ingresa_y_la_respuesta_cumple_el_contrato(client, identidad, openapi_spec):
    respuesta = await _ingresar(client, ADMIN)

    assert respuesta.status_code == 200
    _validar(openapi_spec, "SesionConsola", respuesta.json())
    cuerpo = respuesta.json()
    assert cuerpo["rol"] == "ADMIN"
    assert cuerpo["nombre"] == "Administrador"
    assert cuerpo["expiresIn"] == 900


async def test_el_socio_ingresa_con_su_rol_y_nombre(client, identidad):
    cuerpo = (await _ingresar(client, SOCIO)).json()

    assert (cuerpo["rol"], cuerpo["nombre"]) == ("SOCIO", "Aliado")


async def test_el_correo_no_distingue_mayusculas(client, identidad):
    assert (await _ingresar(client, "ADMIN@Solventa4Bits.COM")).status_code == 200


async def test_contrasena_equivocada_es_401(client, identidad, openapi_spec):
    respuesta = await _ingresar(client, ADMIN, "equivocada")

    assert respuesta.status_code == 401
    _validar(openapi_spec, "Problema", respuesta.json())


async def test_cuenta_inexistente_es_401_igual_que_contrasena_equivocada(client, identidad):
    inexistente = await _ingresar(client, "nadie@solventa4bits.com")
    equivocada = await _ingresar(client, ADMIN, "equivocada")

    assert inexistente.status_code == equivocada.status_code == 401
    assert inexistente.json()["detail"] == equivocada.json()["detail"]


async def test_un_correo_de_cliente_es_403_y_apunta_al_portal(client, identidad, openapi_spec):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)

    respuesta = await _ingresar(client, REGISTRO_VALIDO["email"], REGISTRO_VALIDO["password"])

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "ACCESO_POR_PORTAL_CLIENTE"
    _validar(openapi_spec, "ProblemaConsola", respuesta.json())


async def test_una_cuenta_sin_rol_es_403(client, identidad):
    identidad.sembrar_personal("sinrol@solventa4bits.com", CLAVE, None)

    respuesta = await _ingresar(client, "sinrol@solventa4bits.com")

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "CUENTA_SIN_ROL"


async def test_refrescar_conserva_el_rol(client, identidad, openapi_spec):
    sesion = (await _ingresar(client, SOCIO)).json()

    respuesta = await client.post(
        "/v1/backoffice/sesiones/refresco", json={"refreshToken": sesion["refreshToken"]}
    )

    assert respuesta.status_code == 200
    _validar(openapi_spec, "SesionConsola", respuesta.json())
    assert respuesta.json()["rol"] == "SOCIO"


async def test_refrescar_con_el_token_de_acceso_o_de_cliente_es_401(client, identidad):
    sesion = (await _ingresar(client, ADMIN)).json()
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    refresco_cliente = registro.json()["sesion"]["refreshToken"]

    for token in (sesion["accessToken"], refresco_cliente, "basura"):
        respuesta = await client.post(
            "/v1/backoffice/sesiones/refresco", json={"refreshToken": token}
        )
        assert respuesta.status_code == 401


# --- tokens cruzados ---


async def test_un_token_del_personal_no_sirve_en_las_rutas_del_cliente(client, identidad):
    cabecera = await _cabecera(client, ADMIN)

    for ruta in ("/v1/cuenta", "/v1/cuenta/polizas", "/v1/cotizaciones/actual"):
        assert (await client.get(ruta, headers=cabecera)).status_code == 401


async def test_un_token_de_cliente_no_sirve_en_las_rutas_del_personal(client, identidad):
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    cabecera = {"Authorization": f"Bearer {registro.json()['sesion']['accessToken']}"}

    assert (await client.post(USUARIOS, json=_cuerpo(), headers=cabecera)).status_code == 401


# --- crear cuentas: solo ADMIN ---


@pytest.mark.parametrize(
    "cabecera",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer manipulado.token.xx"},
    ],
)
async def test_sin_sesion_valida_crear_cuentas_es_401(client, identidad, cabecera):
    assert (await client.post(USUARIOS, json=_cuerpo(), headers=cabecera)).status_code == 401


async def test_un_token_expirado_es_401(client, app, identidad):
    expirado = ConsolaSessionIssuer("test-secret", ttl_seconds=-10).emitir(
        ClaimsConsola(uid="u", correo=ADMIN, rol=RolConsola.ADMIN)
    )

    respuesta = await client.post(
        USUARIOS, json=_cuerpo(), headers={"Authorization": f"Bearer {expirado.access_token}"}
    )

    assert respuesta.status_code == 401


async def test_el_socio_no_puede_crear_cuentas_ni_siquiera_otro_admin(
    client, identidad, openapi_spec
):
    cabecera = await _cabecera(client, SOCIO)

    for cuerpo in (_cuerpo(), _cuerpo(rol="ADMIN", nombre=None)):
        respuesta = await client.post(USUARIOS, json=cuerpo, headers=cabecera)
        assert respuesta.status_code == 403
        assert respuesta.json()["codigo"] == "ROL_NO_PERMITIDO"
        _validar(openapi_spec, "ProblemaConsola", respuesta.json())


async def test_el_admin_crea_un_socio_que_luego_puede_ingresar(client, identidad, openapi_spec):
    cabecera = await _cabecera(client, ADMIN)

    respuesta = await client.post(USUARIOS, json=_cuerpo(), headers=cabecera)

    assert respuesta.status_code == 201
    _validar(openapi_spec, "Usuario", respuesta.json())
    assert respuesta.json()["rol"] == "SOCIO"
    assert "contrasena" not in respuesta.text
    ingreso = await _ingresar(client, "nuevo@solventa4bits.com", "otra-clave-larga-1")
    assert (ingreso.json()["rol"], ingreso.json()["nombre"]) == ("SOCIO", "Nuevo aliado")


async def test_el_admin_crea_otro_admin_que_luego_puede_crear_cuentas(client, identidad):
    cabecera = await _cabecera(client, ADMIN)
    nuevo = {
        "correo": "otro.admin@solventa4bits.com",
        "contrasena": "otra-clave-larga-1",
        "rol": "ADMIN",
    }

    assert (await client.post(USUARIOS, json=nuevo, headers=cabecera)).status_code == 201
    ingreso = await _ingresar(client, "otro.admin@solventa4bits.com", "otra-clave-larga-1")
    cabecera_nuevo = {"Authorization": f"Bearer {ingreso.json()['accessToken']}"}
    siguiente = await client.post(USUARIOS, json=_cuerpo(), headers=cabecera_nuevo)

    assert siguiente.status_code == 201


async def test_correo_repetido_es_409(client, identidad, openapi_spec):
    cabecera = await _cabecera(client, ADMIN)

    respuesta = await client.post(USUARIOS, json=_cuerpo(correo=SOCIO), headers=cabecera)

    assert respuesta.status_code == 409
    _validar(openapi_spec, "Problema", respuesta.json())


@pytest.mark.parametrize(
    "cambios",
    [
        {"correo": "alguien@gmail.com"},
        {"nombre": None},
        {"rol": "BACKOFFICE"},
        {"contrasena": "corta"},
        {"correo": "no-es-correo"},
        {"esAdmin": True},
    ],
)
async def test_cuerpos_invalidos_son_400(client, identidad, cambios):
    cabecera = await _cabecera(client, ADMIN)

    respuesta = await client.post(USUARIOS, json=_cuerpo(**cambios), headers=cabecera)

    assert respuesta.status_code == 400


# --- los routers protegen por defecto ---


def _probar(app, router_protegido, ruta):
    """Una ruta de prueba colgada del mismo prefijo y las mismas dependencias que el router real."""
    sonda = APIRouter(prefix=router_protegido.prefix, dependencies=router_protegido.dependencies)

    @sonda.get(ruta)
    async def _sonda(
        claims: Annotated[ClaimsConsola, Depends(claims_consola_actuales)],
    ) -> dict:
        return {"rol": claims.rol}

    app.include_router(sonda)
    return f"{router_protegido.prefix}{ruta}"


async def test_toda_ruta_de_backoffice_exige_admin(client, app, identidad):
    ruta = _probar(app, backoffice_router, "/sonda")

    assert (await client.get(ruta)).status_code == 401
    assert (await client.get(ruta, headers=await _cabecera(client, SOCIO))).status_code == 403
    assert (await client.get(ruta, headers=await _cabecera(client, ADMIN))).status_code == 200


async def test_toda_ruta_de_socio_exige_socio(client, app, identidad):
    ruta = _probar(app, socio_router, "/sonda")

    assert (await client.get(ruta)).status_code == 401
    assert (await client.get(ruta, headers=await _cabecera(client, ADMIN))).status_code == 403
    assert (await client.get(ruta, headers=await _cabecera(client, SOCIO))).status_code == 200


# --- el ingreso es lo único público de /v1/backoffice ---

RUTAS_PUBLICAS = {
    ("POST", "/v1/backoffice/sesiones"),
    ("POST", "/v1/backoffice/sesiones/refresco"),
}


async def test_toda_ruta_de_backoffice_salvo_el_ingreso_exige_sesion(client, app):
    """Red de seguridad: una ruta nueva en /v1/backoffice que quede pública hace fallar esto."""
    propias = [
        (metodo.upper(), path)
        for path, operaciones in app.openapi()["paths"].items()
        if path.startswith("/v1/backoffice")
        for metodo in operaciones
    ]
    assert RUTAS_PUBLICAS <= set(propias)

    for metodo, path in propias:
        if (metodo, path) in RUTAS_PUBLICAS:
            continue
        respuesta = await client.request(metodo, path)
        assert respuesta.status_code == 401, f"{metodo} {path} responde sin sesión"
