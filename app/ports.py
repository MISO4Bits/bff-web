"""Puertos de salida del BFF."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import (
    ClienteCore,
    ConsentimientoVista,
    Cotizacion,
    CotizacionInput,
    CreditosReportados,
    DocumentoLegal,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    RegistroInput,
    ResultadoEvidencia,
)


@runtime_checkable
class IdentityProviderPort(Protocol):
    async def registrar(self, email: str, password: str) -> str:
        """Crea la credencial y devuelve el ``sub``. Lanza ``Conflicto`` si el correo existe."""
        ...

    async def autenticar(self, email: str, password: str) -> str:
        """Valida credenciales y devuelve el ``sub``. Lanza ``NoAutorizado`` si fallan."""
        ...

    async def eliminar(self, sub: str) -> None:
        """Compensación: borra una credencial recién creada."""
        ...

    async def enviar_verificacion(self, id_token: str) -> None:
        """Pide a Identity Platform reenviar el correo de verificación (AC-4)."""
        ...

    async def confirmar_correo(self, oob_code: str) -> str:
        """Canjea el código de un solo uso del enlace de verificación, deja el correo
        verificado en Identity Platform y devuelve el ``sub`` del usuario. Lanza
        ``ReglaNegocio`` si el código no es válido, ya se usó o venció."""
        ...


@runtime_checkable
class CoreIdentityPort(Protocol):
    async def registrar_cliente(
        self, identity_ref: str, datos: RegistroInput, idempotency_key: str | None = None
    ) -> ClienteCore: ...

    async def obtener_cliente(self, cliente_id: str) -> ClienteCore: ...

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore: ...

    async def listar_consentimientos(self, cliente_id: str) -> list[ConsentimientoVista]: ...

    async def otorgar_consentimiento(
        self, cliente_id: str, scope: str, politica_version: str, canal: str
    ) -> ConsentimientoVista: ...

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None: ...

    async def existe_cliente(
        self, email: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict: ...

    async def confirmar_cliente(self, cliente_id: str) -> ClienteCore: ...


@runtime_checkable
class CotizacionPort(Protocol):
    async def crear_cotizacion(
        self, cliente_id: str, edad: int, entrada: CotizacionInput
    ) -> Cotizacion: ...

    async def obtener_cotizacion_activa(self, cliente_id: str) -> Cotizacion: ...

    async def eliminar_cotizacion_activa(self, cliente_id: str) -> None: ...


@runtime_checkable
class DocumentosLegalesPort(Protocol):
    async def listar_vigentes(
        self, mercado: str, idioma: str, tipo: str | None = None
    ) -> list[DocumentoLegal]:
        """Sin ``tipo``, los del registro; con ``tipo``, ese documento (p. ej. ``poliza``)."""
        ...

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal: ...


@runtime_checkable
class CreditosHipotecariosPort(Protocol):
    async def obtener(self, cliente_id: str) -> CreditosReportados:
        """Hipotecas abiertas que Perfilamiento guardó de Open Finance para el cliente."""
        ...


@runtime_checkable
class EntidadesFinancierasPort(Protocol):
    async def listar_entidades(self, mercado: str) -> list[EntidadFinanciera]: ...


@runtime_checkable
class EvidenciasIdentidadPort(Protocol):
    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        """Una foto de identidad; svc-core decide si sirve. Lanza ``RecursoNoEncontrado``
        (cliente), ``SolicitudInvalida`` (no es una imagen) o ``ImagenMuyGrande``."""
        ...

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        """Estado vigente de cada tipo de evidencia (siempre los tres)."""
        ...


@runtime_checkable
class PolizasPort(Protocol):
    """Solicitud y consulta de pólizas en svc-core (BITS-101). Devuelve el cuerpo tal cual lo
    entrega svc-core (ya validado allá); los errores salen como ``BffError`` con su ``codigo``."""

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        """Crea la solicitud. El booleano es ``True`` si se creó ahora, ``False`` si ya existía."""
        ...

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]: ...

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        """La póliza; si está pendiente, svc-core la re-evalúa antes de responder."""
        ...

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        """Estado del documento. El booleano es ``True`` si ya está listo (``False``: se está
        generando)."""
        ...
