# C — Reducir ruido de logs/alertas · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reducir los logs `severity>=ERROR` benignos en origen sin perder señal real, atacando primero la fuente #1 medida (el propio panel, ~890/día).

**Architecture:** Tres frentes independientes por volumen medido. F1 (repo `observability-platform`): los clientes salientes del panel capturan errores de transporte httpx → WARNING conciso + sentinel vacío, en vez de dejar propagar la excepción a uvicorn (ERROR+traceback). F2 (repo `itmind-infrastructure`): `google_logging_project_exclusion` content-specific sobre `_Default`, solo para ruido-progreso de gcs-sync, con flujo disabled→medir→activar. F3 (repo `itmind-infrastructure`): auditoría de las 80 alert policies (confirmar que ninguna es broad-severity).

**Tech Stack:** Python 3.12 / FastAPI / httpx / pytest (F1); Terraform / Google provider / Cloud Logging (F2, F3).

**Spec:** `docs/superpowers/specs/2026-08-25-c-noise-reduction-design.md`

## Global Constraints

- **F1 corte señal/ruido:** capturar SOLO `httpx.TransportError` (connect + timeouts). NO capturar `httpx.HTTPStatusError` (un 5xx real de la fuente es señal → sigue como ERROR).
- **F1 sin traceback:** el WARNING se emite con `_log.warning(...)` SIN `exc_info=True`.
- **F1 no tragar señal:** el sentinel vacío mantiene el estado "fuente degradada" en el front (ya existe); el WARNING deja registro del evento.
- **F2/F3 son terraform en `itmind-infrastructure`:** todo `terraform apply` requiere **aprobación explícita del usuario** en el momento (regla dura del repo). Los pasos de apply están marcados `[GATE]`.
- **F1 deploy:** manual con kubectl (ver `k8s/README.md`), no ArgoCD. También `[GATE]`.
- **rag_admin.py queda FUERA de C:** ya captura (`except httpx.HTTPError: return None`), no produce ERROR. Su fallo silencioso (sin WARNING) es un tema de visibilidad aparte, no de ruido.

---

## File Structure

**F1 (obs repo):**
- Modify `backend/obs_backend/sources/_langfuse.py` — agregar helper `warn_unreachable(logger, source_name, base_url, exc)` (mensaje WARNING centralizado; ambos clientes Langfuse lo usan).
- Modify `backend/obs_backend/sources/langfuse.py` — try/except `httpx.TransportError` alrededor del `client.get` → `warn_unreachable` + `return []`.
- Modify `backend/obs_backend/sources/rag_pipeline.py` — idem alrededor del `with client` paginado → `warn_unreachable` + `return _aggregate(groups)`.
- Test `backend/tests/test_langfuse.py` — caso TransportError.
- Test `backend/tests/test_rag_pipeline.py` — caso TransportError.
- Modify `k8s/deployment.yaml` — bump de tag tras el build (deploy).

**F2 (infra repo `itmind-infrastructure`):**
- Create `fast/tenants/macro/3-project-factory/logging-exclusions.tf` — `google_logging_project_exclusion` por proyecto ruidoso (o el módulo que el paso 1 de F2 confirme como dueño del proyecto).

**F3 (infra repo):**
- Create `scripts/audit-alert-severity.sh` — barrido de las condiciones de las 80 policies.

---

## Task 1: F1 — LangfuseSource captura errores de transporte

**Files:**
- Modify: `backend/obs_backend/sources/_langfuse.py`
- Modify: `backend/obs_backend/sources/langfuse.py`
- Test: `backend/tests/test_langfuse.py`

**Interfaces:**
- Produces: `warn_unreachable(logger: logging.Logger, source_name: str, base_url: str, exc: Exception) -> None` en `_langfuse.py` — emite `logger.warning("%s inalcanzable: %s (%s)", source_name, base_url, type(exc).__name__)` sin exc_info. Lo consume Task 2.

- [ ] **Step 1: Escribir el test que falla (langfuse)**

En `backend/tests/test_langfuse.py`, agregar al final:

