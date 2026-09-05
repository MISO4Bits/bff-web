"""Pruebas de contrato: la implementación del BFF cumple ``openapi/openapi.yaml``."""

from __future__ import annotations

import re

from jsonschema import Draft202012Validator

from tests.conftest import REGISTRO_VALIDO

_PATH_PARAM = re.compile(r"\{[^}]+\}")
_METODOS = {"get", "post", "put", "patch", "delete"}


def _normalize(method: str, path: str) -> tuple[str, str]:
    return method.upper(), _PATH_PARAM.sub("{}", path)


def _spec_ops(spec: dict) -> set[tuple[str, str]]:
    return {
        _normalize(m, p)
        for p, item in spec["paths"].items()
        for m in item
        if m.lower() in _METODOS
    }


def _app_ops(app) -> set[tuple[str, str]]:
    return {
        _normalize(m, p)
        for p, item in app.openapi()["paths"].items()
        for m in item
        if m.lower() in _METODOS
    }


def test_contrato_y_codigo_exponen_las_mismas_operaciones(app, openapi_spec):
    assert _spec_ops(openapi_spec) == _app_ops(app)


def _validar(spec: dict, ref: str, instancia) -> None:
    schema = {"$ref": f"#/components/schemas/{ref}", "components": spec["components"]}
    errores = sorted(Draft202012Validator(schema).iter_errors(instancia), key=str)
    assert not errores, f"{ref}: {[e.message for e in errores]}"


async def test_respuestas_cumplen_el_contrato(client, openapi_spec):
    registro = await client.post("/v1/registro", json=REGISTRO_VALIDO)
    assert registro.status_code == 201
    _validar(openapi_spec, "RegistroResponse", registro.json())

    token = registro.json()["sesion"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}

    sesion = await client.post(
        "/v1/sesiones",
        json={"email": REGISTRO_VALIDO["email"], "password": REGISTRO_VALIDO["password"]},
    )
    _validar(openapi_spec, "Sesion", sesion.json())

    cuenta = await client.get("/v1/cuenta", headers=headers)
    assert cuenta.status_code == 200
    _validar(openapi_spec, "Cuenta", cuenta.json())

    otorgado = await client.post(
        "/v1/cuenta/consentimientos",
        headers=headers,
        json={"scope": "OPEN_FINANCE", "politicaVersion": "v1"},
    )
    assert otorgado.status_code == 201
    _validar(openapi_spec, "ConsentimientoVista", otorgado.json())

    lista = await client.get("/v1/cuenta/consentimientos", headers=headers)
    for item in lista.json():
        _validar(openapi_spec, "ConsentimientoVista", item)


async def test_errores_cumplen_problem_details(client, openapi_spec):
    sin_token = await client.get("/v1/cuenta")
    assert sin_token.status_code == 401
    assert sin_token.headers["content-type"].startswith("application/problem+json")
    _validar(openapi_spec, "Problema", sin_token.json())

    malo = await client.post("/v1/registro", json={"email": "x"})
    assert malo.status_code == 400
    _validar(openapi_spec, "Problema", malo.json())


async def test_expone_el_contrato(client):
    resp = await client.get("/openapi.yaml")
    assert resp.status_code == 200
    assert "openapi" in resp.text
