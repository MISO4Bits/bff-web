from __future__ import annotations

import time

import jwt
import pytest
from pydantic import ValidationError

from app.api.schemas import RegistroRequest, SesionOut
from app.domain import NoAutorizado
from app.security import SessionIssuer

VALIDO = {
    "email": "ana@example.com",
    "password": "claveSegura12",
    "tipoDocumento": "CC",
    "numeroDocumento": "123456",
    "primerNombre": "Ana",
    "primerApellido": "Ríos",
    "fechaNacimiento": "1990-01-01",
    "politicaVersion": "v1",
    "aceptaTerminos": True,
}


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
    sesion = issuer.emitir(sub="s1", cliente_id="c1", email="a@b.com")

    claims = issuer.verificar(sesion.access_token)
    assert claims.sub == "s1"
    assert claims.cliente_id == "c1"


def test_session_issuer_refrescar():
    issuer = SessionIssuer("secreto")
    sesion = issuer.emitir(sub="s1", cliente_id="c1", email="a@b.com")
    nueva = issuer.refrescar(sesion.refresh_token)
    assert issuer.verificar(nueva.access_token).cliente_id == "c1"


def test_verificar_rechaza_token_de_otro_secreto():
    issuer = SessionIssuer("secreto")
    otro = SessionIssuer("otro-secreto").emitir(sub="s", cliente_id="c", email="a@b.com")
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
    sesion = issuer.emitir(sub="s", cliente_id="c", email="a@b.com")
    # el refresh no sirve como access
    with pytest.raises(NoAutorizado):
        issuer.verificar(sesion.refresh_token)
