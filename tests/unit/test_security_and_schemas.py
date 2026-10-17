from __future__ import annotations

import time
from datetime import date

import jwt
import pytest
from pydantic import ValidationError

from app.api.schemas import RegistroRequest, SesionOut
from app.domain import NoAutorizado
from app.security import SessionIssuer

NACIMIENTO = date(1990, 1, 1)

VALIDO = {
    "email": "ana@example.com",
    "password": "claveSegura12",
    "tipoDocumento": "CC",
    "numeroDocumento": "123456",
    "primerNombre": "Ana",
    "primerApellido": "Ríos",
    "fechaNacimiento": "1990-01-01",
    "telefono": "+573001234567",
    "politicaVersion": "v1",
    "aceptaTerminos": True,
    "autorizaTratamientoDatos": True,
    "autorizaDatosFinancieros": True,
}


def test_registro_request_exige_telefono():
    sin_telefono = {k: v for k, v in VALIDO.items() if k != "telefono"}
    with pytest.raises(ValidationError):
        RegistroRequest.model_validate(sin_telefono)


def test_registro_request_valido():
    req = RegistroRequest.model_validate(VALIDO)
    assert req.acepta_terminos is True


@pytest.mark.parametrize(
    "override",
    [
        {"password": "corta"},
        {"aceptaTerminos": False},
        {"email": "no-email"},
        {"tipoDocumento": "XX"},
        {"otro": 1},
    ],
)
def test_registro_request_invalido(override):
    with pytest.raises(ValidationError):
        RegistroRequest.model_validate({**VALIDO, **override})


def test_sesion_out_por_alias():
    data = SesionOut(access_token="a", expires_in=10, refresh_token="r").model_dump(by_alias=True)
    assert data["accessToken"] == "a"
    assert data["tokenType"] == "Bearer"


def test_session_issuer_emitir_y_verificar():
    issuer = SessionIssuer("secreto", ttl_seconds=60)
    sesion = issuer.emitir(sub="s1", cliente_id="c1", email="a@b.com", fecha_nacimiento=NACIMIENTO)

    claims = issuer.verificar(sesion.access_token)
    assert claims.sub == "s1"
    assert claims.cliente_id == "c1"


def test_session_issuer_refrescar():
    issuer = SessionIssuer("secreto")
    sesion = issuer.emitir(sub="s1", cliente_id="c1", email="a@b.com", fecha_nacimiento=NACIMIENTO)
    nueva = issuer.refrescar(sesion.refresh_token)
    assert issuer.verificar(nueva.access_token).cliente_id == "c1"


def test_verificar_rechaza_token_de_otro_secreto():
    issuer = SessionIssuer("secreto")
    otro = SessionIssuer("otro-secreto").emitir(
        sub="s", cliente_id="c", email="a@b.com", fecha_nacimiento=NACIMIENTO
    )
    with pytest.raises(NoAutorizado):
        issuer.verificar(otro.access_token)


def test_verificar_rechaza_token_expirado():
    issuer = SessionIssuer("secreto")
    vencido = jwt.encode(
        {
            "sub": "s",
            "clienteId": "c",
            "email": "a@b.com",
            "typ": "access",
            "exp": int(time.time()) - 10,
        },
        "secreto",
        algorithm="HS256",
    )
    with pytest.raises(NoAutorizado):
        issuer.verificar(vencido)


def test_verificar_rechaza_tipo_incorrecto():
    issuer = SessionIssuer("secreto")
    sesion = issuer.emitir(sub="s", cliente_id="c", email="a@b.com", fecha_nacimiento=NACIMIENTO)
    # el refresh no sirve como access
    with pytest.raises(NoAutorizado):
        issuer.verificar(sesion.refresh_token)


def test_la_sesion_lleva_la_fecha_de_nacimiento_en_el_token():
    issuer = SessionIssuer("secreto")
    sesion = issuer.emitir(sub="s", cliente_id="c", email="a@b.com", fecha_nacimiento=NACIMIENTO)

    assert issuer.verificar(sesion.access_token).fecha_nacimiento == NACIMIENTO
    assert issuer.verificar(
        issuer.refrescar(sesion.refresh_token).access_token
    ).fecha_nacimiento == (NACIMIENTO)


def _token_sin_fecha(tipo: str) -> str:
    return jwt.encode(
        {
            "sub": "s",
            "clienteId": "c",
            "email": "a@b.com",
            "typ": tipo,
            "exp": int(time.time()) + 60,
        },
        "secreto",
        algorithm="HS256",
    )


def test_una_sesion_vieja_sin_fecha_se_verifica_pero_sin_fecha():
    # El BFF la deja pasar: cada operación que necesita la fecha pide iniciar sesión de nuevo.
    assert SessionIssuer("secreto").verificar(_token_sin_fecha("access")).fecha_nacimiento is None


def test_una_sesion_vieja_sin_fecha_no_se_puede_renovar():
    with pytest.raises(NoAutorizado):
        SessionIssuer("secreto").refrescar(_token_sin_fecha("refresh"))


def test_una_fecha_de_nacimiento_ilegible_en_el_token_se_rechaza():
    token = jwt.encode(
        {
            "sub": "s",
            "clienteId": "c",
            "email": "a@b.com",
            "fechaNacimiento": "no-es-una-fecha",
            "typ": "access",
            "exp": int(time.time()) + 60,
        },
        "secreto",
        algorithm="HS256",
    )
    with pytest.raises(NoAutorizado):
        SessionIssuer("secreto").verificar(token)
