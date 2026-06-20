# observability-platform

Plataforma de observabilidad para **macro**: panel único (app + infra + Langfuse) con switch **QA/PROD**, y un **agente** que interpreta logs. Read-only y **aislada de lo que observa** (corre en `itmind-macro-auto-0`).

## Componentes

- `backend/` — API FastAPI read-only (Cloud Logging/Monitoring + Langfuse). **Fase 1.A: pipeline de logs.**
- `frontend/` — el panel (Fase 1.B).
- `agent/` — agente de interpretación sobre Vertex/Gemini, `us-central1` (Fase 2).
- `k8s/` — manifests / chart para ArgoCD.

## Infra

Las identidades, IAM, Vertex, secrets y el acceso por Tailscale viven en `itmind-infrastructure`
(stage `fast/tenants/macro/observability/`). Spec y planes:
`itmind-infrastructure/docs/superpowers/`.

> Principio rector: **la plataforma observa; nunca comparte identidad ni recursos con lo que observa.**
