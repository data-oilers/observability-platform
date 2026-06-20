"""MonitoringSource — conector read-only a Cloud Monitoring (GKE metrics).

Defensivo ante el sistema observado: cualquier shape inesperado en la
respuesta degrada a skipped/None/empty en vez de propagar una excepción.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Callable

from google.cloud import monitoring_v3

from obs_backend.config import ENVIRONMENTS, project_for
from obs_backend.models import InfraSnapshot, NodeStat, PodStat


# ---------------------------------------------------------------------------
# Metric type constants
# ---------------------------------------------------------------------------
_CPU_NODE = "kubernetes.io/node/cpu/allocatable_utilization"
_MEM_NODE = "kubernetes.io/node/memory/allocatable_utilization"
_CPU_POD = "kubernetes.io/container/cpu/core_usage_time"
_MEM_POD = "kubernetes.io/container/memory/used_bytes"
_RESTART_POD = "kubernetes.io/container/restart_count"


# ---------------------------------------------------------------------------
# Default client factory (uses ADC / Workload Identity in cluster)
# ---------------------------------------------------------------------------
def _default_client_factory() -> object:
    return monitoring_v3.MetricServiceClient()


# ---------------------------------------------------------------------------
# Value extraction — correct for both real proto-plus TypedValue and duck-typed
# test fakes. See contract comment in the task spec for why this is non-trivial.
# ---------------------------------------------------------------------------
def _latest_value(series) -> float | None:
    """Return the numeric value of the most recent aligned point, or None.

    Handles:
    - real proto-plus TypedValue (uses WhichOneof to avoid the 0.0 trap)
    - duck-typed SimpleNamespace fakes (attr presence check)
    - series with no points → None
    """
    points = getattr(series, "points", None) or []
    if not points:
        return None
    first = points[0]
    v = getattr(first, "value", None)
    if v is None:
        return None

    pb = getattr(type(v), "pb", None)          # real proto-plus TypedValue
    if pb is not None:
        field = pb(v).WhichOneof("value")
        if field is None:
            return None
        raw = getattr(v, field)
        return float(raw) if isinstance(raw, (int, float)) else None

    # Duck-typed fake: probe by attribute presence (NOT truthiness — 0.0 is valid)
    for attr in ("double_value", "int64_value"):
        if hasattr(v, attr):
            raw = getattr(v, attr)
            return float(raw) if isinstance(raw, (int, float)) else None
    return None


# ---------------------------------------------------------------------------
# MonitoringSource
# ---------------------------------------------------------------------------
class MonitoringSource:
    """Conector read-only a Cloud Monitoring. Lee métricas GKE de los últimos 5 min.

    Usa un único punto alineado por serie (alignment_period=300s).
    """

    def __init__(
        self,
        client_factory: Callable[[], object] = _default_client_factory,
    ):
        self._client_factory = client_factory

    def snapshot(self, env: str) -> InfraSnapshot:
        project = project_for(env)
        app_ns = ENVIRONMENTS[env]["app_namespace"]
        client = self._client_factory()

        now = datetime.now(timezone.utc)
        interval = monitoring_v3.TimeInterval({
            "end_time": {"seconds": int(now.timestamp())},
            "start_time": {"seconds": int((now - timedelta(minutes=5)).timestamp())},
        })

        def _fetch(mtype: str, aligner, ns_filter: bool) -> list:
            """Execute one list_time_series call; return an empty list on any error."""
            aggregation = monitoring_v3.Aggregation({
                "alignment_period": {"seconds": 300},
                "per_series_aligner": aligner,
            })
            flt = f'metric.type="{mtype}"'
            if ns_filter:
                flt += f' AND resource.labels.namespace_name="{app_ns}"'
            try:
                return list(client.list_time_series(request={
                    "name": f"projects/{project}",
                    "filter": flt,
                    "interval": interval,
                    "aggregation": aggregation,
                    "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                }))
            except Exception:
                return []

        ALIGN = monitoring_v3.Aggregation.Aligner

        # Cinco llamadas secuenciales (una por métrica). Intencional y suficiente para
        # un snapshot puntual; candidato a paralelizar (asyncio.gather) o a un filtro
        # combinado si la latencia de /infra llegara a importar.
        node_cpu_series   = _fetch(_CPU_NODE,    ALIGN.ALIGN_MEAN, ns_filter=False)
        node_mem_series   = _fetch(_MEM_NODE,    ALIGN.ALIGN_MEAN, ns_filter=False)
        pod_cpu_series    = _fetch(_CPU_POD,     ALIGN.ALIGN_RATE, ns_filter=True)
        pod_mem_series    = _fetch(_MEM_POD,     ALIGN.ALIGN_MEAN, ns_filter=True)
        pod_restart_series = _fetch(_RESTART_POD, ALIGN.ALIGN_MAX,  ns_filter=True)

        # --- Build node stats ---
        # nodes_map: node_name → NodeStat (mutable dict, then convert to list)
        nodes_map: dict[str, NodeStat] = {}

        for series in node_cpu_series:
            name = _label(series, "node_name")
            if name is None:
                continue
            val = _latest_value(series)
            if val is None:
                # Series present but no usable point: ensure node entry exists
                nodes_map.setdefault(name, NodeStat(name=name))
                continue
            node = nodes_map.setdefault(name, NodeStat(name=name))
            nodes_map[name] = node.model_copy(update={"cpu_pct": val * 100})

        for series in node_mem_series:
            name = _label(series, "node_name")
            if name is None:
                continue
            val = _latest_value(series)
            if val is None:
                nodes_map.setdefault(name, NodeStat(name=name))
                continue
            node = nodes_map.setdefault(name, NodeStat(name=name))
            nodes_map[name] = node.model_copy(update={"mem_pct": val * 100})

        # --- Build pod stats ---
        # pods_map: pod_name → dict accumulator (for summing multi-container fields)
        pods_acc: dict[str, dict] = {}

        def _pod_acc(pod_name: str, ns: str) -> dict:
            if pod_name not in pods_acc:
                pods_acc[pod_name] = {"name": pod_name, "namespace": ns,
                                      "cpu_cores": None, "mem_bytes": None, "restarts": None}
            return pods_acc[pod_name]

        for series in pod_cpu_series:
            pod_name = _label(series, "pod_name")
            if pod_name is None:
                continue
            val = _latest_value(series)
            if val is None:
                continue
            ns = _pod_ns(series)
            acc = _pod_acc(pod_name, ns)
            acc["cpu_cores"] = (0.0 if acc["cpu_cores"] is None else acc["cpu_cores"]) + val

        for series in pod_mem_series:
            pod_name = _label(series, "pod_name")
            if pod_name is None:
                continue
            val = _latest_value(series)
            if val is None:
                continue
            ns = _pod_ns(series)
            acc = _pod_acc(pod_name, ns)
            acc["mem_bytes"] = (0.0 if acc["mem_bytes"] is None else acc["mem_bytes"]) + val

        for series in pod_restart_series:
            pod_name = _label(series, "pod_name")
            if pod_name is None:
                continue
            val = _latest_value(series)
            if val is None:
                continue
            ns = _pod_ns(series)
            acc = _pod_acc(pod_name, ns)
            acc["restarts"] = (0 if acc["restarts"] is None else acc["restarts"]) + int(val)

        nodes = list(nodes_map.values())
        pods = [PodStat(**acc) for acc in pods_acc.values()]

        return InfraSnapshot(
            nodes=nodes,
            pods=pods,
            node_count=len(nodes),
            pod_count=len(pods),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _label(series, key: str) -> str | None:
    """Extract a resource label from a series; return None if missing."""
    labels = getattr(getattr(series, "resource", None), "labels", None)
    if not isinstance(labels, Mapping):
        return None
    return labels.get(key) or None


def _pod_ns(series) -> str:
    """Extract namespace_name from pod series resource labels."""
    labels = getattr(getattr(series, "resource", None), "labels", None)
    if isinstance(labels, Mapping):
        return labels.get("namespace_name", "")
    return ""