```python
def test_recent_traces_degrada_a_vacio_en_transport_error(caplog):
    import logging
    import httpx
    from obs_backend.sources.langfuse import LangfuseSource

    class _RaisingClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, *a, **k):
            raise httpx.ConnectError("Name or service not known")

    src = LangfuseSource(client_factory=lambda base_url, auth: _RaisingClient())
    with caplog.at_level(logging.WARNING):
        result = src.recent_traces("dev")

    assert result == []
    assert any(
        r.levelno == logging.WARNING and "inalcanzable" in r.getMessage().lower()
        for r in caplog.records
    ), "debe logear un WARNING conciso"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), "nada en ERROR"
    assert not any(r.exc_info for r in caplog.records), "sin traceback"
```

- [ ] **Step 2: Correr el test — debe fallar**

Run: `cd backend && . .venv/bin/activate && python -m pytest tests/test_langfuse.py::test_recent_traces_degrada_a_vacio_en_transport_error -v`
Expected: FAIL — `httpx.ConnectError` se propaga (no hay try/except todavía).

- [ ] **Step 3: Agregar el helper `warn_unreachable` a `_langfuse.py`**

Al final de `backend/obs_backend/sources/_langfuse.py`:

```python
import logging


def warn_unreachable(logger: logging.Logger, source_name: str, base_url: str, exc: Exception) -> None:
    """Log conciso (WARNING, sin traceback) para una fuente saliente inalcanzable.

    Se usa cuando httpx no pudo ni conectar (TransportError): DNS/red/timeout. Un 5xx
    real (HTTPStatusError) NO pasa por acá — eso es señal y sigue como ERROR.
    """
    logger.warning("%s inalcanzable: %s (%s)", source_name, base_url, type(exc).__name__)
```

- [ ] **Step 4: Envolver el fetch en `langfuse.py`**

En `backend/obs_backend/sources/langfuse.py`: agregar imports arriba —

```python
import logging

from obs_backend.sources._langfuse import build_auth, default_client_factory as _default_client_factory, warn_unreachable

_log = logging.getLogger(__name__)
```

y reemplazar el bloque `with self._client_factory(...) as client: ... payload = resp.json()` por:

```python
        try:
            with self._client_factory(base_url, auth) as client:
                resp = client.get(
                    "/api/public/traces",
                    params={"limit": limit, "orderBy": "timestamp.desc"},
                )
                resp.raise_for_status()
                payload = resp.json()
        except httpx.TransportError as exc:
            warn_unreachable(_log, "langfuse", base_url, exc)
            return []
```

(El resto del método —los `isinstance` de shape— queda igual. `httpx.HTTPStatusError` de `raise_for_status()` NO se captura: sigue propagando.)

- [ ] **Step 5: Correr el test — debe pasar**

