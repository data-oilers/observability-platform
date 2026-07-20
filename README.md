# observability-platform

Plataforma de observabilidad para **macro**: panel único (app + infra + Langfuse) con switch **QA/PROD**, y un **agente** que interpreta logs. Read-only y **aislada de lo que observa** (corre en `itmind-macro-auto-0`).

## Componentes

- `backend/` — API FastAPI read-only (Cloud Logging/Monitoring + Langfuse). **Fase 1.A: pipeline de logs.**
- `frontend/` — el panel (Fase 1.B).
- `agent/` — agente de interpretación sobre Vertex/Gemini, `us-central1` (Fase 2).
- `k8s/` — manifests / chart para ArgoCD.

### Panel Admin (mirror read-only)

Tab que reproduce el panel de admin del RAG (Supervisión, Reportería, Modelos,
Prompts, Identidad) para DEV/QA, sin login. El backend proxea la API del RAG con
un **service-JWT read-only por entorno** (`RAG_OBS_TOKEN_DEV` / `_QA`, secret
`obs-rag-tokens`).

> ⚠️ **Excepción al principio rector.** Este feature hace que obs sostenga una
> identidad contra lo observado. NO desplegar sin: (1) ratificación del owner del
> charter de obs; (2) service-user sembrado con roles `reporteria`+`analistas`
> (+`gsi` si Identidad) en el DB del RAG DEV/QA; (3) secret `obs-rag-tokens`
> provisionado en `itmind-infrastructure` (`fast/tenants/macro/observability/`).
> Renovación del JWT: es un token firmado con la clave del cluster (patrón
> eval-fixture); definir TTL y rotación en infra.

## Infra

Las identidades, IAM, Vertex, secrets y el acceso por Tailscale viven en `itmind-infrastructure`
(stage `fast/tenants/macro/observability/`). Spec y planes:
`itmind-infrastructure/docs/superpowers/`.

> Principio rector: **la plataforma observa; nunca comparte identidad ni recursos con lo que observa.**
