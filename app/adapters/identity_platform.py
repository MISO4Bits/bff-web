"""Adaptador HTTP hacia Identity Platform (Firebase Auth REST / emulador)."""

from __future__ import annotations

import logging

import httpx

from app.domain import BffError, Conflicto, NoAutorizado, SolicitudInvalida
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_web.adapters.identity_platform")


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
                logger.info("Identity Platform: correo ya registrado")
                raise Conflicto.por_campo("correo")
            logger.info("Identity Platform: registro rechazado (%s)", mensaje)
            raise SolicitudInvalida(mensaje or "registro rechazado por el proveedor")
        _asegurar_ok(resp)
        sub = resp.json()["localId"]
        logger.info("Identity Platform: cuenta creada sub=%s", sub)
        return sub

    async def autenticar(self, email: str, password: str) -> str:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:signInWithPassword",
            params=self._params,
            json={"email": email, "password": password, "returnSecureToken": True},
        )
        if resp.status_code == 400:
            logger.info("Identity Platform: credenciales inválidas")
            raise NoAutorizado("credenciales inválidas")
        _asegurar_ok(resp)
        sub = resp.json()["localId"]
        logger.info("Identity Platform: autenticación exitosa sub=%s", sub)
        return sub

    async def eliminar(self, sub: str) -> None:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:delete",
            params=self._params,
            json={"localId": sub},
        )
        _asegurar_ok(resp)
        logger.info("Identity Platform: credencial eliminada sub=%s", sub)

    async def enviar_verificacion(self, id_token: str) -> None:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:sendOobCode",
            params=self._params,
            json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
        )
        if resp.status_code == 400:
            logger.info(
                "Identity Platform: no se pudo reenviar la verificación (%s)", _mensaje_error(resp)
            )
            raise NoAutorizado("token inválido o expirado")
        _asegurar_ok(resp)
        logger.info("Identity Platform: verificación reenviada")

    async def verificar_correo(self, id_token: str) -> tuple[str, bool]:
        resp = await self._http.request(
            "POST",
            "/v1/accounts:lookup",
            params=self._params,
            json={"idToken": id_token},
        )
        if resp.status_code == 400:
            logger.info(
                "Identity Platform: token inválido en verificar_correo (%s)", _mensaje_error(resp)
            )
            raise NoAutorizado("token inválido o expirado")
        _asegurar_ok(resp)
        usuarios = resp.json().get("users", [])
        if not usuarios:
            raise NoAutorizado("token inválido o expirado")
        usuario = usuarios[0]
        return usuario["localId"], bool(usuario.get("emailVerified", False))


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