Run: `cd backend && . .venv/bin/activate && python -m pytest tests/test_langfuse.py -v`
Expected: PASS (el nuevo + los existentes de langfuse).

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/sources/_langfuse.py backend/obs_backend/sources/langfuse.py backend/tests/test_langfuse.py
git commit -m "fix(langfuse): degradar a vacío + WARNING en error de transporte (C-F1)"
```

---

## Task 2: F1 — RagPipelineSource captura errores de transporte

**Files:**
- Modify: `backend/obs_backend/sources/rag_pipeline.py`
- Test: `backend/tests/test_rag_pipeline.py`

**Interfaces:**
- Consumes: `warn_unreachable` de `_langfuse.py` (Task 1).

- [ ] **Step 1: Escribir el test que falla (rag_pipeline)**

En `backend/tests/test_rag_pipeline.py`, agregar al final:

```python
def test_rag_node_stats_degrada_a_vacio_en_transport_error(caplog):
    import logging
    import httpx
    from obs_backend.sources.rag_pipeline import RagPipelineSource

    class _RaisingClient:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, *a, **k):
            raise httpx.ConnectTimeout("timed out")

    src = RagPipelineSource(client_factory=lambda base_url, auth: _RaisingClient())
    with caplog.at_level(logging.WARNING):
        result = src.rag_node_stats("dev")

    assert result == []
    assert any(
        r.levelno == logging.WARNING and "inalcanzable" in r.getMessage().lower()
        for r in caplog.records
    )
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)
```

- [ ] **Step 2: Correr el test — debe fallar**

Run: `cd backend && . .venv/bin/activate && python -m pytest tests/test_rag_pipeline.py::test_rag_node_stats_degrada_a_vacio_en_transport_error -v`
Expected: FAIL — `httpx.ConnectTimeout` se propaga (docstring actual: "Errores de transporte HTTP se propagan").

- [ ] **Step 3: Envolver el fetch paginado en `rag_pipeline.py`**

En `backend/obs_backend/sources/rag_pipeline.py`: importar el helper —

```python
from obs_backend.sources._langfuse import build_auth, default_client_factory, warn_unreachable
```

y envolver el bloque `with self._client_factory(base_url, auth) as client: for page_num ...` en try/except; al capturar, WARNING + devolver lo agregado hasta el momento:

```python
        try:
            with self._client_factory(base_url, auth) as client:
                for page_num in range(1, _MAX_PAGES + 1):
                    resp = client.get(
                        "/api/public/observations",
                        params={
                            "fromStartTime": cutoff_iso,
                            "limit": _PAGE_SIZE,
                            "page": page_num,
                        },
                    )
                    resp.raise_for_status()
                    payload = resp.json()

                    if not isinstance(payload, dict):
                        break
                    data = payload.get("data")
                    if not isinstance(data, list) or len(data) == 0:
                        break

                    if page_num == _MAX_PAGES and len(data) == _PAGE_SIZE:
                        _log.warning(
                            "rag_node_stats: pagination cap (%d obs) hit for env=%s; stats may be truncated",
                            _MAX_PAGES * _PAGE_SIZE,
                            env,
                        )

                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        try:
                            _parse_obs(item, groups)
                        except Exception:
                            continue
        except httpx.TransportError as exc:
            warn_unreachable(_log, "rag/langfuse-observations", base_url, exc)
            return _aggregate(groups)

        return _aggregate(groups)
```

Actualizar el docstring de la clase: cambiar "Errores de transporte HTTP se propagan." por "Errores de transporte HTTP → WARNING + resultado parcial (no propagan)."

- [ ] **Step 4: Correr el test — debe pasar**

Run: `cd backend && . .venv/bin/activate && python -m pytest tests/test_rag_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Correr la suite completa (no rompimos nada)**

Run: `cd backend && . .venv/bin/activate && python -m pytest -q`
Expected: todos PASS (los existentes + 2 nuevos).

- [ ] **Step 6: Commit**

```bash
git add backend/obs_backend/sources/rag_pipeline.py backend/tests/test_rag_pipeline.py
git commit -m "fix(rag-pipeline): degradar a parcial + WARNING en error de transporte (C-F1)"
```

---

## Task 3: F1 — Deploy y verificación de la caída del ruido  `[GATE]`

**Files:** `k8s/deployment.yaml` (bump de tag).

- [ ] **Step 1: Mergear los commits de F1 al default y buildear**

Requiere OK del usuario para el deploy. Con el default (`feat/fase1a-backend-logs`) ya con F1:

```bash
TAG=$(git rev-parse --short HEAD)
IMAGE=us-east1-docker.pkg.dev/itmind-macro-auto-0/observability/obs-backend:$TAG
docker buildx build --platform linux/amd64 --provenance=false -f backend/Dockerfile -t "$IMAGE" --push .
```

- [ ] **Step 2: Rolling update `[GATE]`**

```bash
CTX=connectgateway_itmind-macro-auto-0_global_macro-auto-gke
kubectl --context $CTX -n observability set image deployment/obs-backend obs-backend=$IMAGE
kubectl --context $CTX -n observability rollout status deployment/obs-backend --timeout=120s
```

- [ ] **Step 3: Bump `deployment.yaml` al tag nuevo + commit**

