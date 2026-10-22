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
- En modo `fake` (por defecto) el BFF **no necesita** svc-core, svc-productos ni Identity Platform: todo corre en memoria (los documentos legales son textos de ejemplo abreviados).

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
| `BFF_PRODUCTOS_BASE_URL` | `http://localhost:8100` | svc-productos: documentos legales del registro (modo `http`) |
| `BFF_IDENTITY_BASE_URL` / `BFF_IDENTITY_API_KEY` | — | Identity Platform (modo `http`) |
| `BFF_HTTP_TIMEOUT_SECONDS` | `0.7` | timeout duro por dependencia (§6.1) |
| `BFF_HTTP_RETRIES` | `2` | reintentos ante fallo transitorio |
| `BFF_CIRCUIT_FAIL_MAX` | `5` | fallos antes de abrir el circuito |
| `BFF_EVIDENCIA_MAX_BYTES` | `3145728` | peso máximo de una foto de identidad (3 MB); lo que pase se rechaza con 413 antes de procesarlo |
| `BFF_EVIDENCIAS_TIMEOUT_SECONDS` | `5.0` | timeout de la subida a svc-core (incluye su análisis); las subidas **no se reintentan** |
| `BFF_SESSION_SECRET` | `dev-only-change-me` | firma del JWT de sesión del BFF |

### Evidencias de identidad (cédula y selfie, BITS-275)

`POST /v1/cuenta/evidencias-identidad` (multipart: `tipo` ∈ `cedula_frente`, `cedula_reverso`, `selfie` e `imagen`)
sube **una foto** y devuelve el veredicto de svc-core: `201` si se guarda (`ACEPTADA` o `REVISION_MANUAL`), `200` con
`motivo` si hay que repetirla. `GET /v1/cuenta/evidencias-identidad` devuelve el estado de los tres tipos. El `clienteId`
sale de la sesión. El BFF no decide nada: reenvía la foto y descarta de entrada lo vacío o lo que supera el máximo.
Con `BFF_ADAPTERS=fake` el veredicto siempre es `ACEPTADA`; para ver los demás estados, correr contra svc-core con
`CORE_VERIFICADOR_BACKEND=mock` y `CORE_VERIFICADOR_MOCK_SECUENCIA` (ver el README de svc-core).

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
