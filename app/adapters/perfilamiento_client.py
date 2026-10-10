"""Adaptador HTTP hacia svc-perfilamiento (``CreditosHipotecariosPort``).

Perfilamiento ya trajo los datos de Open Finance al otorgarse el consentimiento y los
guarda 24 horas: esta llamada lee lo guardado, no consulta a la fuente.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote

from app.domain import (
    BffError,
    CreditosReportados,
    HipotecaReportada,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_web.adapters.perfilamiento")


class PerfilamientoClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def obtener(self, cliente_id: str) -> CreditosReportados:
        resp = await self._http.request(
            "GET", f"/clientes/{quote(cliente_id, safe='')}/creditos-hipotecarios"
        )
        if resp.status_code in (400, 422):
            raise SolicitudInvalida("Solicitud de créditos inválida")
        if resp.status_code >= 400:
            raise BffError(f"svc-perfilamiento respondió {resp.status_code}")
        data = resp.json()
        fecha = data.get("fechaConsulta")
        return CreditosReportados(
            estado=data["estado"],
            hipotecas=tuple(
                HipotecaReportada(
                    entidad_acreedora=h["entidadAcreedora"],
                    valor_credito=Decimal(str(h["valorCredito"])),
                    saldo_insoluto=Decimal(str(h["saldoInsoluto"])),
                    plazo_restante_meses=int(h["plazoRestanteMeses"]),
                    cuota_mensual=Decimal(str(h["cuotaMensual"])),
                )
                for h in data.get("hipotecas", [])
            ),
            fecha_consulta=datetime.fromisoformat(fecha) if fecha else None,
        )
