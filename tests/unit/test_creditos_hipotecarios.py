"""Servicio de créditos hipotecarios: lo guardado por Perfilamiento más la lista del mercado."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.adapters.fakes import FakeCreditosHipotecarios, FakeEntidadesFinancieras
from app.domain import (
    ESTADO_DISPONIBLE,
    ESTADO_NO_DISPONIBLE,
    ESTADO_SIN_CONSENTIMIENTO,
    ESTADO_SIN_HIPOTECAS,
    BffError,
    CreditosReportados,
    DependenciaNoDisponible,
    EntidadFinanciera,
    HipotecaReportada,
)
from app.services import CreditosHipotecariosService, _clave

AHORA = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _hipoteca(banco: str, saldo: int = 160_000_000) -> HipotecaReportada:
    return HipotecaReportada(banco, Decimal("200000000"), Decimal(saldo), 180, Decimal("2100000"))


class _Reloj:
    def __init__(self) -> None:
        self.segundos = 0.0

    def __call__(self) -> float:
        return self.segundos


class _CreditosCaido:
    async def obtener(self, cliente_id: str):
        raise DependenciaNoDisponible("perfilamiento no responde")


class _CreditosRoto:
    async def obtener(self, cliente_id: str):
        raise ValueError("boom")


class _EntidadesCaidas:
    async def listar_entidades(self, mercado: str):
        raise DependenciaNoDisponible("productos no responde")


class _EntidadesQueSeCaen(FakeEntidadesFinancieras):
    caida = False

    async def listar_entidades(self, mercado: str):
        if self.caida:
            raise DependenciaNoDisponible("productos no responde")
        return await super().listar_entidades(mercado)


# --- reconocer la entidad por lo que reporta la fuente ---


@pytest.mark.parametrize(
    ("reportado", "entidad_id"),
    [
        ("BANCOLOMBIA S.A.", "bancolombia"),
        ("Bancolombia", "bancolombia"),
        ("BANCO DE BOGOTÁ S.A.", "banco-de-bogota"),
        ("banco de bogota", "banco-de-bogota"),
        ("BBVA COLOMBIA S.A.", "bbva-colombia"),
        ("DAVIVIENDA SA", "davivienda"),
        ("BANCO CAJA SOCIAL S.A.", None),
    ],
)
async def test_cada_hipoteca_se_asocia_a_la_entidad_del_mercado(reportado, entidad_id):
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(
            CreditosReportados(ESTADO_DISPONIBLE, (_hipoteca(reportado),), AHORA)
        ),
        FakeEntidadesFinancieras(),
    )

    resultado = await servicio.obtener("cli-1", "CO")

    (credito,) = resultado.creditos
    assert credito.entidad_id == entidad_id
    assert credito.valor_credito == Decimal("200000000")


async def test_el_banco_que_no_esta_en_la_lista_conserva_el_nombre_que_reporta_la_fuente():
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(
            CreditosReportados(ESTADO_DISPONIBLE, (_hipoteca("BANCO CAJA SOCIAL S.A."),), AHORA)
        ),
        FakeEntidadesFinancieras(),
    )

    (credito,) = (await servicio.obtener("cli-1", "CO")).creditos

    assert (credito.entidad_id, credito.entidad_nombre) == (None, "BANCO CAJA SOCIAL S.A.")


async def test_el_nombre_que_se_muestra_es_el_de_la_entidad_del_mercado():
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(
            CreditosReportados(ESTADO_DISPONIBLE, (_hipoteca("BANCOLOMBIA S.A."),), AHORA)
        ),
        FakeEntidadesFinancieras(),
    )

    (credito,) = (await servicio.obtener("cli-1", "CO")).creditos

    assert credito.entidad_nombre == "Bancolombia"


def test_la_clave_ignora_tildes_mayusculas_puntuacion_y_sufijo_societario():
    assert _clave("BANCO DE BOGOTÁ S.A.") == _clave("Banco de Bogota") == "banco de bogota"
    assert _clave("S.A.") == ""  # nada que comparar: nunca coincide con una entidad real


# --- estados ---


@pytest.mark.parametrize("estado", [ESTADO_SIN_HIPOTECAS, ESTADO_SIN_CONSENTIMIENTO])
async def test_sin_datos_devuelve_la_lista_de_bancos_para_captura_manual(estado):
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(CreditosReportados(estado)), FakeEntidadesFinancieras()
    )

    resultado = await servicio.obtener("cli-1", "CO")

    assert resultado.estado == estado
    assert resultado.creditos == ()
    assert [e.id for e in resultado.entidades] == [
        "bancolombia",
        "davivienda",
        "banco-de-bogota",
        "bbva-colombia",
    ]


async def test_si_perfilamiento_no_responde_el_cliente_sigue_con_la_lista_de_bancos():
    servicio = CreditosHipotecariosService(_CreditosCaido(), FakeEntidadesFinancieras())

    resultado = await servicio.obtener("cli-1", "CO")

    assert resultado.estado == ESTADO_NO_DISPONIBLE
    assert resultado.creditos == ()
    assert len(resultado.entidades) == 4


async def test_un_error_inesperado_de_perfilamiento_se_propaga():
    servicio = CreditosHipotecariosService(_CreditosRoto(), FakeEntidadesFinancieras())

    with pytest.raises(ValueError):
        await servicio.obtener("cli-1", "CO")


async def test_sin_perfilamiento_ni_productos_no_hay_nada_que_mostrar():
    servicio = CreditosHipotecariosService(_CreditosCaido(), _EntidadesCaidas())

    with pytest.raises(BffError):
        await servicio.obtener("cli-1", "CO")


async def test_con_hipotecas_a_la_vista_no_hace_falta_que_productos_responda():
    servicio = CreditosHipotecariosService(FakeCreditosHipotecarios(), _EntidadesCaidas())

    resultado = await servicio.obtener("cli-1", "CO")

    assert resultado.estado == ESTADO_DISPONIBLE
    assert resultado.entidades == ()
    (credito,) = resultado.creditos
    assert credito.entidad_id is None  # sin lista no se puede reconocer el banco


async def test_sin_hipotecas_y_con_productos_caido_el_error_llega_al_cliente():
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(CreditosReportados(ESTADO_SIN_CONSENTIMIENTO)),
        _EntidadesCaidas(),
    )

    with pytest.raises(DependenciaNoDisponible):
        await servicio.obtener("cli-1", "CO")


# --- la lista del mercado se guarda en el BFF ---


async def test_la_lista_del_mercado_se_pide_una_vez_por_hora():
    entidades = FakeEntidadesFinancieras()
    reloj = _Reloj()
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(), entidades, cache_segundos=3600, reloj=reloj
    )

    await servicio.obtener("cli-1", "CO")
    reloj.segundos = 3599
    await servicio.obtener("cli-2", "CO")
    assert entidades.llamadas == 1

    reloj.segundos = 3600
    await servicio.obtener("cli-3", "CO")
    assert entidades.llamadas == 2


async def test_si_productos_cae_se_usa_la_ultima_lista_guardada():
    entidades = _EntidadesQueSeCaen()
    reloj = _Reloj()
    servicio = CreditosHipotecariosService(
        FakeCreditosHipotecarios(CreditosReportados(ESTADO_SIN_CONSENTIMIENTO)),
        entidades,
        cache_segundos=60,
        reloj=reloj,
    )
    await servicio.obtener("cli-1", "CO")
    entidades.caida = True
    reloj.segundos = 600  # la guardada ya venció, pero es mejor que nada

    resultado = await servicio.obtener("cli-1", "CO")

    assert len(resultado.entidades) == 4


async def test_cada_llamada_pide_los_creditos_del_cliente_que_la_hace():
    creditos = FakeCreditosHipotecarios()
    servicio = CreditosHipotecariosService(creditos, FakeEntidadesFinancieras())

    await servicio.obtener("cli-7", "CO")

    assert creditos.llamadas == ["cli-7"]


def test_la_entidad_del_mercado_es_inmutable():
    entidad = EntidadFinanciera("x", "X")
    with pytest.raises(AttributeError):
        entidad.nombre = "Y"  # type: ignore[misc]
