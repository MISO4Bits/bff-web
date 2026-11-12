"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas."""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.domain import (
    ESTADO_DISPONIBLE,
    MENSAJE_ENLACE_INVALIDO,
    ClienteCore,
    CoberturaPrecio,
    Conflicto,
    ConsentimientoVista,
    Cotizacion,
    CotizacionInput,
    CreditosReportados,
    DatosCreditoInput,
    Desglose,
    DocumentoLegal,
    EntidadFinanciera,
    EstadoEvidenciaTipo,
    HipotecaReportada,
    IdentidadPersonal,
    NoAutorizado,
    Oferta,
    RecursoNoEncontrado,
    RegistroInput,
    ReglaNegocio,
    ResultadoEvidencia,
    RolConsola,
    SolicitudInvalida,
    UsuarioInterno,
    Vigencia,
    es_correo_interno,
)

MENSAJE_CEDULA_INCOMPLETA = (
    "Para subir la selfie primero deben estar vigentes ambas caras de la cédula"
)


class FakeIdentityProvider:
    def __init__(self) -> None:
        # email -> (sub, password)
        self._por_email: dict[str, tuple[str, str]] = {}
        self._verificados: set[str] = set()
        self._codigos: dict[str, str] = {}
        # email -> (identidad, password): cuentas del personal de Solventa.
        self._personal: dict[str, tuple[IdentidadPersonal, str]] = {}

    def sembrar_personal(
        self, email: str, password: str, rol: str | None, nombre: str | None = None
    ) -> IdentidadPersonal:
        """Crea una cuenta del personal (``rol`` puede ser ``None`` o inválido, para probar)."""
        if email in self._personal:
            raise Conflicto.por_campo("correo")
        identidad = IdentidadPersonal(
            uid=f"uid-{uuid.uuid4().hex[:12]}", correo=email, rol=rol, nombre=nombre
        )
        self._personal[email] = (identidad, password)
        return identidad

    async def autenticar_personal(self, email: str, password: str) -> IdentidadPersonal:
        registro = self._personal.get(email)
        if registro is None or registro[1] != password:
            raise NoAutorizado("credenciales inválidas")
        return registro[0]

    async def registrar(self, email: str, password: str) -> str:
        if email in self._por_email:
            raise Conflicto.por_campo("correo")
        sub = f"sub-{uuid.uuid4().hex[:12]}"
        self._por_email[email] = (sub, password)
        return sub

    async def autenticar(self, email: str, password: str) -> str:
        registro = self._por_email.get(email)
        if registro is None or registro[1] != password:
            raise NoAutorizado("credenciales inválidas")
        return registro[0]

    async def eliminar(self, sub: str) -> None:
        self._por_email = {e: v for e, v in self._por_email.items() if v[0] != sub}

    async def enviar_verificacion(self, id_token: str) -> None:
        # El "token" en modo fake es directamente el sub.
        if id_token not in {sub for sub, _ in self._por_email.values()}:
            raise NoAutorizado("token inválido o expirado")

    def emitir_codigo_verificacion(self, sub: str) -> str:
        """Solo para pruebas: el código de un solo uso que el cliente recibiría por correo."""
        codigo = f"oob-{uuid.uuid4().hex}"
        self._codigos[codigo] = sub
        return codigo

    async def confirmar_correo(self, oob_code: str) -> str:
        sub = self._codigos.pop(oob_code, None)  # de un solo uso
        if sub is None:
            raise ReglaNegocio(MENSAJE_ENLACE_INVALIDO)
        self._verificados.add(sub)
        return sub


class FakeCuentasInternas:
    """Imita a svc-core: valida dominio y nombre, y la cuenta queda lista para iniciar sesión."""

    def __init__(self, identity: FakeIdentityProvider) -> None:
        self._identity = identity

    async def crear_usuario_interno(
        self, correo: str, contrasena: str, rol: RolConsola, nombre: str | None
    ) -> UsuarioInterno:
        correo = correo.strip().lower()
        if not es_correo_interno(correo):
            raise SolicitudInvalida(
                "El correo debe ser del dominio @solventa4bits.com",
                errores=[{"campo": "correo", "mensaje": "dominio no permitido"}],
            )
        if rol is RolConsola.SOCIO and not (nombre or "").strip():
            raise SolicitudInvalida(
                "El nombre es obligatorio para una cuenta SOCIO",
                errores=[{"campo": "nombre", "mensaje": "obligatorio"}],
            )
        identidad = self._identity.sembrar_personal(
            correo, contrasena, str(rol), (nombre or "").strip() or None
        )
        return UsuarioInterno(uid=identidad.uid, correo=correo, rol=rol)


