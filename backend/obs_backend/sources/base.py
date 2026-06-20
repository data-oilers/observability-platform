from typing import Protocol

from obs_backend.models import InfraSnapshot, K8sEvent, LogEvent, RagNodeStat, Trace, WorkloadHealth


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


class WorkloadSource(Protocol):
    def health(self, env: str) -> WorkloadHealth:
        """Devuelve el estado de workloads (pods con problemas, réplicas, PVCs) para el entorno."""
        ...


class EventsSource(Protocol):
    def recent_warnings(self, env: str, limit: int = 50, since_minutes: int = 60) -> list[K8sEvent]:
        """Devuelve los K8s Warning events recientes (más nuevos primero) para el entorno."""
        ...


class RagPipelineSource(Protocol):
    def rag_node_stats(self, env: str, since_minutes: int = 60) -> list[RagNodeStat]:
        """Returns per-node stats from Langfuse observations, for RAG topology overlay."""
        ...
