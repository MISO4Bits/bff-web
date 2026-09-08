"""Adaptador HTTP hacia svc-core (``ICustomerIdentity``)."""

from __future__ import annotations

import httpx

from app.domain import (
    BffError,
    ClienteCore,
    Conflicto,
    ConsentimientoVista,
    RecursoNoEncontrado,
    RegistroInput,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient


class CoreClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def registrar_cliente(self, identity_ref: str, datos: RegistroInput) -> ClienteCore:
        cuerpo = {
            "identityRef": identity_ref,
            "tipoDocumento": datos.tipo_documento,
            "numeroDocumento": datos.numero_documento,
            "primerNombre": datos.primer_nombre,
            "primerApellido": datos.primer_apellido,
            "fechaNacimiento": datos.fecha_nacimiento.isoformat(),
            "email": datos.email,
        }
        if datos.segundo_nombre:
            cuerpo["segundoNombre"] = datos.segundo_nombre
        if datos.segundo_apellido:
            cuerpo["segundoApellido"] = datos.segundo_apellido
        if datos.telefono:
            cuerpo["telefono"] = datos.telefono

        resp = await self._http.request("POST", "/clientes", json=cuerpo)
        if resp.status_code == 409:
            raise Conflicto("El documento ya está registrado")
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp, esperado=201)
        return _a_cliente(resp.json())

    async def obtener_cliente(self, cliente_id: str) -> ClienteCore:
        resp = await self._http.request("GET", f"/clientes/{cliente_id}")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return _a_cliente(resp.json())

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        resp = await self._http.request("GET", "/clientes", params={"identityRef": identity_ref})
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return _a_cliente(resp.json())

    async def listar_consentimientos(self, cliente_id: str) -> list[ConsentimientoVista]:
        resp = await self._http.request("GET", f"/clientes/{cliente_id}/consentimientos")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return [_a_consentimiento(item) for item in resp.json()]

    async def otorgar_consentimiento(
        self, cliente_id: str, scope: str, politica_version: str, canal: str
    ) -> ConsentimientoVista:
        resp = await self._http.request(
            "POST",
            f"/clientes/{cliente_id}/consentimientos",
            json={"scope": scope, "politicaVersion": politica_version, "canal": canal},
        )
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp, esperado=201)
        return _a_consentimiento(resp.json())

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        resp = await self._http.request("DELETE", f"/clientes/{cliente_id}/consentimientos/{scope}")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Consentimiento no encontrado")
        _asegurar_ok(resp, esperado=204)


def _detalle(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", "")
    except ValueError:  # pragma: no cover
        return ""


def _asegurar_ok(resp: httpx.Response, *, esperado: int = 200) -> None:
    if resp.status_code != esperado and resp.status_code >= 400:
        raise BffError(f"svc-core respondió {resp.status_code}")


def _a_cliente(data: dict) -> ClienteCore:
    return ClienteCore(
        id=data["id"],
        primer_nombre=data["primerNombre"],
        primer_apellido=data["primerApellido"],
        email=data["email"],
        estado=data["estado"],
        segundo_nombre=data.get("segundoNombre"),
        segundo_apellido=data.get("segundoApellido"),
        telefono=data.get("telefono"),
    )


def _a_consentimiento(data: dict) -> ConsentimientoVista:
    estado = data["estado"]
    return ConsentimientoVista(
        scope=data["scope"],
        estado=estado,
        vigente=data.get("vigente", estado == "OTORGADO"),
        actualizado_en=None,
    )
