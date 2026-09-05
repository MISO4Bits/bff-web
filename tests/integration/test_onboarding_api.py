"""Integración del BFF con adaptadores fake, ejercitando la API completa por HTTP."""

from __future__ import annotations

from tests.conftest import REGISTRO_VALIDO


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
