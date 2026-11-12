"""Adaptador HTTP hacia svc-core (``ICustomerIdentity``)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote

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
    RolConsola,
    SolicitudInvalida,
    UsuarioInterno,
)
from app.logging_utils import sanear_para_log
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_web.adapters.core")

# Rutas de svc-core: todo vive bajo /v1 desde BITS-275.
CLIENTES = "/v1/clientes"
USUARIOS_INTERNOS = "/v1/usuarios-internos"
SINIESTROS = "/v1/siniestros"
POLIZAS = "/v1/polizas"
EVIDENCIAS = "/v1/evidencias-identidad"


class CoreClientAdapter:
    def __init__(
        self,
        http: ResilientHttpClient,
        *,
        timeout_subida: httpx.Timeout | None = None,
        timeout_polizas: httpx.Timeout | None = None,
        timeout_cuentas: httpx.Timeout | None = None,
    ) -> None:
        self._http = http
        # Subir una foto incluye el análisis de svc-core: no cabe en los 700 ms de las
        # demás llamadas.
        self._timeout_subida = timeout_subida or httpx.Timeout(5.0, pool=0.1)
        # Solicitar o leer una póliza pendiente puede emitirla (evaluación, base y evento).
        self._timeout_polizas = timeout_polizas or httpx.Timeout(3.0, pool=0.1)
        # Crear una cuenta incluye la llamada de svc-core a Identity Platform (hasta ~3 s).
        self._timeout_cuentas = timeout_cuentas or httpx.Timeout(4.0, pool=0.1)

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

    async def crear_usuario_interno(
        self, correo: str, contrasena: str, rol: RolConsola, nombre: str | None
    ) -> UsuarioInterno:
        cuerpo: dict = {"correo": correo, "contrasena": contrasena, "rol": str(rol)}
        if nombre:
            cuerpo["nombre"] = nombre
        # Sin reintentos: crear no es idempotente y el segundo intento daría un 409 engañoso.
        resp = await self._http.request(
            "POST", USUARIOS_INTERNOS, json=cuerpo, reintentos=0, timeout=self._timeout_cuentas
        )
        if resp.status_code == 201:
            datos = resp.json()
            logger.info("usuario interno creado uid=%s rol=%s", datos["uid"], datos["rol"])
            return UsuarioInterno(
                uid=datos["uid"], correo=datos["correo"], rol=RolConsola(datos["rol"])
            )
        cuerpo_error = _cuerpo_de_error(resp)
        if resp.status_code == 409:
            raise Conflicto.por_campo("correo")
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(
                cuerpo_error.get("detail") or None, errores=cuerpo_error.get("errores")
            )
        raise BffError(f"svc-core respondió {resp.status_code}")

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

    # --- siniestros (BITS-383, BITS-384) ---

    async def listar_siniestros(self, cliente_id: str) -> list[dict]:
        resp = await self._http.request("GET", f"{CLIENTES}/{cliente_id}/siniestros")
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def obtener_siniestro(self, cliente_id: str, siniestro_id: str) -> dict:
        resp = await self._http.request(
            "GET", f"{CLIENTES}/{cliente_id}/siniestros/{quote(siniestro_id, safe='')}"
        )
        _asegurar_poliza_ok(resp)
        return resp.json()

    # --- bandeja del ADMIN (BITS-377) ---

    async def bandeja_siniestros(self, estado: str | None, automatico: bool | None) -> list[dict]:
        params: dict[str, str] = {}
        if estado is not None:
            params["estado"] = estado
        if automatico is not None:
            params["automatico"] = "true" if automatico else "false"
        resp = await self._http.request("GET", SINIESTROS, params=params or None)
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def detalle_siniestro_interno(self, siniestro_id: str) -> dict:
        resp = await self._http.request("GET", f"{SINIESTROS}/{quote(siniestro_id, safe='')}")
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def archivo_de_siniestro(self, siniestro_id: str, archivo_id: str) -> tuple[bytes, str]:
        resp = await self._http.request(
            "GET",
            f"{SINIESTROS}/{quote(siniestro_id, safe='')}/evidencias/{quote(archivo_id, safe='')}",
            timeout=self._timeout_subida,
        )
        _asegurar_poliza_ok(resp)
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    async def decidir_siniestro(self, siniestro_id: str, cuerpo: dict) -> dict:
        # Sin reintentos: una decisión no se repite a ciegas. Se espera lo que tarden la base y el
        # aviso al cliente.
        resp = await self._http.request(
            "POST",
            f"{SINIESTROS}/{quote(siniestro_id, safe='')}/decisiones",
            json=cuerpo,
            reintentos=0,
            timeout=self._timeout_polizas,
        )
        _asegurar_poliza_ok(resp)
        # Ni el agente (su correo) ni el valor se escriben en el log.
        logger.info("decisión sobre siniestro enviada decision=%s", cuerpo.get("decision"))
        return resp.json()

    # --- solicitudes en revisión del ADMIN (BITS-376) ---

    async def listar_solicitudes(self, limite: int, cursor: str | None) -> dict:
        params = {
            "estado": "PENDIENTE",
            "tipoPendiente": "AGENTE",
            "orden": "creadaEn",
            "limite": str(limite),
        }
        if cursor:
            params["cursor"] = cursor
        resp = await self._http.request("GET", POLIZAS, params=params)
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def obtener_solicitud(self, poliza_id: str) -> dict:
        resp = await self._http.request(
            "GET", f"{POLIZAS}/{quote(poliza_id, safe='')}", timeout=self._timeout_polizas
        )
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def obtener_cliente_de_solicitud(self, cliente_id: str) -> dict:
        resp = await self._http.request("GET", f"{CLIENTES}/{quote(cliente_id, safe='')}")
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def imagen_de_evidencia(self, evidencia_id: str) -> tuple[bytes, str]:
        resp = await self._http.request(
            "GET",
            f"{EVIDENCIAS}/{quote(evidencia_id, safe='')}/imagen",
            timeout=self._timeout_subida,
        )
        _asegurar_poliza_ok(resp)
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    async def decidir_solicitud(self, poliza_id: str, cuerpo: dict) -> dict:
        # Sin reintentos: aprobar el último pendiente emite la póliza (evaluación, base y evento).
        resp = await self._http.request(
            "POST",
            f"{POLIZAS}/{quote(poliza_id, safe='')}/decisiones",
            json=cuerpo,
            reintentos=0,
            timeout=self._timeout_polizas,
        )
        _asegurar_poliza_ok(resp)
        # El agente (su correo) no se escribe en el log.
        logger.info("decisión sobre solicitud enviada decision=%s", cuerpo.get("decision"))
        return resp.json()

    # --- venta directa del socio (BITS-378) ---

    async def buscar_cliente(
        self, *, correo: str | None, tipo_documento: str | None, numero_documento: str | None
    ) -> dict:
        params = {
            nombre: valor
            for nombre, valor in (
                ("correo", correo),
                ("tipoDocumento", tipo_documento),
                ("numeroDocumento", numero_documento),
            )
            if valor is not None
        }
        resp = await self._http.request("GET", CLIENTES, params=params)
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def crear_cliente_asistido(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        # Sin reintentos: crear la cuenta no se repite a ciegas. Se espera a Identity Platform.
        resp = await self._http.request(
            "POST",
            f"{CLIENTES}/asistidos",
            json=cuerpo,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
            reintentos=0,
            timeout=self._timeout_cuentas,
        )
        if resp.status_code == 409:
            campo = _campo_en_conflicto(resp)
            logger.info("alta asistida en conflicto campo=%s", campo)
            raise Conflicto.por_campo(campo)
        _asegurar_poliza_ok(resp)
        # Ni el correo ni el documento se escriben en el log.
        logger.info("cliente asistido creado")
        return resp.json()

    async def calcular_precio(self, cuerpo: dict) -> dict:
        resp = await self._http.request(
            "POST", "/v1/polizas/precio", json=cuerpo, timeout=self._timeout_polizas
        )
        _asegurar_poliza_ok(resp)
        return resp.json()

    async def vender(self, cuerpo: dict, idempotency_key: str | None) -> dict:
        # Sin reintentos: emitir no se repite a ciegas. El reintento lo hace quien llama, con la
        # misma Idempotency-Key.
        resp = await self._http.request(
            "POST",
            "/v1/polizas",
            json=cuerpo,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
            reintentos=0,
            timeout=self._timeout_cuentas,
        )
        _asegurar_poliza_ok(resp)
        logger.info("póliza vendida por un socio")
        return resp.json()

    async def listar_polizas_del_socio(
        self, socio_id: str, limite: int, cursor: str | None
    ) -> dict:
        params = {"socioId": socio_id, "orden": "-creadaEn", "limite": str(limite)}
        if cursor:
            params["cursor"] = cursor
        resp = await self._http.request("GET", POLIZAS, params=params)
        _asegurar_poliza_ok(resp)
        return resp.json()

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


def _cuerpo_de_error(resp: httpx.Response) -> dict:
    try:
        cuerpo = resp.json()
    except ValueError:
        return {}
    return cuerpo if isinstance(cuerpo, dict) else {}


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
