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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api.schemas import (
    ClienteSocioOut,
    CrearClienteSocioRequest,
    CrearUsuarioRequest,
    CredencialesRequest,
    DecidirSiniestroRequest,
    EntidadFinancieraOut,
    EstadoSiniestro,
    PrecioVentaOut,
    PrecioVentaRequest,
    RefrescoRequest,
    SesionConsolaOut,
    SiniestroInternoOut,
    SiniestroInternoResumenOut,
    UsuarioOut,
    VentaOut,
    VentaRequest,
)
from app.consola import ConsolaService
from app.domain import ClaimsConsola, NoAutorizado, Prohibido, RolConsola, SolicitudInvalida
from app.logging_utils import sanear_para_log
from app.services import SiniestrosBackofficeService, VentasSocioService

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


def get_ventas_socio(request: Request) -> VentasSocioService:
    return request.app.state.ventas_socio


VentasSocioDep = Annotated[VentasSocioService, Depends(get_ventas_socio)]


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

# La regla está en el router: cada ruta que se le agregue queda protegida. El `socioId` de una venta
# es siempre el `uid` de la sesión.
socio_router = APIRouter(
    prefix="/v1/socio",
    tags=["Socio"],
    dependencies=[Depends(requiere_socio)],
)


@socio_router.get("/entidades-financieras", response_model=list[EntidadFinancieraOut])
async def listar_entidades_del_socio(
    servicio: VentasSocioDep, mercado: Annotated[Literal["CO"], Query()] = "CO"
) -> list[EntidadFinancieraOut]:
    logger.info("GET /v1/socio/entidades-financieras: solicitud recibida mercado=%s", mercado)
    entidades = await servicio.entidades(mercado)
    return [EntidadFinancieraOut(id=e.id, nombre=e.nombre) for e in entidades]


@socio_router.get("/clientes", response_model=ClienteSocioOut)
async def buscar_cliente_del_socio(
    servicio: VentasSocioDep,
    correo: Annotated[str | None, Query(max_length=254)] = None,
    tipo_documento: Annotated[
        Literal["CC", "CE", "PA"] | None, Query(alias="tipoDocumento")
    ] = None,
    numero_documento: Annotated[
        str | None, Query(alias="numeroDocumento", min_length=4, max_length=20)
    ] = None,
) -> ClienteSocioOut:
    # Ni el correo ni el documento buscados se escriben en el log.
    logger.info("GET /v1/socio/clientes: solicitud recibida")
    cliente = await servicio.buscar_cliente(
        correo=correo, tipo_documento=tipo_documento, numero_documento=numero_documento
    )
    return ClienteSocioOut.model_validate(cliente)


@socio_router.post("/clientes", response_model=ClienteSocioOut, status_code=status.HTTP_201_CREATED)
async def crear_cliente_del_socio(
    payload: CrearClienteSocioRequest,
    servicio: VentasSocioDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=64)] = None,
) -> ClienteSocioOut:
    logger.info("POST /v1/socio/clientes: solicitud recibida")
    cliente = await servicio.crear_cliente(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True), idempotency_key
    )
    return ClienteSocioOut.model_validate(cliente)


@socio_router.post("/ventas/precio", response_model=PrecioVentaOut)
async def calcular_precio_de_venta(
    payload: PrecioVentaRequest, servicio: VentasSocioDep
) -> PrecioVentaOut:
    logger.info("POST /v1/socio/ventas/precio: solicitud recibida")
    precio = await servicio.calcular_precio(payload.model_dump(mode="json", by_alias=True))
    return PrecioVentaOut.model_validate(precio)


@socio_router.post("/ventas", response_model=VentaOut, status_code=status.HTTP_201_CREATED)
async def vender_poliza(
    payload: VentaRequest,
    servicio: VentasSocioDep,
    socio: Annotated[ClaimsConsola, Depends(requiere_socio)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=64)] = None,
) -> VentaOut:
    if not idempotency_key or not idempotency_key.strip():
        # Emitir cobra a nombre de alguien: sin clave un doble clic emitiría dos pólizas.
        raise SolicitudInvalida(
            "Falta el encabezado Idempotency-Key",
            errores=[{"campo": "Idempotency-Key", "mensaje": "Es obligatorio para vender"}],
        )
    logger.info("POST /v1/socio/ventas: solicitud recibida socio_id=%s", sanear_para_log(socio.uid))
    poliza = await servicio.vender(
        socio.uid, payload.model_dump(mode="json", by_alias=True), idempotency_key
    )
    return VentaOut.model_validate(poliza)
