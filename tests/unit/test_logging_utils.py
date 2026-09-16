from __future__ import annotations

import logging

from app.logging_utils import SinRuidoDeHealthCheck


def _record(full_path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:0", "GET", full_path, "1.1", 200),
        exc_info=None,
    )


def test_descarta_el_access_log_de_health():
    assert SinRuidoDeHealthCheck().filter(_record("/health")) is False


def test_conserva_el_access_log_de_endpoints_de_negocio():
    assert SinRuidoDeHealthCheck().filter(_record("/v1/registro")) is True


def test_conserva_registros_sin_argumentos_posicionales():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="mensaje sin formato",
        args=None,
        exc_info=None,
    )
    assert SinRuidoDeHealthCheck().filter(record) is True
