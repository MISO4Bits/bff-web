"""Modelo de dominio del BFF: DTOs de orquestación y errores de aplicación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


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


# --- cotización (orquesta hacia Cotización y Rating, ver CotizacionPort) ---


@dataclass(frozen=True)
class DatosCreditoInput:
    valor_credito: Decimal
    plazo_meses: int
    edad: int
    entidad_acreedora: str
    saldo_insoluto: Decimal


@dataclass(frozen=True)
class CuestionarioHabitosInput:
    consume_tabaco: bool
    actividad_fisica: str
    condiciones_preexistentes: bool
    dependientes_economicos: int


@dataclass(frozen=True)
class CotizacionInput:
    datos_credito: DatosCreditoInput
    cuestionario_habitos: CuestionarioHabitosInput


@dataclass(frozen=True)
class FactorRiesgo:
    descripcion: str
    efecto: str
    peso_relativo: float | None = None


@dataclass(frozen=True)
class PerfilRiesgo:
    nivel_riesgo: str
    factores: tuple[FactorRiesgo, ...]


@dataclass(frozen=True)
class Oferta:
    prima_mensual: float
    prima_base_mensual: float
    suma_asegurada: float
    cobertura_meses: int
    moneda: str
    personalizado: bool
    fuentes_no_disponibles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Vigencia:
    desde: datetime
    hasta: datetime


@dataclass(frozen=True)
class Cotizacion:
    id: str
    estado: str
    producto: str
    oferta: Oferta
    vigencia_cotizacion: Vigencia
    creada_en: datetime
    perfil_riesgo: PerfilRiesgo | None = None


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
