from __future__ import annotations

import pytest

from app.adapters.fakes import FakeEvidencias
from app.domain import ImagenMuyGrande, SolicitudInvalida
from app.services import EvidenciasIdentidadService

IMAGEN = b"\xff\xd8\xff" + b"x" * 50


def _servicio(*veredictos: str, max_bytes: int = 1000) -> EvidenciasIdentidadService:
    return EvidenciasIdentidadService(
        FakeEvidencias(veredictos or ("ACEPTADA",)), max_bytes=max_bytes
    )


async def _subir(servicio, tipo="cedula_frente", imagen=IMAGEN):
    return await servicio.subir("c1", tipo, imagen, "foto.jpg", "image/jpeg")


async def test_una_foto_aceptada_queda_como_evidencia_vigente():
    servicio = _servicio()

    resultado = await _subir(servicio)
    estado = {e.tipo: e for e in await servicio.estado("c1")}

    assert resultado.estado == "ACEPTADA"
    assert estado["cedula_frente"].evidencia_id == resultado.evidencia_id
    assert estado["cedula_reverso"].estado == "PENDIENTE"


async def test_un_rechazo_no_guarda_nada_y_descuenta_un_intento():
    servicio = _servicio("BORROSA")

    resultado = await _subir(servicio)
    estado = {e.tipo: e for e in await servicio.estado("c1")}

    assert (resultado.estado, resultado.motivo, resultado.evidencia_id) == (
        "REINTENTAR",
        "BORROSA",
        None,
    )
    assert resultado.intentos_restantes == 2
    assert estado["cedula_frente"].estado == "PENDIENTE"
    assert estado["cedula_frente"].intentos_restantes == 2


async def test_el_tercer_rechazo_pasa_a_revision_manual():
    servicio = _servicio("OSCURA")

    resultados = [await _subir(servicio) for _ in range(3)]

    assert [r.estado for r in resultados] == ["REINTENTAR", "REINTENTAR", "REVISION_MANUAL"]
    assert resultados[2].evidencia_id is not None


async def test_aceptar_reinicia_los_intentos():
    servicio = _servicio("OSCURA", "ACEPTADA")

    await _subir(servicio)
    aceptada = await _subir(servicio)

    assert aceptada.intentos_restantes == 3


async def test_una_imagen_vacia_no_se_reenvia():
    with pytest.raises(SolicitudInvalida, match="vacía"):
        await _subir(_servicio(), imagen=b"")


async def test_una_imagen_mas_grande_que_el_limite_no_se_reenvia():
    with pytest.raises(ImagenMuyGrande):
        await _subir(_servicio(max_bytes=10))


async def test_sin_subir_nada_los_tres_tipos_estan_pendientes():
    estado = await _servicio().estado("c1")

    assert [e.tipo for e in estado] == ["cedula_frente", "cedula_reverso", "selfie"]
    assert {e.estado for e in estado} == {"PENDIENTE"}
