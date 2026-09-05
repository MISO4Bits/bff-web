"""Patrones de bajo nivel hacia dependencias HTTP: timeout + reintentos + circuit breaker.

- **Timeout** por llamada (httpx) — presupuesto duro (§6.1: 700 ms por dependencia).
- **Reintentos** con backoff exponencial + jitter (tenacity) solo ante fallos transitorios.
- **Circuit breaker** por dependencia: tras N fallos abre el circuito y falla rápido
  durante ``reset_timeout`` en vez de castigar la latencia del journey.

Nota: ``pybreaker`` (la librería del stack) solo trae ``call_async`` sobre Tornado,
no sobre asyncio; se usa aquí un breaker propio mínimo con la misma máquina de
estados (CLOSED → OPEN → HALF_OPEN) descrita por Nygard.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.domain import DependenciaNoDisponible

_TRANSIENT_STATUS = {502, 503, 504}
_TRANSIENT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


class _Transient(Exception):
    """Fallo transitorio: se puede reintentar."""


class CircuitBreakerError(Exception):
    """El circuito está abierto: la llamada no se intentó."""


class AsyncCircuitBreaker:
    def __init__(self, name: str, *, fail_max: int = 5, reset_timeout: float = 30.0) -> None:
        self.name = name
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._failures = 0
        self._state = "closed"
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        if self._state == "open" and time.monotonic() - self._opened_at >= self._reset_timeout:
            self._state = "half_open"
        return self._state

    async def call(self, operacion: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        if self.state == "open":
            raise CircuitBreakerError(f"circuito abierto: {self.name}")
        try:
            resultado = await operacion()
        except Exception:
            self._registrar_fallo()
            raise
        self._registrar_exito()
        return resultado

    def _registrar_exito(self) -> None:
        self._failures = 0
        self._state = "closed"

    def _registrar_fallo(self) -> None:
        self._failures += 1
        if self._failures >= self._fail_max:
            self._state = "open"
            self._opened_at = time.monotonic()


def build_breaker(name: str, *, fail_max: int, reset_timeout: int) -> AsyncCircuitBreaker:
    return AsyncCircuitBreaker(name, fail_max=fail_max, reset_timeout=reset_timeout)


class ResilientHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        breaker: AsyncCircuitBreaker,
        timeout: float = 0.7,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._breaker = breaker
        self._retries = retries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async def _guarded() -> httpx.Response:
            response = await self._client.request(method, url, **kwargs)
            if response.status_code in _TRANSIENT_STATUS:
                raise _Transient(f"{method} {url} -> {response.status_code}")
            return response

        async def _attempt() -> httpx.Response:
            try:
                return await self._breaker.call(_guarded)
            except CircuitBreakerError as exc:
                raise DependenciaNoDisponible(str(exc)) from exc
            except _TRANSIENT_EXCEPTIONS as exc:
                raise _Transient(str(exc)) from exc

        retryer = AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(self._retries + 1),
            wait=wait_exponential_jitter(initial=0.05, max=0.5),
            retry=retry_if_exception_type(_Transient),
        )
        try:
            return await retryer(_attempt)
        except _Transient as exc:
            raise DependenciaNoDisponible(str(exc)) from exc
