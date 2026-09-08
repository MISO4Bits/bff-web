"""Adaptador HTTP hacia Identity Platform (Firebase Auth REST / emulador)."""

from __future__ import annotations

import httpx

from app.domain import BffError, Conflicto, NoAutorizado, SolicitudInvalida
from app.resilience import ResilientHttpClient


class IdentityPlatformAdapter:
    def __init__(self, http: ResilientHttpClient, api_key: str) -> None:
        self._http = http
        self._params = {"key": api_key}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def registrar(self, email: str, password: str) -> str:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:signUp",
            params=self._params,
            json={"email": email, "password": password, "returnSecureToken": True},
        )
        if resp.status_code == 400:
            mensaje = _mensaje_error(resp)
            if "EMAIL_EXISTS" in mensaje:
                raise Conflicto("El correo ya está registrado")
            raise SolicitudInvalida(mensaje or "registro rechazado por el proveedor")
        _asegurar_ok(resp)
        return resp.json()["localId"]

    async def autenticar(self, email: str, password: str) -> str:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:signInWithPassword",
            params=self._params,
            json={"email": email, "password": password, "returnSecureToken": True},
        )
        if resp.status_code == 400:
            raise NoAutorizado("credenciales inválidas")
        _asegurar_ok(resp)
        return resp.json()["localId"]

    async def eliminar(self, sub: str) -> None:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:delete",
            params=self._params,
            json={"localId": sub},
        )
        _asegurar_ok(resp)


def _mensaje_error(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", "")
    except ValueError:  # pragma: no cover - respuesta no JSON
        return ""


def _asegurar_ok(resp: httpx.Response) -> None:
    if resp.status_code >= 500:
        raise BffError(f"Identity Platform respondió {resp.status_code}")
    if resp.status_code >= 400:
        raise BffError(f"Identity Platform respondió {resp.status_code}: {_mensaje_error(resp)}")
