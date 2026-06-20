"""Tests for WorkloadSource (task 1.A-3).

Mock strategy: duck-typed fakes mirroring test_monitoring.py.
KSM metrics live in metric.labels (NOT resource.labels for object fields).
resource.labels holds project/location/cluster/job/instance + scrape namespace.
"""
from __future__ import annotations

import types
from types import SimpleNamespace

import pytest

from obs_backend.sources.workload import WorkloadSource


# ---------------------------------------------------------------------------
# Metric type constants (prometheus.googleapis.com/<name>/gauge)
# ---------------------------------------------------------------------------
_BASE = "prometheus.googleapis.com"
WAITING_REASON   = f"{_BASE}/kube_pod_container_status_waiting_reason/gauge"
POD_PHASE        = f"{_BASE}/kube_pod_status_phase/gauge"
UNSCHEDULABLE    = f"{_BASE}/kube_pod_status_unschedulable/gauge"
DEPLOY_DESIRED   = f"{_BASE}/kube_deployment_spec_replicas/gauge"
DEPLOY_AVAILABLE = f"{_BASE}/kube_deployment_status_replicas_available/gauge"
STS_DESIRED      = f"{_BASE}/kube_statefulset_replicas/gauge"
STS_AVAILABLE    = f"{_BASE}/kube_statefulset_status_replicas_ready/gauge"
PVC_PHASE        = f"{_BASE}/kube_persistentvolumeclaim_status_phase/gauge"


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

class _FakeWorkloadClient:
    """Routes list_time_series by metric.type in the filter string."""

    def __init__(self, series_by_metric: dict):
        self._by = series_by_metric

    def list_time_series(self, request=None, **kw):
        req = request or kw
        flt = req["filter"]
        mtype = flt.split('metric.type = "', 1)[1].split('"', 1)[0]
        return self._by.get(mtype, [])


def _make_source(series_by_metric: dict) -> WorkloadSource:
    client = _FakeWorkloadClient(series_by_metric)
    return WorkloadSource(client_factory=lambda: client)


# ---------------------------------------------------------------------------
# Fake series builders
# ---------------------------------------------------------------------------

def _kube_series(
    metric_labels: dict,
    double_value: float,
    resource_ns: str = "enterprise-ai",
) -> SimpleNamespace:
    """Build a KSM-style series: metric.labels holds the object fields."""
    v = SimpleNamespace(double_value=double_value)
    point = SimpleNamespace(value=v)
    return SimpleNamespace(
        metric=SimpleNamespace(labels=metric_labels),
        resource=SimpleNamespace(labels={
            "namespace": resource_ns,  # scrape namespace (should NOT override metric.labels)
            "cluster": "gke-qa",
        }),
        points=[point],
    )


def _empty_series_map() -> dict:
    """Return a map with all metrics empty (all dimensions return [])."""
    return {
        WAITING_REASON:   [],
        POD_PHASE:        [],
        UNSCHEDULABLE:    [],
        DEPLOY_DESIRED:   [],
        DEPLOY_AVAILABLE: [],
        STS_DESIRED:      [],
        STS_AVAILABLE:    [],
        PVC_PHASE:        [],
    }


# ---------------------------------------------------------------------------
# Pod issues — waiting reason (CrashLoopBackOff)
# ---------------------------------------------------------------------------

def test_waiting_reason_crashloop_produces_pod_issue():
    """CrashLoopBackOff waiting reason → PodIssue with problem=CrashLoopBackOff."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "api-7f9b-x", "container": "api",
             "reason": "CrashLoopBackOff"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.pod_issues) == 1
    issue = result.pod_issues[0]
    assert issue.namespace == "enterprise-ai"
    assert issue.pod == "api-7f9b-x"
    assert issue.problem == "CrashLoopBackOff"
    assert issue.detail == "api"   # container name


def test_waiting_reason_imagepullbackoff_produces_pod_issue():
    """ImagePullBackOff waiting reason → PodIssue with problem=ImagePullBackOff."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "worker-abc", "container": "worker",
             "reason": "ImagePullBackOff"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.pod_issues) == 1
    assert result.pod_issues[0].problem == "ImagePullBackOff"


