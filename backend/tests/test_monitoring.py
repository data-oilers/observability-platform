"""Tests for MonitoringSource (task 1.A-2b).

All GCP calls are mocked via duck-typed fakes — no real GCP contact.
Follows the same patterns as test_cloud_logging.py and test_langfuse.py.
"""
import types
from types import SimpleNamespace, MappingProxyType

import pytest

from obs_backend.sources.monitoring import MonitoringSource, _latest_value


# ---------------------------------------------------------------------------
# Fake client helpers
# ---------------------------------------------------------------------------

class _FakeMetricClient:
    """Routes list_time_series calls by metric.type found in the filter string."""

    def __init__(self, series_by_metric: dict):
        self._by = series_by_metric
        self.requests: list = []

    def list_time_series(self, request=None, **kw):
        req = request or kw
        self.requests.append(req)
        flt = req["filter"]
        mtype = flt.split('metric.type="', 1)[1].split('"', 1)[0]
        return self._by.get(mtype, [])


def _node_series(node_name: str, double_value: float) -> SimpleNamespace:
    """Fake series for a node metric (resource label: node_name)."""
    v = SimpleNamespace(double_value=double_value)
    point = SimpleNamespace(value=v)
    return SimpleNamespace(
        resource=SimpleNamespace(labels={"node_name": node_name}),
        metric=SimpleNamespace(type="", labels={}),
        points=[point],
    )


def _pod_series(pod_name: str, namespace: str, double_value: float) -> SimpleNamespace:
    """Fake series for a pod/container metric (resource label: pod_name)."""
    v = SimpleNamespace(double_value=double_value)
    point = SimpleNamespace(value=v)
    return SimpleNamespace(
        resource=SimpleNamespace(labels={"pod_name": pod_name, "namespace_name": namespace}),
        metric=SimpleNamespace(type="", labels={}),
        points=[point],
    )


def _pod_series_int(pod_name: str, namespace: str, int64_value: int) -> SimpleNamespace:
    """Fake series with int64_value (e.g. restart_count)."""
    v = SimpleNamespace(int64_value=int64_value)
    point = SimpleNamespace(value=v)
    return SimpleNamespace(
        resource=SimpleNamespace(labels={"pod_name": pod_name, "namespace_name": namespace}),
        metric=SimpleNamespace(type="", labels={}),
        points=[point],
    )


def _make_source(series_by_metric: dict) -> MonitoringSource:
    client = _FakeMetricClient(series_by_metric)
    return MonitoringSource(client_factory=lambda: client), client


# ---------------------------------------------------------------------------
# _latest_value unit tests
# ---------------------------------------------------------------------------

def test_latest_value_double():
    """double_value extracted correctly from duck-typed fake."""
    v = SimpleNamespace(double_value=0.42)
    point = SimpleNamespace(value=v)
    series = SimpleNamespace(points=[point])
    assert _latest_value(series) == pytest.approx(0.42)


def test_latest_value_int64():
    """int64_value extracted correctly from duck-typed fake."""
    v = SimpleNamespace(int64_value=3)
    point = SimpleNamespace(value=v)
    series = SimpleNamespace(points=[point])
    assert _latest_value(series) == pytest.approx(3.0)


def test_latest_value_zero_preserved():
    """0.0 utilization must NOT be dropped — truthiness trap check."""
    v = SimpleNamespace(double_value=0.0)
    point = SimpleNamespace(value=v)
    series = SimpleNamespace(points=[point])
    result = _latest_value(series)
    assert result is not None
    assert result == pytest.approx(0.0)


def test_latest_value_empty_points():
    """Series with no points returns None (skipped)."""
    series = SimpleNamespace(points=[])
    assert _latest_value(series) is None


def test_latest_value_no_points_attr():
    """Series without points attribute returns None."""
    series = SimpleNamespace()
    assert _latest_value(series) is None


# ---------------------------------------------------------------------------
# Node cpu/mem mapping
# ---------------------------------------------------------------------------

CPU_NODE = "kubernetes.io/node/cpu/allocatable_utilization"
MEM_NODE = "kubernetes.io/node/memory/allocatable_utilization"
CPU_POD = "kubernetes.io/container/cpu/core_usage_time"
MEM_POD = "kubernetes.io/container/memory/used_bytes"
RESTART_POD = "kubernetes.io/container/restart_count"


