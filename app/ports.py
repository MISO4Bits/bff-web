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
    IdentidadPersonal,
    RegistroInput,
    ResultadoEvidencia,
    RolConsola,
    UsuarioInterno,
)


@runtime_checkable
class IdentityProviderPort(Protocol):
    async def registrar(self, email: str, password: str) -> str:
        """Crea la credencial y devuelve el ``sub``. Lanza ``Conflicto`` si el correo existe."""
        ...

    async def autenticar(self, email: str, password: str) -> str:
        """Valida credenciales y devuelve el ``sub``. Lanza ``NoAutorizado`` si fallan."""
        ...

    async def autenticar_personal(self, email: str, password: str) -> IdentidadPersonal:
        """Como ``autenticar``, pero devuelve también el rol y el nombre de la cuenta del
        personal (claims del token de Identity Platform). Lanza ``NoAutorizado`` si fallan."""
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
class CuentasInternasPort(Protocol):
    async def crear_usuario_interno(
        self, correo: str, contrasena: str, rol: RolConsola, nombre: str | None
    ) -> UsuarioInterno:
        """Pide a svc-core crear la cuenta del personal. Lanza ``Conflicto`` si el correo existe,
        ``SolicitudInvalida`` si no cumple las reglas y ``DependenciaNoDisponible`` si el
        proveedor de identidad falla."""
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


@runtime_checkable
class VentasSocioPort(Protocol):
    """Venta directa del socio en svc-core (BITS-378). Devuelve el cuerpo tal cual lo entrega
    svc-core; los errores salen como ``BffError`` con su ``codigo``."""

    async def buscar_cliente(
        self, *, correo: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict:
        """El cliente por correo, por documento o por los dos (deben ser de la misma cuenta:
        ``Conflicto`` con ``codigo = CLIENTES_DISTINTOS`` si no). ``RecursoNoEncontrado`` si no
        existe."""
        ...

    async def crear_cliente_asistido(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        """Da de alta al cliente con cuenta y correo confirmado. ``Conflicto`` (con el campo)
        si el correo o el documento ya existen."""
        ...

    async def calcular_precio(self, cuerpo: dict) -> dict:
        """El precio del seguro para el crédito, sin guardar nada. ``ReglaNegocio`` si el cliente
        queda fuera del rango de edad."""
        ...

    async def vender(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        """Emite la póliza al instante. El ``cuerpo`` lleva el ``socioId`` de la sesión."""
        ...


@runtime_checkable
class SiniestrosPort(Protocol):
    """Siniestros del cliente en svc-core (BITS-383, BITS-384). Devuelve el cuerpo tal cual lo
    entrega svc-core (ya validado allá); los errores salen como ``BffError`` con su ``codigo``.
    El portal web solo consulta: el reporte y lo que falta se envían desde la app móvil."""

    async def listar_siniestros(self, cliente_id: str) -> list[dict]:
        """Los siniestros del cliente, del más reciente al más antiguo."""
        ...

    async def obtener_siniestro(self, cliente_id: str, siniestro_id: str) -> dict:
        """El detalle. Uno de otro cliente responde igual que uno que no existe."""
        ...

    # --- bandeja del ADMIN (BITS-377) ---

    async def bandeja_siniestros(self, estado: str | None, automatico: bool | None) -> list[dict]:
        """Los siniestros de todos los clientes, ordenados por plazo."""
        ...

    async def detalle_siniestro_interno(self, siniestro_id: str) -> dict:
        """El detalle del ``ADMIN``: cliente, póliza, saldo guardado, destino del pago y
        decisiones."""
        ...

    async def archivo_de_siniestro(self, siniestro_id: str, archivo_id: str) -> tuple[bytes, str]:
        """Un archivo del siniestro y su tipo de contenido."""
        ...

    async def decidir_siniestro(self, siniestro_id: str, cuerpo: dict) -> dict:
        """Paga, pide más evidencia o rechaza. ``cuerpo`` lleva el ``agente`` (la sesión). Un
        siniestro ya cerrado se devuelve como quedó; uno en ``FALTA_INFORMACION`` responde
        ``Conflicto``; un valor sobre la suma asegurada, ``ReglaNegocio``."""
        ...


@runtime_checkable
class SolicitudesBackofficePort(Protocol):
    """Solicitudes de póliza que esperan una revisión del ``ADMIN`` (BITS-376). Devuelve el cuerpo
    tal cual lo entrega svc-core (ya validado allá); los errores salen como ``BffError`` con su
    ``codigo``."""

    async def listar_solicitudes(self, limite: int, cursor: str | None) -> dict:
        """Las ``PENDIENTE`` con un pendiente de agente, de la más antigua a la más reciente:
        ``{items, siguienteCursor}``."""
        ...

    async def obtener_solicitud(self, poliza_id: str) -> dict:
        """El detalle interno de la póliza: crédito, coberturas, evidencias y decisiones."""
        ...

    async def obtener_cliente_de_solicitud(self, cliente_id: str) -> dict:
        """El cliente completo, para mostrar su nombre y su documento al ``ADMIN``."""
        ...

    async def imagen_de_evidencia(self, evidencia_id: str) -> tuple[bytes, str]:
        """La imagen de una evidencia de identidad y su tipo de contenido."""
        ...

    async def decidir_solicitud(self, poliza_id: str, cuerpo: dict) -> dict:
        """Aprueba pendientes o rechaza. ``cuerpo`` lleva el ``agente`` (la sesión). Una
        solicitud vencida o ya decidida responde ``Conflicto``; un pendiente que no tiene,
        ``ReglaNegocio``."""
        ...
