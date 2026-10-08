"""Documentos legales: adaptador HTTP hacia svc-productos, doble en memoria y servicio."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.factory import build_dependencias
from app.adapters.fakes import FakeDocumentosLegales
from app.adapters.productos_client import ProductosClientAdapter
from app.config import Settings
from app.domain import BffError, DocumentoLegal, RecursoNoEncontrado, SolicitudInvalida
from app.ports import DocumentosLegalesPort
from app.resilience import ResilientHttpClient, build_breaker
from app.services import DocumentosLegalesService

PRODUCTOS = "http://productos.local"


def _adapter() -> ProductosClientAdapter:
    http = ResilientHttpClient(
        PRODUCTOS,
        breaker=build_breaker("productos", fail_max=9, reset_timeout=5),
        timeout=0.3,
        retries=0,
    )
    return ProductosClientAdapter(http)


def _json(**cambios) -> dict:
    base = {
        "tipo": "terminos",
        "version": "V2",
        "titulo": "Términos y condiciones",
        "subtitulo": "Las reglas de uso de Solventa.",
        "baseLegal": "Ley 527 de 1999",
        "contenido": "<h3>1 · Uno</h3><p>Texto</p>",
        "notaPie": "Pie",
    }
    return {**base, **cambios}


# --- adaptador HTTP ---


@respx.mock
async def test_listar_vigentes_envia_mercado_e_idioma_y_mapea_los_campos():
    ruta = respx.get(f"{PRODUCTOS}/documentos-legales").mock(
        return_value=httpx.Response(200, json=[_json(), _json(tipo="open-data", version="V1")])
    )
    adapter = _adapter()
    try:
        documentos = await adapter.listar_vigentes("CO", "es-CO")
    finally:
        await adapter.aclose()

    assert dict(ruta.calls.last.request.url.params) == {"mercado": "CO", "idioma": "es-CO"}
    assert [(d.tipo, d.version) for d in documentos] == [("terminos", "V2"), ("open-data", "V1")]
    assert documentos[0] == DocumentoLegal(
        tipo="terminos",
        version="V2",
        titulo="Términos y condiciones",
        subtitulo="Las reglas de uso de Solventa.",
        base_legal="Ley 527 de 1999",
        contenido="<h3>1 · Uno</h3><p>Texto</p>",
        nota_pie="Pie",
    )


@respx.mock
async def test_campos_opcionales_ausentes_quedan_en_none():
    respx.get(f"{PRODUCTOS}/documentos-legales").mock(
        return_value=httpx.Response(200, json=[_json(subtitulo=None, notaPie=None)])
    )
    adapter = _adapter()
    try:
        (documento,) = await adapter.listar_vigentes("CO", "es-CO")
    finally:
        await adapter.aclose()
    assert documento.subtitulo is None
    assert documento.nota_pie is None


@respx.mock
async def test_listar_vigentes_sin_textos_devuelve_lista_vacia():
    respx.get(f"{PRODUCTOS}/documentos-legales").mock(return_value=httpx.Response(200, json=[]))
    adapter = _adapter()
    try:
        assert await adapter.listar_vigentes("CO", "en-US") == []
    finally:
        await adapter.aclose()


@respx.mock
async def test_obtener_version_arma_la_ruta_y_mapea_el_documento():
    ruta = respx.get(f"{PRODUCTOS}/documentos-legales/open-data/versiones/V3").mock(
        return_value=httpx.Response(200, json=_json(tipo="open-data", version="V3"))
    )
    adapter = _adapter()
    try:
        documento = await adapter.obtener_version("open-data", "V3", "CO", "es-CO")
    finally:
        await adapter.aclose()
    assert (documento.tipo, documento.version) == ("open-data", "V3")
    assert dict(ruta.calls.last.request.url.params) == {"mercado": "CO", "idioma": "es-CO"}


@respx.mock
async def test_obtener_version_inexistente_es_recurso_no_encontrado():
    respx.get(f"{PRODUCTOS}/documentos-legales/terminos/versiones/V9").mock(
        return_value=httpx.Response(404, json={"detail": "no existe"})
    )
    adapter = _adapter()
    try:
        with pytest.raises(RecursoNoEncontrado):
            await adapter.obtener_version("terminos", "V9", "CO", "es-CO")
    finally:
        await adapter.aclose()


@respx.mock
@pytest.mark.parametrize("status", [400, 422])
async def test_solicitud_rechazada_por_productos_es_solicitud_invalida(status):
    respx.get(f"{PRODUCTOS}/documentos-legales").mock(
        return_value=httpx.Response(status, json={"detail": "mercado inválido"})
    )
    respx.get(f"{PRODUCTOS}/documentos-legales/terminos/versiones/V1").mock(
        return_value=httpx.Response(status, json={"detail": "versión inválida"})
    )
    adapter = _adapter()
    try:
        with pytest.raises(SolicitudInvalida, match="mercado inválido"):
            await adapter.listar_vigentes("CO", "es-CO")
        with pytest.raises(SolicitudInvalida, match="versión inválida"):
            await adapter.obtener_version("terminos", "V1", "CO", "es-CO")
    finally:
        await adapter.aclose()


@respx.mock
async def test_error_de_servidor_se_propaga_como_error_del_bff():
    respx.get(f"{PRODUCTOS}/documentos-legales").mock(return_value=httpx.Response(500))
    adapter = _adapter()
    try:
        with pytest.raises(BffError):
            await adapter.listar_vigentes("CO", "es-CO")
    finally:
        await adapter.aclose()


# --- doble en memoria ---


async def test_el_doble_cumple_el_puerto_y_devuelve_los_tres_tipos_en_orden():
    fake = FakeDocumentosLegales()
    assert isinstance(fake, DocumentosLegalesPort)
    documentos = await fake.listar_vigentes("CO", "es-CO")
    assert [d.tipo for d in documentos] == ["terminos", "open-data", "open-finance"]
    assert {d.version for d in documentos} == {"V1"}


async def test_el_doble_obtiene_una_version_y_rechaza_lo_desconocido():
    fake = FakeDocumentosLegales()
    documento = await fake.obtener_version("open-finance", "V1", "CO", "es-CO")
    assert documento.base_legal == "Ley 1266 de 2008"
    with pytest.raises(RecursoNoEncontrado):
        await fake.obtener_version("open-finance", "V2", "CO", "es-CO")
    with pytest.raises(RecursoNoEncontrado):
        await fake.obtener_version("terminos", "V1", "CO", "en-US")


async def test_el_doble_sin_textos_para_otro_idioma_devuelve_lista_vacia():
    assert await FakeDocumentosLegales().listar_vigentes("CO", "en-US") == []


# --- servicio ---


async def test_el_servicio_reenvia_sin_transformar():
    service = DocumentosLegalesService(FakeDocumentosLegales())
    vigentes = await service.listar_vigentes("CO", "es-CO")
    assert len(vigentes) == 3
    uno = await service.obtener_version("terminos", "V1", "CO", "es-CO")
    assert uno == vigentes[0]


# --- fábrica ---


async def test_la_fabrica_construye_el_adaptador_segun_el_modo():
    fake = build_dependencias(Settings(adapters="fake"))
    assert type(fake.documentos_legales).__name__ == "FakeDocumentosLegales"
    await fake.aclose()

    http = build_dependencias(Settings(adapters="http", productos_base_url=PRODUCTOS))
    assert type(http.documentos_legales).__name__ == "ProductosClientAdapter"
    await http.aclose()