class FakeCoreIdentity:
    def __init__(self) -> None:
        self._clientes: dict[str, ClienteCore] = {}
        self._por_identidad: dict[str, str] = {}
        self._por_documento: set[tuple[str, str]] = set()
        self._por_correo: set[str] = set()
        self._consentimientos: dict[tuple[str, str], ConsentimientoVista] = {}

    async def registrar_cliente(
        self, identity_ref: str, datos: RegistroInput, idempotency_key: str | None = None
    ) -> ClienteCore:
        clave = (datos.tipo_documento, datos.numero_documento)
        if datos.email in self._por_correo:
            raise Conflicto.por_campo("correo")
        if clave in self._por_documento:
            raise Conflicto.por_campo("documento")
        cliente = ClienteCore(
            id=str(uuid.uuid4()),
            primer_nombre=datos.primer_nombre,
            primer_apellido=datos.primer_apellido,
            email=datos.email,
            estado="ACTIVO",
            correo_confirmado=False,
            segundo_nombre=datos.segundo_nombre,
            segundo_apellido=datos.segundo_apellido,
            telefono=datos.telefono,
            fecha_nacimiento=datos.fecha_nacimiento,
        )
        self._clientes[cliente.id] = cliente
        self._por_identidad[identity_ref] = cliente.id
        self._por_documento.add(clave)
        self._por_correo.add(datos.email)
        return cliente

    async def obtener_cliente(self, cliente_id: str) -> ClienteCore:
        cliente = self._clientes.get(cliente_id)
        if cliente is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return cliente

    async def existe_cliente(
        self, email: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict:
        correo_disponible = None if email is None else email not in self._por_correo
        documento_disponible = None
        if tipo_documento is not None and numero_documento is not None:
            documento_disponible = (tipo_documento, numero_documento) not in self._por_documento
        return {"correoDisponible": correo_disponible, "documentoDisponible": documento_disponible}

    async def confirmar_cliente(self, cliente_id: str) -> ClienteCore:
        cliente = await self.obtener_cliente(cliente_id)
        if not cliente.correo_confirmado:
            cliente = ClienteCore(
                id=cliente.id,
                primer_nombre=cliente.primer_nombre,
                primer_apellido=cliente.primer_apellido,
                email=cliente.email,
                estado=cliente.estado,
                correo_confirmado=True,
                segundo_nombre=cliente.segundo_nombre,
                segundo_apellido=cliente.segundo_apellido,
                telefono=cliente.telefono,
                fecha_nacimiento=cliente.fecha_nacimiento,
            )
            self._clientes[cliente_id] = cliente
        return cliente

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        cliente_id = self._por_identidad.get(identity_ref)
        if cliente_id is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return self._clientes[cliente_id]

    async def listar_consentimientos(self, cliente_id: str) -> list[ConsentimientoVista]:
        await self.obtener_cliente(cliente_id)
        return [v for (cid, _s), v in sorted(self._consentimientos.items()) if cid == cliente_id]

    async def otorgar_consentimiento(
        self, cliente_id: str, scope: str, politica_version: str, canal: str
    ) -> ConsentimientoVista:
        await self.obtener_cliente(cliente_id)
        vista = ConsentimientoVista(
            scope=scope,
            estado="OTORGADO",
            vigente=True,
            actualizado_en=datetime.now(UTC),
        )
        self._consentimientos[(cliente_id, scope)] = vista
        return vista

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        await self.obtener_cliente(cliente_id)
        self._consentimientos[(cliente_id, scope)] = ConsentimientoVista(
            scope=scope,
            estado="REVOCADO",
            vigente=False,
            actualizado_en=datetime.now(UTC),
        )


class FakeEvidencias:
    """Evidencias en memoria. ``veredictos`` se recorre en rotación, una entrada por foto:
    ``ACEPTADA``, ``REVISION_MANUAL`` o un motivo de rechazo (``BORROSA``, ...)."""

    _TIPOS = ("cedula_frente", "cedula_reverso", "selfie")

    def __init__(self, veredictos=("ACEPTADA",), intentos_max: int = 3) -> None:
        self._veredictos = itertools.cycle(tuple(veredictos))
        self._intentos_max = intentos_max
        self._vigentes: dict[tuple[str, str], EstadoEvidenciaTipo] = {}
        self._rechazos: dict[tuple[str, str], int] = {}

    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        clave = (cliente_id, tipo)
        if tipo == "selfie" and not all(
            (cliente_id, cara) in self._vigentes for cara in ("cedula_frente", "cedula_reverso")
        ):
            # Como svc-core: la selfie se compara con la cédula, que debe estar ya vigente.
            raise ReglaNegocio(MENSAJE_CEDULA_INCOMPLETA)
        veredicto = next(self._veredictos)
        if veredicto not in ("ACEPTADA", "REVISION_MANUAL"):
            rechazos = self._rechazos.get(clave, 0) + 1
            self._rechazos[clave] = rechazos
            if rechazos < self._intentos_max:
                return ResultadoEvidencia(
                    tipo=tipo,
                    estado="REINTENTAR",
                    motivo=veredicto,
                    intentos_restantes=self._intentos_max - rechazos,
                )
            veredicto = "REVISION_MANUAL"  # tercer rechazo: lo revisa una persona
        self._rechazos.pop(clave, None)
        evidencia_id = str(uuid.uuid4())
        self._vigentes[clave] = EstadoEvidenciaTipo(
            tipo=tipo,
            estado=veredicto,
            intentos_restantes=self._intentos_max,
            evidencia_id=evidencia_id,
            creada_en=datetime.now(UTC),
        )
        return ResultadoEvidencia(
            tipo=tipo,
            estado=veredicto,
            evidencia_id=evidencia_id,
            intentos_restantes=self._intentos_max,
        )

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        return [
            self._vigentes.get((cliente_id, tipo))
            or EstadoEvidenciaTipo(
                tipo=tipo,
                estado="PENDIENTE",
                intentos_restantes=self._intentos_max - self._rechazos.get((cliente_id, tipo), 0),
            )
            for tipo in self._TIPOS
        ]


class FakeCotizacion:
    """Rating simplificado, sin llamar a Perfilamiento — solo para modo standalone/pruebas.

    Guarda una sola cotización activa por cliente: crear otra reemplaza la anterior.
    """

    _TASA_MENSUAL = Decimal("0.0005")
    _VIGENCIA = timedelta(days=30)
    _HOGAR_TOPE = Decimal("240000000")
    _HOGAR_TASA_POR_MIL = Decimal("0.099")
    _IVA = Decimal("0.19")

    def __init__(self) -> None:
        self._activas: dict[str, Cotizacion] = {}
        self.ultima_edad: int | None = None

    @classmethod
    def _desglose(cls, datos: DatosCreditoInput, prima_vida: float) -> Desglose:
        suma_hogar = min(cls._HOGAR_TOPE, datos.valor_credito)
        neta = (suma_hogar * cls._HOGAR_TASA_POR_MIL / 1000).quantize(Decimal("1"), ROUND_HALF_UP)
        iva = (neta * cls._IVA).quantize(Decimal("1"), ROUND_HALF_UP)
        return Desglose(
            coberturas=(
                CoberturaPrecio(
                    "VIDA", True, float(datos.saldo_insoluto), prima_vida, 0, prima_vida
                ),
                CoberturaPrecio(
                    "HOGAR", False, float(suma_hogar), float(neta), float(iva), float(neta + iva)
                ),
            )
        )

    async def crear_cotizacion(
        self, cliente_id: str, edad: int, entrada: CotizacionInput
    ) -> Cotizacion:
        self.ultima_edad = edad
        saldo = entrada.datos_credito.saldo_insoluto
        prima = float((saldo * self._TASA_MENSUAL).quantize(Decimal("1")))
        ahora = datetime.now(UTC)
        cotizacion = Cotizacion(
            estado="VIGENTE",
            producto="VIDA_HIPOTECARIO",
            datos_credito=entrada.datos_credito,
            origen_datos="DECLARADO",
            oferta=Oferta(
                prima_mensual=prima,
                prima_base_mensual=prima,
                suma_asegurada=float(saldo),
                cobertura_meses=entrada.datos_credito.plazo_restante_meses,
                moneda="COP",
                personalizado=False,
                desglose=self._desglose(entrada.datos_credito, prima),
                fuentes_no_disponibles=("perfilamiento",),
            ),
            vigencia_cotizacion=Vigencia(desde=ahora, hasta=ahora + self._VIGENCIA),
            creada_en=ahora,
        )
        self._activas[cliente_id] = cotizacion
        return cotizacion

    async def obtener_cotizacion_activa(self, cliente_id: str) -> Cotizacion:
        cotizacion = self._activas.get(cliente_id)
        if cotizacion is None:
            raise RecursoNoEncontrado("El cliente no tiene una cotización activa")
        return cotizacion

    async def eliminar_cotizacion_activa(self, cliente_id: str) -> None:
        if self._activas.pop(cliente_id, None) is None:
            raise RecursoNoEncontrado("El cliente no tiene una cotización activa")


_DOCUMENTOS_LEGALES_DE_EJEMPLO = (
    DocumentoLegal(
        tipo="terminos",
        version="V1",
        titulo="Términos y condiciones",
        subtitulo="Las reglas de uso de Solventa. Está corto a propósito.",
        base_legal="Ley 527 de 1999",
        contenido=(
            "<h3>1 · Quién te presta el servicio</h3>"
            "<p>Solventa Colombia S.A.S. Somos intermediarios de seguros.</p>"
            "<h3>2 · Tu firma electrónica</h3>"
            "<p>Cuando aceptas desde tu cuenta, esa aceptación vale como tu firma.</p>"
        ),
        nota_pie="Aceptar estos términos no te compromete a comprar.",
    ),
    DocumentoLegal(
        tipo="open-data",
        version="V1",
        titulo="Tratamiento de datos personales",
        subtitulo="Qué datos tuyos usamos y para qué. Ley 1581 de 2012.",
        base_legal="Ley 1581 de 2012",
        contenido=(
            "<h3>1 · Qué datos recogemos</h3>"
            "<p>Tu nombre, documento, fecha de nacimiento, correo y celular.</p>"
            "<h3>2 · Tus derechos</h3>"
            "<p>Conocer, actualizar, corregir y borrar tus datos, o revocar esta autorización.</p>"
        ),
        nota_pie="Por ley del sector conservamos tu información mientras tengas póliza.",
    ),
    DocumentoLegal(
        tipo="open-finance",
        version="V1",
        titulo="Consulta en centrales de riesgo",
        subtitulo="Es opcional. Si la das, tu precio puede bajar.",
        base_legal="Ley 1266 de 2008",
        contenido=(
            "<h3>1 · Qué autorizas</h3>"
            "<p>Consultar y reportar tu comportamiento crediticio ante los operadores.</p>"
            "<h3>2 · Puedes retirarla</h3>"
            "<p>Quitas esta autorización cuando quieras desde tu perfil.</p>"
        ),
        nota_pie="Es opcional. Si no la autorizas puedes seguir igual.",
    ),
    DocumentoLegal(
        tipo="poliza",
        version="V1",
        titulo="Condiciones de tu póliza",
        subtitulo="Lo que contratas, en pocas líneas. Léelo antes de solicitar.",
        base_legal="Código de Comercio (contrato de seguro) y Ley 527 de 1999",
        contenido=(
            "<h3>1 · Qué contratas</h3>"
            "<p>Un seguro de vida deudores asociado a tu crédito de vivienda.</p>"
            "<h3>2 · Tu firma electrónica</h3>"
            "<p>Al aceptar desde tu cuenta, esa aceptación vale como tu firma.</p>"
        ),
        nota_pie="Solicitar la póliza no te cobra nada todavía.",
    ),
)


class FakeDocumentosLegales:
    """Textos de ejemplo abreviados (mercado ``CO``, idioma ``es-CO``) para correr
    el BFF standalone. Los textos reales viven en svc-productos."""

    def __init__(self, documentos: tuple[DocumentoLegal, ...] = _DOCUMENTOS_LEGALES_DE_EJEMPLO):
        self._documentos = documentos

    @staticmethod
    def _hay_textos(mercado: str, idioma: str) -> bool:
        return (mercado, idioma) == ("CO", "es-CO")

    async def listar_vigentes(
        self, mercado: str, idioma: str, tipo: str | None = None
    ) -> list[DocumentoLegal]:
        if not self._hay_textos(mercado, idioma):
            return []
        if tipo is not None:
            return [d for d in self._documentos if d.tipo == tipo]
        return [d for d in self._documentos if d.tipo != "poliza"]

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        if self._hay_textos(mercado, idioma):
            for documento in self._documentos:
                if (documento.tipo, documento.version) == (tipo, version):
                    return documento
        raise RecursoNoEncontrado("Documento legal no encontrado")


class FakeCreditosHipotecarios:
    """Perfilamiento simulado: devuelve ``respuesta`` (por defecto, una hipoteca de BBVA)."""

    def __init__(self, respuesta: CreditosReportados | None = None) -> None:
        self.respuesta = respuesta or CreditosReportados(
            estado=ESTADO_DISPONIBLE,
            hipotecas=(
                HipotecaReportada(
                    entidad_acreedora="BBVA COLOMBIA S.A.",
                    valor_credito=Decimal("200000000"),
                    saldo_insoluto=Decimal("160000000"),
                    plazo_restante_meses=180,
                    cuota_mensual=Decimal("2100000"),
                ),
            ),
            fecha_consulta=datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
        )
        self.llamadas: list[str] = []

    async def obtener(self, cliente_id: str) -> CreditosReportados:
        self.llamadas.append(cliente_id)
        return self.respuesta


_ENTIDADES_DE_EJEMPLO = (
    EntidadFinanciera("bancolombia", "Bancolombia", ("BANCOLOMBIA S.A.",)),
    EntidadFinanciera("davivienda", "Davivienda", ("DAVIVIENDA S.A.",)),
    EntidadFinanciera("banco-de-bogota", "Banco de Bogotá", ("BANCO DE BOGOTÁ S.A.",)),
    EntidadFinanciera("bbva-colombia", "BBVA Colombia", ("BBVA COLOMBIA S.A.",)),
)


class FakeEntidadesFinancieras:
    """Lista del mercado ``CO`` para correr el BFF standalone."""

    def __init__(self, entidades: tuple[EntidadFinanciera, ...] = _ENTIDADES_DE_EJEMPLO) -> None:
        self._entidades = entidades
        self.llamadas = 0

    async def listar_entidades(self, mercado: str) -> list[EntidadFinanciera]:
        self.llamadas += 1
        return list(self._entidades) if mercado == "CO" else []


class FakePolizas:
    """Pólizas en memoria con la forma que entrega svc-core. ``pendientes`` define qué falta:
    vacío, la póliza sale emitida; si no, ``PENDIENTE`` con esos códigos."""

    _PRECIOS = {"VIDA": (140000000, 24696, 0), "HOGAR": (150000000, 14850, 2822)}
    _MENSAJES = {
        "CORREO_SIN_CONFIRMAR": ("USUARIO", "Confirma tu cuenta con el enlace que enviamos."),
        "EVIDENCIA_FALTANTE": ("USUARIO", "Sube la evidencia de identidad que falta."),
        "EVIDENCIA_EN_REVISION": ("AGENTE", "Un agente está revisando tus documentos."),
        "CREDITO_NO_VERIFICADO": ("AGENTE", "Un agente debe verificar los datos de tu crédito."),
    }

    def __init__(self, pendientes: tuple[str, ...] = ()) -> None:
        self.pendientes = pendientes
        self.documento_listo = True
        self.llamadas: list[tuple] = []
        self._polizas: dict[str, dict] = {}

    def _poliza(self, cliente_id: str, cuerpo: dict) -> dict:
        ahora = datetime.now(UTC).isoformat()
        coberturas = [
            {
                "codigo": codigo,
                "obligatoria": codigo == "VIDA",
                "sumaAsegurada": self._PRECIOS[codigo][0],
                "primaNeta": self._PRECIOS[codigo][1],
                "iva": self._PRECIOS[codigo][2],
                "primaTotal": self._PRECIOS[codigo][1] + self._PRECIOS[codigo][2],
            }
            for codigo in ("VIDA", "HOGAR")
            if codigo in cuerpo["coberturas"]
        ]
        emitida = not self.pendientes
        return {
            "polizaId": str(uuid.uuid4()),
            "clienteId": cliente_id,
            "numero": "SOL-VH-2026-FAKE01" if emitida else None,
            "estado": "EMITIDA" if emitida else "PENDIENTE",
            "producto": "VIDA_HIPOTECARIO",
            "creadaEn": ahora,
            "emitidaEn": ahora if emitida else None,
            "solicitudVigenteHasta": None if emitida else ahora,
            "vigencia": {"desde": ahora, "hasta": ahora} if emitida else None,
            "coberturas": coberturas,
            "primaTotalMensual": sum(c["primaTotal"] for c in coberturas),
            "moneda": "COP",
            "datosCredito": {
                "valorCredito": 150000000,
                "plazoRestanteMeses": 240,
                "entidadAcreedora": "Bancolombia",
                "saldoInsoluto": 140000000,
            },
            "origenDatos": "CONFIRMADO",
            "cotizacionCreadaEn": cuerpo["cotizacionCreadaEn"],
            "pendientes": [
                {"codigo": c, "tipo": self._MENSAJES[c][0], "mensaje": self._MENSAJES[c][1]}
                for c in self.pendientes
            ],
            "documento": {"estado": "DISPONIBLE" if emitida else "NO_DISPONIBLE"},
            "documentosAceptados": [
                {**d, "aceptadoEn": ahora} for d in cuerpo["documentosAceptados"]
            ],
            "rechazo": None,
        }

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        self.llamadas.append(("solicitar", cliente_id, cuerpo))
        for existente in self._polizas.values():
            if (
                existente["clienteId"] == cliente_id
                and existente["cotizacionCreadaEn"] == cuerpo["cotizacionCreadaEn"]
            ):
                return existente, False
        poliza = self._poliza(cliente_id, cuerpo)
        self._polizas[poliza["polizaId"]] = poliza
        return poliza, True

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]:
        return [
            p
            for p in self._polizas.values()
            if p["clienteId"] == cliente_id and (estado is None or p["estado"] == estado)
        ]

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        poliza = self._polizas.get(poliza_id)
        if poliza is None or poliza["clienteId"] != cliente_id:
            raise RecursoNoEncontrado("Póliza no encontrada")
        return poliza

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        poliza = await self.obtener(cliente_id, poliza_id)
        if poliza["estado"] != "EMITIDA":
            raise Conflicto("La póliza todavía no está emitida", codigo="POLIZA_NO_EMITIDA")
        if not self.documento_listo:
            return {"estado": "GENERANDO", "url": None, "expiraEn": None}, False
        return {
            "estado": "LISTO",
            "url": f"https://documentos.local/{poliza['numero']}.pdf",
            "expiraEn": datetime.now(UTC).isoformat(),
        }, True