def test_node_cpu_pct_mapped():
    """0.42 raw value → 42.0% for cpu_pct."""
    src, _ = _make_source({
        CPU_NODE: [_node_series("n1", 0.42)],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert len(snap.nodes) == 1
    assert snap.nodes[0].name == "n1"
    assert snap.nodes[0].cpu_pct == pytest.approx(42.0)
    assert snap.nodes[0].mem_pct is None


def test_node_mem_pct_mapped():
    """0.80 raw value → 80.0% for mem_pct."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [_node_series("n1", 0.80)],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert len(snap.nodes) == 1
    assert snap.nodes[0].name == "n1"
    assert snap.nodes[0].mem_pct == pytest.approx(80.0)
    assert snap.nodes[0].cpu_pct is None


def test_node_cpu_and_mem_merged():
    """Same node appearing in both metrics is merged into one NodeStat."""
    src, _ = _make_source({
        CPU_NODE: [_node_series("n1", 0.50)],
        MEM_NODE: [_node_series("n1", 0.70)],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert len(snap.nodes) == 1
    assert snap.nodes[0].cpu_pct == pytest.approx(50.0)
    assert snap.nodes[0].mem_pct == pytest.approx(70.0)


def test_node_zero_utilization_preserved():
    """0.0 cpu_pct must appear as 0.0, not None (no truthiness drop)."""
    src, _ = _make_source({
        CPU_NODE: [_node_series("n1", 0.0)],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert snap.nodes[0].cpu_pct is not None
    assert snap.nodes[0].cpu_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Pod cpu/mem/restarts mapping
# ---------------------------------------------------------------------------

def test_pod_cpu_cores_mapped():
    """Pod cpu_cores mapped as-is (no scaling)."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [_pod_series("pod-a", "enterprise-ai", 0.25)],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-a")
    assert pod.cpu_cores == pytest.approx(0.25)


def test_pod_mem_bytes_mapped():
    """Pod mem_bytes mapped as-is."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [_pod_series("pod-a", "enterprise-ai", 1048576.0)],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-a")
    assert pod.mem_bytes == pytest.approx(1048576.0)


def test_pod_restarts_mapped():
    """Pod restart_count mapped as int."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [_pod_series_int("pod-a", "enterprise-ai", 3)],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-a")
    assert pod.restarts == 3


def test_pod_namespace_set():
    """Pod namespace is populated from resource.labels.namespace_name."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [_pod_series("pod-x", "enterprise-ai", 0.1)],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-x")
    assert pod.namespace == "enterprise-ai"


# ---------------------------------------------------------------------------
# Multi-container pod: fields are summed per pod
# ---------------------------------------------------------------------------

def test_multicontainer_pod_cpu_summed():
    """Two containers for same pod → cpu_cores summed."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [
            _pod_series("pod-a", "enterprise-ai", 0.3),
            _pod_series("pod-a", "enterprise-ai", 0.2),
        ],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-a")
    assert pod.cpu_cores == pytest.approx(0.5)


