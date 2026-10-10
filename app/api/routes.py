from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status

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
    OtorgarConsentimientoRequest,
    RefrescoRequest,
    RegistroRequest,
    RegistroResponse,
    SesionOut,
    TipoDocumentoLegal,
)
from app.domain import (
    Claims,
    CotizacionInput,
    CuestionarioHabitosInput,
    DatosCreditoInput,
    NoAutorizado,
    RegistroInput,
)
from app.logging_utils import sanear_para_log
from app.services import CreditosHipotecariosService, DocumentosLegalesService, OnboardingService

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


def _a_dominio_cotizacion(payload: CotizacionRequest) -> CotizacionInput:
    dc = payload.datos_credito
    q = payload.cuestionario_habitos
    return CotizacionInput(
        datos_credito=DatosCreditoInput(
            valor_credito=dc.valor_credito,
            plazo_meses=dc.plazo_meses,
            edad=dc.edad,
            entidad_acreedora=dc.entidad_acreedora,
            saldo_insoluto=dc.saldo_insoluto,
        ),
        cuestionario_habitos=CuestionarioHabitosInput(
            consume_tabaco=q.consume_tabaco,
            actividad_fisica=q.actividad_fisica,
            condiciones_preexistentes=q.condiciones_preexistentes,
            dependientes_economicos=q.dependientes_economicos,
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
    cotizacion = await service.crear_cotizacion(claims.cliente_id, _a_dominio_cotizacion(payload))
    return CotizacionOut.model_validate(cotizacion)


@router.get(
    "/cotizaciones/{cotizacion_id}",
    response_model=CotizacionOut,
    response_model_exclude_none=True,
    tags=["Cotización"],
)
async def obtener_cotizacion(
    cotizacion_id: str,
    claims: ClaimsDep,
    service: ServiceDep,
) -> CotizacionOut:
    logger.info(
        "GET /v1/cotizaciones/%s: solicitud recibida cliente_id=%s",
        sanear_para_log(cotizacion_id),
        claims.cliente_id,
    )
    cotizacion = await service.obtener_cotizacion(claims.cliente_id, cotizacion_id)
    return CotizacionOut.model_validate(cotizacion)


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
) -> list[DocumentoLegalOut]:
    logger.info("GET /v1/documentos-legales: solicitud recibida mercado=%s", mercado)
    documentos = await service.listar_vigentes(mercado, idioma)
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
