"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain import (
    MENSAJE_ENLACE_INVALIDO,
    ClienteCore,
    Conflicto,
    ConsentimientoVista,
    Cotizacion,
    CotizacionInput,
    DocumentoLegal,
    NoAutorizado,
    Oferta,
    RecursoNoEncontrado,
    RegistroInput,
    ReglaNegocio,
    Vigencia,
)


class FakeIdentityProvider:
    def __init__(self) -> None:
        # email -> (sub, password)
        self._por_email: dict[str, tuple[str, str]] = {}
        self._verificados: set[str] = set()
        self._codigos: dict[str, str] = {}

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


class FakeCotizacion:
    """Rating simplificado, sin llamar a Perfilamiento — solo para modo standalone/pruebas."""

    _TASA_MENSUAL = Decimal("0.0005")
    _VIGENCIA = timedelta(days=30)

    def __init__(self) -> None:
        self._cotizaciones: dict[str, Cotizacion] = {}

    async def crear_cotizacion(self, cliente_id: str, entrada: CotizacionInput) -> Cotizacion:
        saldo = entrada.datos_credito.saldo_insoluto
        prima = float((saldo * self._TASA_MENSUAL).quantize(Decimal("1")))
        ahora = datetime.now(UTC)
        cotizacion = Cotizacion(
            id=str(uuid.uuid4()),
            estado="VIGENTE",
            producto="VIDA_HIPOTECARIO",
            oferta=Oferta(
                prima_mensual=prima,
                prima_base_mensual=prima,
                suma_asegurada=float(saldo),
                cobertura_meses=entrada.datos_credito.plazo_meses,
                moneda="COP",
                personalizado=False,
                fuentes_no_disponibles=("perfilamiento",),
            ),
            vigencia_cotizacion=Vigencia(desde=ahora, hasta=ahora + self._VIGENCIA),
            creada_en=ahora,
        )
        self._cotizaciones[cotizacion.id] = cotizacion
        return cotizacion

    async def obtener_cotizacion(self, cliente_id: str, cotizacion_id: str) -> Cotizacion:
        cotizacion = self._cotizaciones.get(cotizacion_id)
        if cotizacion is None:
            raise RecursoNoEncontrado("Cotización no encontrada")
        return cotizacion


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
)


class FakeDocumentosLegales:
    """Textos de ejemplo abreviados (mercado ``CO``, idioma ``es-CO``) para correr
    el BFF standalone. Los textos reales viven en svc-productos."""

    def __init__(self, documentos: tuple[DocumentoLegal, ...] = _DOCUMENTOS_LEGALES_DE_EJEMPLO):
        self._documentos = documentos

    @staticmethod
    def _hay_textos(mercado: str, idioma: str) -> bool:
        return (mercado, idioma) == ("CO", "es-CO")

    async def listar_vigentes(self, mercado: str, idioma: str) -> list[DocumentoLegal]:
        return list(self._documentos) if self._hay_textos(mercado, idioma) else []

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        if self._hay_textos(mercado, idioma):
            for documento in self._documentos:
                if (documento.tipo, documento.version) == (tipo, version):
                    return documento
        raise RecursoNoEncontrado("Documento legal no encontrado")
