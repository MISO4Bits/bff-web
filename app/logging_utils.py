"""Utilidades de logging compartidas por el bootstrap de la app."""

from __future__ import annotations

import logging


class SinRuidoDeHealthCheck(logging.Filter):
    """Descarta el access log de uvicorn para ``/health``.

    Los probes de Kubernetes lo golpean cada 10-20s — no aporta nada para
    entender el comportamiento de un endpoint de negocio y ahoga, tanto en
    consola como en Grafana Cloud, los logs que sí importan (mismo criterio
    aplicado a las trazas en ``telemetry.py`` vía ``excluded_urls``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access llama a logger.info(fmt, client_addr, method,
        # full_path, http_version, status_code) — full_path es args[2].
        return not (record.args and len(record.args) >= 3 and record.args[2] == "/health")
