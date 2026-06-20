from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException, Query

from obs_backend.config import ENVIRONMENTS
from obs_backend.health import summarize
from obs_backend.latency import build_latency_summary
from obs_backend.models import HealthSummary, InfraSnapshot, K8sEvent, LatencySummary, LogEvent, RagNodeStat, Trace, WorkloadHealth
from obs_backend.sources.base import EventsSource, LogSource, MetricsSource, RagPipelineSource, TraceSource, WorkloadSource

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
    from obs_backend.sources.rag_pipeline import RagPipelineSource as _RagPipelineSourceImpl  # noqa: F401
    from obs_backend.sources.workload import WorkloadSource as _WorkloadSourceImpl  # noqa: F401


def create_app(
    log_source: LogSource | None = None,
    trace_source: TraceSource | None = None,
    metrics_source: MetricsSource | None = None,
    workload_source: WorkloadSource | None = None,
    events_source: EventsSource | None = None,
    rag_source: RagPipelineSource | None = None,
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

    def _check_env(env: str) -> None:
        if env not in ENVIRONMENTS:
            raise HTTPException(status_code=404, detail=f"entorno desconocido: {env}")

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
        limit: int = Query(20, ge=1, le=200),
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

    # Serve the frontend as a static site when OBS_FRONTEND_DIR is set.
    # Mounted last so API routes always take precedence.
    frontend_dir = os.environ.get("OBS_FRONTEND_DIR")
    if frontend_dir and Path(frontend_dir).is_dir():
        from fastapi.staticfiles import StaticFiles  # noqa: PLC0415

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
