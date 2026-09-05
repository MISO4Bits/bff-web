# bff-web

Backend for Frontend del canal web — orquesta el **journey de originación**
(registro de cliente + consentimientos). Contrato: [`openapi/openapi.yaml`](openapi/openapi.yaml).

## Correr el servicio en local para pruebas

Requiere Python 3.12+.

```bash
# 1. entorno e instalación (incluye dependencias de prueba)
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash);  en Linux/Mac: source .venv/bin/activate
pip install -e ".[dev]"

# 2. levantar la API (modo standalone: adaptadores en memoria, sin dependencias)
uvicorn app.main:app --reload --port 8081
```

- Docs interactivas: <http://localhost:8081/docs> · contrato: <http://localhost:8081/openapi.yaml> · salud: <http://localhost:8081/health>
- En modo `fake` (por defecto) el BFF **no necesita** svc-core ni Identity Platform: todo corre en memoria.

### Modos de adaptadores (variable `BFF_ADAPTERS`)

| Valor | Qué hace |
|---|---|
| `fake` (default) | Identity Platform y svc-core simulados en memoria. Para desarrollo del canal y pruebas. |
| `http` | Llama a los servicios reales vía HTTP con timeout + reintentos + circuit breaker. |

Para `http`, apuntar a un svc-core corriendo:

```bash
BFF_ADAPTERS=http BFF_CORE_BASE_URL=http://localhost:8080 \
BFF_IDENTITY_BASE_URL=http://localhost:9099 BFF_IDENTITY_API_KEY=xxx \
uvicorn app.main:app --port 8081
```

### Variables de entorno (prefijo `BFF_`)

| Variable | Default | Notas |
|---|---|---|
| `BFF_ADAPTERS` | `fake` | `fake` \| `http` |
| `BFF_CORE_BASE_URL` | `http://localhost:8080` | svc-core (modo `http`) |
| `BFF_IDENTITY_BASE_URL` / `BFF_IDENTITY_API_KEY` | — | Identity Platform (modo `http`) |
| `BFF_HTTP_TIMEOUT_SECONDS` | `0.7` | timeout duro por dependencia (§6.1) |
| `BFF_HTTP_RETRIES` | `2` | reintentos ante fallo transitorio |
| `BFF_CIRCUIT_FAIL_MAX` | `5` | fallos antes de abrir el circuito |
| `BFF_SESSION_SECRET` | `dev-only-change-me` | firma del JWT de sesión del BFF |

### Prueba rápida con curl

```bash
curl -X POST http://localhost:8081/v1/registro -H 'content-type: application/json' -d '{
  "email":"ana.rios@example.com","password":"unaClaveSegura1","tipoDocumento":"CC",
  "numeroDocumento":"1032456789","primerNombre":"Ana","primerApellido":"Rios",
  "fechaNacimiento":"1991-05-20","politicaVersion":"2026-01","aceptaTerminos":true}'
```

## Pruebas

```bash
pytest                                        # unit + contrato + integración
pytest --cov=app --cov-report=term-missing    # con cobertura (gate 80%)
pytest tests/unit    tests/contract    tests/integration   # por tipo
```

- `tests/unit/test_resilience.py` ejercita timeout, reintentos y circuit breaker.
- `tests/integration/test_http_flow.py` usa `respx` para simular Identity Platform y svc-core.
- No requiere Docker ni servicios externos.
