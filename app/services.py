"""Orquestación del journey de originación."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, date, datetime

from app.domain import (
    ESTADO_DISPONIBLE,
    ESTADO_NO_DISPONIBLE,
    BffError,
    ClienteCore,
    Conflicto,
    Cotizacion,
    CotizacionInput,
    CreditoHipotecario,
    CreditosHipotecarios,
    Cuenta,
    DocumentoLegal,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    ImagenMuyGrande,
    NoAutorizado,
    RecursoNoEncontrado,
    RegistroInput,
    ResultadoEvidencia,
    Sesion,
    SolicitudInvalida,
    calcular_edad,
    hoy_en_colombia,
)
from app.logging_utils import sanear_para_log
from app.ports import (
    CoreIdentityPort,
    CotizacionPort,
    CreditosHipotecariosPort,
    DocumentosLegalesPort,
    EntidadesFinancierasPort,
    EvidenciasIdentidadPort,
    IdentityProviderPort,
    PolizasPort,
    SiniestrosPort,
    SolicitudesBackofficePort,
    VentasSocioPort,
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
        hoy: Callable[[], date] = hoy_en_colombia,
    ) -> None:
        self._hoy = hoy
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
                    sub=sub,
                    cliente_id=existente.id,
                    email=existente.email,
                    fecha_nacimiento=existente.fecha_nacimiento,
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

        sesion = self._sessions.emitir(
            sub=sub,
            cliente_id=cliente.id,
            email=cliente.email,
            fecha_nacimiento=datos.fecha_nacimiento,
        )
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
        return self._sessions.emitir(
            sub=sub,
            cliente_id=cliente.id,
            email=cliente.email,
            fecha_nacimiento=cliente.fecha_nacimiento,
        )

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

    async def crear_cotizacion(
        self, cliente_id: str, fecha_nacimiento: date | None, entrada: CotizacionInput
    ) -> Cotizacion:
        """Cotiza con la edad derivada de la fecha de nacimiento de la sesión.

        La cotización queda como la activa del cliente (reemplaza la anterior).
        """
        if fecha_nacimiento is None:
            # Sesión emitida antes de que la fecha viajara en el token.
            raise NoAutorizado("la sesión está desactualizada: inicia sesión de nuevo")
        edad = calcular_edad(fecha_nacimiento, self._hoy())
        logger.info(
            "crear_cotizacion: solicitando cotización a svc-cotizacion cliente_id=%s", cliente_id
        )
        cotizacion = await self._cotizacion.crear_cotizacion(cliente_id, edad, entrada)
        logger.info("crear_cotizacion: cotización activa estado=%s", cotizacion.estado)
        return cotizacion

    async def obtener_cotizacion_activa(self, cliente_id: str) -> Cotizacion:
        logger.info("obtener_cotizacion_activa: consultando cliente_id=%s", cliente_id)
        return await self._cotizacion.obtener_cotizacion_activa(cliente_id)

    async def eliminar_cotizacion_activa(self, cliente_id: str) -> None:
        """El cliente declinó las condiciones: vuelve al estado de cero cotizaciones."""
        logger.info("eliminar_cotizacion_activa: eliminando cliente_id=%s", cliente_id)
        await self._cotizacion.eliminar_cotizacion_activa(cliente_id)


class DocumentosLegalesService:
    """Textos legales del registro. El BFF los reenvía sin transformarlos: el
    versionado y el contenido son de Productos y Configuración de Mercado, y la
    aceptación la registra CoreTransaccional con lo que envía la web."""

    def __init__(self, documentos: DocumentosLegalesPort) -> None:
        self._documentos = documentos

    async def listar_vigentes(
        self, mercado: str, idioma: str, tipo: str | None = None
    ) -> list[DocumentoLegal]:
        return await self._documentos.listar_vigentes(mercado, idioma, tipo)

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        return await self._documentos.obtener_version(tipo, version, mercado, idioma)


_TOKEN = re.compile(r"[a-z0-9]+")


def _clave(nombre: str) -> str:
    """Forma comparable de un nombre de entidad: sin tildes, mayúsculas ni puntuación y sin
    el sufijo societario, para que ``BANCO DE BOGOTÁ S.A.`` y ``Banco de Bogotá`` coincidan."""
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    tokens = _TOKEN.findall(sin_tildes.lower())
    if tokens[-2:] == ["s", "a"]:
        tokens = tokens[:-2]
    elif tokens[-1:] == ["sa"]:
        tokens = tokens[:-1]
    return " ".join(tokens)


class CreditosHipotecariosService:
    """Pantalla previa a la cotización: las hipotecas del cliente, ya traídas de Open
    Finance por Perfilamiento, junto con la lista de bancos del mercado.

    Si Perfilamiento no responde, la respuesta es ``NO_DISPONIBLE`` con la lista de bancos:
    el cliente reintenta o escribe los datos a mano, nunca se bloquea.
    """

    def __init__(
        self,
        creditos: CreditosHipotecariosPort,
        entidades: EntidadesFinancierasPort,
        *,
        cache_segundos: float = 3600,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._creditos = creditos
        self._entidades = entidades
        self._cache_segundos = cache_segundos
        self._reloj = reloj
        self._cache: dict[str, tuple[float, tuple[EntidadFinanciera, ...]]] = {}

    async def obtener(self, cliente_id: str, mercado: str) -> CreditosHipotecarios:
        reportados, entidades = await asyncio.gather(
            self._creditos.obtener(cliente_id),
            self._entidades_del_mercado(mercado),
            return_exceptions=True,
        )
        if isinstance(reportados, BaseException):
            if not isinstance(reportados, BffError):
                raise reportados
            logger.warning("créditos hipotecarios no disponibles cliente_id=%s", cliente_id)
            if isinstance(entidades, BaseException):
                raise entidades
            return CreditosHipotecarios(ESTADO_NO_DISPONIBLE, (), entidades)

        if isinstance(entidades, BaseException):
            # Con hipotecas a la vista la lista del mercado no hace falta; sin ellas sí.
            if reportados.estado != ESTADO_DISPONIBLE:
                raise entidades
            entidades = ()

        por_clave = {
            _clave(nombre): entidad
            for entidad in entidades
            for nombre in (entidad.nombre, *entidad.alias)
        }
        creditos = []
        for hipoteca in reportados.hipotecas:
            entidad = por_clave.get(_clave(hipoteca.entidad_acreedora))
            creditos.append(
                CreditoHipotecario(
                    entidad_id=entidad.id if entidad else None,
                    entidad_nombre=entidad.nombre if entidad else hipoteca.entidad_acreedora,
                    valor_credito=hipoteca.valor_credito,
                    saldo_insoluto=hipoteca.saldo_insoluto,
                    plazo_restante_meses=hipoteca.plazo_restante_meses,
                    cuota_mensual=hipoteca.cuota_mensual,
                )
            )
        return CreditosHipotecarios(
            reportados.estado, tuple(creditos), tuple(entidades), reportados.fecha_consulta
        )

    async def listar_entidades(self, mercado: str) -> tuple[EntidadFinanciera, ...]:
        """Los bancos del mercado (con la caché de siempre), sin las hipotecas de nadie."""
        return await self._entidades_del_mercado(mercado)

    async def _entidades_del_mercado(self, mercado: str) -> tuple[EntidadFinanciera, ...]:
        guardada = self._cache.get(mercado)
        ahora = self._reloj()
        if guardada is not None and ahora - guardada[0] < self._cache_segundos:
            return guardada[1]
        try:
            entidades = tuple(await self._entidades.listar_entidades(mercado))
        except BffError:
            if guardada is None:
                raise
            logger.warning("Productos no responde: se usa la lista guardada mercado=%s", mercado)
            return guardada[1]
        self._cache[mercado] = (ahora, entidades)
        return entidades


class EvidenciasIdentidadService:
    """Foto de la cédula y selfie. svc-core decide si una foto sirve; aquí solo se descartan
    los envíos que ni vale la pena reenviar."""

    def __init__(self, evidencias: EvidenciasIdentidadPort, *, max_bytes: int) -> None:
        self._evidencias = evidencias
        self.max_bytes = max_bytes

    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        if len(imagen) > self.max_bytes:
            raise ImagenMuyGrande(f"La imagen supera el máximo de {self.max_bytes} bytes")
        if not imagen:
            raise SolicitudInvalida("La imagen está vacía")
        return await self._evidencias.subir(cliente_id, tipo, imagen, nombre, content_type)

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        return await self._evidencias.estado(cliente_id)


class PolizasService:
    """Solicitud y consulta de pólizas. svc-core decide qué falta y cuándo emitir; aquí solo se
    pasan las llamadas con el ``clienteId`` del token."""

    def __init__(self, polizas: PolizasPort) -> None:
        self._polizas = polizas

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        return await self._polizas.solicitar(cliente_id, cuerpo)

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]:
        return await self._polizas.listar(cliente_id, estado)

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        return await self._polizas.obtener(cliente_id, poliza_id)

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        return await self._polizas.documento(cliente_id, poliza_id)


class SiniestrosService:
    """Consulta de los siniestros del cliente. svc-core es el dueño del dominio y decide los
    estados; aquí solo se pasan las llamadas con el ``clienteId`` del token."""

    def __init__(self, siniestros: SiniestrosPort) -> None:
        self._siniestros = siniestros

    async def listar(self, cliente_id: str) -> list[dict]:
        return await self._siniestros.listar_siniestros(cliente_id)

    async def obtener(self, cliente_id: str, siniestro_id: str) -> dict:
        return await self._siniestros.obtener_siniestro(cliente_id, siniestro_id)


class SiniestrosBackofficeService:
    """Bandeja de siniestros del ``ADMIN`` (BITS-377). svc-core decide los estados y el pago; aquí
    solo se pasan las llamadas y se pone como ``agente`` el correo de la sesión: nunca lo escribe
    quien decide."""

    def __init__(self, siniestros: SiniestrosPort) -> None:
        self._siniestros = siniestros

    async def bandeja(self, estado: str | None, automatico: bool | None) -> list[dict]:
        return await self._siniestros.bandeja_siniestros(estado, automatico)

    async def detalle(self, siniestro_id: str) -> dict:
        """El detalle de svc-core más el cliente (nombre y documento, nunca correo ni teléfono).
        Si no se alcanza al cliente, el detalle sale igual con ``cliente = null``: sirve para
        decidir aunque falte el nombre."""
        detalle = await self._siniestros.detalle_siniestro_interno(siniestro_id)
        return {**detalle, "cliente": await self._cliente(detalle["clienteId"])}

    async def _cliente(self, cliente_id: str) -> dict | None:
        try:
            return vista_de_cliente_de_solicitud(
                await self._siniestros.cliente_del_siniestro(cliente_id)
            )
        except BffError as exc:
            # Sin el cliente no se cae el detalle. Ni su id ni su nombre van al log.
            logger.warning("detalle de siniestro sin cliente (%s)", type(exc).__name__)
            return None

    async def archivo(self, siniestro_id: str, archivo_id: str) -> tuple[bytes, str]:
        return await self._siniestros.archivo_de_siniestro(siniestro_id, archivo_id)

    async def decidir(self, agente: str, siniestro_id: str, decision: dict) -> dict:
        return await self._siniestros.decidir_siniestro(
            siniestro_id, {**decision, "agente": agente}
        )


def vista_de_cliente_de_solicitud(cliente: dict) -> dict:
    """Lo que el ``ADMIN`` ve del cliente de una solicitud: quién es y su documento completo (lo
    necesita para validar la identidad contra la cédula). Nunca correo ni teléfono."""
    nombre = " ".join(
        parte
        for parte in (
            cliente.get("primerNombre"),
            cliente.get("segundoNombre"),
            cliente.get("primerApellido"),
            cliente.get("segundoApellido"),
        )
        if parte
    )
    return {
        "id": cliente["id"],
        "nombre": nombre,
        "tipoDocumento": cliente["tipoDocumento"],
        "numeroDocumento": cliente["numeroDocumento"],
    }


class SolicitudesBackofficeService:
    """Solicitudes en revisión del ``ADMIN`` (BITS-376). svc-core decide los estados y emite la
    póliza; aquí se pasan las llamadas, se arma el detalle con el cliente, se comprueba que una
    imagen sea de la solicitud que se revisa y se pone como ``agente`` el correo de la sesión:
    nunca lo escribe quien decide."""

    def __init__(self, solicitudes: SolicitudesBackofficePort) -> None:
        self._solicitudes = solicitudes

    async def bandeja(self, limite: int, cursor: str | None) -> dict:
        return await self._solicitudes.listar_solicitudes(limite, cursor)

    async def detalle(self, poliza_id: str) -> dict:
        poliza = await self._solicitudes.obtener_solicitud(poliza_id)
        cliente = await self._solicitudes.obtener_cliente_de_solicitud(poliza["clienteId"])
        return {**poliza, "cliente": vista_de_cliente_de_solicitud(cliente)}

    async def imagen(self, poliza_id: str, evidencia_id: str) -> tuple[bytes, str]:
        poliza = await self._solicitudes.obtener_solicitud(poliza_id)
        if evidencia_id not in {e.get("evidenciaId") for e in poliza.get("evidencias", [])}:
            # svc-core entrega cualquier imagen por su id: aquí se exige que sea de esta solicitud.
            raise RecursoNoEncontrado("Evidencia no encontrada")
        return await self._solicitudes.imagen_de_evidencia(evidencia_id)

    async def decidir(
        self, agente: str, poliza_id: str, decision: str, pendientes: list[str]
    ) -> dict:
        return await self._solicitudes.decidir_solicitud(
            poliza_id, {"decision": decision, "agente": agente, "pendientes": pendientes}
        )


class VentasSocioService:
    """Venta directa del socio (BITS-378). svc-core es el dueño del dominio; aquí solo se pasan las
    llamadas, se pone como ``socioId`` el ``uid`` de la sesión (nunca lo escribe quien vende) y se
    deja al socio ver lo mínimo del cliente que busca."""

    def __init__(self, ventas: VentasSocioPort, creditos: CreditosHipotecariosService) -> None:
        self._ventas = ventas
        self._creditos = creditos

    async def entidades(self, mercado: str) -> tuple[EntidadFinanciera, ...]:
        return await self._creditos.listar_entidades(mercado)

    async def buscar_cliente(
        self, *, correo: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict:
        if (tipo_documento is None) != (numero_documento is None):
            raise SolicitudInvalida("El tipo y el número de documento van juntos")
        if correo is None and tipo_documento is None:
            raise SolicitudInvalida("Indica el correo, o el tipo y el número de documento")
        cliente = await self._ventas.buscar_cliente(
            correo=correo, tipo_documento=tipo_documento, numero_documento=numero_documento
        )
        return vista_de_cliente(cliente)

    async def crear_cliente(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        return vista_de_cliente(await self._ventas.crear_cliente_asistido(cuerpo, idempotency_key))

    async def calcular_precio(self, cuerpo: dict) -> dict:
        return await self._ventas.calcular_precio(cuerpo)

    async def vender(self, socio_id: str, cuerpo: dict, idempotency_key: str) -> dict:
        return await self._ventas.vender({**cuerpo, "socioId": socio_id}, idempotency_key)

    async def polizas_del_socio(self, socio_id: str) -> dict:
        """Todo lo que vendió el socio (BITS-380), de la más reciente a la más antigua. El
        Backoffice cuenta y filtra las vistas sobre esta lista, así que se recorren las páginas de
        svc-core hasta traerla completa. Con un tope: si lo supera devuelve lo que alcanzó y
        ``incompleta = true``, para que el Backoffice no presente una cifra como si fuera total."""
        items: list[dict] = []
        cursor: str | None = None
        incompleta = False
        for _ in range(MAX_PAGINAS_POLIZAS_DEL_SOCIO):
            pagina = await self._ventas.listar_polizas_del_socio(
                socio_id, TAMANO_PAGINA_POLIZAS_DEL_SOCIO, cursor
            )
            items.extend(pagina["items"])
            cursor = pagina.get("siguienteCursor")
            if cursor is None:
                break
        else:
            incompleta = True
            logger.warning("pólizas del socio: se alcanzó el tope de %d y quedan más", len(items))
        return {
            "items": [_fila_de_poliza_vendida(p) for p in items],
            "consultadoEn": datetime.now(UTC),
            "incompleta": incompleta,
        }


# Las pólizas del socio se piden de a 100 (el máximo de svc-core) y hasta 20 páginas: 2.000 pólizas.
TAMANO_PAGINA_POLIZAS_DEL_SOCIO = 100
MAX_PAGINAS_POLIZAS_DEL_SOCIO = 20


def _fila_de_poliza_vendida(poliza: dict) -> dict:
    """Lo que el socio ve de cada póliza que vendió: nada del resumen interno de svc-core."""
    return {
        "polizaId": poliza["polizaId"],
        "numero": poliza.get("numero"),
        "estado": poliza["estado"],
        "emitidaEn": poliza.get("emitidaEn"),
        "creadaEn": poliza["creadaEn"],
        "primaTotalMensual": poliza["primaTotalMensual"],
        "cliente": poliza.get("cliente"),
    }


def vista_de_cliente(cliente: dict) -> dict:
    """Lo que el socio necesita para elegir al cliente: nombre, tipo de documento y los últimos
    cuatro dígitos para distinguir homónimos. No lleva correo, teléfono, fecha de nacimiento ni el
    documento completo."""
    nombre = " ".join(
        p
        for p in (
            cliente["primerNombre"],
            cliente.get("segundoNombre"),
            cliente["primerApellido"],
            cliente.get("segundoApellido"),
        )
        if p
    )
    return {
        "id": cliente["id"],
        "nombre": nombre,
        "tipoDocumento": cliente["tipoDocumento"],
        "ultimosDigitosDocumento": cliente["numeroDocumento"][-4:],
        "estado": cliente["estado"],
    }
