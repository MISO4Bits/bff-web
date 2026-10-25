from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from app.api.schemas import (
    PATRON_IDIOMA,
    PATRON_VERSION,
    ConfirmacionRequest,
    ConsentimientoVistaOut,
    CotizacionOut,
    CotizacionRequest,
    CredencialesRequest,
    CreditosHipotecariosOut,
    CuentaOut,
    DisponibilidadOut,
    DocumentoLegalOut,
    DocumentoPolizaOut,
    EstadoEvidenciaOut,
    EstadoPoliza,
    EvidenciaSubidaOut,
    OtorgarConsentimientoRequest,
    PolizaOut,
    RefrescoRequest,
    RegistroRequest,
    RegistroResponse,
    SesionOut,
    SolicitarPolizaRequest,
    TipoDocumentoLegal,
    TipoEvidencia,
)
from app.domain import (
    Claims,
    CotizacionInput,
    DatosCreditoInput,
    NoAutorizado,
    RegistroInput,
)
from app.logging_utils import sanear_para_log
from app.services import (
    CreditosHipotecariosService,
    DocumentosLegalesService,
    EvidenciasIdentidadService,
    OnboardingService,
    PolizasService,
)

logger = logging.getLogger("bff_web.api")
router = APIRouter(prefix="/v1")


def get_service(request: Request) -> OnboardingService:
    return request.app.state.service


ServiceDep = Annotated[OnboardingService, Depends(get_service)]


def get_documentos_legales(request: Request) -> DocumentosLegalesService:
    return request.app.state.documentos_legales


DocumentosLegalesDep = Annotated[DocumentosLegalesService, Depends(get_documentos_legales)]


def get_creditos(request: Request) -> CreditosHipotecariosService:
    return request.app.state.creditos


CreditosDep = Annotated[CreditosHipotecariosService, Depends(get_creditos)]


def get_polizas(request: Request) -> PolizasService:
    return request.app.state.polizas


PolizasDep = Annotated[PolizasService, Depends(get_polizas)]


def get_evidencias(request: Request) -> EvidenciasIdentidadService:
    return request.app.state.evidencias


EvidenciasDep = Annotated[EvidenciasIdentidadService, Depends(get_evidencias)]
MercadoQuery = Annotated[Literal["CO"], Query()]
IdiomaQuery = Annotated[str, Query(pattern=PATRON_IDIOMA)]

# Una versión publicada nunca cambia: el navegador la puede guardar para siempre.
CACHE_VERSION_INMUTABLE = "public, max-age=31536000, immutable"


async def claims_actuales(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Claims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NoAutorizado("falta el encabezado Authorization")
    token = authorization.split(" ", 1)[1]
    return request.app.state.sessions.verificar(token)


ClaimsDep = Annotated[Claims, Depends(claims_actuales)]


async def identity_token_actual(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Token de Identity Platform tal cual (no la sesión propia del BFF) —
    ver esquema ``identityPlatformToken`` en el contrato. El BFF no lo decodifica
    localmente, solo lo reenvía a Identity Platform para que lo valide."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NoAutorizado("falta el encabezado Authorization")
    return authorization.split(" ", 1)[1]


IdentityTokenDep = Annotated[str, Depends(identity_token_actual)]


@router.post(
    "/registro",
    response_model=RegistroResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Registro"],
)
async def registrarse(
    payload: RegistroRequest,
    service: ServiceDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=64)] = None,
) -> RegistroResponse:
    logger.info("POST /v1/registro: solicitud recibida")
    entrada = RegistroInput(
        email=payload.email,
        password=payload.password,
        tipo_documento=payload.tipo_documento,
        numero_documento=payload.numero_documento,
        primer_nombre=payload.primer_nombre,
        primer_apellido=payload.primer_apellido,
        fecha_nacimiento=payload.fecha_nacimiento,
        politica_version=payload.politica_version,
        autoriza_tratamiento_datos=payload.autoriza_tratamiento_datos,
        autoriza_datos_financieros=payload.autoriza_datos_financieros,
        segundo_nombre=payload.segundo_nombre,
        segundo_apellido=payload.segundo_apellido,
        telefono=payload.telefono,
        politica_version_tratamiento_datos=payload.politica_version_tratamiento_datos,
        politica_version_datos_financieros=payload.politica_version_datos_financieros,
    )
    cuenta, sesion = await service.registrar(entrada, idempotency_key)
    return RegistroResponse(
        cuenta=CuentaOut.model_validate(cuenta),
        sesion=SesionOut.model_validate(sesion),
    )


