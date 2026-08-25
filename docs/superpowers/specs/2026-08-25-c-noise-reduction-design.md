# Diseño — C: reducir el ruido de logs/alertas en origen

**Fecha:** 2026-08-25
**Repos:** `observability-platform` (Frente 1) + `itmind-infrastructure` (Frentes 2 y 3)
**Estado:** diseño propuesto, pendiente aprobación
**Alcance:** C del roadmap de observabilidad — reducir el "ruido" (logs `severity>=ERROR` que en
realidad son benignos) sin perder señal real.

## Problema

El campo `severity` de Cloud Logging está mal clasificado en la plataforma: componentes que escriben
a stderr terminan como `ERROR` aunque el contenido sea INFO (ver `log_severity_misclassification_0812`
en las memorias del repo de infra). Eso: (a) infla el volumen/costo de logs, (b) hace inútil cualquier
query cruda `severity>=ERROR`, (c) invalida alertas severity-based. El panel ya lo esquiva en display
(`classify.py` re-deriva la severidad del contenido), pero el ruido sigue en la fuente y es mala higiene.

## Medición (2026-08-25, `severity>=ERROR`, últimas 24h, muestra 1000/proyecto)

El "95%" **no es difuso**: está concentrado en pocas fuentes, y la #1 es el propio panel.

| Fuente | Vol. | Contenido real | Naturaleza |
|---|---|---|---|
| `observability/obs-backend` (auto-0) | **890** | `httpx.ConnectError: Name or service not known` + traceback Python completo | **Ruido auto-infligido del panel** — deja escapar errores de conexión a fuentes salientes (langfuse/RAG) y los logea como ERROR+traceback |
| `airflow/gcs-sync` (dev) | 169 | `"Building synchronization state…"`, `"Starting synchronization…"` | Misclasificación stderr→ERROR — progreso INFO benigno |
| `argocd` app-controller+repo-server (auto-0) | 105 | logs de ArgoCD | Misclasificación (ArgoCD logea ERROR por default) |
| `kube-system/cilium-agent` (dev) | 96 | dataplane GKE | Probablemente benigno |

**Hallazgos que reencuadran C:**
1. La fuente #1 es el panel mismo (890) → se arregla en **código del panel**, no con un filtro de infra.
2. Las 80 alert policies condicionan por **content/metric** (OOMKilling, `prometheus.googleapis.com/up`,
   cloudsql cpu/disk, log-metrics de airflow), **no** por severity cruda → el ruido **no** genera spam a
   canales; vive en los logs. El Frente 3 es auditoría, no rework.
3. No existe ningún `google_logging_project_exclusion` en el repo de infra hoy (las exclusiones actuales
   son del audit sink a nivel folder). Los filtros del Frente 2 serían recursos nuevos.

## Diseño (orden por volumen medido: #1 → #2 → #3)

### Frente 1 — Panel: eliminar los tracebacks auto-infligidos  *(repo `observability-platform`)*

**Qué:** los clientes de fuentes salientes (langfuse, RAG pipeline, RAG admin) dejan escapar
`httpx.ConnectError`/timeout, que se logea como ERROR con traceback completo (~890/día en auto-0).

**Cambio:** una frontera de error en las llamadas httpx salientes. Cada cliente (o un helper httpx
compartido) captura los errores de conexión/timeout esperables y:
- logea **un** `WARNING` conciso — `"<fuente> inalcanzable: <host> (<motivo>)"`, **sin traceback**;
- devuelve el sentinel de "no disponible" que los callers ya manejan (`None`/lista vacía → el panel
  marca la fuente como degradada, ya existe ese estado en el front).

No se **traga** la señal: el WARNING queda registrado y la fuente sigue mostrándose degradada; solo
se baja la severidad y se elimina el traceback repetido.

**Módulos candidatos** (a pinear en el plan): `sources/langfuse.py`, `sources/_langfuse.py`,
`sources/rag_pipeline.py`, `sources/rag_admin.py`, `sources/_rag.py`. Ver cuáles dejan propagar la
excepción hoy; centralizar en un `_safe_outbound_get` si hay repetición (DRY).

