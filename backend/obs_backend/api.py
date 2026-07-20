from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException, Query

from obs_backend.config import ENVIRONMENTS
from obs_backend.health import summarize
from obs_backend.latency import build_latency_summary
from obs_backend.models import HealthSummary, InfraSnapshot, K8sEvent, LatencySummary, LogEvent, RagNodeStat, Trace, WorkloadHealth
from obs_backend.sources.base import EventsSource, LogSource, MetricsSource, RagAdminSource, RagPipelineSource, TraceSource, WorkloadSource

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

if TYPE_CHECKING:
    from obs_backend.sources.cloud_logging import CloudLoggingSource  # noqa: F401
    from obs_backend.sources.events import EventsSource as _EventsSourceImpl  # noqa: F401
    from obs_backend.sources.langfuse import LangfuseSource  # noqa: F401
    from obs_backend.sources.monitoring import MonitoringSource  # noqa: F401
    from obs_backend.sources.rag_admin import RagAdminSource as _RagAdminSourceImpl  # noqa: F401
    from obs_backend.sources.rag_pipeline import RagPipelineSource as _RagPipelineSourceImpl  # noqa: F401
    from obs_backend.sources.workload import WorkloadSource as _WorkloadSourceImpl  # noqa: F401


def create_app(
    log_source: LogSource | None = None,
    trace_source: TraceSource | None = None,
    metrics_source: MetricsSource | None = None,
    workload_source: WorkloadSource | None = None,
    events_source: EventsSource | None = None,
    rag_source: RagPipelineSource | None = None,
    rag_admin_source: RagAdminSource | None = None,
) -> FastAPI:
    app = FastAPI(title="obs·macro backend", version="0.1.0")

    @app.middleware("http")
    async def _security_headers(request, call_next):
        response = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    if log_source is None:
        # Import lazy: evita arrastrar google-cloud-logging cuando se pasa una
        # fuente explícita (tests, mocks).
        from obs_backend.sources.cloud_logging import CloudLoggingSource
        source: LogSource = CloudLoggingSource()
    else:
        source = log_source

    if trace_source is None:
        # Import lazy: evita arrastrar httpx cuando se pasa una fuente explícita.
        from obs_backend.sources.langfuse import LangfuseSource
        tsource: TraceSource = LangfuseSource()
    else:
        tsource = trace_source

    if metrics_source is None:
        # Import lazy: evita arrastrar google-cloud-monitoring cuando se pasa
        # una fuente explícita (tests, mocks).
        from obs_backend.sources.monitoring import MonitoringSource
        msource: MetricsSource = MonitoringSource()
    else:
        msource = metrics_source

    if workload_source is None:
        # Import lazy: evita arrastrar google-cloud-monitoring cuando se pasa
        # una fuente explícita (tests, mocks).
        from obs_backend.sources.workload import WorkloadSource as _WorkloadSourceImpl
        wsource: WorkloadSource = _WorkloadSourceImpl()
    else:
        wsource = workload_source

    if events_source is None:
        # Import lazy: evita arrastrar google-cloud-logging cuando se pasa
        # una fuente explícita (tests, mocks).
        from obs_backend.sources.events import EventsSource as _EventsSourceImpl
        esource: EventsSource = _EventsSourceImpl()
    else:
        esource = events_source

    if rag_source is None:
        # Import lazy: evita arrastrar httpx cuando se pasa una fuente explícita.
        from obs_backend.sources.rag_pipeline import RagPipelineSource as _RagPipelineSourceImpl
        rsource: RagPipelineSource = _RagPipelineSourceImpl()
    else:
        rsource = rag_source

    if rag_admin_source is None:
        from obs_backend.sources.rag_admin import RagAdminSource as _RagAdminSourceImpl
        adminsource: RagAdminSource = _RagAdminSourceImpl()
    else:
        adminsource = rag_admin_source

    def _check_env(env: str) -> None:
        if env not in ENVIRONMENTS:
            raise HTTPException(status_code=404, detail=f"entorno desconocido: {env}")

    def _check_admin_env(env: str) -> None:
        _check_env(env)
        if env == "prod":
            raise HTTPException(status_code=404, detail="panel admin no disponible en prod")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/{env}/logs", response_model=list[LogEvent])
    def logs(
        env: str,
        severity: Literal["DEFAULT", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEFAULT",
        limit: int = Query(100, ge=1, le=500),
    ) -> list[LogEvent]:
        _check_env(env)
        return source.recent(env, min_severity=severity, limit=limit)

    @app.get("/v1/{env}/health", response_model=HealthSummary)
    def health(env: str) -> HealthSummary:
        _check_env(env)
        return summarize(source.recent(env, min_severity="DEFAULT", limit=300))

    @app.get("/v1/{env}/traces", response_model=list[Trace])
    def traces(
        env: str,
        limit: int = Query(20, ge=1, le=100),  # Langfuse capa el API público en 100 (>100 -> HTTP 400)
    ) -> list[Trace]:
        _check_env(env)
        return tsource.recent_traces(env, limit=limit)

    @app.get("/v1/{env}/infra", response_model=InfraSnapshot)
    def infra(env: str) -> InfraSnapshot:
        _check_env(env)
        return msource.snapshot(env)

    @app.get("/v1/{env}/latency", response_model=LatencySummary)
    def latency(env: str) -> LatencySummary:
        _check_env(env)
        return build_latency_summary(env, source, tsource)

    @app.get("/v1/{env}/workloads", response_model=WorkloadHealth)
    def workloads(env: str) -> WorkloadHealth:
        _check_env(env)
        return wsource.health(env)

    @app.get("/v1/{env}/events", response_model=list[K8sEvent])
    def events(
        env: str,
        limit: int = Query(50, ge=1, le=200),
        since_minutes: int = Query(60, ge=1, le=1440),
    ) -> list[K8sEvent]:
        _check_env(env)
        return esource.recent_warnings(env, limit=limit, since_minutes=since_minutes)

    @app.get("/v1/{env}/rag-nodes", response_model=list[RagNodeStat])
    def rag_nodes(
        env: str,
        since_minutes: int = Query(60, ge=1, le=1440),
    ) -> list[RagNodeStat]:
        _check_env(env)
        return rsource.rag_node_stats(env, since_minutes=since_minutes)

    @app.get("/v1/{env}/admin/supervision/documents")
    def admin_supervision_documents(
        env: str,
        area: str | None = None,
        search: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        sort_by: str = "usage_desc",
    ) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/admin/governance/documents", params={
            "area": area, "search": search, "page": page,
            "page_size": page_size, "sort_by": sort_by,
        })

    @app.get("/v1/{env}/admin/supervision/documents/{document_id}/chunks")
    def admin_supervision_chunks(env: str, document_id: int) -> object:
        _check_admin_env(env)
        return adminsource.get(
            env, f"/api/v1/admin/governance/documents/{document_id}/chunks"
        )

    @app.get("/v1/{env}/admin/reporteria")
    def admin_reporteria(env: str, date_from: str, date_to: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/analytics/dashboard/executive",
                               params={"date_from": date_from, "date_to": date_to})

    @app.get("/v1/{env}/admin/modelos")
    def admin_modelos(env: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/admin/model-routing")

    @app.get("/v1/{env}/admin/prompts")
    def admin_prompts(env: str) -> object:
        _check_admin_env(env)
        return adminsource.get(env, "/api/v1/admin/prompts")

    # Serve the frontend as a static site when OBS_FRONTEND_DIR is set.
    # Mounted last so API routes always take precedence.
    frontend_dir = os.environ.get("OBS_FRONTEND_DIR")
    if frontend_dir and Path(frontend_dir).is_dir():
        from fastapi.staticfiles import StaticFiles  # noqa: PLC0415

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
