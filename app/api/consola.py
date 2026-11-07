"""Rutas del Backoffice (BITS-375): ingreso del personal y rutas protegidas por rol.

Van en un módulo aparte de ``routes.py`` para que el trabajo de la consola no choque con el del
portal del cliente. Tres prefijos, cada uno con su regla:

- ``/v1/backoffice/sesiones`` y ``/v1/backoffice/sesiones/refresco``: públicas. Ingreso y refresco
  de ``ADMIN`` y ``SOCIO``. Son las únicas rutas públicas del prefijo.
- ``/v1/backoffice``: el resto exige ``ADMIN``. La regla está en el router, no en cada ruta: toda
  ruta nueva queda protegida sin acordarse de ponerla. Por eso el ingreso vive en su propio router
  (``sesion_router``) y nunca se le agregan rutas de negocio.
- ``/v1/socio``: solo ``SOCIO``, con la misma regla en el router.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api.schemas import (
    CrearUsuarioRequest,
    CredencialesRequest,
    DecidirSiniestroRequest,
    EstadoSiniestro,
    RefrescoRequest,
    SesionConsolaOut,
    SiniestroInternoOut,
    SiniestroInternoResumenOut,
    UsuarioOut,
)
from app.consola import ConsolaService
from app.domain import ClaimsConsola, NoAutorizado, Prohibido, RolConsola
from app.logging_utils import sanear_para_log
from app.services import SiniestrosBackofficeService

logger = logging.getLogger("bff_web.api")


def get_consola(request: Request) -> ConsolaService:
    return request.app.state.consola


ConsolaDep = Annotated[ConsolaService, Depends(get_consola)]


def get_siniestros_backoffice(request: Request) -> SiniestrosBackofficeService:
    return request.app.state.siniestros_backoffice


SiniestrosBackofficeDep = Annotated[SiniestrosBackofficeService, Depends(get_siniestros_backoffice)]


def claims_consola_actuales(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> ClaimsConsola:
    """Sesión del personal. Un token de cliente (o de otro tipo) recibe 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NoAutorizado("falta el encabezado Authorization")
    return request.app.state.consola_sessions.verificar(authorization.split(" ", 1)[1])


ClaimsConsolaDep = Annotated[ClaimsConsola, Depends(claims_consola_actuales)]


def exigir_rol(rol: RolConsola):
    """Dependencia que exige una sesión del personal con ese rol: 401 sin sesión, 403 con otro."""

    def _exigir(claims: ClaimsConsolaDep) -> ClaimsConsola:
        if claims.rol is not rol:
            raise Prohibido("Tu rol no permite esta operación", codigo="ROL_NO_PERMITIDO")
        return claims

    return _exigir


requiere_admin = exigir_rol(RolConsola.ADMIN)
requiere_socio = exigir_rol(RolConsola.SOCIO)

# --- Ingreso (públicas) ---

sesion_router = APIRouter(prefix="/v1/backoffice", tags=["Ingreso del personal"])


@sesion_router.post("/sesiones", response_model=SesionConsolaOut)
async def iniciar_sesion(payload: CredencialesRequest, servicio: ConsolaDep) -> SesionConsolaOut:
    logger.info("POST /v1/backoffice/sesiones: solicitud recibida")
    sesion = await servicio.iniciar_sesion(payload.email, payload.password)
    return SesionConsolaOut.model_validate(sesion)


@sesion_router.post("/sesiones/refresco", response_model=SesionConsolaOut)
async def refrescar_sesion(payload: RefrescoRequest, servicio: ConsolaDep) -> SesionConsolaOut:
    logger.info("POST /v1/backoffice/sesiones/refresco: solicitud recibida")
    return SesionConsolaOut.model_validate(servicio.refrescar(payload.refresh_token))


# --- Solo ADMIN ---

backoffice_router = APIRouter(
    prefix="/v1/backoffice",
    tags=["Backoffice"],
    dependencies=[Depends(requiere_admin)],
)


@backoffice_router.post("/usuarios", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
async def crear_usuario(
    payload: CrearUsuarioRequest,
    servicio: ConsolaDep,
    actor: Annotated[ClaimsConsola, Depends(requiere_admin)],
) -> UsuarioOut:
    # Ni la contraseña ni el correo nuevo se escriben en el log.
    logger.info("POST /v1/backoffice/usuarios: solicitud recibida rol=%s", payload.rol)
    usuario = await servicio.crear_usuario(
        actor, payload.correo, payload.contrasena, RolConsola(payload.rol), payload.nombre
    )
    return UsuarioOut.model_validate(usuario)


@backoffice_router.get(
    "/siniestros", response_model=list[SiniestroInternoResumenOut], tags=["Backoffice"]
)
async def listar_bandeja_de_siniestros(
    servicio: SiniestrosBackofficeDep,
    estado: Annotated[EstadoSiniestro | None, Query()] = None,
    automatico: Annotated[bool | None, Query()] = None,
) -> list[SiniestroInternoResumenOut]:
    logger.info("GET /v1/backoffice/siniestros: solicitud recibida")
    siniestros = await servicio.bandeja(estado, automatico)
    return [SiniestroInternoResumenOut.model_validate(s) for s in siniestros]


@backoffice_router.get(
    "/siniestros/{siniestro_id}", response_model=SiniestroInternoOut, tags=["Backoffice"]
)
async def obtener_siniestro_de_la_bandeja(
    siniestro_id: str, servicio: SiniestrosBackofficeDep
) -> SiniestroInternoOut:
    logger.info(
        "GET /v1/backoffice/siniestros/%s: solicitud recibida", sanear_para_log(siniestro_id)
    )
    return SiniestroInternoOut.model_validate(await servicio.detalle(siniestro_id))


@backoffice_router.get(
    "/siniestros/{siniestro_id}/evidencias/{evidencia_id}",
    tags=["Backoffice"],
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}},
)
async def obtener_archivo_de_la_bandeja(
    siniestro_id: str, evidencia_id: str, servicio: SiniestrosBackofficeDep
) -> Response:
    logger.info(
        "GET /v1/backoffice/siniestros/%s/evidencias: solicitud recibida",
        sanear_para_log(siniestro_id),
    )
    contenido, tipo = await servicio.archivo(siniestro_id, evidencia_id)
    return Response(
        content=contenido,
        media_type=tipo,
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
    )


@backoffice_router.post(
    "/siniestros/{siniestro_id}/decisiones",
    response_model=SiniestroInternoOut,
    tags=["Backoffice"],
)
async def decidir_siniestro(
    siniestro_id: str,
    payload: DecidirSiniestroRequest,
    servicio: SiniestrosBackofficeDep,
    actor: Annotated[ClaimsConsola, Depends(requiere_admin)],
) -> SiniestroInternoOut:
    # El agente es el correo de la sesión y no se escribe en el log.
    logger.info(
        "POST /v1/backoffice/siniestros/%s/decisiones: solicitud recibida decision=%s",
        sanear_para_log(siniestro_id),
        payload.decision,
    )
    siniestro = await servicio.decidir(
        actor.correo, siniestro_id, payload.model_dump(by_alias=True, exclude_none=True)
    )
    return SiniestroInternoOut.model_validate(siniestro)


# --- Solo SOCIO ---

# Todavía sin rutas (las ventas del socio llegan con BITS-378): el router ya exige el rol, así que
# cada ruta que se le agregue queda protegida.
socio_router = APIRouter(
    prefix="/v1/socio",
    tags=["Socio"],
    dependencies=[Depends(requiere_socio)],
)
