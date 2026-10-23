from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.adapters.fakes import FakeEvidencias
from app.api.app import create_app
from app.config import Settings
from app.services import EvidenciasIdentidadService
from tests.conftest import REGISTRO_VALIDO

RUTA = "/v1/cuenta/evidencias-identidad"
IMAGEN = b"\xff\xd8\xff" + b"x" * 200


def _subir(client, headers, tipo="cedula_frente", imagen=IMAGEN):
    return client.post(
        RUTA,
        headers=headers,
        data={"tipo": tipo},
        files={"imagen": ("foto.jpg", imagen, "image/jpeg")},
    )


async def test_una_foto_aceptada_responde_201(cliente_autenticado):
    client, headers = cliente_autenticado

    respuesta = await _subir(client, headers)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ACEPTADA"
    assert cuerpo["tipo"] == "cedula_frente"
    assert cuerpo["evidenciaId"]
    assert cuerpo["motivo"] is None
    assert cuerpo["intentosRestantes"] == 3
    assert "numeroDetectado" not in cuerpo
    assert respuesta.headers["cache-control"] == "no-store"


async def test_un_rechazo_responde_200_con_el_motivo(app, cliente_autenticado):
    client, headers = cliente_autenticado
    app.state.evidencias = EvidenciasIdentidadService(
        FakeEvidencias(("REFLEJO",)), max_bytes=3_000_000
    )

    respuesta = await _subir(client, headers, "cedula_reverso")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "evidenciaId": None,
        "tipo": "cedula_reverso",
        "estado": "REINTENTAR",
        "motivo": "REFLEJO",
        "intentosRestantes": 2,
    }


async def test_el_estado_lista_los_tres_tipos_y_refleja_lo_subido(cliente_autenticado):
    client, headers = cliente_autenticado

    antes = (await client.get(RUTA, headers=headers)).json()
    await _subir(client, headers, "cedula_frente")
    respuesta = await client.get(RUTA, headers=headers)
    despues = {e["tipo"]: e for e in respuesta.json()}

    assert [e["tipo"] for e in antes] == ["cedula_frente", "cedula_reverso", "selfie"]
    assert {e["estado"] for e in antes} == {"PENDIENTE"}
    assert despues["cedula_frente"]["estado"] == "ACEPTADA"
    assert despues["cedula_frente"]["creadaEn"]
    assert despues["cedula_reverso"]["estado"] == "PENDIENTE"
    assert respuesta.headers["cache-control"] == "no-store"


async def test_el_estado_es_del_cliente_del_token(client):
    uno = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    otro = await client.post(
        "/v1/registro",
        json={
            **REGISTRO_VALIDO,
            "email": "otro@example.com",
            "numeroDocumento": "9988776655",
            "telefono": "+573009998877",
        },
    )
    cabecera_uno = {"Authorization": f"Bearer {uno.json()['sesion']['accessToken']}"}
    cabecera_otro = {"Authorization": f"Bearer {otro.json()['sesion']['accessToken']}"}

    await _subir(client, cabecera_uno)
    del_otro = {
        e["tipo"]: e["estado"] for e in (await client.get(RUTA, headers=cabecera_otro)).json()
    }

    assert del_otro["cedula_frente"] == "PENDIENTE"


async def test_sin_token_responde_401(client):
    subida = await client.post(
        RUTA, data={"tipo": "selfie"}, files={"imagen": ("f.jpg", IMAGEN, "image/jpeg")}
    )
    estado = await client.get(RUTA)

    assert subida.status_code == estado.status_code == 401


async def test_un_tipo_fuera_de_la_lista_responde_400(cliente_autenticado):
    client, headers = cliente_autenticado

    respuesta = await _subir(client, headers, "pasaporte")

    assert respuesta.status_code == 400
    assert any("tipo" in e["campo"] for e in respuesta.json()["errores"])


async def test_sin_tipo_o_sin_imagen_responde_400(cliente_autenticado):
    client, headers = cliente_autenticado

    sin_tipo = await client.post(
        RUTA, headers=headers, files={"imagen": ("f.jpg", IMAGEN, "image/jpeg")}
    )
    sin_imagen = await client.post(RUTA, headers=headers, data={"tipo": "selfie"})

    assert sin_tipo.status_code == sin_imagen.status_code == 400


async def test_una_imagen_vacia_responde_400(cliente_autenticado):
    client, headers = cliente_autenticado

    respuesta = await _subir(client, headers, imagen=b"")

    assert respuesta.status_code == 400


