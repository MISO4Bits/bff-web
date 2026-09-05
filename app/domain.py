"""Modelo de dominio del BFF: DTOs de orquestación y errores de aplicación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Credencial:
    sub: str
    email: str


@dataclass(frozen=True)
class Sesion:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"


@dataclass(frozen=True)
class RegistroInput:
    email: str
    password: str
    tipo_documento: str
    numero_documento: str
    primer_nombre: str
    primer_apellido: str
    fecha_nacimiento: date
    politica_version: str
    segundo_nombre: str | None = None
    segundo_apellido: str | None = None
    telefono: str | None = None


@dataclass(frozen=True)
class ClienteCore:
    id: str
    primer_nombre: str
    primer_apellido: str
    email: str
    estado: str
    segundo_nombre: str | None = None
    segundo_apellido: str | None = None
    telefono: str | None = None


@dataclass(frozen=True)
class Cuenta:
    cliente_id: str
    primer_nombre: str
    primer_apellido: str
    email: str
    estado: str
    segundo_nombre: str | None = None
    segundo_apellido: str | None = None
    telefono: str | None = None

    @classmethod
    def desde_core(cls, c: ClienteCore) -> Cuenta:
        return cls(
            cliente_id=c.id,
            primer_nombre=c.primer_nombre,
            primer_apellido=c.primer_apellido,
            email=c.email,
            estado=c.estado,
            segundo_nombre=c.segundo_nombre,
            segundo_apellido=c.segundo_apellido,
            telefono=c.telefono,
        )


@dataclass(frozen=True)
class ConsentimientoVista:
    scope: str
    estado: str
    vigente: bool
    actualizado_en: datetime | None = None


@dataclass(frozen=True)
class Claims:
    sub: str
    cliente_id: str
    email: str


# --- errores de aplicación (se traducen a RFC 9457 en la capa API) ---


class BffError(Exception):
    status = 500
    title = "Error interno"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail


class SolicitudInvalida(BffError):
    status = 400
    title = "Solicitud inválida"


class NoAutorizado(BffError):
    status = 401
    title = "No autorizado"


class RecursoNoEncontrado(BffError):
    status = 404
    title = "Recurso no encontrado"


class Conflicto(BffError):
    status = 409
    title = "Conflicto"


class ReglaNegocio(BffError):
    status = 422
    title = "Regla de negocio no satisfecha"


class DependenciaNoDisponible(BffError):
    status = 503
    title = "Dependencia no disponible"
