"""Adaptador HTTP hacia svc-core (``ICustomerIdentity``)."""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from app.domain import (
    BffError,
    ClienteCore,
    Conflicto,
    ConsentimientoVista,
    DependenciaNoDisponible,
    EstadoEvidenciaTipo,
    ImagenMuyGrande,
    Prohibido,
    RecursoNoEncontrado,
    RegistroInput,
    ReglaNegocio,
    ResultadoEvidencia,
    SolicitudInvalida,
)
from app.logging_utils import sanear_para_log
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_web.adapters.core")

# Rutas de svc-core: todo vive bajo /v1 desde BITS-275.
CLIENTES = "/v1/clientes"


class CoreClientAdapter:
    def __init__(
        self,
        http: ResilientHttpClient,
        *,
        timeout_subida: httpx.Timeout | None = None,
        timeout_polizas: httpx.Timeout | None = None,
    ) -> None:
        self._http = http
        # Subir una foto incluye el análisis de svc-core: no cabe en los 700 ms de las
        # demás llamadas.
        self._timeout_subida = timeout_subida or httpx.Timeout(5.0, pool=0.1)
        # Solicitar o leer una póliza pendiente puede emitirla (evaluación, base y evento).
        self._timeout_polizas = timeout_polizas or httpx.Timeout(3.0, pool=0.1)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def registrar_cliente(
        self, identity_ref: str, datos: RegistroInput, idempotency_key: str | None = None
    ) -> ClienteCore:
        cuerpo = {
            "identityRef": identity_ref,
            "tipoDocumento": datos.tipo_documento,
            "numeroDocumento": datos.numero_documento,
            "primerNombre": datos.primer_nombre,
            "primerApellido": datos.primer_apellido,
            "fechaNacimiento": datos.fecha_nacimiento.isoformat(),
            "email": datos.email,
            "telefono": datos.telefono,
            # El BFF web es el único canal hoy; igual que en otorgar_consentimiento,
            # se fija "WEB" aquí en vez de pedírselo al cliente final.
            "canal": "WEB",
            "autorizaTratamientoDatos": datos.autoriza_tratamiento_datos,
            "autorizaDatosFinancieros": datos.autoriza_datos_financieros,
        }
        if datos.segundo_nombre:
            cuerpo["segundoNombre"] = datos.segundo_nombre
        if datos.segundo_apellido:
            cuerpo["segundoApellido"] = datos.segundo_apellido
        if datos.politica_version_tratamiento_datos:
            cuerpo["politicaVersionTratamientoDatos"] = datos.politica_version_tratamiento_datos
        if datos.politica_version_datos_financieros:
            cuerpo["politicaVersionDatosFinancieros"] = datos.politica_version_datos_financieros

        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        resp = await self._http.request("POST", CLIENTES, json=cuerpo, headers=headers)
        if resp.status_code == 409:
            campo = _campo_en_conflicto(resp)
            logger.info("registro en conflicto campo=%s", campo)
            raise Conflicto.por_campo(campo)
        if resp.status_code in (400, 422):
            logger.info("solicitud inválida (%s)", _detalle(resp))
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp, esperado=201)
        cliente = _a_cliente(resp.json())
        logger.info("cliente creado cliente_id=%s", cliente.id)
        return cliente

    async def obtener_cliente(self, cliente_id: str) -> ClienteCore:
        resp = await self._http.request("GET", f"{CLIENTES}/{cliente_id}")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return _a_cliente(resp.json())

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        resp = await self._http.request("GET", CLIENTES, params={"identityRef": identity_ref})
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return _a_cliente(resp.json())

    async def listar_consentimientos(self, cliente_id: str) -> list[ConsentimientoVista]:
        resp = await self._http.request("GET", f"{CLIENTES}/{cliente_id}/consentimientos")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return [_a_consentimiento(item) for item in resp.json()]

    async def otorgar_consentimiento(
        self, cliente_id: str, scope: str, politica_version: str, canal: str
    ) -> ConsentimientoVista:
        resp = await self._http.request(
            "POST",
            f"{CLIENTES}/{cliente_id}/consentimientos",
            json={"scope": scope, "politicaVersion": politica_version, "canal": canal},
        )
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp, esperado=201)
        logger.info(
            "consentimiento otorgado cliente_id=%s scope=%s",
            cliente_id,
            sanear_para_log(scope),
        )
        return _a_consentimiento(resp.json())

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        resp = await self._http.request(
            "DELETE", f"{CLIENTES}/{cliente_id}/consentimientos/{scope}"
        )
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Consentimiento no encontrado")
        _asegurar_ok(resp, esperado=204)
        logger.info(
            "consentimiento revocado cliente_id=%s scope=%s",
            cliente_id,
            sanear_para_log(scope),
        )

    async def existe_cliente(
        self, email: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict:
        params = {}
        if email is not None:
            params["correo"] = email
        if tipo_documento is not None and numero_documento is not None:
            params["tipoDocumento"] = tipo_documento
            params["numeroDocumento"] = numero_documento
        resp = await self._http.request("GET", f"{CLIENTES}/disponibilidad", params=params)
        _asegurar_ok(resp)
        return resp.json()

    async def confirmar_cliente(self, cliente_id: str) -> ClienteCore:
        resp = await self._http.request("POST", f"{CLIENTES}/{cliente_id}/confirmacion")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        logger.info("cliente confirmado cliente_id=%s", cliente_id)
        return _a_cliente(resp.json())

    async def subir(
        self, cliente_id: str, tipo: str, imagen: bytes, nombre: str, content_type: str
    ) -> ResultadoEvidencia:
        resp = await self._http.request(
            "POST",
            f"{CLIENTES}/{cliente_id}/evidencias-identidad",
            data={"tipo": tipo},
            files={"imagen": (nombre, imagen, content_type)},
            timeout=self._timeout_subida,
            reintentos=0,  # cada envío cuenta como un intento en svc-core
        )
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        if resp.status_code == 413:
            raise ImagenMuyGrande()
        if resp.status_code == 422:
            # La selfie exige ambas caras de la cédula vigentes: es una regla de negocio, no un
            # campo mal formado.
            raise ReglaNegocio(_detalle(resp))
        if resp.status_code == 400:
            raise SolicitudInvalida(_detalle(resp))
        if resp.status_code not in (200, 201):
            raise BffError(f"svc-core respondió {resp.status_code}")
        datos = resp.json()
        logger.info(
            "evidencia procesada cliente_id=%s tipo=%s estado=%s",
            cliente_id,
            sanear_para_log(tipo),
            datos["estado"],
        )
        return ResultadoEvidencia(
            tipo=datos["tipo"],
            estado=datos["estado"],
            intentos_restantes=datos["intentosRestantes"],
            evidencia_id=datos.get("evidenciaId"),
            motivo=datos.get("motivo"),
        )

    async def estado(self, cliente_id: str) -> list[EstadoEvidenciaTipo]:
        resp = await self._http.request("GET", f"{CLIENTES}/{cliente_id}/evidencias-identidad")
        if resp.status_code == 404:
            raise RecursoNoEncontrado("Cliente no encontrado")
        _asegurar_ok(resp)
        return [
            EstadoEvidenciaTipo(
                tipo=item["tipo"],
                estado=item["estado"],
                intentos_restantes=item["intentosRestantes"],
                evidencia_id=item.get("evidenciaId"),
                creada_en=_fecha(item.get("creadaEn")),
            )
            for item in resp.json()
        ]

    # --- pólizas (BITS-101) ---

    async def solicitar(self, cliente_id: str, cuerpo: dict) -> tuple[dict, bool]:
        resp = await self._http.request(
            "POST",
            f"{CLIENTES}/{cliente_id}/polizas",
            json=cuerpo,
            timeout=self._timeout_polizas,
            reintentos=0,  # emitir no se repite a ciegas
        )
        _asegurar_poliza_ok(resp)
        creada = resp.status_code == 201
        logger.info("póliza solicitada cliente_id=%s creada=%s", cliente_id, creada)
        return resp.json(), creada

    async def listar(self, cliente_id: str, estado: str | None) -> list[dict]:
        resp = await self._http.request(
            "GET",
            f"{CLIENTES}/{cliente_id}/polizas",
            params={"estado": estado} if estado else None,
        )
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def obtener(self, cliente_id: str, poliza_id: str) -> dict:
        resp = await self._http.request(
            "GET", f"{CLIENTES}/{cliente_id}/polizas/{poliza_id}", timeout=self._timeout_polizas
        )
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def documento(self, cliente_id: str, poliza_id: str) -> tuple[dict, bool]:
        resp = await self._http.request(
            "GET",
            f"{CLIENTES}/{cliente_id}/polizas/{poliza_id}/documento",
            timeout=self._timeout_polizas,
        )
        _asegurar_poliza_ok(resp)
        return resp.json(), resp.status_code == 200


_ERRORES_DE_POLIZA = {
    400: SolicitudInvalida,
    403: Prohibido,
    404: RecursoNoEncontrado,
    409: Conflicto,
    422: ReglaNegocio,
    503: DependenciaNoDisponible,
}


def _asegurar_poliza_ok(resp: httpx.Response) -> None:
    """Traduce el error de svc-core conservando su ``codigo`` y el detalle por campo."""
    if resp.status_code < 400:
        return
    try:
        cuerpo = resp.json()
    except ValueError:
        cuerpo = {}
    clase = _ERRORES_DE_POLIZA.get(resp.status_code)
    if clase is None:
        raise BffError(f"svc-core respondió {resp.status_code}")
    raise clase(
        cuerpo.get("detail") or None, errores=cuerpo.get("errores"), codigo=cuerpo.get("codigo")
    )


def _fecha(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


def _detalle(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", "")
    except ValueError:  # pragma: no cover
        return ""


def _campo_en_conflicto(resp: httpx.Response) -> str:
    """Campo que CoreTransaccional reporta como repetido; ``documento`` si no lo indica."""
    try:
        campo = resp.json()["errores"][0]["campo"]
    except (ValueError, KeyError, IndexError, TypeError):
        return "documento"
    return campo if campo in ("correo", "documento") else "documento"


def _asegurar_ok(resp: httpx.Response, *, esperado: int = 200) -> None:
    if resp.status_code != esperado and resp.status_code >= 400:
        raise BffError(f"svc-core respondió {resp.status_code}")


def _a_cliente(data: dict) -> ClienteCore:
    return ClienteCore(
        id=data["id"],
        primer_nombre=data["primerNombre"],
        primer_apellido=data["primerApellido"],
        email=data["email"],
        estado=data["estado"],
        correo_confirmado=data.get("correoConfirmado", False),
        segundo_nombre=data.get("segundoNombre"),
        segundo_apellido=data.get("segundoApellido"),
        telefono=data["telefono"],
        fecha_nacimiento=date.fromisoformat(data["fechaNacimiento"]),
    )


def _a_consentimiento(data: dict) -> ConsentimientoVista:
    estado = data["estado"]
    return ConsentimientoVista(
        scope=data["scope"],
        estado=estado,
        vigente=data.get("vigente", estado == "OTORGADO"),
        actualizado_en=None,
    )
