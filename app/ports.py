"""Puertos de salida del BFF."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain import ClienteCore, ConsentimientoVista, RegistroInput


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


@runtime_checkable
class CoreIdentityPort(Protocol):
    async def registrar_cliente(self, identity_ref: str, datos: RegistroInput) -> ClienteCore: ...

    async def obtener_cliente(self, cliente_id: str) -> ClienteCore: ...

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore: ...

    async def listar_consentimientos(self, cliente_id: str) -> list[ConsentimientoVista]: ...

    async def otorgar_consentimiento(
        self, cliente_id: str, scope: str, politica_version: str, canal: str
    ) -> ConsentimientoVista: ...

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None: ...
