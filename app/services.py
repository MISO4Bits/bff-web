"""Orquestación del journey de originación."""

from __future__ import annotations

import logging

from app.domain import (
    BffError,
    Cuenta,
    RegistroInput,
    Sesion,
)
from app.ports import CoreIdentityPort, IdentityProviderPort
from app.security import SessionIssuer

logger = logging.getLogger("bff_web.onboarding")


class OnboardingService:
    def __init__(
        self,
        identity: IdentityProviderPort,
        core: CoreIdentityPort,
        sessions: SessionIssuer,
    ) -> None:
        self._identity = identity
        self._core = core
        self._sessions = sessions

    async def registrar(self, datos: RegistroInput) -> tuple[Cuenta, Sesion]:
        sub = await self._identity.registrar(datos.email, datos.password)
        try:
            cliente = await self._core.registrar_cliente(sub, datos)
        except BffError:
            await self._compensar(sub)
            raise
        except Exception:  # noqa: BLE001 - garantiza que no queden credenciales huérfanas
            await self._compensar(sub)
            raise

        sesion = self._sessions.emitir(sub=sub, cliente_id=cliente.id, email=cliente.email)
        return Cuenta.desde_core(cliente), sesion

    async def _compensar(self, sub: str) -> None:
        try:
            await self._identity.eliminar(sub)
        except Exception:  # noqa: BLE001
            logger.warning("no se pudo revertir la credencial sub=%s", sub)

    async def iniciar_sesion(self, email: str, password: str) -> Sesion:
        sub = await self._identity.autenticar(email, password)
        cliente = await self._core.buscar_cliente_por_identidad(sub)
        return self._sessions.emitir(sub=sub, cliente_id=cliente.id, email=cliente.email)

    def refrescar(self, refresh_token: str) -> Sesion:
        return self._sessions.refrescar(refresh_token)

    async def obtener_cuenta(self, cliente_id: str) -> Cuenta:
        cliente = await self._core.obtener_cliente(cliente_id)
        return Cuenta.desde_core(cliente)

    async def listar_consentimientos(self, cliente_id: str):
        return await self._core.listar_consentimientos(cliente_id)

    async def otorgar_consentimiento(self, cliente_id: str, scope: str, politica_version: str):
        return await self._core.otorgar_consentimiento(cliente_id, scope, politica_version, "WEB")

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        await self._core.revocar_consentimiento(cliente_id, scope)