def test_multicontainer_pod_mem_summed():
    """Two containers for same pod → mem_bytes summed."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [
            _pod_series("pod-a", "enterprise-ai", 500000.0),
            _pod_series("pod-a", "enterprise-ai", 300000.0),
        ],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-a")
    assert pod.mem_bytes == pytest.approx(800000.0)


def test_multicontainer_pod_restarts_summed():
    """Two containers for same pod → restarts summed."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [
            _pod_series_int("pod-a", "enterprise-ai", 2),
            _pod_series_int("pod-a", "enterprise-ai", 1),
        ],
    })
    snap = src.snapshot("qa")
    pod = next(p for p in snap.pods if p.name == "pod-a")
    assert pod.restarts == 3


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def test_node_count():
    """node_count equals number of distinct nodes."""
    src, _ = _make_source({
        CPU_NODE: [_node_series("n1", 0.3), _node_series("n2", 0.4)],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert snap.node_count == 2


def test_pod_count():
    """pod_count equals number of distinct pods."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [
            _pod_series("pod-a", "enterprise-ai", 0.1),
            _pod_series("pod-b", "enterprise-ai", 0.2),
        ],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert snap.pod_count == 2


# ---------------------------------------------------------------------------
# Defensive cases
# ---------------------------------------------------------------------------

def test_series_with_empty_points_skipped():
    """A series with no points is silently skipped, not an error."""
    empty_series = SimpleNamespace(
        resource=SimpleNamespace(labels={"node_name": "n1"}),
        metric=SimpleNamespace(type="", labels={}),
        points=[],
    )
    src, _ = _make_source({
        CPU_NODE: [empty_series],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    # n1 exists but cpu_pct is None (no valid value)
    assert all(n.cpu_pct is None for n in snap.nodes if n.name == "n1")


def test_series_missing_group_label_skipped():
    """A series missing the group label (e.g. node_name) is silently skipped."""
    bad_series = SimpleNamespace(
        resource=SimpleNamespace(labels={}),  # no node_name key
        metric=SimpleNamespace(type="", labels={}),
        points=[SimpleNamespace(value=SimpleNamespace(double_value=0.5))],
    )
    src, _ = _make_source({
        CPU_NODE: [bad_series],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    # No nodes built from the bad series
    assert len(snap.nodes) == 0


def test_metric_returning_empty_list_degrades_gracefully():
    """If an entire metric call returns [], that dimension is empty — rest still built."""
    src, _ = _make_source({
        CPU_NODE: [_node_series("n1", 0.5)],
        MEM_NODE: [],           # empty — no mem_pct
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert len(snap.nodes) == 1
    assert snap.nodes[0].cpu_pct == pytest.approx(50.0)
    assert snap.nodes[0].mem_pct is None  # empty metric → None, not error


def test_all_metrics_empty_returns_empty_snapshot():
    """All metrics empty → valid InfraSnapshot with empty lists and zero counts."""
    src, _ = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert snap.nodes == []
    assert snap.pods == []
    assert snap.node_count == 0
    assert snap.pod_count == 0


def test_namespace_filter_sent_for_pod_metrics():
    """Pod metrics requests must include namespace filter for app_namespace."""
    src, client = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    src.snapshot("qa")
    pod_requests = [
        r for r in client.requests
        if any(m in r["filter"] for m in [CPU_POD, MEM_POD, RESTART_POD])
    ]
    for req in pod_requests:
        assert "enterprise-ai" in req["filter"], (
            f"Pod metric request missing namespace filter: {req['filter']}"
        )


def test_node_metrics_no_namespace_filter():
    """Node metrics requests must NOT include a namespace filter."""
    src, client = _make_source({
        CPU_NODE: [],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    src.snapshot("qa")
    node_requests = [
        r for r in client.requests
        if any(m in r["filter"] for m in [CPU_NODE, MEM_NODE])
    ]
    for req in node_requests:
        assert "namespace" not in req["filter"], (
            f"Node metric request should not have namespace filter: {req['filter']}"
        )


# ---------------------------------------------------------------------------
# M3 — Regression tests for real-path shapes (C1 / I2)
# ---------------------------------------------------------------------------

def test_non_dict_mapping_labels_not_skipped():
    """C1 regression: resource.labels is a MappingProxyType (a Mapping, not a dict).

    With the old isinstance(labels, dict) guard this series is silently skipped
    and no node appears.  After C1 the node must be present with cpu_pct == 42.0.
    """
    v = SimpleNamespace(double_value=0.42)
    point = SimpleNamespace(value=v)
    series = SimpleNamespace(
        resource=SimpleNamespace(labels=MappingProxyType({"node_name": "n1"})),
        metric=SimpleNamespace(type="", labels={}),
        points=[point],
    )
    src, _ = _make_source({
        CPU_NODE: [series],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")
    assert len(snap.nodes) == 1, "node n1 must be present (non-dict Mapping labels)"
    assert snap.nodes[0].name == "n1"
    assert snap.nodes[0].cpu_pct == pytest.approx(42.0)


def test_point_without_value_attr_degrades_gracefully():
    """I2 regression: points[0] has no .value attribute at all.

    With the unguarded points[0].value access this raises AttributeError → 500.
    After I2 snapshot() must complete without raising and that series is skipped
    (its field stays None) while the rest of the snapshot still builds.
    """
    # Series whose point has NO .value attribute (mimics a malformed proto object)
    bad_point = SimpleNamespace()  # no 'value' attr
    bad_series = SimpleNamespace(
        resource=SimpleNamespace(labels={"node_name": "bad-node"}),
        metric=SimpleNamespace(type="", labels={}),
        points=[bad_point],
    )
    good_series = _node_series("good-node", 0.5)
    src, _ = _make_source({
        CPU_NODE: [bad_series, good_series],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")  # must not raise
    good_node = next((n for n in snap.nodes if n.name == "good-node"), None)
    assert good_node is not None, "good-node must still be built"
    assert good_node.cpu_pct == pytest.approx(50.0)
    bad_node = next((n for n in snap.nodes if n.name == "bad-node"), None)
    # bad-node may be present (inserted by setdefault) but its cpu_pct must be None
    if bad_node is not None:
        assert bad_node.cpu_pct is None


def test_resource_none_skipped_gracefully():
    """Lock-in: series with resource=None is silently skipped, no exception raised."""
    series = SimpleNamespace(
        resource=None,
        metric=SimpleNamespace(type="", labels={}),
        points=[SimpleNamespace(value=SimpleNamespace(double_value=0.5))],
    )
    src, _ = _make_source({
        CPU_NODE: [series],
        MEM_NODE: [],
        CPU_POD: [],
        MEM_POD: [],
        RESTART_POD: [],
    })
    snap = src.snapshot("qa")  # must not raise
    assert len(snap.nodes) == 0