Editar `k8s/deployment.yaml` (línea `image:`) al `$TAG`, luego:
```bash
git add k8s/deployment.yaml && git commit -m "chore(deploy): bump image tag a $TAG — C-F1 (menos ruido ERROR)"
```

- [ ] **Step 4: Verificar la caída (esperar ~15 min de tráfico)**

```bash
gcloud logging read 'resource.labels.container_name="obs-backend" AND severity>=ERROR' \
  --project=itmind-macro-auto-0 --freshness=1h --limit=1000 --format='value(severity)' | wc -l
gcloud logging read 'resource.labels.container_name="obs-backend" AND severity=WARNING AND textPayload:"inalcanzable"' \
  --project=itmind-macro-auto-0 --freshness=1h --limit=1000 --format='value(severity)' | wc -l
```
Expected: ERROR cae a ~0 (antes ~37/h); el WARNING "inalcanzable" refleja el mismo evento (registrado, sin traceback).

---

## Task 4: F2 — Exclusion filter de gcs-sync, creado `disabled=true`  (infra repo)

**Files:**
- Create: `fast/tenants/macro/3-project-factory/logging-exclusions.tf` (confirmar en Step 1 que 3-project-factory es dueño del proyecto ai-dev; si no, mover al stage/módulo correcto).

- [ ] **Step 1: Confirmar el dueño del proyecto y los permisos**

Run: `grep -rln "itmind-macro-ai-dev-0\|project_id\|module.*project" fast/tenants/macro/3-project-factory/*.tf | head`
Verificar que el stage cree/administre `itmind-macro-ai-dev-0` y que su SA tenga `roles/logging.configWriter` (o `roles/logging.admin`) sobre el proyecto. Si el dueño es otro stage, ahí va el recurso.

- [ ] **Step 2: Escribir el recurso `disabled=true`**

En `fast/tenants/macro/3-project-factory/logging-exclusions.tf`:

```hcl
# C-F2: excluir del sink _Default el ruido-progreso de gcs-sync (gsutil rsync de DAGs),
# que Cloud Logging tagea ERROR por escribirse a stderr sin severity estructurada.
# Content-specific y conservador: SOLO los mensajes de progreso, no source-wide.
# Se crea disabled=true; se activa (Step del Task 5) tras validar el match real.
resource "google_logging_project_exclusion" "gcs_sync_progress_dev" {
  name        = "gcs-sync-progress-noise"
  project     = "itmind-macro-ai-dev-0"
  description = "C-F2: progreso benigno de gcs-sync mal clasificado como ERROR (spec 2026-08-25)"
  disabled    = true

  filter = <<-EOT
    resource.type="k8s_container"
    AND resource.labels.container_name="gcs-sync"
    AND severity>=ERROR
    AND (textPayload:"Building synchronization state" OR textPayload:"Starting synchronization")
  EOT
}
```

- [ ] **Step 3: `terraform plan` (validar sintaxis del recurso, sin aplicar)**

Run (dir del stage): `terraform init` (si hace falta) `&& terraform plan -var-file=envs/dev.tfvars`
Expected: `1 to add` (la exclusion, disabled). Revisar que no arrastre drift ajeno.

- [ ] **Step 4: Commit (código, sin apply)**

```bash
git add fast/tenants/macro/3-project-factory/logging-exclusions.tf
git commit -m "feat(logging): exclusion filter gcs-sync progreso, disabled (C-F2)"
```

---

## Task 5: F2 — Medir el match real y activar  `[GATE]`

- [ ] **Step 1: Medir qué matchearía el filtro (antes de borrar nada)**

Run:
```bash
gcloud logging read 'resource.type="k8s_container" AND resource.labels.container_name="gcs-sync" AND severity>=ERROR AND (textPayload:"Building synchronization state" OR textPayload:"Starting synchronization")' \
  --project=itmind-macro-ai-dev-0 --freshness=1d --limit=500 --format='value(textPayload)' | sort | uniq -c | sort -rn
```
Expected: solo mensajes de progreso benignos (Building/Starting synchronization). **Si aparece cualquier mensaje que no sea progreso, PARAR** y ajustar el filtro (no activar).

