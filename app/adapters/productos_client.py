"""Adaptador HTTP hacia svc-productos (``DocumentosLegalesPort``)."""

from __future__ import annotations

import logging

import httpx

from app.domain import (
    BffError,
    DocumentoLegal,
    EntidadFinanciera,
    RecursoNoEncontrado,
    SolicitudInvalida,
)
from app.logging_utils import sanear_para_log
from app.resilience import ResilientHttpClient

logger = logging.getLogger("bff_web.adapters.productos")


class ProductosClientAdapter:
    def __init__(self, http: ResilientHttpClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def listar_vigentes(self, mercado: str, idioma: str) -> list[DocumentoLegal]:
        resp = await self._http.request(
            "GET", "/documentos-legales", params={"mercado": mercado, "idioma": idioma}
        )
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp)
        return [_a_documento(item) for item in resp.json()]

    async def obtener_version(
        self, tipo: str, version: str, mercado: str, idioma: str
    ) -> DocumentoLegal:
        resp = await self._http.request(
            "GET",
            f"/documentos-legales/{tipo}/versiones/{version}",
            params={"mercado": mercado, "idioma": idioma},
        )
        if resp.status_code == 404:
            logger.info(
                "documento legal no encontrado tipo=%s version=%s",
                sanear_para_log(tipo),
                sanear_para_log(version),
            )
            raise RecursoNoEncontrado("Documento legal no encontrado")
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp)
        return _a_documento(resp.json())

    async def listar_entidades(self, mercado: str) -> list[EntidadFinanciera]:
        resp = await self._http.request(
            "GET", "/entidades-financieras", params={"mercado": mercado}
        )
        if resp.status_code in (400, 422):
            raise SolicitudInvalida(_detalle(resp))
        _asegurar_ok(resp)
        return [
            EntidadFinanciera(
                id=item["id"], nombre=item["nombre"], alias=tuple(item.get("alias", ()))
            )
            for item in resp.json()
        ]


def _detalle(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", "")
    except ValueError:  # pragma: no cover
        return ""


def _asegurar_ok(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise BffError(f"svc-productos respondió {resp.status_code}")


def _a_documento(data: dict) -> DocumentoLegal:
    return DocumentoLegal(
        tipo=data["tipo"],
        version=data["version"],
        titulo=data["titulo"],
        subtitulo=data.get("subtitulo"),
        base_legal=data["baseLegal"],
        contenido=data["contenido"],
        nota_pie=data.get("notaPie"),
    )
