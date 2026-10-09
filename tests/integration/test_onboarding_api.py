"""Integración del BFF con adaptadores fake, ejercitando la API completa por HTTP."""

from __future__ import annotations

from tests.conftest import REGISTRO_VALIDO

SOLICITUD_COTIZACION = {
    "datosCredito": {
        "valorCredito": 120000000,
        "plazoMeses": 120,
        "edad": 35,
        "entidadAcreedora": "Banco Solventa",
        "saldoInsoluto": 100000000,
    },
    "cuestionarioHabitos": {
        "consumeTabaco": False,
        "actividadFisica": "REGULAR",
        "condicionesPreexistentes": False,
        "dependientesEconomicos": 1,
    },
}


async def test_journey_completo(client):
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    assert registro.status_code == 201
    cuerpo = registro.json()
    assert cuerpo["cuenta"]["estado"] == "ACTIVO"
    assert cuerpo["sesion"]["tokenType"] == "Bearer"

    headers = {"Authorization": f"Bearer {cuerpo['sesion']['accessToken']}"}

    cuenta = await client.get("/v1/cuenta", headers=headers)
    assert cuenta.status_code == 200
    assert cuenta.json()["email"] == REGISTRO_VALIDO["email"]

    otorgar = await client.post(
        "/v1/cuenta/consentimientos",
        headers=headers,
        json={"scope": "OPEN_FINANCE", "politicaVersion": "2026-01"},
    )
    assert otorgar.status_code == 201
    assert otorgar.json()["vigente"] is True

    lista = await client.get("/v1/cuenta/consentimientos", headers=headers)
    assert [c["scope"] for c in lista.json()] == ["OPEN_FINANCE"]

    revocar = await client.delete("/v1/cuenta/consentimientos/OPEN_FINANCE", headers=headers)
    assert revocar.status_code == 204

    lista = await client.get("/v1/cuenta/consentimientos", headers=headers)
    assert lista.json()[0]["vigente"] is False


async def test_login_y_refresco(client):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)

    login = await client.post(
        "/v1/sesiones",
        json={"email": REGISTRO_VALIDO["email"], "password": REGISTRO_VALIDO["password"]},
    )
    assert login.status_code == 200

    refresco = await client.post(
        "/v1/sesiones/refresco", json={"refreshToken": login.json()["refreshToken"]}
    )
    assert refresco.status_code == 200
    assert refresco.json()["accessToken"]


async def test_login_credenciales_invalidas(client):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)
    resp = await client.post(
        "/v1/sesiones",
        json={"email": REGISTRO_VALIDO["email"], "password": "incorrecta12"},
    )
    assert resp.status_code == 401


async def test_registro_duplicado_devuelve_409(client):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)
    repetido = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    assert repetido.status_code == 409
    assert repetido.json()["errores"] == [
        {"campo": "correo", "mensaje": "El correo ya está registrado"}
    ]


async def test_registro_con_documento_repetido_indica_el_campo_documento(client):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)
    repetido = await client.post(
        "/v1/registro", json={**REGISTRO_VALIDO, "email": "otra.persona@example.com"}
    )
    assert repetido.status_code == 409
    assert repetido.json()["errores"][0]["campo"] == "documento"


async def test_reintento_con_idempotency_key_devuelve_la_misma_cuenta(client):
    cabeceras = {"Idempotency-Key": "reintento-1"}
    primero = await client.post("/v1/registro", json=REGISTRO_VALIDO, headers=cabeceras)
    segundo = await client.post("/v1/registro", json=REGISTRO_VALIDO, headers=cabeceras)

    assert primero.status_code == 201
    assert segundo.status_code == 201
    assert segundo.json()["cuenta"]["clienteId"] == primero.json()["cuenta"]["clienteId"]


async def test_idempotency_key_demasiado_larga_devuelve_400(client):
    resp = await client.post(
        "/v1/registro", json=REGISTRO_VALIDO, headers={"Idempotency-Key": "x" * 65}
    )
    assert resp.status_code == 400


async def test_registro_sin_telefono_devuelve_400(client):
    sin_telefono = {k: v for k, v in REGISTRO_VALIDO.items() if k != "telefono"}
    resp = await client.post("/v1/registro", json=sin_telefono)
    assert resp.status_code == 400
    assert any(e["campo"].endswith("telefono") for e in resp.json()["errores"])


async def test_body_invalido_devuelve_400(client):
    resp = await client.post("/v1/registro", json={"email": "malo", "password": "x"})
    assert resp.status_code == 400
    assert resp.json()["errores"]


async def test_sin_token_devuelve_401(client):
    assert (await client.get("/v1/cuenta")).status_code == 401
    assert (
        await client.get("/v1/cuenta", headers={"Authorization": "Bearer basura"})
    ).status_code == 401


