"""WorkloadSource — detección de problemas de workload via kube-state-metrics en GMP.

Lee métricas KSM expuestas en Google Managed Prometheus (Cloud Monitoring).
Defensivo: cualquier serie con shape inesperado se saltea silenciosamente.
Read-only: solo list_time_series.
"""
from __future__ import annotations

import logging
from typing import Callable

from google.cloud import monitoring_v3

from obs_backend.config import ENVIRONMENTS, project_for
from obs_backend.models import PodIssue, PvcIssue, ReplicaShortfall, WorkloadHealth
from obs_backend.sources._gmp import build_request, get_label, latest_value

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric type constants (prometheus.googleapis.com/<name>/gauge)
# ---------------------------------------------------------------------------
_BASE = "prometheus.googleapis.com"
_WAITING_REASON   = f"{_BASE}/kube_pod_container_status_waiting_reason/gauge"
_POD_PHASE        = f"{_BASE}/kube_pod_status_phase/gauge"
_UNSCHEDULABLE    = f"{_BASE}/kube_pod_status_unschedulable/gauge"
_DEPLOY_DESIRED   = f"{_BASE}/kube_deployment_spec_replicas/gauge"
_DEPLOY_AVAILABLE = f"{_BASE}/kube_deployment_status_replicas_available/gauge"
_STS_DESIRED      = f"{_BASE}/kube_statefulset_replicas/gauge"
_STS_AVAILABLE    = f"{_BASE}/kube_statefulset_status_replicas_ready/gauge"
_PVC_PHASE        = f"{_BASE}/kube_persistentvolumeclaim_status_phase/gauge"

# Severity order: index 0 = most severe. Lower index wins in dedup.
_SEVERITY_ORDER = [
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "InvalidImageName",
    "Unschedulable",
    "Failed",
    "Unknown",
    "Pending",
]

# These are normal transient states — not problems.
_WAITING_REASON_EXCLUDE = {"ContainerCreating", "PodInitializing"}

# Phase values that indicate a problem.
_PROBLEM_PHASES = {"Pending", "Failed", "Unknown"}

# PVC phases that indicate a problem.
_PROBLEM_PVC_PHASES = {"Pending", "Lost"}


def _default_client_factory() -> object:
    return monitoring_v3.MetricServiceClient()


def _severity_rank(problem: str) -> int:
    """Return severity rank (lower = more severe). Unknown problems rank last."""
    try:
        return _SEVERITY_ORDER.index(problem)
    except ValueError:
        return len(_SEVERITY_ORDER)


