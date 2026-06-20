# obs·backend

Backend read-only de la plataforma de observabilidad. Lee Cloud Logging (y, en
fases siguientes, Monitoring/Langfuse) y expone una API normalizada.

## Local

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn obs_backend.main:app --reload --port 8800
```

Para leer datos reales de QA en local, autenticar con impersonación de la SA read-only:

```bash
gcloud auth application-default login \
  --impersonate-service-account=obs-backend@itmind-macro-auto-0.iam.gserviceaccount.com
```

## Endpoints

- `GET /healthz` — liveness.
- `GET /v1/{env}/logs?severity=&limit=` — logs recientes clasificados (`env` = `qa` | `prod`).
- `GET /v1/{env}/health` — semáforo (ok/warn/crit) + conteo de errores y Gemini-429.