@router.get("/registro/disponibilidad", response_model=DisponibilidadOut, tags=["Registro"])
async def consultar_disponibilidad(
    service: ServiceDep,
    correo: Annotated[str | None, Query(max_length=254)] = None,
    tipo_documento: Annotated[str | None, Query(alias="tipoDocumento")] = None,
    numero_documento: Annotated[
        str | None, Query(alias="numeroDocumento", min_length=4, max_length=20)
    ] = None,
) -> DisponibilidadOut:
    logger.info("GET /v1/registro/disponibilidad: solicitud recibida")
    resultado = await service.verificar_disponibilidad(
        email=correo, tipo_documento=tipo_documento, numero_documento=numero_documento
    )
    return DisponibilidadOut.model_validate(resultado)


@router.post(
    "/registro/reenvio-confirmacion",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Registro"],
)
async def reenviar_confirmacion(id_token: IdentityTokenDep, service: ServiceDep) -> Response:
    logger.info("POST /v1/registro/reenvio-confirmacion: solicitud recibida")
    await service.reenviar_confirmacion(id_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/registro/confirmacion", response_model=CuentaOut, tags=["Registro"])
async def confirmar_cuenta(
    payload: ConfirmacionRequest, service: ServiceDep, response: Response
) -> CuentaOut:
    # Nunca se registra el código del enlace (es una credencial de un solo uso).
    logger.info("POST /v1/registro/confirmacion: solicitud recibida")
    cuenta = await service.confirmar_cuenta(payload.oob_code)
    response.headers["Cache-Control"] = "no-store"
    return CuentaOut.model_validate(cuenta)


@router.post("/sesiones", response_model=SesionOut, tags=["Sesión"])
async def iniciar_sesion(payload: CredencialesRequest, service: ServiceDep) -> SesionOut:
    logger.info("POST /v1/sesiones: solicitud recibida")
    sesion = await service.iniciar_sesion(payload.email, payload.password)
    return SesionOut.model_validate(sesion)


@router.post("/sesiones/refresco", response_model=SesionOut, tags=["Sesión"])
async def refrescar_sesion(payload: RefrescoRequest, service: ServiceDep) -> SesionOut:
    logger.info("POST /v1/sesiones/refresco: solicitud recibida")
    return SesionOut.model_validate(service.refrescar(payload.refresh_token))


@router.get("/cuenta", response_model=CuentaOut, tags=["Cuenta"])
async def obtener_cuenta(claims: ClaimsDep, service: ServiceDep) -> CuentaOut:
    logger.info("GET /v1/cuenta: solicitud recibida cliente_id=%s", claims.cliente_id)
    cuenta = await service.obtener_cuenta(claims.cliente_id)
    return CuentaOut.model_validate(cuenta)


@router.get(
    "/cuenta/consentimientos",
    response_model=list[ConsentimientoVistaOut],
    tags=["Consentimientos"],
)
async def listar_mis_consentimientos(
    claims: ClaimsDep, service: ServiceDep
) -> list[ConsentimientoVistaOut]:
    logger.info(
        "GET /v1/cuenta/consentimientos: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    items = await service.listar_consentimientos(claims.cliente_id)
    return [ConsentimientoVistaOut.model_validate(i) for i in items]


@router.post(
    "/cuenta/consentimientos",
    response_model=ConsentimientoVistaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Consentimientos"],
)
async def otorgar_mi_consentimiento(
    payload: OtorgarConsentimientoRequest,
    claims: ClaimsDep,
    service: ServiceDep,
) -> ConsentimientoVistaOut:
    logger.info(
        "POST /v1/cuenta/consentimientos: solicitud recibida cliente_id=%s scope=%s",
        claims.cliente_id,
        payload.scope,
    )
    vista = await service.otorgar_consentimiento(
        claims.cliente_id, payload.scope, payload.politica_version
    )
    return ConsentimientoVistaOut.model_validate(vista)


@router.delete(
    "/cuenta/consentimientos/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Consentimientos"],
)
async def revocar_mi_consentimiento(scope: str, claims: ClaimsDep, service: ServiceDep) -> Response:
    logger.info(
        "DELETE /v1/cuenta/consentimientos/%s: solicitud recibida cliente_id=%s",
        sanear_para_log(scope),
        claims.cliente_id,
    )
    await service.revocar_consentimiento(claims.cliente_id, scope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/cuenta/evidencias-identidad",
    response_model=EvidenciaSubidaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Evidencias de identidad"],
)
async def subir_mi_evidencia(
    tipo: Annotated[TipoEvidencia, Form()],
    imagen: Annotated[UploadFile, File()],
    claims: ClaimsDep,
    service: EvidenciasDep,
    response: Response,
) -> EvidenciaSubidaOut:
    logger.info(
        "POST /v1/cuenta/evidencias-identidad: solicitud recibida cliente_id=%s tipo=%s",
        claims.cliente_id,
        tipo,
    )
    # Se lee un byte de más para detectar el exceso sin cargar un archivo sin límite.
    datos = await imagen.read(service.max_bytes + 1)
    resultado = await service.subir(
        claims.cliente_id,
        tipo,
        datos,
        imagen.filename or "imagen",
        imagen.content_type or "application/octet-stream",
    )
    if resultado.estado == "REINTENTAR":
        response.status_code = status.HTTP_200_OK  # veredicto normal: no se creó nada
    response.headers["Cache-Control"] = "no-store"
    return EvidenciaSubidaOut.model_validate(resultado)


@router.get(
    "/cuenta/evidencias-identidad",
    response_model=list[EstadoEvidenciaOut],
    tags=["Evidencias de identidad"],
)
async def estado_mis_evidencias(
    claims: ClaimsDep, service: EvidenciasDep, response: Response
) -> list[EstadoEvidenciaOut]:
    logger.info(
        "GET /v1/cuenta/evidencias-identidad: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    items = await service.estado(claims.cliente_id)
    response.headers["Cache-Control"] = "no-store"
    return [EstadoEvidenciaOut.model_validate(i) for i in items]


def _a_dominio_cotizacion(payload: CotizacionRequest) -> CotizacionInput:
    dc = payload.datos_credito
    return CotizacionInput(
        datos_credito=DatosCreditoInput(
            valor_credito=dc.valor_credito,
            plazo_restante_meses=dc.plazo_restante_meses,
            entidad_acreedora=dc.entidad_acreedora,
            saldo_insoluto=dc.saldo_insoluto,
        ),
    )


@router.post(
    "/cotizaciones",
    response_model=CotizacionOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    tags=["Cotización"],
)
async def crear_cotizacion(
    payload: CotizacionRequest,
    claims: ClaimsDep,
    service: ServiceDep,
) -> CotizacionOut:
    logger.info("POST /v1/cotizaciones: solicitud recibida cliente_id=%s", claims.cliente_id)
    cotizacion = await service.crear_cotizacion(
        claims.cliente_id, claims.fecha_nacimiento, _a_dominio_cotizacion(payload)
    )
    return CotizacionOut.model_validate(cotizacion)


@router.get(
    "/cotizaciones/actual",
    response_model=CotizacionOut,
    response_model_exclude_none=True,
    tags=["Cotización"],
)
async def obtener_cotizacion_activa(
    claims: ClaimsDep,
    service: ServiceDep,
) -> CotizacionOut:
    logger.info("GET /v1/cotizaciones/actual: solicitud recibida cliente_id=%s", claims.cliente_id)
    cotizacion = await service.obtener_cotizacion_activa(claims.cliente_id)
    return CotizacionOut.model_validate(cotizacion)


@router.delete(
    "/cotizaciones/actual",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Cotización"],
)
async def eliminar_cotizacion_activa(claims: ClaimsDep, service: ServiceDep) -> Response:
    logger.info(
        "DELETE /v1/cotizaciones/actual: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    await service.eliminar_cotizacion_activa(claims.cliente_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/documentos-legales",
    response_model=list[DocumentoLegalOut],
    response_model_exclude_none=True,
    tags=["Documentos legales"],
)
async def listar_documentos_legales(
    service: DocumentosLegalesDep,
    mercado: MercadoQuery,
    idioma: IdiomaQuery = "es-CO",
    tipo: Annotated[TipoDocumentoLegal | None, Query()] = None,
) -> list[DocumentoLegalOut]:
    logger.info("GET /v1/documentos-legales: solicitud recibida mercado=%s tipo=%s", mercado, tipo)
    documentos = await service.listar_vigentes(mercado, idioma, tipo)
    return [DocumentoLegalOut.model_validate(d) for d in documentos]


@router.get(
    "/documentos-legales/{tipo}/versiones/{version}",
    response_model=DocumentoLegalOut,
    response_model_exclude_none=True,
    tags=["Documentos legales"],
)
async def obtener_version_documento_legal(
    tipo: TipoDocumentoLegal,
    version: Annotated[str, Path(pattern=PATRON_VERSION)],
    service: DocumentosLegalesDep,
    response: Response,
    mercado: MercadoQuery,
    idioma: IdiomaQuery = "es-CO",
) -> DocumentoLegalOut:
    logger.info(
        "GET /v1/documentos-legales/%s/versiones/%s: solicitud recibida mercado=%s",
        tipo,
        version,
        mercado,
    )
    documento = await service.obtener_version(tipo, version, mercado, idioma)
    response.headers["Cache-Control"] = CACHE_VERSION_INMUTABLE
    return DocumentoLegalOut.model_validate(documento)


@router.get(
    "/creditos-hipotecarios",
    response_model=CreditosHipotecariosOut,
    response_model_exclude_none=True,
    tags=["Cotización"],
)
async def obtener_creditos_hipotecarios(
    claims: ClaimsDep,
    service: CreditosDep,
    response: Response,
    mercado: Annotated[Literal["CO"], Query()] = "CO",
) -> CreditosHipotecariosOut:
    logger.info(
        "GET /v1/creditos-hipotecarios: solicitud recibida cliente_id=%s", claims.cliente_id
    )
    creditos = await service.obtener(claims.cliente_id, mercado)
    response.headers["Cache-Control"] = "no-store"  # datos financieros del cliente
    return CreditosHipotecariosOut.model_validate(creditos)


REINTENTO_DOCUMENTO_S = 2


@router.post(
    "/cuenta/polizas",
    response_model=PolizaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Pólizas"],
)
async def solicitar_mi_poliza(
    payload: SolicitarPolizaRequest, claims: ClaimsDep, service: PolizasDep, response: Response
) -> PolizaOut:
    logger.info("POST /v1/cuenta/polizas: solicitud recibida cliente_id=%s", claims.cliente_id)
    poliza, creada = await service.solicitar(
        claims.cliente_id, payload.model_dump(by_alias=True, mode="json")
    )
    if not creada:
        response.status_code = status.HTTP_200_OK
    response.headers["Cache-Control"] = "no-store"
    return PolizaOut.model_validate(poliza)


@router.get("/cuenta/polizas", response_model=list[PolizaOut], tags=["Pólizas"])
async def listar_mis_polizas(
    claims: ClaimsDep,
    service: PolizasDep,
    response: Response,
    estado: Annotated[EstadoPoliza | None, Query()] = None,
) -> list[PolizaOut]:
    logger.info("GET /v1/cuenta/polizas: solicitud recibida cliente_id=%s", claims.cliente_id)
    response.headers["Cache-Control"] = "no-store"
    return [PolizaOut.model_validate(p) for p in await service.listar(claims.cliente_id, estado)]


@router.get("/cuenta/polizas/{poliza_id}", response_model=PolizaOut, tags=["Pólizas"])
async def obtener_mi_poliza(
    poliza_id: Annotated[str, Path(min_length=1, max_length=64)],
    claims: ClaimsDep,
    service: PolizasDep,
    response: Response,
) -> PolizaOut:
    logger.info(
        "GET /v1/cuenta/polizas/%s: solicitud recibida cliente_id=%s",
        sanear_para_log(poliza_id),
        claims.cliente_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return PolizaOut.model_validate(await service.obtener(claims.cliente_id, poliza_id))


@router.get(
    "/cuenta/polizas/{poliza_id}/documento",
    response_model=DocumentoPolizaOut,
    tags=["Pólizas"],
)
async def documento_de_mi_poliza(
    poliza_id: Annotated[str, Path(min_length=1, max_length=64)],
    claims: ClaimsDep,
    service: PolizasDep,
    response: Response,
) -> DocumentoPolizaOut:
    logger.info(
        "GET /v1/cuenta/polizas/%s/documento: solicitud recibida cliente_id=%s",
        sanear_para_log(poliza_id),
        claims.cliente_id,
    )
    cuerpo, listo = await service.documento(claims.cliente_id, poliza_id)
    if not listo:
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Retry-After"] = str(REINTENTO_DOCUMENTO_S)
    response.headers["Cache-Control"] = "no-store"
    return DocumentoPolizaOut.model_validate(cuerpo)
