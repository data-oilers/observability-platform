from typing import Protocol

from obs_backend.models import InfraSnapshot, LogEvent, Trace


class LogSource(Protocol):
    def recent(self, env: str, min_severity: str = "DEFAULT", limit: int = 100) -> list[LogEvent]:
        """Devuelve los logs recientes (más nuevos primero) del namespace de app."""
        ...


class TraceSource(Protocol):
    def recent_traces(self, env: str, limit: int = 20) -> list[Trace]:
        """Devuelve las trazas recientes (más nuevas primero) desde Langfuse."""
        ...


class MetricsSource(Protocol):
    def snapshot(self, env: str) -> InfraSnapshot:
        """Devuelve un snapshot de métricas de infra (nodos y pods) para el entorno."""
        ...
