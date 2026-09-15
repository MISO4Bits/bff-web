"""Modelos Pydantic de la API del BFF. Reflejan ``openapi/openapi.yaml``."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_TELEFONO = r"^\+?[0-9]{7,15}$"
_DOCUMENTO = r"^[0-9A-Za-z-]+$"

Scope = Literal["OPEN_FINANCE", "OPEN_DATA"]


class _Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )


class RegistroRequest(_Model):
    email: str = Field(max_length=254, pattern=_EMAIL)
    password: str = Field(min_length=10, max_length=128)
    tipo_documento: Literal["CC", "CE", "PA"]
    numero_documento: str = Field(min_length=4, max_length=20, pattern=_DOCUMENTO)
    primer_nombre: str = Field(min_length=1, max_length=60)
    segundo_nombre: str | None = Field(default=None, max_length=60)
    primer_apellido: str = Field(min_length=1, max_length=60)
    segundo_apellido: str | None = Field(default=None, max_length=60)
    fecha_nacimiento: date
    telefono: str | None = Field(default=None, pattern=_TELEFONO)
    politica_version: str = Field(max_length=20)
    acepta_terminos: Literal[True]


class CredencialesRequest(_Model):
    email: str = Field(pattern=_EMAIL)
    password: str


class RefrescoRequest(_Model):
    refresh_token: str


class SesionOut(_Model):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str


class CuentaOut(_Model):
    cliente_id: str
    primer_nombre: str
    segundo_nombre: str | None = None
    primer_apellido: str
    segundo_apellido: str | None = None
    email: str
    telefono: str | None = None
    estado: Literal["ACTIVO", "BLOQUEADO", "INACTIVO"]


class RegistroResponse(_Model):
    cuenta: CuentaOut
    sesion: SesionOut


class OtorgarConsentimientoRequest(_Model):
    scope: Scope
    politica_version: str = Field(max_length=20)


class ConsentimientoVistaOut(_Model):
    scope: Scope
    estado: Literal["OTORGADO", "REVOCADO", "NO_OTORGADO"]
    vigente: bool
    actualizado_en: datetime | None = None


# --- cotización (orquesta hacia Cotización y Rating — mismo contrato de
# entrada que svc-cotizacion, el BFF no agrega reglas propias en esta
# primera versión) ---


class DatosCreditoRequest(_Model):
    valor_credito: Decimal = Field(ge=10_000_000)
    plazo_meses: int = Field(ge=12, le=480)
    edad: int = Field(ge=18, le=65)
    entidad_acreedora: str = Field(min_length=2, max_length=80)
    saldo_insoluto: Decimal = Field(gt=0)


class CuestionarioHabitosRequest(_Model):
    consume_tabaco: bool
    actividad_fisica: Literal["NUNCA", "OCASIONAL", "REGULAR"]
    condiciones_preexistentes: bool
    dependientes_economicos: int = Field(ge=0, le=20)


class CotizacionRequest(_Model):
    datos_credito: DatosCreditoRequest
    cuestionario_habitos: CuestionarioHabitosRequest


class FactorRiesgoOut(_Model):
    descripcion: str
    efecto: Literal["POSITIVO", "NEGATIVO"]
    peso_relativo: float | None = None


class PerfilRiesgoOut(_Model):
    nivel_riesgo: Literal["BAJO", "MEDIO", "ALTO"]
    factores: list[FactorRiesgoOut]


class OfertaOut(_Model):
    prima_mensual: float
    prima_base_mensual: float
    suma_asegurada: float
    cobertura_meses: int
    moneda: Literal["COP"]
    personalizado: bool
    fuentes_no_disponibles: list[str] = Field(default_factory=list)


class VigenciaOut(_Model):
    desde: datetime
    hasta: datetime


class CotizacionOut(_Model):
    id: str
    estado: Literal["VIGENTE", "EXPIRADA"]
    producto: Literal["VIDA_HIPOTECARIO"]
    oferta: OfertaOut
    perfil_riesgo: PerfilRiesgoOut | None = None
    vigencia_cotizacion: VigenciaOut
    creada_en: datetime
