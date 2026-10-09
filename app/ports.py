"""Puertos de salida del BFF."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import (
    ClienteCore,
    ConsentimientoVista,
    Cotizacion,
    CotizacionInput,
    DocumentoLegal,
    RegistroInput,
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

    async def verificar_correo(self, id_token: str) -> tuple[str, bool]:
        """Devuelve ``(sub, email_verified)`` según Identity Platform lo reporte ahora mismo."""
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
    async def crear_cotizacion(self, cliente_id: str, entrada: CotizacionInput) -> Cotizacion: ...

    async def obtener_cotizacion(self, cliente_id: str, cotizacion_id: str) -> Cotizacion: ...


@runtime_checkable
class DocumentosLegalesPort(Protocol):
    async def listar_vigentes(self, mercado: str, idioma: str) -> list[DocumentoLegal]: ...

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal: ...