class FakeSiniestros:
    """Siniestros en memoria con la forma que entrega svc-core, con las reglas de las decisiones
    del ``ADMIN``. El portal del cliente solo consulta; la bandeja decide. No hay cómo crearlos
    desde aquí: ``sembrar`` deja uno ya armado (con los campos internos: ``clienteId``, ``poliza``,
    ``saldoCredito``, ``destinoPago`` y ``decisiones``), para pruebas y para el front."""

    _RESUMEN = (
        "id",
        "numero",
        "polizaId",
        "tipo",
        "automatico",
        "estado",
        "reportadoEn",
        "actualizadoEn",
    )

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, dict]] = {}
        self._archivos: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.decisiones: list[tuple[str, dict]] = []

    def sembrar(self, cliente_id: str, siniestro: dict) -> dict:
        self._items[siniestro["id"]] = (cliente_id, siniestro)
        return siniestro

    def sembrar_archivo(
        self, siniestro_id: str, archivo_id: str, contenido: bytes, content_type: str
    ) -> None:
        self._archivos[(siniestro_id, archivo_id)] = (contenido, content_type)

    # --- el cliente ---

    async def listar_siniestros(self, cliente_id: str) -> list[dict]:
        propios = [s for dueno, s in self._items.values() if dueno == cliente_id]
        propios.sort(key=lambda s: s["reportadoEn"], reverse=True)
        return [{c: s[c] for c in self._RESUMEN} for s in propios]

    async def obtener_siniestro(self, cliente_id: str, siniestro_id: str) -> dict:
        dueno, siniestro = self._items.get(siniestro_id, (None, {}))
        if dueno != cliente_id:
            raise RecursoNoEncontrado("Siniestro no encontrado")
        return dict(siniestro)

    # --- el ADMIN ---

    def _por_id(self, siniestro_id: str) -> dict:
        if siniestro_id not in self._items:
            raise RecursoNoEncontrado("Siniestro no encontrado")
        return self._items[siniestro_id][1]

    async def bandeja_siniestros(self, estado: str | None, automatico: bool | None) -> list[dict]:
        coinciden = [
            (dueno, s)
            for dueno, s in self._items.values()
            if (estado is None or s["estado"] == estado)
            and (automatico is None or s["automatico"] is automatico)
        ]
        coinciden.sort(key=lambda par: par[1]["reportadoEn"], reverse=True)
        con_plazo = sorted(
            (p for p in coinciden if p[1].get("fechaLimite")), key=lambda p: p[1]["fechaLimite"]
        )
        sin_plazo = [p for p in coinciden if not p[1].get("fechaLimite")]
        return [
            {
                **{c: s[c] for c in self._RESUMEN},
                "clienteId": dueno,
                "fechaLimite": s.get("fechaLimite"),
            }
            for dueno, s in [*con_plazo, *sin_plazo]
        ]

    async def detalle_siniestro_interno(self, siniestro_id: str) -> dict:
        dueno, siniestro = self._items.get(siniestro_id, (None, None))
        if siniestro is None:
            raise RecursoNoEncontrado("Siniestro no encontrado")
        return {**siniestro, "clienteId": dueno}

    async def archivo_de_siniestro(self, siniestro_id: str, archivo_id: str) -> tuple[bytes, str]:
        self._por_id(siniestro_id)
        if (siniestro_id, archivo_id) not in self._archivos:
            raise RecursoNoEncontrado("Archivo no encontrado")
        return self._archivos[(siniestro_id, archivo_id)]

    async def decidir_siniestro(self, siniestro_id: str, cuerpo: dict) -> dict:
        self.decisiones.append((siniestro_id, cuerpo))
        siniestro = self._por_id(siniestro_id)
        dueno = self._items[siniestro_id][0]
        if siniestro["estado"] in ("PAGADO", "RECHAZADO"):
            return {**siniestro, "clienteId": dueno}
        if siniestro["estado"] != "EN_ESTUDIO":
            raise Conflicto(
                f"El siniestro está {siniestro['estado']}: no admite esa acción",
                codigo="ESTADO_NO_PERMITE",
            )
        ahora = datetime.now(UTC).isoformat()
        decision = {
            "agente": cuerpo["agente"],
            "decision": cuerpo["decision"],
            "fecha": ahora,
            "dano": cuerpo.get("dano"),
            "valor": cuerpo.get("valor"),
            "detalle": cuerpo.get("detalle"),
            "motivo": cuerpo.get("motivo"),
        }
        if cuerpo["decision"] == "PAGAR":
            siniestro["pago"] = {"pagadoEn": ahora, "lineas": self._lineas(siniestro, cuerpo)}
            siniestro["estado"] = "PAGADO"
        elif cuerpo["decision"] == "PEDIR_EVIDENCIA":
            if not (cuerpo.get("detalle") or "").strip():
                raise SolicitudInvalida("Escribe qué falta")
            siniestro["evidenciaPedida"] = {"detalle": cuerpo["detalle"].strip(), "pedidaEn": ahora}
            siniestro["estado"] = "FALTA_INFORMACION"
        else:
            if not (cuerpo.get("motivo") or "").strip():
                raise SolicitudInvalida("Escribe el motivo")
            siniestro["motivoRechazo"] = cuerpo["motivo"].strip()
            siniestro["estado"] = "RECHAZADO"
        siniestro["fechaLimite"] = None
        siniestro["actualizadoEn"] = ahora
        siniestro["decisiones"] = [*siniestro["decisiones"], decision]
        return {**siniestro, "clienteId": dueno}

    @staticmethod
    def _lineas(siniestro: dict, cuerpo: dict) -> list[dict]:
        saldo = siniestro["saldoCredito"]
        al_banco = f"{saldo['entidad']}, abono al saldo del crédito"
        if siniestro["tipo"] == "INCAPACIDAD_TOTAL":
            return [{"beneficiario": "BANCO", "valor": saldo["valor"], "destino": al_banco}]
        valor, dano = cuerpo.get("valor"), cuerpo.get("dano")
        if dano is None or valor is None:
            raise SolicitudInvalida("Indica el daño y el valor")
        suma = siniestro["poliza"]["sumaAsegurada"]
        if valor > suma:
            raise ReglaNegocio(
                f"El valor no puede superar la suma asegurada ({suma})",
                codigo="VALOR_SUPERA_SUMA_ASEGURADA",
            )
        al_cliente = {"beneficiario": "CLIENTE", "valor": valor, "destino": "Cuenta del cliente"}
        if dano == "REPARABLE":
            return [al_cliente]
        para_banco = min(valor, saldo["valor"])
        lineas = [{"beneficiario": "BANCO", "valor": para_banco, "destino": al_banco}]
        if valor > para_banco:
            lineas.append({**al_cliente, "valor": valor - para_banco})
        return lineas