def test_waiting_reason_zero_value_skipped():
    """A waiting_reason series with value < 1 is not an active problem."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "api-xyz", "container": "api",
             "reason": "CrashLoopBackOff"},
            double_value=0.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


def test_waiting_reason_container_creating_excluded():
    """ContainerCreating is normal startup — must NOT produce a PodIssue."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "api-xyz", "container": "api",
             "reason": "ContainerCreating"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


def test_waiting_reason_pod_initializing_excluded():
    """PodInitializing is normal startup — must NOT produce a PodIssue."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "init-pod", "container": "init",
             "reason": "PodInitializing"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


# ---------------------------------------------------------------------------
# Pod issues — phase (Pending/Failed/Unknown flagged; Running/Succeeded not)
# ---------------------------------------------------------------------------

def test_pod_phase_pending_produces_issue():
    """Phase=Pending → PodIssue with problem=Pending."""
    m = _empty_series_map()
    m[POD_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "slow-pod", "phase": "Pending"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert any(i.pod == "slow-pod" and i.problem == "Pending" for i in result.pod_issues)


def test_pod_phase_failed_produces_issue():
    """Phase=Failed → PodIssue with problem=Failed."""
    m = _empty_series_map()
    m[POD_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "dead-pod", "phase": "Failed"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert any(i.pod == "dead-pod" and i.problem == "Failed" for i in result.pod_issues)


def test_pod_phase_running_ignored():
    """Phase=Running with value=1 must NOT produce a PodIssue."""
    m = _empty_series_map()
    m[POD_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "healthy-pod", "phase": "Running"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


def test_pod_phase_succeeded_ignored():
    """Phase=Succeeded must NOT produce a PodIssue."""
    m = _empty_series_map()
    m[POD_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "job-pod", "phase": "Succeeded"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


# ---------------------------------------------------------------------------
# Pod issues — unschedulable
# ---------------------------------------------------------------------------

def test_unschedulable_produces_pod_issue():
    """kube_pod_status_unschedulable value≥1 → PodIssue with problem=Unschedulable."""
    m = _empty_series_map()
    m[UNSCHEDULABLE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "stuck-pod"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert any(i.pod == "stuck-pod" and i.problem == "Unschedulable" for i in result.pod_issues)


# ---------------------------------------------------------------------------
# Severity dedup: same pod in two sources → only most severe wins
# ---------------------------------------------------------------------------

def test_severity_dedup_crashloop_beats_pending():
    """A pod with both Pending (phase) and CrashLoopBackOff (reason) → single PodIssue = CrashLoopBackOff."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "bad-pod", "container": "app",
             "reason": "CrashLoopBackOff"},
            double_value=1.0,
        )
    ]
    m[POD_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "bad-pod", "phase": "Pending"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    pod_issues = [i for i in result.pod_issues if i.pod == "bad-pod"]
    assert len(pod_issues) == 1, "Must emit exactly one PodIssue per (namespace, pod)"
    assert pod_issues[0].problem == "CrashLoopBackOff"


def test_severity_dedup_imagepull_beats_pending():
    """ImagePullBackOff > Pending in severity ordering."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "x-pod", "container": "c",
             "reason": "ImagePullBackOff"},
            double_value=1.0,
        )
    ]
    m[POD_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "x-pod", "phase": "Pending"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    pod_issues = [i for i in result.pod_issues if i.pod == "x-pod"]
    assert len(pod_issues) == 1
    assert pod_issues[0].problem == "ImagePullBackOff"


# ---------------------------------------------------------------------------
# Namespace scoping
# ---------------------------------------------------------------------------

def test_pod_in_kube_system_ignored():
    """kube-system namespace is outside ENVIRONMENTS[qa]['namespaces'] — must be ignored."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "kube-system", "pod": "coredns-abc", "container": "coredns",
             "reason": "CrashLoopBackOff"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


def test_metric_label_namespace_wins_over_resource_label_namespace():
    """metric.labels.namespace (object ns) must win over resource.labels.namespace (scrape ns).

    The series has metric.labels.namespace='enterprise-ai' (in scope) but
    resource.labels.namespace='kube-system' (out of scope).  The pod must appear.
    """
    m = _empty_series_map()
    # Build the series manually: metric.labels.namespace=enterprise-ai, resource.labels.namespace=kube-system
    v = SimpleNamespace(double_value=1.0)
    point = SimpleNamespace(value=v)
    series = SimpleNamespace(
        metric=SimpleNamespace(labels={
            "namespace": "enterprise-ai",
            "pod": "real-pod",
            "container": "c",
            "reason": "CrashLoopBackOff",
        }),
        resource=SimpleNamespace(labels={
            "namespace": "kube-system",  # scrape target — should NOT win
            "cluster": "gke-qa",
        }),
        points=[point],
    )
    m[WAITING_REASON] = [series]
    src = _make_source(m)
    result = src.health("qa")
    assert any(i.pod == "real-pod" for i in result.pod_issues), (
        "metric.labels.namespace must take precedence over resource.labels.namespace"
    )


# ---------------------------------------------------------------------------
# Replica shortfalls — Deployments
# ---------------------------------------------------------------------------

def test_deployment_shortfall_available_less_than_desired():
    """available=1 < desired=3 → ReplicaShortfall for that deployment."""
    m = _empty_series_map()
    m[DEPLOY_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "api-deploy"},
            double_value=3.0,
        )
    ]
    m[DEPLOY_AVAILABLE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "api-deploy"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.replica_shortfalls) == 1
    sf = result.replica_shortfalls[0]
    assert sf.kind == "Deployment"
    assert sf.name == "api-deploy"
    assert sf.namespace == "enterprise-ai"
    assert sf.desired == 3
    assert sf.available == 1