async def test_token_valido_pero_cliente_inexistente_devuelve_404(client):
    """Un token bien firmado cuyo clienteId ya no está en core -> 404."""
    import jwt

    token = jwt.encode(
        {"sub": "s", "clienteId": "fantasma", "email": "a@b.com", "typ": "access"},
        "test-secret",
        algorithm="HS256",
    )
    resp = await client.get("/v1/cuenta", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


async def test_health(client):
    resp = await client.get("/health")
    assert resp.json()["status"] == "ok"


async def test_disponibilidad(client):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)

    ocupado = await client.get(
        "/v1/registro/disponibilidad", params={"correo": REGISTRO_VALIDO["email"]}
    )
    assert ocupado.status_code == 200
    assert ocupado.json()["correoDisponible"] is False

    libre = await client.get("/v1/registro/disponibilidad", params={"correo": "libre@example.com"})
    assert libre.json()["correoDisponible"] is True


async def test_reenvio_confirmacion_requiere_token_de_identity_platform(client, app):
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    assert registro.status_code == 201
    sub = app.state.deps.identity._por_email[REGISTRO_VALIDO["email"]][0]

    ok = await client.post(
        "/v1/registro/reenvio-confirmacion", headers={"Authorization": f"Bearer {sub}"}
    )
    assert ok.status_code == 204

    sin_token = await client.post("/v1/registro/reenvio-confirmacion")
    assert sin_token.status_code == 401


async def test_confirmacion_de_cuenta_con_el_codigo_del_enlace(client, app):
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    assert registro.status_code == 201
    sub = app.state.deps.identity._por_email[REGISTRO_VALIDO["email"]][0]
    codigo = app.state.deps.identity.emitir_codigo_verificacion(sub)

    # Es público: el usuario puede abrir el enlace sin sesión, en otro dispositivo.
    confirmada = await client.post("/v1/registro/confirmacion", json={"oobCode": codigo})

    assert confirmada.status_code == 200
    assert confirmada.json()["correoConfirmado"] is True
    assert confirmada.json()["clienteId"] == registro.json()["cuenta"]["clienteId"]
    assert confirmada.headers["cache-control"] == "no-store"


async def test_el_codigo_del_enlace_es_de_un_solo_uso(client, app):
    await client.post("/v1/registro", json=REGISTRO_VALIDO)
    sub = app.state.deps.identity._por_email[REGISTRO_VALIDO["email"]][0]
    codigo = app.state.deps.identity.emitir_codigo_verificacion(sub)
    await client.post("/v1/registro/confirmacion", json={"oobCode": codigo})

    segundo = await client.post("/v1/registro/confirmacion", json={"oobCode": codigo})

    assert segundo.status_code == 422
    assert "ya se usó" in segundo.json()["detail"]


async def test_confirmacion_con_un_codigo_desconocido_devuelve_422(client):
    resp = await client.post("/v1/registro/confirmacion", json={"oobCode": "codigo-que-no-existe"})
    assert resp.status_code == 422


async def test_confirmacion_con_un_codigo_mal_formado_devuelve_400_sin_repetirlo(client):
    for malo in ("corto", "con espacios en el codigo", "con/caracteres$raros" * 2, "x" * 513):
        resp = await client.post("/v1/registro/confirmacion", json={"oobCode": malo})
        assert resp.status_code == 400
        assert malo not in resp.text


async def test_confirmacion_sin_codigo_devuelve_400(client):
    resp = await client.post("/v1/registro/confirmacion", json={})
    assert resp.status_code == 400


async def test_journey_cotizacion(cliente_autenticado):
    client, headers = cliente_autenticado

    creada = await client.post("/v1/cotizaciones", headers=headers, json=SOLICITUD_COTIZACION)
    assert creada.status_code == 201
    cuerpo = creada.json()
    assert cuerpo["estado"] == "VIGENTE"
    assert cuerpo["producto"] == "VIDA_HIPOTECARIO"
    assert cuerpo["oferta"]["primaMensual"] > 0

    obtenida = await client.get(f"/v1/cotizaciones/{cuerpo['id']}", headers=headers)
    assert obtenida.status_code == 200
    assert obtenida.json()["id"] == cuerpo["id"]


async def test_cotizacion_sin_token_devuelve_401(client):
    resp = await client.post("/v1/cotizaciones", json=SOLICITUD_COTIZACION)
    assert resp.status_code == 401


async def test_cotizacion_inexistente_devuelve_404(cliente_autenticado):
    client, headers = cliente_autenticado
    resp = await client.get("/v1/cotizaciones/no-existe", headers=headers)
    assert resp.status_code == 404
