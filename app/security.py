"""Sesión del journey: el BFF emite y valida su propio JWT.

En producción el token del cliente puede ser el de Identity Platform (validado en
firma/expiración por el API Gateway); el BFF solo lee los claims de negocio. Aquí
el BFF emite un JWT de sesión propio para que el servicio sea standalone y
verificable en pruebas.
"""

from __future__ import annotations

import time

import jwt

from app.domain import Claims, NoAutorizado, Sesion

_ALG = "HS256"


class SessionIssuer:
    def __init__(
        self, secret: str, *, ttl_seconds: int = 3600, refresh_ttl_seconds: int = 86400
    ) -> None:
        self._secret = secret
        self._ttl = ttl_seconds
        self._refresh_ttl = refresh_ttl_seconds

    def emitir(self, *, sub: str, cliente_id: str, email: str) -> Sesion:
        ahora = int(time.time())
        base = {"sub": sub, "clienteId": cliente_id, "email": email, "iat": ahora}
        access = jwt.encode(
            {**base, "typ": "access", "exp": ahora + self._ttl}, self._secret, algorithm=_ALG
        )
        refresh = jwt.encode(
            {**base, "typ": "refresh", "exp": ahora + self._refresh_ttl},
            self._secret,
            algorithm=_ALG,
        )
        return Sesion(access_token=access, refresh_token=refresh, expires_in=self._ttl)

    def _decodificar(self, token: str, *, tipo: str) -> dict:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[_ALG])
        except jwt.ExpiredSignatureError as exc:
            raise NoAutorizado("token expirado") from exc
        except jwt.InvalidTokenError as exc:
            raise NoAutorizado("token inválido") from exc
        if payload.get("typ") != tipo:
            raise NoAutorizado("tipo de token incorrecto")
        return payload

    def verificar(self, token: str) -> Claims:
        payload = self._decodificar(token, tipo="access")
        return Claims(sub=payload["sub"], cliente_id=payload["clienteId"], email=payload["email"])

    def refrescar(self, refresh_token: str) -> Sesion:
        payload = self._decodificar(refresh_token, tipo="refresh")
        return self.emitir(
            sub=payload["sub"],
            cliente_id=payload["clienteId"],
            email=payload["email"],
        )