class WorkloadSource:
    """Conector read-only a kube-state-metrics via GMP. Detecta pods problemáticos,
    shortfalls de réplicas y PVCs en mal estado.
    """

    def __init__(
        self,
        client_factory: Callable[[], object] = _default_client_factory,
    ):
        self._client_factory = client_factory

    def health(self, env: str) -> WorkloadHealth:
        project = project_for(env)
        allowed_ns: set[str] = set(ENVIRONMENTS[env]["namespaces"])
        client = self._client_factory()
        ALIGN = monitoring_v3.Aggregation.Aligner

        errors: list[str] = []

        def _fetch(metric_type: str, aligner) -> list:
            try:
                return list(client.list_time_series(
                    request=build_request(project, metric_type, aligner)
                ))
            except Exception:
                _log.warning("workload: fallo leyendo %s", metric_type, exc_info=True)
                errors.append(metric_type)
                return []

        # ----------------------------------------------------------------
        # Fetch all metrics (one call each)
        # STATE metrics (0/1 gauge): ALIGN_MAX — stickiness is deliberate.
        # Un pod que crasheó hace <5 min se reporta aunque ahora esté sano —
        # deliberado para detección; la UI lo encuadra como 'visto en últimos 5 min'.
        # COUNT metrics (replica gauges): ALIGN_NEXT_OLDER — valor puntual más
        # reciente (true current value); evita que MEAN suavice dips transitorios.
        # ----------------------------------------------------------------
        waiting_series   = _fetch(_WAITING_REASON,   ALIGN.ALIGN_MAX)
        phase_series     = _fetch(_POD_PHASE,         ALIGN.ALIGN_MAX)
        unsched_series   = _fetch(_UNSCHEDULABLE,     ALIGN.ALIGN_MAX)
        dep_des_series   = _fetch(_DEPLOY_DESIRED,    ALIGN.ALIGN_NEXT_OLDER)
        dep_avail_series = _fetch(_DEPLOY_AVAILABLE,  ALIGN.ALIGN_NEXT_OLDER)
        sts_des_series   = _fetch(_STS_DESIRED,       ALIGN.ALIGN_NEXT_OLDER)
        sts_avail_series = _fetch(_STS_AVAILABLE,     ALIGN.ALIGN_NEXT_OLDER)
        pvc_series       = _fetch(_PVC_PHASE,         ALIGN.ALIGN_MAX)

        # ----------------------------------------------------------------
        # Pod issues — accumulate candidates, then dedup by severity
        # pod_candidates: (namespace, pod) → list of (problem, detail)
        # ----------------------------------------------------------------
        # Using a list per key so we can pick the most severe at the end.
        pod_candidates: dict[tuple[str, str], list[tuple[str, str]]] = {}

        def _add_pod_candidate(ns: str, pod: str, problem: str, detail: str = "") -> None:
            key = (ns, pod)
            pod_candidates.setdefault(key, []).append((problem, detail))

        # Waiting reasons
        for series in waiting_series:
            val = latest_value(series)
            if val is None or val < 1:
                continue
            ns = get_label(series, "namespace")
            pod = get_label(series, "pod")
            reason = get_label(series, "reason")
            if not ns or not pod or not reason:
                continue
            if ns not in allowed_ns:
                continue
            if reason in _WAITING_REASON_EXCLUDE:
                continue
            container = get_label(series, "container") or ""
            _add_pod_candidate(ns, pod, reason, container)

        # Pod phase
        for series in phase_series:
            val = latest_value(series)
            if val is None or val < 1:
                continue
            ns = get_label(series, "namespace")
            pod = get_label(series, "pod")
            phase = get_label(series, "phase")
            if not ns or not pod or not phase:
                continue
            if ns not in allowed_ns:
                continue
            if phase not in _PROBLEM_PHASES:
                continue
            _add_pod_candidate(ns, pod, phase)

        # Unschedulable
        for series in unsched_series:
            val = latest_value(series)
            if val is None or val < 1:
                continue
            ns = get_label(series, "namespace")
            pod = get_label(series, "pod")
            if not ns or not pod:
                continue
            if ns not in allowed_ns:
                continue
            _add_pod_candidate(ns, pod, "Unschedulable")

        # Dedup: pick most severe problem per (ns, pod)
        pod_issues: list[PodIssue] = []
        for (ns, pod), candidates in pod_candidates.items():
            best_problem, best_detail = min(
                candidates, key=lambda c: _severity_rank(c[0])
            )
            pod_issues.append(PodIssue(
                namespace=ns, pod=pod, problem=best_problem, detail=best_detail
            ))

        # ----------------------------------------------------------------
        # Replica shortfalls — Deployments
        # ----------------------------------------------------------------
        dep_desired: dict[tuple[str, str], int] = {}
        dep_available: dict[tuple[str, str], int] = {}

        for series in dep_des_series:
            ns = get_label(series, "namespace")
            dep = get_label(series, "deployment")
            if not ns or not dep or ns not in allowed_ns:
                continue
            val = latest_value(series)
            if val is None:
                continue
            dep_desired[(ns, dep)] = round(val)

        for series in dep_avail_series:
            ns = get_label(series, "namespace")
            dep = get_label(series, "deployment")
            if not ns or not dep or ns not in allowed_ns:
                continue
            val = latest_value(series)
            if val is None:
                continue
            dep_available[(ns, dep)] = round(val)

        replica_shortfalls: list[ReplicaShortfall] = []

        for (ns, dep), desired in dep_desired.items():
            if desired == 0:
                continue
            if (ns, dep) not in dep_available:
                # available series absent (scrape gap/KSM churn) — unknown, don't alarm
                continue
            available = dep_available[(ns, dep)]
            if available < desired:
                replica_shortfalls.append(ReplicaShortfall(
                    kind="Deployment", name=dep, namespace=ns,
                    desired=desired, available=available,
                ))

        # ----------------------------------------------------------------
        # Replica shortfalls — StatefulSets
        # ----------------------------------------------------------------
        sts_desired: dict[tuple[str, str], int] = {}
        sts_available: dict[tuple[str, str], int] = {}

        for series in sts_des_series:
            ns = get_label(series, "namespace")
            sts = get_label(series, "statefulset")
            if not ns or not sts or ns not in allowed_ns:
                continue
            val = latest_value(series)
            if val is None:
                continue
            sts_desired[(ns, sts)] = round(val)

        for series in sts_avail_series:
            ns = get_label(series, "namespace")
            sts = get_label(series, "statefulset")
            if not ns or not sts or ns not in allowed_ns:
                continue
            val = latest_value(series)
            if val is None:
                continue
            sts_available[(ns, sts)] = round(val)

        for (ns, sts), desired in sts_desired.items():
            if desired == 0:
                continue
            if (ns, sts) not in sts_available:
                # available series absent (scrape gap/KSM churn) — unknown, don't alarm
                continue
            available = sts_available[(ns, sts)]
            if available < desired:
                replica_shortfalls.append(ReplicaShortfall(
                    kind="StatefulSet", name=sts, namespace=ns,
                    desired=desired, available=available,
                ))

        # ----------------------------------------------------------------
        # PVC issues
        # ----------------------------------------------------------------
        pvc_issues: list[PvcIssue] = []

        for series in pvc_series:
            val = latest_value(series)
            if val is None or val < 1:
                continue
            ns = get_label(series, "namespace")
            name = get_label(series, "persistentvolumeclaim")
            phase = get_label(series, "phase")
            if not ns or not name or not phase:
                continue
            if ns not in allowed_ns:
                continue
            if phase not in _PROBLEM_PVC_PHASES:
                continue
            pvc_issues.append(PvcIssue(namespace=ns, name=name, phase=phase))

        return WorkloadHealth(
            pod_issues=pod_issues,
            replica_shortfalls=replica_shortfalls,
            pvc_issues=pvc_issues,
            errors=errors,
        )
