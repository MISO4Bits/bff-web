from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.cors import configure_cors
from app.config import Settings


def _cliente(**cfg) -> TestClient:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    configure_cors(app, Settings(**cfg))
    return TestClient(app)


def _preflight(client: TestClient, origen: str):
    return client.options(
        "/ping",
        headers={"Origin": origen, "Access-Control-Request-Method": "GET"},
    )


def test_origen_configurado_se_permite():
    client = _cliente(cors_origins="https://solventa4bits.com, https://www.solventa4bits.com")
    resp = _preflight(client, "https://www.solventa4bits.com")
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://www.solventa4bits.com"


def test_origen_desconocido_se_rechaza():
    client = _cliente(cors_origins="https://solventa4bits.com")
    resp = _preflight(client, "https://malicioso.example")
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_por_defecto_solo_el_servidor_local_de_desarrollo():
    client = _cliente()
    assert _preflight(client, "http://localhost:4200").status_code == 200
    assert _preflight(client, "https://solventa4bits.com").status_code == 400
