"""Orquestación del journey de originación."""

from __future__ import annotations

import logging

from app.domain import (
    BffError,
    Cotizacion,
    CotizacionInput,
    Cuenta,
    RegistroInput,
    Sesion,
)
from app.ports import CoreIdentityPort, CotizacionPort, IdentityProviderPort
from app.security import SessionIssuer

logger = logging.getLogger("bff_web.onboarding")


class OnboardingService:
    def __init__(
        self,
        identity: IdentityProviderPort,
        core: CoreIdentityPort,
        cotizacion: CotizacionPort,
        sessions: SessionIssuer,
    ) -> None:
        self._identity = identity
        self._core = core
        self._cotizacion = cotizacion
        self._sessions = sessions

    async def registrar(self, datos: RegistroInput) -> tuple[Cuenta, Sesion]:
        logger.info("registrar: creando credencial en Identity Platform")
        sub = await self._identity.registrar(datos.email, datos.password)
        logger.info(
            "registrar: credencial creada, registrando cliente en CoreTransaccional sub=%s", sub
        )
        try:
            cliente = await self._core.registrar_cliente(sub, datos)
        except BffError:
            logger.warning(
                "registrar: CoreTransaccional rechazó el registro, revirtiendo credencial sub=%s",
                sub,
            )
            await self._compensar(sub)
            raise
        except Exception:  # noqa: BLE001 - garantiza que no queden credenciales huérfanas
            logger.warning(
                "registrar: fallo inesperado en CoreTransaccional, revirtiendo credencial sub=%s",
                sub,
            )
            await self._compensar(sub)
            raise

        sesion = self._sessions.emitir(sub=sub, cliente_id=cliente.id, email=cliente.email)
        logger.info("registrar: registro completado cliente_id=%s", cliente.id)
        return Cuenta.desde_core(cliente), sesion

    async def _compensar(self, sub: str) -> None:
        try:
            await self._identity.eliminar(sub)
        except Exception:  # noqa: BLE001
            logger.warning("no se pudo revertir la credencial sub=%s", sub)

    async def iniciar_sesion(self, email: str, password: str) -> Sesion:
        logger.info("iniciar_sesion: autenticando contra Identity Platform")
        sub = await self._identity.autenticar(email, password)
        cliente = await self._core.buscar_cliente_por_identidad(sub)
        logger.info("iniciar_sesion: sesión iniciada cliente_id=%s", cliente.id)
        return self._sessions.emitir(sub=sub, cliente_id=cliente.id, email=cliente.email)

    def refrescar(self, refresh_token: str) -> Sesion:
        return self._sessions.refrescar(refresh_token)

    async def obtener_cuenta(self, cliente_id: str) -> Cuenta:
        logger.info("obtener_cuenta: consultando cliente_id=%s", cliente_id)
        cliente = await self._core.obtener_cliente(cliente_id)
        return Cuenta.desde_core(cliente)

    async def listar_consentimientos(self, cliente_id: str):
        logger.info("listar_consentimientos: consultando cliente_id=%s", cliente_id)
        return await self._core.listar_consentimientos(cliente_id)

    async def otorgar_consentimiento(self, cliente_id: str, scope: str, politica_version: str):
        logger.info(
            "otorgar_consentimiento: cliente_id=%s scope=%s politica_version=%s",
            cliente_id,
            scope,
            politica_version,
        )
        return await self._core.otorgar_consentimiento(cliente_id, scope, politica_version, "WEB")

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        logger.info("revocar_consentimiento: cliente_id=%s scope=%s", cliente_id, scope)
        await self._core.revocar_consentimiento(cliente_id, scope)

    async def crear_cotizacion(self, cliente_id: str, entrada: CotizacionInput) -> Cotizacion:
        logger.info(
            "crear_cotizacion: solicitando cotización a svc-cotizacion cliente_id=%s", cliente_id
        )
        cotizacion = await self._cotizacion.crear_cotizacion(cliente_id, entrada)
        logger.info(
            "crear_cotizacion: cotización creada id=%s estado=%s", cotizacion.id, cotizacion.estado
        )
        return cotizacion

    async def obtener_cotizacion(self, cliente_id: str, cotizacion_id: str) -> Cotizacion:
        logger.info(
            "obtener_cotizacion: consultando id=%s cliente_id=%s", cotizacion_id, cliente_id
        )
        return await self._cotizacion.obtener_cotizacion(cliente_id, cotizacion_id)