def test_deployment_no_shortfall_when_available_equals_desired():
    """available==desired → no shortfall."""
    m = _empty_series_map()
    m[DEPLOY_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "api-deploy"},
            double_value=2.0,
        )
    ]
    m[DEPLOY_AVAILABLE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "api-deploy"},
            double_value=2.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.replica_shortfalls == []


def test_deployment_desired_zero_is_not_a_shortfall():
    """desired=0 (scaled down) must NOT produce a shortfall even if available=0."""
    m = _empty_series_map()
    m[DEPLOY_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "idle-deploy"},
            double_value=0.0,
        )
    ]
    # No available series at all
    src = _make_source(m)
    result = src.health("qa")
    assert result.replica_shortfalls == []


def test_deployment_missing_available_series_no_shortfall():
    """If the available series is absent (scrape gap / KSM churn), no shortfall is emitted.

    A genuine 0/N outage still produces an available-series with value 0 (KSM exports it),
    so absence means unknown — not down.
    """
    m = _empty_series_map()
    m[DEPLOY_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "new-deploy"},
            double_value=2.0,
        )
    ]
    # DEPLOY_AVAILABLE is empty for this deployment — series absent, not zero
    src = _make_source(m)
    result = src.health("qa")
    assert result.replica_shortfalls == []


def test_deployment_available_present_zero_is_shortfall():
    """available-series present with value 0, desired=N → shortfall with available=0."""
    m = _empty_series_map()
    m[DEPLOY_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "broken-deploy"},
            double_value=3.0,
        )
    ]
    m[DEPLOY_AVAILABLE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "deployment": "broken-deploy"},
            double_value=0.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.replica_shortfalls) == 1
    sf = result.replica_shortfalls[0]
    assert sf.available == 0
    assert sf.desired == 3


# ---------------------------------------------------------------------------
# Replica shortfalls — StatefulSets
# ---------------------------------------------------------------------------

