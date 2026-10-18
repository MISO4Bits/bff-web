"""Adaptador HTTP hacia svc-cotizacion (``CotizacionPort``).

POST /v1/cotizaciones        crea la cotización y la deja como la activa del cliente
GET  /v1/cotizaciones/actual la cotización activa del cliente
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

import httpx

from app.domain import (
    BffError,
    CoberturaPrecio,
    Cotizacion,
    CotizacionInput,
    DatosCreditoInput,
    Desglose,
    FactorRiesgo,
    Oferta,
    PerfilRiesgo,
    RecursoNoEncontrado,
    SolicitudInvalida,
    Vigencia,
)
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_web.adapters.cotizacion")


class CotizacionClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def crear_cotizacion(
        self, cliente_id: str, edad: int, entrada: CotizacionInput
    ) -> Cotizacion:
        dc = entrada.datos_credito
        cuerpo = {
            "edad": edad,
            "datosCredito": {
                "valorCredito": str(dc.valor_credito),
                "plazoRestanteMeses": dc.plazo_restante_meses,
                "entidadAcreedora": dc.entidad_acreedora,
                "saldoInsoluto": str(dc.saldo_insoluto),
            },
        }
        resp = await self._http.request(
            "POST", "/v1/cotizaciones", json=cuerpo, headers={"X-Cliente-Id": cliente_id}
        )
        if resp.status_code in (400, 422):
            detalle, errores = _problema(resp)
            logger.info("solicitud inválida (%s)", detalle)
            raise SolicitudInvalida(detalle, errores=errores)
        _asegurar_ok(resp, esperado=201)
        cotizacion = _a_cotizacion(resp.json())
        logger.info("cotización activa creada estado=%s", cotizacion.estado)
        return cotizacion

    async def obtener_cotizacion_activa(self, cliente_id: str) -> Cotizacion:
        resp = await self._http.request(
            "GET", "/v1/cotizaciones/actual", headers={"X-Cliente-Id": cliente_id}
        )
        if resp.status_code == 404:
            logger.info("el cliente no tiene cotización activa")
            raise RecursoNoEncontrado("El cliente no tiene una cotización activa")
        _asegurar_ok(resp)
        return _a_cotizacion(resp.json())


def _problema(resp: httpx.Response) -> tuple[str, list[dict[str, str]] | None]:
    """Detalle y errores por campo del Problem Details de svc-cotizacion."""
    try:
        cuerpo = resp.json()
    except ValueError:  # pragma: no cover
        return "", None
    return cuerpo.get("detail", ""), cuerpo.get("errores") or None


def _asegurar_ok(resp: httpx.Response, *, esperado: int = 200) -> None:
    if resp.status_code != esperado and resp.status_code >= 400:
        raise BffError(f"svc-cotizacion respondió {resp.status_code}")


def _a_cotizacion(data: dict) -> Cotizacion:
    oferta = data["oferta"]
    perfil = data.get("perfilRiesgo")
    vigencia = data["vigenciaCotizacion"]
    credito = data["datosCredito"]
    return Cotizacion(
        estado=data["estado"],
        producto=data["producto"],
        datos_credito=DatosCreditoInput(
            valor_credito=Decimal(str(credito["valorCredito"])),
            plazo_restante_meses=credito["plazoRestanteMeses"],
            entidad_acreedora=credito["entidadAcreedora"],
            saldo_insoluto=Decimal(str(credito["saldoInsoluto"])),
        ),
        origen_datos=data["origenDatos"],
        oferta=Oferta(
            prima_mensual=float(Decimal(str(oferta["primaMensual"]))),
            prima_base_mensual=float(Decimal(str(oferta["primaBaseMensual"]))),
            suma_asegurada=float(Decimal(str(oferta["sumaAsegurada"]))),
            cobertura_meses=oferta["coberturaMeses"],
            moneda=oferta["moneda"],
            personalizado=oferta["personalizado"],
            desglose=Desglose(
                coberturas=tuple(
                    CoberturaPrecio(
                        codigo=c["codigo"],
                        obligatoria=c["obligatoria"],
                        suma_asegurada=float(Decimal(str(c["sumaAsegurada"]))),
                        prima_neta=float(Decimal(str(c["primaNeta"]))),
                        iva=float(Decimal(str(c["iva"]))),
                        prima_total=float(Decimal(str(c["primaTotal"]))),
                    )
                    for c in oferta["desglose"]["coberturas"]
                )
            ),
            fuentes_no_disponibles=tuple(oferta.get("fuentesNoDisponibles", [])),
        ),
        perfil_riesgo=(
            PerfilRiesgo(
                nivel_riesgo=perfil["nivelRiesgo"],
                factores=tuple(
                    FactorRiesgo(
                        descripcion=f["descripcion"],
                        efecto=f["efecto"],
                        peso_relativo=f.get("pesoRelativo"),
                    )
                    for f in perfil["factores"]
                ),
            )
            if perfil is not None
            else None
        ),
        vigencia_cotizacion=Vigencia(
            desde=_parse_fecha(vigencia["desde"]), hasta=_parse_fecha(vigencia["hasta"])
        ),
        creada_en=_parse_fecha(data["creadaEn"]),
    )


def _parse_fecha(valor: str) -> datetime:
    return datetime.fromisoformat(valor.replace("Z", "+00:00"))
