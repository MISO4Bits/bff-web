from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.api.schemas import (
    ConsentimientoVistaOut,
    CredencialesRequest,
    CuentaOut,
    OtorgarConsentimientoRequest,
    RefrescoRequest,
    RegistroRequest,
    RegistroResponse,
    SesionOut,
)
from app.domain import Claims, NoAutorizado, RegistroInput
from app.services import OnboardingService

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
async def iniciar_sesion(
    payload: CredencialesRequest, service: ServiceDep
) -> SesionOut:
    sesion = await service.iniciar_sesion(payload.email, payload.password)
    return SesionOut.model_validate(sesion)


@router.post("/sesiones/refresco", response_model=SesionOut, tags=["Sesión"])
async def refrescar_sesion(
    payload: RefrescoRequest, service: ServiceDep
) -> SesionOut:
    return SesionOut.model_validate(service.refrescar(payload.refresh_token))


@router.get("/cuenta", response_model=CuentaOut, tags=["Cuenta"])
async def obtener_cuenta(claims: ClaimsDep, service: ServiceDep) -> CuentaOut:
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
    vista = await service.otorgar_consentimiento(
        claims.cliente_id, payload.scope, payload.politica_version
    )
    return ConsentimientoVistaOut.model_validate(vista)


@router.delete(
    "/cuenta/consentimientos/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Consentimientos"],
)
async def revocar_mi_consentimiento(
    scope: str, claims: ClaimsDep, service: ServiceDep
) -> Response:
    await service.revocar_consentimiento(claims.cliente_id, scope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
