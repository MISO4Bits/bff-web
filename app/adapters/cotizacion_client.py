"""Adaptador HTTP hacia svc-cotizacion (``CotizacionPort``)."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

import httpx

from app.domain import (
    BffError,
    Cotizacion,
    CotizacionInput,
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

    async def crear_cotizacion(self, cliente_id: str, entrada: CotizacionInput) -> Cotizacion:
        dc = entrada.datos_credito
        q = entrada.cuestionario_habitos
        cuerpo = {
            "datosCredito": {
                "valorCredito": str(dc.valor_credito),
                "plazoMeses": dc.plazo_meses,
                "edad": dc.edad,
                "entidadAcreedora": dc.entidad_acreedora,
                "saldoInsoluto": str(dc.saldo_insoluto),
            },
            "cuestionarioHabitos": {
                "consumeTabaco": q.consume_tabaco,
                "actividadFisica": q.actividad_fisica,
                "condicionesPreexistentes": q.condiciones_preexistentes,
                "dependientesEconomicos": q.dependientes_economicos,
            },
        }
        resp = await self._http.request(
            "POST", "/cotizaciones", json=cuerpo, headers={"X-Cliente-Id": cliente_id}
        )
        if resp.status_code in (400, 422):
            logger.info("svc-cotizacion: solicitud inválida (%s)", _detalle(resp))
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp, esperado=201)
        cotizacion = _a_cotizacion(resp.json())
        logger.info(
            "svc-cotizacion: cotización creada id=%s estado=%s", cotizacion.id, cotizacion.estado
        )
        return cotizacion

    async def obtener_cotizacion(self, cliente_id: str, cotizacion_id: str) -> Cotizacion:
        resp = await self._http.request(
            "GET", f"/cotizaciones/{cotizacion_id}", headers={"X-Cliente-Id": cliente_id}
        )
        if resp.status_code == 404:
            logger.info("svc-cotizacion: cotización no encontrada id=%s", cotizacion_id)
            raise RecursoNoEncontrado("Cotización no encontrada")
        _asegurar_ok(resp)
        return _a_cotizacion(resp.json())


def _detalle(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", "")
    except ValueError:  # pragma: no cover
        return ""


def _asegurar_ok(resp: httpx.Response, *, esperado: int = 200) -> None:
    if resp.status_code != esperado and resp.status_code >= 400:
        raise BffError(f"svc-cotizacion respondió {resp.status_code}")


def _a_cotizacion(data: dict) -> Cotizacion:
    oferta = data["oferta"]
    perfil = data.get("perfilRiesgo")
    vigencia = data["vigenciaCotizacion"]
    return Cotizacion(
        id=data["id"],
        estado=data["estado"],
        producto=data["producto"],
        oferta=Oferta(
            prima_mensual=float(Decimal(str(oferta["primaMensual"]))),
            prima_base_mensual=float(Decimal(str(oferta["primaBaseMensual"]))),
            suma_asegurada=float(Decimal(str(oferta["sumaAsegurada"]))),
            cobertura_meses=oferta["coberturaMeses"],
            moneda=oferta["moneda"],
            personalizado=oferta["personalizado"],
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
