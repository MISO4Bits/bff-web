"""Fábrica de adaptadores del BFF (sin framework de DI)."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.core_client import CoreClientAdapter
from app.adapters.fakes import FakeCoreIdentity, FakeIdentityProvider
from app.adapters.identity_platform import IdentityPlatformAdapter
from app.config import Settings
from app.ports import CoreIdentityPort, IdentityProviderPort
from app.resilience import ResilientHttpClient, build_breaker
from app.security import SessionIssuer


@dataclass
class Dependencias:
    identity: IdentityProviderPort
    core: CoreIdentityPort
    sessions: SessionIssuer

    async def aclose(self) -> None:
        for adapter in (self.identity, self.core):
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
        return Dependencias(FakeIdentityProvider(), FakeCoreIdentity(), sessions)

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
        )
        return Dependencias(
            IdentityPlatformAdapter(identity_http, settings.identity_api_key),
            CoreClientAdapter(core_http),
            sessions,
        )

    raise ValueError(f"adapters no soportado: {settings.adapters}")