@pytest.fixture
async def cliente_con_limite_bajo():
    app = create_app(Settings(adapters="fake", session_secret="t", evidencia_max_bytes=100))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
        yield client, {"Authorization": f"Bearer {registro.json()['sesion']['accessToken']}"}


async def test_una_imagen_sobre_el_limite_responde_413(cliente_con_limite_bajo):
    client, headers = cliente_con_limite_bajo

    respuesta = await _subir(
        client, headers, imagen=b"x" * 200
    )  # cabe en el multipart, no en el límite

    assert respuesta.status_code == 413
    assert respuesta.json()["title"] == "Imagen demasiado grande"


async def test_un_cuerpo_enorme_se_rechaza_sin_procesarlo(cliente_con_limite_bajo):
    client, headers = cliente_con_limite_bajo

    respuesta = await _subir(client, headers, imagen=b"x" * 200_000)

    assert respuesta.status_code == 413
    assert respuesta.headers["content-type"].startswith("application/problem+json")


async def test_el_limite_no_afecta_a_otras_rutas(cliente_con_limite_bajo):
    client, headers = cliente_con_limite_bajo

    assert (await client.get("/v1/cuenta", headers=headers)).status_code == 200


# ---------- de punta a punta contra un svc-core simulado ----------

CORE = "http://core.test"


@pytest.fixture
async def http_client_con_sesion():
    app = create_app(
        Settings(adapters="http", core_base_url=CORE, session_secret="t", http_retries=1)
    )
    from datetime import date

    sesion = app.state.sessions.emitir(
        sub="sub-1", cliente_id="c-7", email="ana@example.com", fecha_nacimiento=date(1991, 5, 20)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, {"Authorization": f"Bearer {sesion.access_token}"}


@respx.mock
async def test_la_subida_llega_a_core_con_el_cliente_del_token(http_client_con_sesion):
    client, headers = http_client_con_sesion
    en_core = respx.post(f"{CORE}/v1/clientes/c-7/evidencias-identidad").mock(
        return_value=httpx.Response(
            201,
            json={
                "evidenciaId": "e9",
                "tipo": "selfie",
                "estado": "REVISION_MANUAL",
                "motivo": None,
                "intentosRestantes": 3,
            },
        )
    )

    respuesta = await _subir(client, headers, "selfie")

    assert respuesta.status_code == 201
    assert respuesta.json()["estado"] == "REVISION_MANUAL"
    assert b'name="tipo"' in en_core.calls.last.request.content
    assert IMAGEN in en_core.calls.last.request.content


@respx.mock
async def test_si_core_no_responde_la_subida_falla_con_503_sin_reintentar(http_client_con_sesion):
    client, headers = http_client_con_sesion
    en_core = respx.post(f"{CORE}/v1/clientes/c-7/evidencias-identidad").mock(
        return_value=httpx.Response(503)
    )

    respuesta = await _subir(client, headers)

    assert respuesta.status_code == 503
    assert en_core.call_count == 1


async def test_la_selfie_sin_la_cedula_responde_422(cliente_autenticado):
    client, headers = cliente_autenticado

    respuesta = await _subir(client, headers, "selfie")

    assert respuesta.status_code == 422
    assert respuesta.headers["content-type"].startswith("application/problem+json")
    assert "cédula" in respuesta.json()["detail"]


async def test_la_selfie_se_acepta_cuando_la_cedula_ya_esta(cliente_autenticado):
    client, headers = cliente_autenticado
    await _subir(client, headers, "cedula_frente")
    await _subir(client, headers, "cedula_reverso")

    respuesta = await _subir(client, headers, "selfie")

    assert respuesta.status_code == 201
    assert respuesta.json()["estado"] == "ACEPTADA"


async def test_un_motivo_de_la_selfie_llega_al_cliente(app, cliente_autenticado):
    client, headers = cliente_autenticado
    app.state.evidencias = EvidenciasIdentidadService(
        FakeEvidencias(("ACEPTADA", "ACEPTADA", "POSIBLE_SUPLANTACION")), max_bytes=3_000_000
    )
    await _subir(client, headers, "cedula_frente")
    await _subir(client, headers, "cedula_reverso")

    respuesta = await _subir(client, headers, "selfie")

    assert respuesta.status_code == 200
    assert respuesta.json()["motivo"] == "POSIBLE_SUPLANTACION"
