"""Orquestación del journey de originación."""

from __future__ import annotations

import logging

from app.domain import (
    BffError,
    ClienteCore,
    Conflicto,
    Cotizacion,
    CotizacionInput,
    Cuenta,
    DocumentoLegal,
    NoAutorizado,
    RecursoNoEncontrado,
    RegistroInput,
    Sesion,
)
from app.logging_utils import sanear_para_log
from app.ports import (
    CoreIdentityPort,
    CotizacionPort,
    DocumentosLegalesPort,
    IdentityProviderPort,
)
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

    async def registrar(
        self, datos: RegistroInput, idempotency_key: str | None = None
    ) -> tuple[Cuenta, Sesion]:
        logger.info("registrar: creando credencial en Identity Platform")
        compensar_si_falla = True
        try:
            sub = await self._identity.registrar(datos.email, datos.password)
        except Conflicto as conflicto:
            # Con Idempotency-Key, un correo ya registrado puede ser el reintento de
            # esta misma solicitud (la respuesta se perdió): se reconoce si el cliente
            # demuestra la contraseña. Sin clave, es un duplicado normal.
            if idempotency_key is None:
                raise
            sub = await self._credencial_del_reintento(datos, conflicto)
            existente = await self._cliente_por_identidad(sub)
            if existente is not None:
                logger.info("registrar: reintento idempotente cliente_id=%s", existente.id)
                sesion = self._sessions.emitir(
                    sub=sub, cliente_id=existente.id, email=existente.email
                )
                return Cuenta.desde_core(existente), sesion
            # Credencial huérfana (el alta en Core nunca se completó): se termina el
            # alta con ella y, si vuelve a fallar, no se borra lo que ya existía.
            logger.info("registrar: credencial sin cliente, completando alta sub=%s", sub)
            compensar_si_falla = False
        else:
            logger.info("registrar: credencial creada sub=%s", sub)
        logger.info("registrar: registrando cliente en CoreTransaccional sub=%s", sub)
        try:
            cliente = await self._core.registrar_cliente(sub, datos, idempotency_key)
        except BffError:
            logger.warning(
                "registrar: CoreTransaccional rechazó el registro sub=%s compensar=%s",
                sub,
                compensar_si_falla,
            )
            if compensar_si_falla:
                await self._compensar(sub)
            raise
        except Exception:  # noqa: BLE001 - garantiza que no queden credenciales huérfanas
            logger.warning(
                "registrar: fallo inesperado en CoreTransaccional sub=%s compensar=%s",
                sub,
                compensar_si_falla,
            )
            if compensar_si_falla:
                await self._compensar(sub)
            raise

        sesion = self._sessions.emitir(sub=sub, cliente_id=cliente.id, email=cliente.email)
        logger.info("registrar: registro completado cliente_id=%s", cliente.id)
        return Cuenta.desde_core(cliente), sesion

    async def _credencial_del_reintento(self, datos: RegistroInput, conflicto: Conflicto) -> str:
        try:
            return await self._identity.autenticar(datos.email, datos.password)
        except NoAutorizado:
            # No es el mismo solicitante: se responde el conflicto original.
            raise conflicto from None

    async def _cliente_por_identidad(self, sub: str) -> ClienteCore | None:
        try:
            return await self._core.buscar_cliente_por_identidad(sub)
        except RecursoNoEncontrado:
            return None

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

    async def verificar_disponibilidad(
        self,
        *,
        email: str | None,
        tipo_documento: str | None,
        numero_documento: str | None,
    ) -> dict:
        logger.info("verificar_disponibilidad: consultando disponibilidad")
        return await self._core.existe_cliente(email, tipo_documento, numero_documento)

    async def reenviar_confirmacion(self, id_token: str) -> None:
        logger.info("reenviar_confirmacion: solicitando reenvío a Identity Platform")
        await self._identity.enviar_verificacion(id_token)

    async def confirmar_cuenta(self, oob_code: str) -> Cuenta:
        logger.info("confirmar_cuenta: canjeando el código de verificación")
        sub = await self._identity.confirmar_correo(oob_code)
        cliente = await self._core.buscar_cliente_por_identidad(sub)
        confirmado = await self._core.confirmar_cliente(cliente.id)
        logger.info("confirmar_cuenta: cuenta confirmada cliente_id=%s", confirmado.id)
        return Cuenta.desde_core(confirmado)

    async def listar_consentimientos(self, cliente_id: str):
        logger.info("listar_consentimientos: consultando cliente_id=%s", cliente_id)
        return await self._core.listar_consentimientos(cliente_id)

    async def otorgar_consentimiento(self, cliente_id: str, scope: str, politica_version: str):
        logger.info(
            "otorgar_consentimiento: cliente_id=%s scope=%s politica_version=%s",
            cliente_id,
            sanear_para_log(scope),
            politica_version,
        )
        return await self._core.otorgar_consentimiento(cliente_id, scope, politica_version, "WEB")

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        logger.info(
            "revocar_consentimiento: cliente_id=%s scope=%s", cliente_id, sanear_para_log(scope)
        )
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
            "obtener_cotizacion: consultando id=%s cliente_id=%s",
            sanear_para_log(cotizacion_id),
            cliente_id,
        )
        return await self._cotizacion.obtener_cotizacion(cliente_id, cotizacion_id)


class DocumentosLegalesService:
    """Textos legales del registro. El BFF los reenvía sin transformarlos: el
    versionado y el contenido son de Productos y Configuración de Mercado, y la
    aceptación la registra CoreTransaccional con lo que envía la web."""

    def __init__(self, documentos: DocumentosLegalesPort) -> None:
        self._documentos = documentos

    async def listar_vigentes(self, mercado: str, idioma: str) -> list[DocumentoLegal]:
        return await self._documentos.listar_vigentes(mercado, idioma)

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        return await self._documentos.obtener_version(tipo, version, mercado, idioma)