- [ ] **Step 2: Activar (`disabled=false`) + `terraform apply` `[GATE]`**

Editar el recurso: `disabled = false`. Luego, con aprobación explícita del usuario:
```bash
terraform apply -target=google_logging_project_exclusion.gcs_sync_progress_dev -var-file=envs/dev.tfvars
```

- [ ] **Step 3: Commit inmediato del apply**

```bash
git add fast/tenants/macro/3-project-factory/logging-exclusions.tf
git commit -m "feat(logging): activar exclusion gcs-sync progreso en dev (C-F2)"
```

- [ ] **Step 4: Verificar la caída sin pérdida de señal**

Run (esperar ~30 min):
```bash
gcloud logging read 'resource.labels.container_name="gcs-sync" AND severity>=ERROR' \
  --project=itmind-macro-ai-dev-0 --freshness=1h --limit=500 --format='value(textPayload)' | wc -l
```
Expected: cae a ~0. Si quedan ERROR de gcs-sync que NO son progreso, es señal real que el filtro (correctamente) no tocó.

- [ ] **Step 5: (opcional) Replicar a qa/prod/auto**

Si dev valida bien, agregar recursos análogos por proyecto donde gcs-sync produzca el mismo ruido (medir cada uno con el Step 1 antes de activar). Cada apply es un `[GATE]`.

---

## Task 6: F3 — Auditoría de alert policies broad-severity  (infra repo)

**Files:**
- Create: `scripts/audit-alert-severity.sh`

- [ ] **Step 1: Escribir el script de auditoría**

En `scripts/audit-alert-severity.sh`:

```bash
#!/usr/bin/env bash
# C-F3: lista las condiciones de las google_monitoring_alert_policy que filtran por
# severity SIN scoping por content/metric (candidatas a broad-severity = disparan sobre ruido).
set -euo pipefail
cd "$(dirname "$0")/.."
grep -rEn 'filter|query' fast/ --include=*.tf \
  | grep -iE 'severity *[>=]' \
  | grep -viE 'metric\.type|jsonPayload\.|textPayload:|resource\.labels\.(container_name|pod_name)|reason *=' \
  || echo "OK: ninguna policy filtra por severity sin scoping por content/metric."
```

- [ ] **Step 2: Correr la auditoría**

Run: `chmod +x scripts/audit-alert-severity.sh && ./scripts/audit-alert-severity.sh`
Expected: idealmente el mensaje "OK: ninguna...". Si lista alguna línea, es una policy broad-severity a re-scopear.

- [ ] **Step 3: Re-scopear las que aparezcan (si hay)**

Por cada policy listada: acotar su `filter` a `content/metric` (agregar `AND (textPayload:"<patrón>" OR jsonPayload.reason="<x>")` o cambiar a un log-metric). Un test = re-correr el script y que ya no la liste. Cambios con aprobación (`terraform apply` `[GATE]`).

- [ ] **Step 4: Commit**

```bash
git add scripts/audit-alert-severity.sh
git commit -m "chore(alerting): script de auditoría de policies broad-severity (C-F3)"
```

---

## Self-Review (cobertura del spec)

- **F1 panel (890)** → Tasks 1-3. ✓ langfuse + rag_pipeline capturan TransportError; rag_admin excluido a propósito (ya captura).
- **F2 exclusion filter selectivo** → Tasks 4-5. ✓ solo gcs-sync, disabled→medir→activar, content-specific.
- **F3 auditoría** → Task 6. ✓ script + re-scoping condicional.
- **Corte señal/ruido (TransportError sí, HTTPStatusError no)** → constraint global + Steps de Task 1/2. ✓
- **Fuera de alcance** (conectividad panel↔langfuse/RAG, source-wide ArgoCD/cilium, dead-policies) → no aparece como tarea. ✓
- **Gates de apply/deploy** → marcados `[GATE]` en Tasks 3, 5. ✓
