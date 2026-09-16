from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.api.schemas import (
    ConsentimientoVistaOut,
    CotizacionOut,
    CotizacionRequest,
    CredencialesRequest,
    CuentaOut,
    OtorgarConsentimientoRequest,
    RefrescoRequest,
    RegistroRequest,
    RegistroResponse,
    SesionOut,
)
from app.domain import (
    Claims,
    CotizacionInput,
    CuestionarioHabitosInput,
    DatosCreditoInput,
    NoAutorizado,
    RegistroInput,
)
from app.services import OnboardingService

logger = logging.getLogger("bff_web.api")
router = APIRouter(prefix="/v1")


def get_service(request: Request) -> OnboardingService:
    return request.app.state.service


ServiceDep = Annotated[OnboardingService, Depends(get_service)]


async def claims_actuales(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Claims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NoAutorizado("falta el encabezado Authorization")
    token = authorization.split(" ", 1)[1]
    return request.app.state.sessions.verificar(token)


ClaimsDep = Annotated[Claims, Depends(claims_actuales)]


@router.post(
    "/registro",
    response_model=RegistroResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Registro"],
)
async def registrarse(payload: RegistroRequest, service: ServiceDep) -> RegistroResponse:
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
        segundo_nombre=payload.segundo_nombre,
        segundo_apellido=payload.segundo_apellido,
        telefono=payload.telefono,
    )
    cuenta, sesion = await service.registrar(entrada)
    return RegistroResponse(
        cuenta=CuentaOut.model_validate(cuenta),
        sesion=SesionOut.model_validate(sesion),
    )


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
        scope,
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
        cotizacion_id,
        claims.cliente_id,
    )
    cotizacion = await service.obtener_cotizacion(claims.cliente_id, cotizacion_id)
    return CotizacionOut.model_validate(cotizacion)
