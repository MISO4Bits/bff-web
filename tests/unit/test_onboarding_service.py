from __future__ import annotations

from datetime import date

import pytest

from app.adapters.fakes import FakeCoreIdentity, FakeIdentityProvider
from app.domain import (
    ClienteCore,
    Conflicto,
    DependenciaNoDisponible,
    NoAutorizado,
    RegistroInput,
)
from app.security import SessionIssuer
from app.services import OnboardingService

DATOS = RegistroInput(
    email="ana@example.com",
    password="claveSegura12",
    tipo_documento="CC",
    numero_documento="123456",
    primer_nombre="Ana",
    primer_apellido="Ríos",
    fecha_nacimiento=date(1990, 1, 1),
    politica_version="v1",
)


@pytest.fixture
def identity() -> FakeIdentityProvider:
    return FakeIdentityProvider()


@pytest.fixture
def core() -> FakeCoreIdentity:
    return FakeCoreIdentity()


@pytest.fixture
def service(identity, core) -> OnboardingService:
    return OnboardingService(identity, core, SessionIssuer("secreto"))


async def test_registrar_devuelve_cuenta_y_sesion(service):
    cuenta, sesion = await service.registrar(DATOS)

    assert cuenta.cliente_id
    assert cuenta.estado == "ACTIVO"
    assert sesion.token_type == "Bearer"
    assert sesion.access_token and sesion.refresh_token


async def test_registrar_correo_duplicado_no_toca_core(service, core):
    await service.registrar(DATOS)
    with pytest.raises(Conflicto):
        await service.registrar(DATOS)
    # el documento no debe haberse consumido dos veces
    assert len(core._clientes) == 1


async def test_registrar_compensa_si_core_falla(identity, core):
    async def _boom(*_a, **_k):
        raise DependenciaNoDisponible("core caído")

    core.registrar_cliente = _boom  # type: ignore[assignment]
    service = OnboardingService(identity, core, SessionIssuer("secreto"))

    with pytest.raises(DependenciaNoDisponible):
        await service.registrar(DATOS)

    # la credencial creada en Identity Platform fue revertida (compensación)
    assert identity._por_email == {}


async def test_registrar_compensa_ante_error_inesperado(identity, core):
    async def _boom(*_a, **_k):
        raise RuntimeError("inesperado")

    core.registrar_cliente = _boom  # type: ignore[assignment]
    service = OnboardingService(identity, core, SessionIssuer("secreto"))

    with pytest.raises(RuntimeError):
        await service.registrar(DATOS)
    assert identity._por_email == {}


async def test_iniciar_sesion(service):
    await service.registrar(DATOS)
    sesion = await service.iniciar_sesion(DATOS.email, DATOS.password)
    assert sesion.access_token


async def test_iniciar_sesion_credenciales_malas(service):
    await service.registrar(DATOS)
    with pytest.raises(NoAutorizado):
        await service.iniciar_sesion(DATOS.email, "otra-clave")


async def test_refrescar(service):
    _, sesion = await service.registrar(DATOS)
    nueva = service.refrescar(sesion.refresh_token)
    assert nueva.access_token


async def test_consentimientos_via_service(service):
    cuenta, _ = await service.registrar(DATOS)

    otorgado = await service.otorgar_consentimiento(
        cuenta.cliente_id, "OPEN_FINANCE", "v1"
    )
    assert otorgado.vigente is True

    listado = await service.listar_consentimientos(cuenta.cliente_id)
    assert [c.scope for c in listado] == ["OPEN_FINANCE"]

    await service.revocar_consentimiento(cuenta.cliente_id, "OPEN_FINANCE")
    listado = await service.listar_consentimientos(cuenta.cliente_id)
    assert listado[0].vigente is False


async def test_obtener_cuenta(service):
    cuenta, _ = await service.registrar(DATOS)
    recuperada = await service.obtener_cuenta(cuenta.cliente_id)
    assert recuperada.email == DATOS.email


async def test_iniciar_sesion_sin_cliente_en_core(identity, core):
    """Credencial válida pero sin cliente asociado en core -> RecursoNoEncontrado."""
    from app.domain import RecursoNoEncontrado

    service = OnboardingService(identity, core, SessionIssuer("s"))
    await identity.registrar(DATOS.email, DATOS.password)
    with pytest.raises(RecursoNoEncontrado):
        await service.iniciar_sesion(DATOS.email, DATOS.password)


async def test_fake_core_documento_duplicado(core):
    otros = RegistroInput(
        email="b@example.com",
        password="claveSegura12",
        tipo_documento="CC",
        numero_documento="123456",
        primer_nombre="Otra",
        primer_apellido="Persona",
        fecha_nacimiento=date(1985, 3, 3),
        politica_version="v1",
    )
    await core.registrar_cliente("sub-a", DATOS)
    with pytest.raises(Conflicto):
        await core.registrar_cliente("sub-b", otros)


async def test_compensacion_tolera_fallo_al_revertir(identity, core, caplog):
    async def _boom_core(*_a, **_k):
        raise Conflicto("documento repetido")

    async def _boom_delete(*_a, **_k):
        raise RuntimeError("no se pudo borrar")

    core.registrar_cliente = _boom_core  # type: ignore[assignment]
    identity.eliminar = _boom_delete  # type: ignore[assignment]
    service = OnboardingService(identity, core, SessionIssuer("s"))

    with pytest.raises(Conflicto):
        await service.registrar(DATOS)
    assert "no se pudo revertir" in caplog.text


def test_cuenta_desde_core_mapea_campos():
    from app.domain import Cuenta

    core = ClienteCore(
        id="c1",
        primer_nombre="Ana",
        primer_apellido="Ríos",
        email="a@b.com",
        estado="ACTIVO",
        telefono="+571234567",
    )
    cuenta = Cuenta.desde_core(core)
    assert cuenta.cliente_id == "c1"
    assert cuenta.telefono == "+571234567"