class FakeVentasSocio:
    """Venta directa en memoria con la forma que entrega svc-core: clientes asistidos, precio y
    pólizas emitidas. Repite la respuesta de una venta con la misma ``Idempotency-Key``."""

    _TASA_VIDA = 0.000176  # prima mensual por peso de crédito
    _TASA_HOGAR = 0.000099

    def __init__(self) -> None:
        self.clientes: dict[str, dict] = {}
        self.ventas: list[dict] = []
        self.llamadas: list[tuple] = []
        self._por_clave: dict[tuple[str, str], dict] = {}

    def _cliente(self, cliente_id: str) -> dict:
        cliente = self.clientes.get(cliente_id)
        if cliente is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return cliente

    async def buscar_cliente(
        self, *, correo: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict:
        self.llamadas.append(("buscar_cliente", correo, tipo_documento, numero_documento))
        del_correo = next(
            (
                c
                for c in self.clientes.values()
                if correo is not None and c["email"].lower() == correo.strip().lower()
            ),
            None,
        )
        del_documento = next(
            (
                c
                for c in self.clientes.values()
                if tipo_documento is not None
                and c["tipoDocumento"] == tipo_documento
                and c["numeroDocumento"] == numero_documento
            ),
            None,
        )
        if correo is not None and tipo_documento is not None:
            # Como svc-core: con los dos, deben ser de la misma cuenta.
            if del_documento is not None and del_correo is not None:
                if del_documento["id"] != del_correo["id"]:
                    raise self._distintos("correo")
                return del_documento
            if del_documento is not None:
                return del_documento
            if del_correo is not None:
                raise self._distintos("documento")
        else:
            cliente = del_correo if correo is not None else del_documento
            if cliente is not None:
                return cliente
        raise RecursoNoEncontrado("Cliente no encontrado")

    @staticmethod
    def _distintos(campo: str) -> Conflicto:
        mensaje = "El correo y el documento son de cuentas distintas"
        return Conflicto(
            mensaje, errores=[{"campo": campo, "mensaje": mensaje}], codigo="CLIENTES_DISTINTOS"
        )

    async def crear_cliente_asistido(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        self.llamadas.append(("crear_cliente_asistido", cuerpo, idempotency_key))
        correo = cuerpo["email"].strip().lower()
        for cliente in self.clientes.values():
            if cliente["email"] == correo:
                raise Conflicto.por_campo("correo")
            if (cliente["tipoDocumento"], cliente["numeroDocumento"]) == (
                cuerpo["tipoDocumento"],
                cuerpo["numeroDocumento"],
            ):
                raise Conflicto.por_campo("documento")
        cliente = {
            "id": str(uuid.uuid4()),
            "identityRef": f"fake-{uuid.uuid4()}",
            **{k: v for k, v in cuerpo.items() if k != "email"},
            "email": correo,
            "estado": "ACTIVO",
            "correoConfirmado": True,
            "creadoEn": datetime.now(UTC).isoformat(),
        }
        self.clientes[cliente["id"]] = cliente
        return cliente

    async def calcular_precio(self, cuerpo: dict) -> dict:
        self.llamadas.append(("calcular_precio", cuerpo))
        cliente = self._cliente(cuerpo["clienteId"])
        valor = float(cuerpo["valorCredito"])
        ahora = datetime.now(UTC)
        nacimiento = datetime.fromisoformat(cliente["fechaNacimiento"]).date()
        edad = (
            ahora.year
            - nacimiento.year
            - ((ahora.month, ahora.day) < (nacimiento.month, nacimiento.day))
        )
        if not 18 <= edad <= 65 or edad * 12 + cuerpo["plazoRestanteMeses"] > 75 * 12:
            raise ReglaNegocio("Edad o plazo fuera de rango", codigo="FUERA_DE_RANGO")
        vida = round(valor * self._TASA_VIDA)
        hogar = round(valor * self._TASA_HOGAR)
        return {
            "edad": edad,
            "datosCredito": {
                "valorCredito": valor,
                "plazoRestanteMeses": cuerpo["plazoRestanteMeses"],
                "entidadAcreedora": cuerpo["entidadAcreedora"],
                "saldoInsoluto": valor,
            },
            "origenDatos": "CONFIRMADO",
            "tasa": 0.196,
            "versionTarifa": 2,
            "coberturaMeses": cuerpo["plazoRestanteMeses"],
            "coberturas": [
                {
                    "codigo": "VIDA",
                    "obligatoria": True,
                    "sumaAsegurada": valor,
                    "primaNeta": vida,
                    "iva": 0,
                    "primaTotal": vida,
                },
                {
                    "codigo": "HOGAR",
                    "obligatoria": False,
                    "sumaAsegurada": valor,
                    "primaNeta": hogar,
                    "iva": round(hogar * 0.19),
                    "primaTotal": hogar + round(hogar * 0.19),
                },
            ],
            "vigenciaCotizacion": {
                "desde": ahora.isoformat(),
                "hasta": (ahora + timedelta(days=30)).isoformat(),
            },
            "creadaEn": ahora.isoformat(),
        }

    async def vender(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        self.llamadas.append(("vender", cuerpo, idempotency_key))
        clave = (cuerpo["socioId"], idempotency_key or "")
        if idempotency_key and clave in self._por_clave:
            return self._por_clave[clave]
        if "VIDA" not in cuerpo["coberturas"]:
            raise SolicitudInvalida(
                "La cobertura VIDA es obligatoria",
                errores=[{"campo": "coberturas", "mensaje": "La cobertura VIDA es obligatoria"}],
            )
        precio = await self.calcular_precio(cuerpo)
        coberturas = [c for c in precio["coberturas"] if c["codigo"] in cuerpo["coberturas"]]
        ahora = datetime.now(UTC).isoformat()
        poliza = {
            "polizaId": str(uuid.uuid4()),
            "clienteId": cuerpo["clienteId"],
            "socioId": cuerpo["socioId"],
            "numero": f"SOL-VH-2026-FAKE{len(self.ventas) + 1:02d}",
            "estado": "EMITIDA",
            "producto": "VIDA_HIPOTECARIO",
            "creadaEn": ahora,
            "emitidaEn": ahora,
            "solicitudVigenteHasta": None,
            "vigencia": {"desde": ahora, "hasta": precio["vigenciaCotizacion"]["hasta"]},
            "coberturas": coberturas,
            "primaTotalMensual": sum(c["primaTotal"] for c in coberturas),
            "moneda": "COP",
            "datosCredito": precio["datosCredito"],
            "origenDatos": "CONFIRMADO",
            "cotizacionCreadaEn": precio["creadaEn"],
            "pendientes": [],
            "documento": {"estado": "DISPONIBLE"},
            "documentosAceptados": [{"tipo": "poliza", "version": "V1", "aceptadoEn": ahora}],
            "rechazo": None,
            "datosVenta": {
                "direccion": cuerpo["direccion"],
                "municipio": cuerpo["municipio"],
                "cuotaMensual": cuerpo["cuotaMensual"],
            },
        }
        self.ventas.append(poliza)
        if idempotency_key:
            self._por_clave[clave] = poliza
        return poliza

    async def listar_polizas_del_socio(
        self, socio_id: str, limite: int, cursor: str | None
    ) -> dict:
        """Como svc-core con ``socioId``: solo lo que vendió ese socio, de la más reciente a la
        más antigua, con el cliente (nombre y últimos cuatro dígitos) en cada fila."""
        self.llamadas.append(("listar_polizas_del_socio", socio_id, limite, cursor))
        propias = [p for p in reversed(self.ventas) if p["socioId"] == socio_id]
        desde = int(cursor) if cursor else 0
        pagina = propias[desde : desde + limite]
        return {
            "items": [
                {
                    "polizaId": p["polizaId"],
                    "numero": p["numero"],
                    "clienteId": p["clienteId"],
                    "estado": p["estado"],
                    "creadaEn": p["creadaEn"],
                    "solicitudVigenteHasta": None,
                    "primaTotalMensual": p["primaTotalMensual"],
                    "origenDatos": p["origenDatos"],
                    "pendientes": [],
                    "socioId": p["socioId"],
                    "emitidaEn": p["emitidaEn"],
                    "cliente": self._del_cliente(p["clienteId"]),
                }
                for p in pagina
            ],
            "siguienteCursor": str(desde + limite) if desde + limite < len(propias) else None,
        }

    def _del_cliente(self, cliente_id: str) -> dict | None:
        cliente = self.clientes.get(cliente_id)
        if cliente is None:
            return None
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


class FakeSolicitudes:
    """Solicitudes en revisión en memoria con la forma que entrega svc-core y sus reglas de
    decisión. ``sembrar`` deja una póliza ya armada (con ``clienteId``, ``pendientes``,
    ``evidencias`` y ``decisiones``) y el cliente dueño, para pruebas y para el front."""

    _MENSAJES_RECHAZO = {
        "EVIDENCIA_EN_REVISION": "No pudimos verificar tu identidad con los documentos enviados.",
        "CREDITO_NO_VERIFICADO": "No pudimos verificar los datos de tu crédito.",
    }

    def __init__(self) -> None:
        self._polizas: dict[str, dict] = {}
        self._clientes: dict[str, dict] = {}
        self._imagenes: dict[str, tuple[bytes, str]] = {}
        self.decisiones: list[tuple[str, dict]] = []

    def sembrar(self, poliza: dict, cliente: dict) -> dict:
        self._polizas[poliza["polizaId"]] = poliza
        self._clientes[cliente["id"]] = cliente
        return poliza

    def sembrar_imagen(self, evidencia_id: str, contenido: bytes, content_type: str) -> None:
        self._imagenes[evidencia_id] = (contenido, content_type)

    def _por_id(self, poliza_id: str) -> dict:
        if poliza_id not in self._polizas:
            raise RecursoNoEncontrado("Póliza no encontrada")
        return self._polizas[poliza_id]

    async def listar_solicitudes(self, limite: int, cursor: str | None) -> dict:
        en_revision = sorted(
            (
                p
                for p in self._polizas.values()
                if p["estado"] == "PENDIENTE"
                and any(x["tipo"] == "AGENTE" for x in p["pendientes"])
            ),
            key=lambda p: p["creadaEn"],
        )
        desde = int(cursor) if cursor else 0
        pagina = en_revision[desde : desde + limite]
        return {
            "items": [
                {
                    "polizaId": p["polizaId"],
                    "numero": p.get("numero"),
                    "clienteId": p["clienteId"],
                    "estado": p["estado"],
                    "creadaEn": p["creadaEn"],
                    "solicitudVigenteHasta": p.get("solicitudVigenteHasta"),
                    "primaTotalMensual": p["primaTotalMensual"],
                    "origenDatos": p["origenDatos"],
                    "pendientes": [
                        {"codigo": x["codigo"], "tipo": x["tipo"]} for x in p["pendientes"]
                    ],
                }
                for p in pagina
            ],
            "siguienteCursor": (str(desde + limite) if desde + limite < len(en_revision) else None),
        }

    async def obtener_solicitud(self, poliza_id: str) -> dict:
        return dict(self._por_id(poliza_id))

    async def obtener_cliente_de_solicitud(self, cliente_id: str) -> dict:
        if cliente_id not in self._clientes:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return dict(self._clientes[cliente_id])

    async def imagen_de_evidencia(self, evidencia_id: str) -> tuple[bytes, str]:
        if evidencia_id not in self._imagenes:
            raise RecursoNoEncontrado("Archivo no encontrado")
        return self._imagenes[evidencia_id]

    async def decidir_solicitud(self, poliza_id: str, cuerpo: dict) -> dict:
        self.decisiones.append((poliza_id, cuerpo))
        poliza = self._por_id(poliza_id)
        ahora = datetime.now(UTC)
        vigente_hasta = poliza.get("solicitudVigenteHasta")
        if poliza["estado"] == "PENDIENTE" and vigente_hasta:
            if datetime.fromisoformat(vigente_hasta.replace("Z", "+00:00")) < ahora:
                raise Conflicto("La solicitud venció", codigo="SOLICITUD_VENCIDA")
        if poliza["estado"] != "PENDIENTE":
            raise Conflicto("La póliza no está pendiente", codigo="POLIZA_NO_PENDIENTE")
        vigentes = {x["codigo"] for x in poliza["pendientes"]}
        for codigo in cuerpo["pendientes"]:
            if codigo not in vigentes:
                raise ReglaNegocio(
                    f"La póliza no tiene el pendiente {codigo}", codigo="PENDIENTE_NO_APLICA"
                )
        poliza["decisiones"] = [
            *poliza["decisiones"],
            {
                "decision": cuerpo["decision"],
                "agente": cuerpo["agente"],
                "pendientes": list(cuerpo["pendientes"]),
                "nota": cuerpo.get("nota"),
                "decididaEn": ahora.isoformat(),
            },
        ]
        if cuerpo["decision"] == "RECHAZAR":
            poliza["estado"] = "RECHAZADA"
            poliza["pendientes"] = []
            poliza["rechazo"] = {
                "motivos": [
                    {"codigo": c, "mensaje": self._MENSAJES_RECHAZO[c]}
                    for c in cuerpo["pendientes"]
                ],
                "rechazadaEn": ahora.isoformat(),
            }
        else:
            poliza["pendientes"] = [
                x for x in poliza["pendientes"] if x["codigo"] not in cuerpo["pendientes"]
            ]
            if not poliza["pendientes"]:
                poliza["estado"] = "EMITIDA"
                poliza["numero"] = "SOL-VH-2026-FAKE02"
                poliza["emitidaEn"] = ahora.isoformat()
                poliza["solicitudVigenteHasta"] = None
        return dict(poliza)
