from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.core_client import CoreClientAdapter
from app.domain import (
    BffError,
    DependenciaNoDisponible,
    ImagenMuyGrande,
    RecursoNoEncontrado,
    SolicitudInvalida,
)
from app.resilience import ResilientHttpClient, build_breaker

CORE = "http://core.local"
RUTA = f"{CORE}/v1/clientes/c1/evidencias-identidad"
IMAGEN = b"\xff\xd8\xff\xe0" + b"jpeg-de-mentira" * 20


def _core(*, retries: int = 0) -> CoreClientAdapter:
    http = ResilientHttpClient(
        CORE,
        breaker=build_breaker("core", fail_max=99, reset_timeout=5),
        timeout=0.3,
        retries=retries,
    )
    return CoreClientAdapter(http)


async def _subir(adapter: CoreClientAdapter, tipo: str = "cedula_frente"):
    return await adapter.subir("c1", tipo, IMAGEN, "frente.jpg", "image/jpeg")


@respx.mock
async def test_subir_envia_el_tipo_y_la_imagen_como_multipart():
    ruta = respx.post(RUTA).mock(
        return_value=httpx.Response(
            201,
            json={
                "evidenciaId": "e1",
                "tipo": "cedula_frente",
                "estado": "ACEPTADA",
                "motivo": None,
                "intentosRestantes": 3,
            },
        )
    )
    adapter = _core()
    try:
        resultado = await _subir(adapter)
    finally:
        await adapter.aclose()

    enviado = ruta.calls.last.request
    assert enviado.headers["content-type"].startswith("multipart/form-data")
    assert b'name="tipo"' in enviado.content and b"cedula_frente" in enviado.content
    assert b'name="imagen"; filename="frente.jpg"' in enviado.content
    assert IMAGEN in enviado.content
    assert (resultado.estado, resultado.evidencia_id, resultado.intentos_restantes) == (
        "ACEPTADA",
        "e1",
        3,
    )
    assert resultado.motivo is None


@respx.mock
async def test_subir_un_rechazo_trae_el_motivo_y_ninguna_evidencia():
    respx.post(RUTA).mock(
        return_value=httpx.Response(
            200,
            json={
                "evidenciaId": None,
                "tipo": "cedula_reverso",
                "estado": "REINTENTAR",
                "motivo": "REFLEJO",
                "intentosRestantes": 2,
            },
        )
    )
    adapter = _core()
    try:
        resultado = await _subir(adapter, "cedula_reverso")
    finally:
        await adapter.aclose()

    assert (resultado.estado, resultado.motivo, resultado.evidencia_id) == (
        "REINTENTAR",
        "REFLEJO",
        None,
    )
    assert resultado.intentos_restantes == 2


@pytest.mark.parametrize(
    ("estado_http", "error"),
    [
        (404, RecursoNoEncontrado),
        (413, ImagenMuyGrande),
        (400, SolicitudInvalida),
        (422, SolicitudInvalida),
        (500, BffError),
    ],
)
@respx.mock
async def test_subir_traduce_los_errores_de_core(estado_http, error):
    respx.post(RUTA).mock(return_value=httpx.Response(estado_http, json={"detail": "no sirve"}))
    adapter = _core()
    try:
        with pytest.raises(error):
            await _subir(adapter)
    finally:
        await adapter.aclose()


@respx.mock
async def test_el_detalle_de_core_llega_al_cliente_en_una_solicitud_invalida():
    respx.post(RUTA).mock(
        return_value=httpx.Response(400, json={"detail": "El archivo no es una imagen JPEG o PNG"})
    )
    adapter = _core()
    try:
        with pytest.raises(SolicitudInvalida, match="no es una imagen"):
            await _subir(adapter)
    finally:
        await adapter.aclose()


@respx.mock
async def test_una_subida_nunca_se_reintenta_aunque_el_cliente_http_reintente():
    # Cada envío cuenta como un intento en svc-core: repetirlo lo descontaría dos veces.
    ruta = respx.post(RUTA).mock(return_value=httpx.Response(503))
    adapter = _core(retries=2)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await _subir(adapter)
    finally:
        await adapter.aclose()

    assert ruta.call_count == 1


@respx.mock
async def test_las_demas_llamadas_si_se_reintentan():
    ruta = respx.get(f"{CORE}/v1/clientes/c1/evidencias-identidad").mock(
        return_value=httpx.Response(503)
    )
    adapter = _core(retries=2)
    try:
        with pytest.raises(DependenciaNoDisponible):
            await adapter.estado("c1")
    finally:
        await adapter.aclose()

    assert ruta.call_count == 3


@respx.mock
async def test_estado_devuelve_los_tres_tipos_con_su_fecha():
    respx.get(RUTA).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tipo": "cedula_frente",
                    "estado": "ACEPTADA",
                    "evidenciaId": "e1",
                    "creadaEn": "2026-10-21T12:00:00Z",
                    "intentosRestantes": 3,
                },
                {
                    "tipo": "cedula_reverso",
                    "estado": "PENDIENTE",
                    "evidenciaId": None,
                    "creadaEn": None,
                    "intentosRestantes": 2,
                },
            ],
        )
    )
    adapter = _core()
    try:
        frente, reverso = await adapter.estado("c1")
    finally:
        await adapter.aclose()

    assert (frente.estado, frente.evidencia_id) == ("ACEPTADA", "e1")
    assert frente.creada_en is not None and frente.creada_en.year == 2026
    assert (reverso.estado, reverso.creada_en, reverso.intentos_restantes) == ("PENDIENTE", None, 2)


@respx.mock
async def test_estado_de_un_cliente_inexistente():
    respx.get(RUTA).mock(return_value=httpx.Response(404, json={}))
    adapter = _core()
    try:
        with pytest.raises(RecursoNoEncontrado):
            await adapter.estado("c1")
    finally:
        await adapter.aclose()
