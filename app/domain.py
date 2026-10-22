"""Modelo de dominio del BFF: DTOs de orquestación y errores de aplicación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
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
    autoriza_tratamiento_datos: bool
    autoriza_datos_financieros: bool
    telefono: str
    segundo_nombre: str | None = None
    segundo_apellido: str | None = None
    politica_version_tratamiento_datos: str | None = None
    politica_version_datos_financieros: str | None = None


@dataclass(frozen=True)
class ClienteCore:
    id: str
    primer_nombre: str
    primer_apellido: str
    email: str
    estado: str
    telefono: str
    fecha_nacimiento: date
    correo_confirmado: bool = False
    segundo_nombre: str | None = None
    segundo_apellido: str | None = None


@dataclass(frozen=True)
class Cuenta:
    cliente_id: str
    primer_nombre: str
    primer_apellido: str
    email: str
    estado: str
    telefono: str
    correo_confirmado: bool = False
    segundo_nombre: str | None = None
    segundo_apellido: str | None = None

    @classmethod
    def desde_core(cls, c: ClienteCore) -> Cuenta:
        return cls(
            cliente_id=c.id,
            primer_nombre=c.primer_nombre,
            primer_apellido=c.primer_apellido,
            email=c.email,
            estado=c.estado,
            correo_confirmado=c.correo_confirmado,
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
class DocumentoLegal:
    """Texto legal tal cual lo publica Productos y Configuración de Mercado."""

    tipo: str
    version: str
    titulo: str
    base_legal: str
    contenido: str
    subtitulo: str | None = None
    nota_pie: str | None = None


@dataclass(frozen=True)
class EntidadFinanciera:
    """Banco o entidad del mercado. ``alias`` son los nombres con los que las
    fuentes (Open Finance) la reportan."""

    id: str
    nombre: str
    alias: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claims:
    sub: str
    cliente_id: str
    email: str
    fecha_nacimiento: date | None = None


# --- cotización (orquesta hacia Cotización y Rating, ver CotizacionPort) ---

# Colombia no tiene horario de verano: UTC-5 fijo, sin depender de la base de zonas horarias.
ZONA_COLOMBIA = timezone(timedelta(hours=-5))


def hoy_en_colombia() -> date:
    return datetime.now(UTC).astimezone(ZONA_COLOMBIA).date()


def calcular_edad(fecha_nacimiento: date, hoy: date) -> int:
    """Años cumplidos a la fecha ``hoy``."""
    cumplio = (hoy.month, hoy.day) >= (fecha_nacimiento.month, fecha_nacimiento.day)
    return hoy.year - fecha_nacimiento.year - (0 if cumplio else 1)


@dataclass(frozen=True)
class DatosCreditoInput:
    """Datos del crédito que el cliente elige o escribe. La edad no viaja aquí: la deriva el
    BFF de la fecha de nacimiento de la sesión."""

    valor_credito: Decimal
    plazo_restante_meses: int
    entidad_acreedora: str
    saldo_insoluto: Decimal


@dataclass(frozen=True)
class CotizacionInput:
    datos_credito: DatosCreditoInput


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
class CoberturaPrecio:
    """Precio mensual de una cobertura, tal como lo calcula svc-cotizacion."""

    codigo: str  # VIDA | HOGAR
    obligatoria: bool
    suma_asegurada: float
    prima_neta: float
    iva: float
    prima_total: float


@dataclass(frozen=True)
class Desglose:
    coberturas: tuple[CoberturaPrecio, ...]


@dataclass(frozen=True)
class Oferta:
    prima_mensual: float
    prima_base_mensual: float
    suma_asegurada: float
    cobertura_meses: int
    moneda: str
    personalizado: bool
    desglose: Desglose
    fuentes_no_disponibles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Vigencia:
    desde: datetime
    hasta: datetime


@dataclass(frozen=True)
class Cotizacion:
    """La cotización activa del cliente. No tiene id: es única y una nueva la reemplaza."""

    estado: str
    producto: str
    datos_credito: DatosCreditoInput
    origen_datos: str
    oferta: Oferta
    vigencia_cotizacion: Vigencia
    creada_en: datetime
    perfil_riesgo: PerfilRiesgo | None = None


# --- créditos hipotecarios (lo que Perfilamiento guardó de Open Finance) ---

ESTADO_DISPONIBLE = "DISPONIBLE"
ESTADO_SIN_HIPOTECAS = "SIN_HIPOTECAS"
ESTADO_SIN_CONSENTIMIENTO = "SIN_CONSENTIMIENTO"
ESTADO_NO_DISPONIBLE = "NO_DISPONIBLE"


@dataclass(frozen=True)
class HipotecaReportada:
    """Hipoteca abierta como la reporta la fuente (nombre de la entidad sin normalizar)."""

    entidad_acreedora: str
    valor_credito: Decimal
    saldo_insoluto: Decimal
    plazo_restante_meses: int
    cuota_mensual: Decimal


@dataclass(frozen=True)
class CreditosReportados:
    estado: str
    hipotecas: tuple[HipotecaReportada, ...] = ()
    fecha_consulta: datetime | None = None


@dataclass(frozen=True)
class CreditoHipotecario:
    """Hipoteca lista para precargar el formulario. ``entidad_id`` es la entidad del
    mercado a la que corresponde, o ``None`` si el banco no está en la lista."""

    entidad_id: str | None
    entidad_nombre: str
    valor_credito: Decimal
    saldo_insoluto: Decimal
    plazo_restante_meses: int
    cuota_mensual: Decimal


@dataclass(frozen=True)
class CreditosHipotecarios:
    estado: str
    creditos: tuple[CreditoHipotecario, ...]
    entidades: tuple[EntidadFinanciera, ...]
    fecha_consulta: datetime | None = None


# --- errores de aplicación (se traducen a RFC 9457 en la capa API) ---


MENSAJE_ENLACE_INVALIDO = "El enlace de confirmación no es válido, ya se usó o venció"


@dataclass(frozen=True)
class ResultadoEvidencia:
    """Veredicto de svc-core sobre una foto de identidad. ``evidencia_id`` es ``None`` si hay
    que reintentar: no se guardó nada."""

    tipo: str
    estado: str
    intentos_restantes: int
    evidencia_id: str | None = None
    motivo: str | None = None


@dataclass(frozen=True)
class EstadoEvidenciaTipo:
    tipo: str
    estado: str  # ACEPTADA | REVISION_MANUAL | PENDIENTE
    intentos_restantes: int
    evidencia_id: str | None = None
    creada_en: datetime | None = None


class BffError(Exception):
    status = 500
    title = "Error interno"

    def __init__(
        self, detail: str | None = None, *, errores: list[dict[str, str]] | None = None
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errores = errores


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

    _MENSAJES = {
        "correo": "El correo ya está registrado",
        "documento": "El documento ya está registrado",
    }

    @classmethod
    def por_campo(cls, campo: str) -> Conflicto:
        """Conflicto de registro que indica el campo repetido, sin datos de otra persona."""
        mensaje = cls._MENSAJES[campo]
        return cls(mensaje, errores=[{"campo": campo, "mensaje": mensaje}])


class ImagenMuyGrande(BffError):
    status = 413
    title = "Imagen demasiado grande"


class ReglaNegocio(BffError):
    status = 422
    title = "Regla de negocio no satisfecha"


class DependenciaNoDisponible(BffError):
    status = 503
    title = "Dependencia no disponible"
