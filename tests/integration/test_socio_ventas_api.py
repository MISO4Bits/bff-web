"""BITS-378: las rutas de venta directa del socio, de punta a punta con dobles."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from app.api.consola import socio_router

CLAVE = "clave-del-personal-1"
ADMIN = "admin@solventa4bits.com"
SOCIO = "socio@solventa4bits.com"
OTRO_SOCIO = "otro@solventa4bits.com"
CLIENTE = {
    "tipoDocumento": "CC",
    "numeroDocumento": "52123456",
    "primerNombre": "Laura",
    "segundoNombre": "María",
    "primerApellido": "Gómez",
    "fechaNacimiento": "1988-03-14",
    "email": "laura.gomez@example.com",
    "telefono": "+573101112233",
}
CREDITO = {
    "valorCredito": 150000000,
    "plazoRestanteMeses": 240,
    "entidadAcreedora": "Bancolombia",
}
VENTA = {
    "coberturas": ["VIDA", "HOGAR"],
    "cuotaMensual": 1250000,
    "direccion": "Calle 10 # 5-20",
    "municipio": "Bogotá",
    **CREDITO,
}


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


async def _cabecera(client, correo, **extra) -> dict:
    respuesta = await client.post(
        "/v1/backoffice/sesiones", json={"email": correo, "password": CLAVE}
    )
    assert respuesta.status_code == 200
    return {"Authorization": f"Bearer {respuesta.json()['accessToken']}", **extra}


@pytest.fixture
async def socio(client, identidad) -> dict:
    return await _cabecera(client, SOCIO)


async def _crear_cliente(client, cabecera, **cambios) -> dict:
    respuesta = await client.post(
        "/v1/socio/clientes", json={**CLIENTE, **cambios}, headers=cabecera
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# --- el rol ---


async def test_toda_ruta_de_socio_exige_la_sesion_de_un_socio(client, app, identidad):
    rutas = [
        (metodo.upper(), path)
        for path, operaciones in app.openapi()["paths"].items()
        if path.startswith("/v1/socio")
        for metodo in operaciones
    ]
    assert len(rutas) == 5
    for metodo, path in rutas:
        sin_sesion = await client.request(metodo, path)
        como_admin = await client.request(
            metodo, path, headers=await _cabecera(client, ADMIN, **{"Idempotency-Key": "k"})
        )
        assert sin_sesion.status_code == 401, (metodo, path)
        assert como_admin.status_code == 403, (metodo, path)
        assert como_admin.json()["codigo"] == "ROL_NO_PERMITIDO"


async def test_el_token_de_un_cliente_no_sirve_en_las_rutas_de_socio(client):
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

    respuesta = await client.get(
        "/v1/socio/entidades-financieras", headers={"Authorization": f"Bearer {token}"}
    )

    assert respuesta.status_code == 401


async def test_las_rutas_de_backoffice_siguen_siendo_solo_del_admin(client, identidad):
    cabecera = await _cabecera(client, SOCIO)

    respuesta = await client.get("/v1/backoffice/siniestros", headers=cabecera)

    assert respuesta.status_code == 403


def test_el_router_de_socio_conserva_su_regla():
    assert socio_router.prefix == "/v1/socio"
    assert socio_router.dependencies


# --- entidades ---


async def test_lista_los_bancos_del_mercado(client, socio, openapi_spec):
    respuesta = await client.get("/v1/socio/entidades-financieras", headers=socio)

    assert respuesta.status_code == 200
    entidades = respuesta.json()
    assert entidades and all(set(e) == {"id", "nombre"} for e in entidades)
    for entidad in entidades:
        _validar(openapi_spec, "EntidadFinanciera", entidad)


# --- clientes ---


async def test_crea_un_cliente_y_solo_ve_lo_minimo(client, socio, openapi_spec):
    respuesta = await client.post("/v1/socio/clientes", json=CLIENTE, headers=socio)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    _validar(openapi_spec, "ClienteSocio", cuerpo)
    assert cuerpo["nombre"] == "Laura María Gómez"
    assert cuerpo["ultimosDigitosDocumento"] == "3456"
    assert cuerpo["estado"] == "ACTIVO"
    texto = respuesta.text
    for privado in ("laura.gomez", "52123456", "3101112233", "1988", "identityRef"):
        assert privado not in texto


async def test_un_correo_o_documento_repetido_es_409_con_el_campo(client, socio):
    await _crear_cliente(client, socio)

    por_correo = await client.post(
        "/v1/socio/clientes", json={**CLIENTE, "numeroDocumento": "99999999"}, headers=socio
    )
    por_documento = await client.post(
        "/v1/socio/clientes", json={**CLIENTE, "email": "otra@example.com"}, headers=socio
    )

    assert por_correo.status_code == 409
    assert por_correo.json()["errores"][0]["campo"] == "correo"
    assert por_documento.status_code == 409
    assert por_documento.json()["errores"][0]["campo"] == "documento"


async def test_un_cuerpo_invalido_al_crear_el_cliente_es_400(client, socio):
    sin_telefono = {k: v for k, v in CLIENTE.items() if k != "telefono"}

    assert (
        await client.post("/v1/socio/clientes", json=sin_telefono, headers=socio)
    ).status_code == 400
    assert (
        await client.post(
            "/v1/socio/clientes", json={**CLIENTE, "email": "no-es-correo"}, headers=socio
        )
    ).status_code == 400
    assert (
        await client.post("/v1/socio/clientes", json={**CLIENTE, "canal": "WEB"}, headers=socio)
    ).status_code == 400


async def test_busca_por_correo_y_por_documento(client, socio):
    creado = await _crear_cliente(client, socio)

    por_correo = await client.get(
        "/v1/socio/clientes", params={"correo": "LAURA.GOMEZ@example.com"}, headers=socio
    )
    por_documento = await client.get(
        "/v1/socio/clientes",
        params={"tipoDocumento": "CC", "numeroDocumento": "52123456"},
        headers=socio,
    )

    assert por_correo.status_code == por_documento.status_code == 200
    assert por_correo.json() == por_documento.json() == creado


async def test_buscar_a_quien_no_existe_es_404(client, socio):
    respuesta = await client.get(
        "/v1/socio/clientes", params={"correo": "nadie@example.com"}, headers=socio
    )

    assert respuesta.status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"tipoDocumento": "CC"},
        {"numeroDocumento": "52123456"},
        {"correo": "a@b.co", "tipoDocumento": "CC", "numeroDocumento": "1234"},
        {"tipoDocumento": "XX", "numeroDocumento": "1234"},
    ],
)
async def test_buscar_sin_dato_completo_o_mezclando_formas_es_400(client, socio, params):
    respuesta = await client.get("/v1/socio/clientes", params=params, headers=socio)

    assert respuesta.status_code == 400


# --- precio ---


async def test_calcula_el_precio_sin_guardar_nada(client, socio, app, openapi_spec):
    cliente = await _crear_cliente(client, socio)

    respuesta = await client.post(
        "/v1/socio/ventas/precio", json={"clienteId": cliente["id"], **CREDITO}, headers=socio
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    _validar(openapi_spec, "PrecioVenta", cuerpo)
    assert [c["codigo"] for c in cuerpo["coberturas"]] == ["VIDA", "HOGAR"]
    assert cuerpo["datosCredito"]["saldoInsoluto"] == cuerpo["datosCredito"]["valorCredito"]
    assert app.state.deps.ventas.ventas == []
    assert "tasa" not in cuerpo and "versionTarifa" not in cuerpo


async def test_el_precio_de_un_cliente_que_no_existe_es_404(client, socio):
    respuesta = await client.post(
        "/v1/socio/ventas/precio", json={"clienteId": "no-existe", **CREDITO}, headers=socio
    )

    assert respuesta.status_code == 404


async def test_el_precio_fuera_del_rango_de_edad_es_422(client, socio):
    cliente = await _crear_cliente(client, socio, fechaNacimiento="1950-01-01")

    respuesta = await client.post(
        "/v1/socio/ventas/precio", json={"clienteId": cliente["id"], **CREDITO}, headers=socio
    )

    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "FUERA_DE_RANGO"


@pytest.mark.parametrize(
    "cambios",
    [
        {"valorCredito": 9_999_999},
        {"plazoRestanteMeses": 0},
        {"plazoRestanteMeses": 481},
        {"entidadAcreedora": "x"},
        {"saldoInsoluto": 1},
    ],
)
async def test_el_precio_con_un_credito_invalido_es_400(client, socio, cambios):
    cliente = await _crear_cliente(client, socio)

    respuesta = await client.post(
        "/v1/socio/ventas/precio",
        json={"clienteId": cliente["id"], **CREDITO, **cambios},
        headers=socio,
    )

    assert respuesta.status_code == 400


# --- venta ---


async def _vender(client, cabecera, cliente_id, clave="venta-1", **cambios):
    return await client.post(
        "/v1/socio/ventas",
        json={"clienteId": cliente_id, **VENTA, **cambios},
        headers={**cabecera, "Idempotency-Key": clave},
    )


async def test_la_venta_emite_a_nombre_del_cliente_y_cumple_el_contrato(
    client, socio, openapi_spec, app
):
    cliente = await _crear_cliente(client, socio)

    respuesta = await _vender(client, socio, cliente["id"])

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    _validar(openapi_spec, "Venta", cuerpo)
    assert cuerpo["estado"] == "EMITIDA"
    assert cuerpo["clienteId"] == cliente["id"]
    assert cuerpo["datosVenta"] == {
        "direccion": "Calle 10 # 5-20",
        "municipio": "Bogotá",
        "cuotaMensual": 1250000,
    }
    assert [c["codigo"] for c in cuerpo["coberturas"]] == ["VIDA", "HOGAR"]
    assert cuerpo["pendientes"] == []
    assert len(app.state.deps.ventas.ventas) == 1


async def test_el_socio_de_la_venta_es_el_del_token_aunque_el_cuerpo_diga_otro(client, socio, app):
    cliente = await _crear_cliente(client, socio)
    uid = (
        await client.post(
            "/v1/backoffice/sesiones/refresco",
            json={
                "refreshToken": (
                    await client.post(
                        "/v1/backoffice/sesiones", json={"email": SOCIO, "password": CLAVE}
                    )
                ).json()["refreshToken"]
            },
        )
    ).status_code
    assert uid == 200

    falsa = await client.post(
        "/v1/socio/ventas",
        json={"clienteId": cliente["id"], **VENTA, "socioId": "otro-socio"},
        headers={**socio, "Idempotency-Key": "k-falsa"},
    )
    verdadera = await _vender(client, socio, cliente["id"], clave="k-verdadera")

    assert falsa.status_code == 400  # el cuerpo no acepta socioId
    assert verdadera.json()["socioId"] == app.state.deps.ventas.ventas[0]["socioId"]
    assert verdadera.json()["socioId"] != "otro-socio"


async def test_dos_socios_distintos_firman_con_su_propio_uid(client, identidad, app):
    uno = await _cabecera(client, SOCIO)
    otro = await _cabecera(client, OTRO_SOCIO)
    cliente = await _crear_cliente(client, uno)

    a = await _vender(client, uno, cliente["id"], clave="misma")
    b = await _vender(client, otro, cliente["id"], clave="misma")

    assert a.json()["socioId"] != b.json()["socioId"]
    assert a.json()["polizaId"] != b.json()["polizaId"]


async def test_la_venta_sin_idempotency_key_es_400_y_no_emite(client, socio, app):
    cliente = await _crear_cliente(client, socio)

    respuesta = await client.post(
        "/v1/socio/ventas", json={"clienteId": cliente["id"], **VENTA}, headers=socio
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["errores"][0]["campo"] == "Idempotency-Key"
    assert app.state.deps.ventas.ventas == []


async def test_el_reintento_con_la_misma_clave_devuelve_la_misma_poliza(client, socio, app):
    cliente = await _crear_cliente(client, socio)

    primera = await _vender(client, socio, cliente["id"])
    repetida = await _vender(client, socio, cliente["id"])

    assert repetida.json() == primera.json()
    assert len(app.state.deps.ventas.ventas) == 1


async def test_la_venta_envia_la_clave_a_svc_core(client, socio, app):
    cliente = await _crear_cliente(client, socio, email="otro@example.com")
    await _vender(client, socio, cliente["id"], clave="clave-123")

    llamada = next(c for c in app.state.deps.ventas.llamadas if c[0] == "vender")
    assert llamada[2] == "clave-123"


async def test_vender_sin_vida_es_400(client, socio):
    cliente = await _crear_cliente(client, socio)

    respuesta = await _vender(client, socio, cliente["id"], coberturas=["HOGAR"])

    assert respuesta.status_code == 400


@pytest.mark.parametrize(
    "cambios",
    [
        {"direccion": ""},
        {"municipio": ""},
        {"cuotaMensual": 0},
        {"coberturas": []},
        {"coberturas": ["VIDA", "OTRA"]},
        {"valorCredito": 1000},
    ],
)
async def test_los_datos_de_la_venta_se_validan(client, socio, cambios):
    cliente = await _crear_cliente(client, socio)

    respuesta = await _vender(client, socio, cliente["id"], **cambios)

    assert respuesta.status_code == 400


async def test_vender_a_un_cliente_que_no_existe_es_404(client, socio):
    assert (await _vender(client, socio, "no-existe")).status_code == 404


async def test_vender_fuera_del_rango_de_edad_es_422(client, socio, app):
    cliente = await _crear_cliente(client, socio, fechaNacimiento="1950-01-01")

    respuesta = await _vender(client, socio, cliente["id"])

    assert respuesta.status_code == 422
    assert app.state.deps.ventas.ventas == []
