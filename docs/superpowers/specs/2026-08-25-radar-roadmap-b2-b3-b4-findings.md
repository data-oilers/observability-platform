# Roadmap radar B-2/B-3/B-4 — hallazgos verificados y decisiones

**Fecha:** 2026-08-25
**Repo:** `observability-platform` (+ infra en `itmind-infrastructure`)
**Estado:** hallazgos verificados; B-4 lista para aprobación, B-2/B-3 bloqueadas por prerrequisito de infra
**Contexto:** continuación de `2026-08-25-radar-platform-namespaces-design.md` (B-1, ya implementado).

## TL;DR

El plan original asumía que B-2/B-3/B-4 eran, como B-1, "ampliar un filtro con datos que ya
se traen". **Verificado que no.** Ninguna es código del panel: las tres están gateadas por infra/IAM.

| Item | Gate real (verificado) | Código del panel hoy |
|---|---|---|
| B-2 ECM | ECM vive en **proyecto+cluster aparte** (`itmind-macro-ecm-{env}-0`); workload aún **no corre** | Código muerto hasta que ECM corra + haya IAM |
| B-3 ArgoCD | GMP de `auto-0` **no tiene** métricas `argocd_*` (COUNT 0) | Observaría nada hasta que exista scrape o token |
| B-4 PROD | Front+back **ya soportan prod**; falta solo grant IAM read-only sobre proyecto PROD del banco | **Cero** — es un one-liner de infra |

## B-4 — habilitar PROD  → LISTA, requiere OK escrito para aplicar

**Verificado:**
- Backend: `_check_env` acepta cualquier env en `ENVIRONMENTS`; `dev/qa/prod` los tres válidos.
  El único gate prod es el **panel admin** (`_check_admin_env` bloquea prod, línea 112 `api.py`) — correcto, se mantiene.
- Frontend: `index.html` ya tiene el botón `PROD` cableado igual que QA, banner `prod-only` "solo lectura", y `VALID_ENVS`/switch ya incluyen prod. **Sin cambios de front.**
- Gate real: `fast/tenants/macro/observability/terraform/terraform.tfvars`
  → `observed_projects = ["itmind-macro-ai-dev-0", "itmind-macro-ai-qa-0"]`.
  `cross-project-iam.tf` hace `for_each = toset(var.observed_projects)` y otorga
  `roles/monitoring.viewer` + `roles/logging.viewer` al SA `obs-backend@itmind-macro-auto-0`.

**Cambio:** agregar `"itmind-macro-ai-prod-0"` a `observed_projects`. El `for_each` crea 2 recursos:
- `google_project_iam_member.obs_backend_monitoring_viewer["itmind-macro-ai-prod-0"]`
- `google_project_iam_member.obs_backend_logging_viewer["itmind-macro-ai-prod-0"]`

**Naturaleza:** grant **read-only** (viewer) sobre el proyecto **PROD del banco**. Reversible.
Efecto colateral esperado y deseable: el panel prod prenderá los issues reales de PROD que
hoy no se ven (external-secrets `Pending`, system-pool roto desde 2026-08-14) — que es
justamente para lo que B-1 amplió el radar.

**Regla que aplica:** CLAUDE.md #2 — cambios sobre PROD del banco requieren **confirmación
escrita**, no inferencia. Aunque sea viewer, toca el proyecto del banco. **No se aplica sin OK
explícito.** El apply va con `plan -out` + auditoría de drift previo (el stage no se aplica
hace tiempo; ver `feedback_audit_drift_before_apply`).

## B-3 — source de ArgoCD/GitOps  → BLOQUEADA, necesita decisión de prerrequisito

**Verificado (2026-08-25):** `metricDescriptors` de `itmind-macro-auto-0` filtrando
`prometheus.googleapis.com/argocd*` → **COUNT 0**. Confirma la memoria
`argocd_outofsync_alert_no_cheap_path`: **ArgoCD no se scrapea a GMP**. No hay dato read-only
que leer con el patrón actual del panel.

Caminos posibles (los tres necesitan algo *antes* del código del panel):

1. **Scrape ArgoCD → GMP (recomendado).** Un `PodMonitoring`/`ClusterPodMonitoring` en el
   cluster `auto` seleccionando `argocd-*-metrics` (ArgoCD ya expone `/metrics`). Luego la source
   es una copia del patrón GMP de `workload.py` (`argocd_app_info{sync_status,health_status}`),
   sin dependencia nueva ni token. **Mantiene el principio read-only / sin identidad compartida.**
   Prerrequisito: aplicar 1 manifiesto en `auto` (decidir si vive en este repo `k8s/` o en el
   stage `automation`).
2. **API de ArgoCD con token read-only.** Rompe el principio rector ("nunca comparte identidad
   con lo que observa"). Descartado salvo pedido explícito.
3. **Leer los CRD `Application` vía k8s API in-cluster + RBAC read-only.** El backend corre en
   `auto`. Introduce un patrón nuevo (el panel hoy no habla k8s API directo; todo es GMP +
   Cloud Logging) y una dependencia de cliente k8s. Más código que (1).

**Decisión pendiente del usuario** (fue diferida una vez ya). Recomendación: (1). No se escribe
la source hasta que exista el scrape — sería código contra un dato inexistente.

## B-2 — namespace ECM al radar  → BLOQUEADA, prematura

**Verificado:**
- ECM containerizado (stage `9-deploy-ecm`, commit #246) corre en **proyecto propio**
  `itmind-macro-ecm-{env}-0` y cluster propio, **no** en `itmind-macro-ai-{env}-0` (que es lo que
  lee `WorkloadSource` vía `project_for(env)`). ns objetivo = `"ecm"` (`dev.tfvars:200`).
- Los clusters de app dev/qa **no** tienen ns `ecm`; **no hay contexto kubectl al cluster ECM**
  (sin creds todavía). El workload aún no está desplegado — el stage aplicó infra, no la app.

**Por lo tanto:** sumar `"ecm"` a la lista de namespaces de app **no haría nada** — el panel
consultaría el GMP del proyecto AI, y las métricas del ns `ecm` viven en el GMP del proyecto ECM.
Observar ECM = **sumar un proyecto observado nuevo** (mismo shape que B-4): grant IAM cross-project
del SA sobre `itmind-macro-ecm-{dev,qa}-0` + modelar ECM como target propio por entorno (hoy
`WorkloadSource` asume 1 proyecto por env).

**Decisión:** **diferir** hasta que el ECM containerizado esté efectivamente corriendo. Escribir
la source multi-proyecto ahora es generalidad especulativa (YAGNI) contra un workload inexistente.
Retomar cuando ECM dev tenga pods vivos; ahí se decide IAM + modelado.

## Qué se hizo en esta iteración

- B-4: preparado el diff de infra (1 línea en `terraform.tfvars`), documentado el efecto exacto;
  **no aplicado** — espera OK escrito.
- B-3, B-2: **no se escribió código** (sería código muerto). Documentado el prerrequisito real de
  cada una y la recomendación.

## C — reducir el ruido (sin cambios; sigue como estaba)

Fuera de alcance de esta iteración. Vive en `itmind-infrastructure`: exclusion filters en el sink
`_Default` + alinear alert policies a contenido/métrica en vez de `severity` cruda
(la severidad de GCP está mal clasificada, `log_severity_misclassification_0812`). El panel ya
esquiva esto por diseño (`classify.py` re-deriva severidad), así que C es higiene del canal de
alertas, no del panel.
