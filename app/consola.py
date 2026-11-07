"""Sesión del personal de Solventa en el Backoffice (BITS-375) y alta de sus cuentas.

Los clientes entran por solventa4bits.com y el personal por el Backoffice: son dos sesiones
distintas. Las cuentas del personal viven en Identity Platform (las crea svc-core, BITS-374) y su
rol llega como claim en el token que Identity Platform emite al autenticarlas.
"""

from __future__ import annotations

import logging

from app.domain import (
    ClaimsConsola,
    Prohibido,
    RolConsola,
    SesionConsola,
    UsuarioInterno,
    es_correo_interno,
)
from app.ports import CuentasInternasPort, IdentityProviderPort
from app.security import ConsolaSessionIssuer

logger = logging.getLogger("bff_web.consola")


class ConsolaService:
    def __init__(
        self,
        identity: IdentityProviderPort,
        cuentas: CuentasInternasPort,
        sessions: ConsolaSessionIssuer,
    ) -> None:
        self._identity = identity
        self._cuentas = cuentas
        self._sessions = sessions

    async def iniciar_sesion(self, correo: str, contrasena: str) -> SesionConsola:
        correo = correo.strip().lower()
        if not es_correo_interno(correo):
            # Sin llamar a Identity Platform: una cuenta de cliente no entra por aquí y se le
            # indica el portal correcto. La regla del dominio es pública, no revela cuentas.
            raise Prohibido(
                "Tu acceso es por solventa4bits.com", codigo="ACCESO_POR_PORTAL_CLIENTE"
            )
        identidad = await self._identity.autenticar_personal(correo, contrasena)
        rol = _rol_valido(identidad.rol)
        if rol is None:
            logger.warning("cuenta sin rol del personal uid=%s", identidad.uid)
            raise Prohibido("La cuenta no tiene un rol asignado", codigo="CUENTA_SIN_ROL")
        sesion = self._sessions.emitir(
            ClaimsConsola(uid=identidad.uid, correo=correo, rol=rol, nombre=identidad.nombre)
        )
        logger.info("sesión de consola iniciada uid=%s rol=%s", identidad.uid, rol)
        return sesion

    def refrescar(self, refresh_token: str) -> SesionConsola:
        return self._sessions.refrescar(refresh_token)

    async def crear_usuario(
        self,
        actor: ClaimsConsola,
        correo: str,
        contrasena: str,
        rol: RolConsola,
        nombre: str | None,
    ) -> UsuarioInterno:
        usuario = await self._cuentas.crear_usuario_interno(correo, contrasena, rol, nombre)
        logger.info(
            "cuenta interna creada por actor=%s uid=%s rol=%s", actor.uid, usuario.uid, usuario.rol
        )
        return usuario


def _rol_valido(rol: str | None) -> RolConsola | None:
    try:
        return RolConsola(rol) if rol else None
    except ValueError:
        return None