**Test (TDD):** cliente de fuente con httpx fake que levanta `ConnectError` → `recent()/get()`
devuelve el sentinel vacío **y** emite un `WARNING` (no ERROR, sin traceback). Un test por cliente
tocado.

**Riesgo:** nulo (es logging propio). Guardarraíl: no bajar a WARNING errores que **no** sean de
conexión/timeout (un 5xx real de la fuente sí es señal) — capturar solo `ConnectError`/`TimeoutException`,
no `HTTPStatusError`.

### Frente 2 — Exclusion filters de infra: selectivo y content-specific  *(repo `itmind-infrastructure`)*

**Qué:** `google_logging_project_exclusion` sobre el sink `_Default`, **por proyecto**, **solo** para
ruido-progreso con cero valor de señal. Caso confirmado: `gcs-sync` (169/día, 100% benigno).

**Filtro (content-specific, NO source-wide):**
```
resource.type="k8s_container"
AND resource.labels.container_name="gcs-sync"
AND severity>=ERROR
AND (textPayload:"Building synchronization state" OR textPayload:"Starting synchronization")
```

**Explícitamente NO se hace:** exclusión source-wide de ArgoCD/cilium — riesgo de tirar errores reales
enterrados en el stream mal-clasificado. Para display el panel ya los reclasifica; los logs se dejan.

**Dónde:** `google_logging_project_exclusion` es un recurso a nivel proyecto. Aplica a cada proyecto con
ruido (dev/qa/prod-ai + auto). Placement en terraform a decidir en el plan: candidato natural
`3-project-factory` (dueño de los proyectos) o un módulo `logging/` chico. Requiere
`roles/logging.configWriter` sobre cada proyecto.

**Riesgo:** una exclusión **borra** los logs que matchea (no se guardan). Mitigación: filtro
content-specific + **validar contra logs reales antes de aplicar** (ver `feedback_verify_alert_filters_against_real_data`:
`validate` acepta filtros que no matchean nunca). Se puede crear la exclusión con `disabled=true`
primero y medir qué matchearía, antes de activarla.

### Frente 3 — Alert policies: auditoría  *(repo `itmind-infrastructure`)*

**Qué:** confirmar que ninguna de las 80 `google_monitoring_alert_policy` condiciona sobre
`severity>=ERROR` broad (sin scoping por content/metric). La medición ya indica que son content/metric,
pero se cierra con un barrido explícito de las condiciones + spot-check.

**Acción:** grep/auditoría de los `filter`/`query` de las policies; si aparece alguna broad-severity,
removerla o re-scopearla a contenido. Estimado: **0-2 cambios**. No incluye limpieza de dead-policies
(higiene aparte).

## Plan de implementación

1. **Frente 1 (panel)** — TDD por cliente tocado; deploy con el flujo manual de `k8s/README.md`.
   Verificar caída del volumen ERROR de `obs-backend` en auto-0.
2. **Frente 2 (filters)** — spec de infra propia si hace falta (regla del repo de infra); crear la
   exclusión `disabled=true`, medir match real, activar; apply con aprobación.
3. **Frente 3 (audit)** — barrido de policies; cambios mínimos con aprobación.

## Fuera de alcance

- **Conectividad del panel a langfuse/RAG desde auto-0** (por qué no resuelve esos hosts): si *debería*
  poder alcanzarlos, sus features de traces/rag-nodes están degradadas — investigación aparte, no es
  ruido.
- Exclusiones source-wide de ArgoCD/cilium (riesgo de perder señal).
- Limpieza de dead-policies y re-scoping general de alerting (higiene separada).
- Corregir la severidad en ingestión (Cloud Logging no lo permite fácil para logs de plataforma; el
  lever práctico es exclusión + el `classify.py` que ya existe).

## Verificación

- **F1:** post-deploy, `severity>=ERROR AND container="obs-backend"` en auto-0 cae de ~890/día a ~0;
  aparece el conteo equivalente en WARNING (evento aún registrado, sin traceback).
- **F2:** post-apply, `severity>=ERROR AND container="gcs-sync"` en dev cae a ~0; el volumen total
  `severity>=ERROR` de dev baja ~169/día. Sin pérdida de errores reales (validado antes de activar).
- **F3:** la auditoría muestra 0 policies broad-severity (o las encontradas, corregidas).
