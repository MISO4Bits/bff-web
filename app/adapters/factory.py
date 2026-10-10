"""Fábrica de adaptadores del BFF (sin framework de DI)."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.core_client import CoreClientAdapter
from app.adapters.cotizacion_client import CotizacionClientAdapter
from app.adapters.fakes import (
    FakeCoreIdentity,
    FakeCotizacion,
    FakeCreditosHipotecarios,
    FakeDocumentosLegales,
    FakeEntidadesFinancieras,
    FakeIdentityProvider,
)
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.adapters.perfilamiento_client import PerfilamientoClientAdapter
from app.adapters.productos_client import ProductosClientAdapter
from app.config import Settings
from app.ports import (
    CoreIdentityPort,
    CotizacionPort,
    CreditosHipotecariosPort,
    DocumentosLegalesPort,
    EntidadesFinancierasPort,
    IdentityProviderPort,
)
from app.resilience import ResilientHttpClient, build_breaker
from app.security import SessionIssuer


@dataclass
class Dependencias:
    identity: IdentityProviderPort
    core: CoreIdentityPort
    cotizacion: CotizacionPort
    documentos_legales: DocumentosLegalesPort
    sessions: SessionIssuer
    creditos: CreditosHipotecariosPort
    entidades: EntidadesFinancierasPort

    async def aclose(self) -> None:
        # ``entidades`` comparte cliente HTTP con ``documentos_legales`` (los dos son Productos).
        for adapter in (
            self.identity,
            self.core,
            self.cotizacion,
            self.documentos_legales,
            self.creditos,
        ):
            cerrar = getattr(adapter, "aclose", None)
            if cerrar is not None:
                await cerrar()


def build_dependencias(settings: Settings) -> Dependencias:
    sessions = SessionIssuer(
        settings.session_secret,
        ttl_seconds=settings.session_ttl_seconds,
        refresh_ttl_seconds=settings.refresh_ttl_seconds,
    )

    if settings.adapters == "fake":
        return Dependencias(
            FakeIdentityProvider(),
            FakeCoreIdentity(),
            FakeCotizacion(),
            FakeDocumentosLegales(),
            sessions,
            FakeCreditosHipotecarios(),
            FakeEntidadesFinancieras(),
        )

    if settings.adapters == "http":
        identity_http = ResilientHttpClient(
            settings.identity_base_url,
            breaker=build_breaker(
                "identity-platform",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            pool_timeout=settings.http_pool_timeout_seconds,
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive_connections,
        )
        core_http = ResilientHttpClient(
            settings.core_base_url,
            breaker=build_breaker(
                "svc-core",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            pool_timeout=settings.http_pool_timeout_seconds,
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive_connections,
        )
        cotizacion_http = ResilientHttpClient(
            settings.cotizacion_base_url,
            breaker=build_breaker(
                "svc-cotizacion",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            pool_timeout=settings.http_pool_timeout_seconds,
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive_connections,
        )
        productos_http = ResilientHttpClient(
            settings.productos_base_url,
            breaker=build_breaker(
                "svc-productos",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            pool_timeout=settings.http_pool_timeout_seconds,
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive_connections,
        )
        perfilamiento_http = ResilientHttpClient(
            settings.perfilamiento_base_url,
            breaker=build_breaker(
                "svc-perfilamiento",
                fail_max=settings.circuit_fail_max,
                reset_timeout=settings.circuit_reset_timeout_seconds,
            ),
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            pool_timeout=settings.http_pool_timeout_seconds,
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive_connections,
        )
        productos = ProductosClientAdapter(productos_http)
        return Dependencias(
            IdentityPlatformAdapter(identity_http, settings.identity_api_key),
            CoreClientAdapter(core_http),
            CotizacionClientAdapter(cotizacion_http),
            productos,
            sessions,
            PerfilamientoClientAdapter(perfilamiento_http),
            productos,
        )

    raise ValueError(f"adapters no soportado: {settings.adapters}")
