from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración por variables de entorno (prefijo ``BFF_``)."""

    model_config = SettingsConfigDict(env_prefix="BFF_", env_file=".env", extra="ignore")

    service_name: str = "bff-web"
    environment: str = "local"

    # Adaptadores de salida: "fake" (todo en memoria, standalone) | "http" (servicios reales)
    adapters: str = "fake"

    identity_base_url: str = "http://localhost:9099/identitytoolkit.googleapis.com"
    identity_api_key: str = "fake-api-key"
    core_base_url: str = "http://localhost:8080"

    # Patrones de resiliencia hacia dependencias (§6.1: timeout duro 700 ms)
    http_timeout_seconds: float = 0.7
    http_retries: int = 2
    circuit_fail_max: int = 5
    circuit_reset_timeout_seconds: int = 30

    # Sesión emitida por el BFF para el journey
    session_secret: str = "dev-only-change-me"
    session_ttl_seconds: int = 3600
    refresh_ttl_seconds: int = 86400


@lru_cache
def get_settings() -> Settings:
    return Settings()