def test_statefulset_shortfall():
    """available=0 < desired=1 → ReplicaShortfall kind=StatefulSet."""
    m = _empty_series_map()
    m[STS_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "statefulset": "redis"},
            double_value=1.0,
        )
    ]
    m[STS_AVAILABLE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "statefulset": "redis"},
            double_value=0.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.replica_shortfalls) == 1
    sf = result.replica_shortfalls[0]
    assert sf.kind == "StatefulSet"
    assert sf.name == "redis"
    assert sf.desired == 1
    assert sf.available == 0


def test_statefulset_no_shortfall_when_healthy():
    """StatefulSet with available==desired → no shortfall."""
    m = _empty_series_map()
    m[STS_DESIRED] = [
        _kube_series(
            {"namespace": "enterprise-ai", "statefulset": "kafka"},
            double_value=3.0,
        )
    ]
    m[STS_AVAILABLE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "statefulset": "kafka"},
            double_value=3.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.replica_shortfalls == []


# ---------------------------------------------------------------------------
# PVC issues
# ---------------------------------------------------------------------------

def test_pvc_pending_produces_issue():
    """PVC phase=Pending → PvcIssue."""
    m = _empty_series_map()
    m[PVC_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "persistentvolumeclaim": "data-pvc",
             "phase": "Pending"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.pvc_issues) == 1
    pvc = result.pvc_issues[0]
    assert pvc.namespace == "enterprise-ai"
    assert pvc.name == "data-pvc"
    assert pvc.phase == "Pending"


