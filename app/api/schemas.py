"""Modelos Pydantic de la API del BFF. Reflejan ``openapi/openapi.yaml``."""

from __future__ import annotations

from datetime import date, datetime
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
