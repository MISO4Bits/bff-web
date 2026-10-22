"""Límite de tamaño de las subidas de fotos, antes de procesar el cuerpo."""

from __future__ import annotations

from fastapi import FastAPI, Request

from app.api.errors import problema

RUTA_SUBIDAS = "/v1/cuenta/evidencias-identidad"
# El multipart agrega nombres de campo y delimitadores al peso de la imagen.
HOLGURA_MULTIPART = 64 * 1024


def install_limite_de_subidas(app: FastAPI, max_bytes: int) -> None:
    limite = max_bytes + HOLGURA_MULTIPART

    @app.middleware("http")
    async def _limitar_subidas(request: Request, call_next):
        if request.method == "POST" and request.url.path.endswith(RUTA_SUBIDAS):
            largo = request.headers.get("content-length", "")
            if largo.isdigit() and int(largo) > limite:
                return problema(
                    413,
                    "Imagen demasiado grande",
                    detail=f"La imagen supera el máximo de {max_bytes} bytes",
                    instance=str(request.url),
                )
        return await call_next(request)