def test_pvc_lost_produces_issue():
    """PVC phase=Lost → PvcIssue."""
    m = _empty_series_map()
    m[PVC_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "persistentvolumeclaim": "lost-pvc",
             "phase": "Lost"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert any(p.name == "lost-pvc" and p.phase == "Lost" for p in result.pvc_issues)


def test_pvc_bound_not_an_issue():
    """PVC phase=Bound (normal) must NOT produce a PvcIssue."""
    m = _empty_series_map()
    m[PVC_PHASE] = [
        _kube_series(
            {"namespace": "enterprise-ai", "persistentvolumeclaim": "ok-pvc",
             "phase": "Bound"},
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pvc_issues == []


# ---------------------------------------------------------------------------
# Defensive: series missing expected label or points → skip, no exception
# ---------------------------------------------------------------------------

def test_waiting_reason_series_missing_pod_label_skipped():
    """Series without 'pod' in metric.labels is silently skipped."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "container": "api", "reason": "CrashLoopBackOff"},
            # no 'pod' key
            double_value=1.0,
        )
    ]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


def test_series_without_points_skipped():
    """Series with empty points list is silently skipped, no exception."""
    m = _empty_series_map()
    no_points = SimpleNamespace(
        metric=SimpleNamespace(labels={
            "namespace": "enterprise-ai", "pod": "test-pod", "container": "c",
            "reason": "CrashLoopBackOff",
        }),
        resource=SimpleNamespace(labels={}),
        points=[],
    )
    m[WAITING_REASON] = [no_points]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == []


def test_metric_returning_empty_list_partial_result():
    """If one metric returns [] (e.g. UNSCHEDULABLE), other dimensions still built."""
    m = _empty_series_map()
    m[WAITING_REASON] = [
        _kube_series(
            {"namespace": "enterprise-ai", "pod": "api-xyz", "container": "api",
             "reason": "CrashLoopBackOff"},
            double_value=1.0,
        )
    ]
    # UNSCHEDULABLE is already [] in empty map — should not affect pod_issues
    src = _make_source(m)
    result = src.health("qa")
    assert len(result.pod_issues) == 1
    assert result.pvc_issues == []
    assert result.replica_shortfalls == []


def test_all_metrics_empty_returns_empty_workload_health():
    """All metrics empty → valid WorkloadHealth with all empty lists."""
    src = _make_source(_empty_series_map())
    result = src.health("qa")
    assert result.pod_issues == []
    assert result.replica_shortfalls == []
    assert result.pvc_issues == []


# ---------------------------------------------------------------------------
# I2 — errors field: partial/blind reads are visible, not silent all-clear
# ---------------------------------------------------------------------------

class _RaisingClient:
    """Client that raises for one metric type, returns empty for the rest."""

    def __init__(self, raise_for: str):
        self._raise_for = raise_for

    def list_time_series(self, request=None, **kw):
        req = request or kw
        flt = req["filter"]
        mtype = flt.split('metric.type = "', 1)[1].split('"', 1)[0]
        if mtype == self._raise_for:
            raise PermissionError("simulated auth failure")
        return []


def test_fetch_failure_populates_errors_field():
    """A PermissionError on one metric → WorkloadHealth.errors lists that metric type."""
    client = _RaisingClient(raise_for=WAITING_REASON)
    src = WorkloadSource(client_factory=lambda: client)
    result = src.health("qa")
    assert WAITING_REASON in result.errors, (
        "errors must list metric types that failed to fetch"
    )


def test_fetch_failure_does_not_block_other_dimensions():
    """A failure on WAITING_REASON still lets pod-phase and pvc issues through."""
    # Build a client that raises for WAITING_REASON but returns a real PVC series
    class _PartialClient:
        def list_time_series(self, request=None, **kw):
            req = request or kw
            flt = req["filter"]
            mtype = flt.split('metric.type = "', 1)[1].split('"', 1)[0]
            if mtype == WAITING_REASON:
                raise RuntimeError("quota exceeded")
            if mtype == PVC_PHASE:
                return [_kube_series(
                    {"namespace": "enterprise-ai", "persistentvolumeclaim": "err-pvc",
                     "phase": "Lost"},
                    double_value=1.0,
                )]
            return []

    src = WorkloadSource(client_factory=lambda: _PartialClient())
    result = src.health("qa")
    assert WAITING_REASON in result.errors
    assert any(p.name == "err-pvc" for p in result.pvc_issues), (
        "other dimensions must still be built when one metric fetch fails"
    )


def test_no_errors_on_clean_run():
    """All-empty but successful fetches → errors list is empty."""
    src = _make_source(_empty_series_map())
    result = src.health("qa")
    assert result.errors == []


# ---------------------------------------------------------------------------
# M3 — MappingProxyType labels are parsed correctly (Mapping, not dict)
# ---------------------------------------------------------------------------

def test_mapping_proxy_labels_parsed_correctly():
    """metric.labels as MappingProxyType (not a plain dict) must still be read.

    Mirrors test_monitoring.py's MappingProxyType test for _gmp.get_label.
    """
    proxy_labels = types.MappingProxyType({
        "namespace": "enterprise-ai",
        "pod": "proxy-pod",
        "phase": "Pending",
    })
    v = SimpleNamespace(double_value=1.0)
    point = SimpleNamespace(value=v)
    series = SimpleNamespace(
        metric=SimpleNamespace(labels=proxy_labels),
        resource=SimpleNamespace(labels={}),
        points=[point],
    )
    m = _empty_series_map()
    m[POD_PHASE] = [series]
    src = _make_source(m)
    result = src.health("qa")
    assert any(i.pod == "proxy-pod" and i.problem == "Pending" for i in result.pod_issues), (
        "MappingProxyType metric.labels must be parsed the same as plain dict"
    )


# ---------------------------------------------------------------------------
# M4 — points[0] with no .value attribute → treated as None, no crash
# ---------------------------------------------------------------------------

def test_point_without_value_attribute_does_not_crash():
    """A series whose points[0] has no .value attr → latest_value returns None → skip."""
    point_no_value = SimpleNamespace()  # no .value attribute at all
    series = SimpleNamespace(
        metric=SimpleNamespace(labels={
            "namespace": "enterprise-ai",
            "pod": "ghost-pod",
            "phase": "Pending",
        }),
        resource=SimpleNamespace(labels={}),
        points=[point_no_value],
    )
    m = _empty_series_map()
    m[POD_PHASE] = [series]
    src = _make_source(m)
    result = src.health("qa")
    assert result.pod_issues == [], (
        "a point with no .value attribute must not produce a PodIssue or raise"
    )
