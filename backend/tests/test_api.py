from fastapi.testclient import TestClient

from obs_backend.api import create_app
from obs_backend.models import (
    InfraSnapshot, K8sEvent, LogEvent, NodeStat, PodStat, RagNodeStat, Trace,
    WorkloadHealth, PodIssue, ReplicaShortfall, PvcIssue,
)


class _FakeSource:
    def __init__(self, events):
        self._events = events

    def recent(self, env, min_severity="DEFAULT", limit=100):
        return self._events[:limit]


class _FakeTraceSource:
    def __init__(self, traces):
        self._traces = traces

    def recent_traces(self, env, limit=20):
        return self._traces[:limit]


def _client(events=None):
    return TestClient(create_app(log_source=_FakeSource(events or [])))


def _trace_client(traces=None):
    return TestClient(create_app(
        log_source=_FakeSource([]),
        trace_source=_FakeTraceSource(traces or []),
    ))


def test_healthz_ok():
    assert _client().get("/healthz").json() == {"status": "ok"}


def test_logs_unknown_env_404():
    assert _client().get("/v1/staging/logs").status_code == 404


def test_logs_returns_events():
    evs = [LogEvent(ts="t", severity="WARNING", pod="api", message="RAGAS OpenAIError")]
    resp = _client(evs).get("/v1/qa/logs")
    assert resp.status_code == 200
    assert resp.json()[0]["severity"] == "WARNING"


def test_health_warn_on_error():
    evs = [LogEvent(ts="t", severity="ERROR", pod="api", message="boom")]
    resp = _client(evs).get("/v1/qa/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "warn"


# ---------------------------------------------------------------------------
# /v1/{env}/traces
# ---------------------------------------------------------------------------

def test_traces_returns_list():
    trs = [Trace(id="t1", name="chat", ts="2026-06-19T10:00:00Z", latency_ms=250.0)]
    resp = _trace_client(trs).get("/v1/qa/traces")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "t1"
    assert data[0]["latency_ms"] == 250.0


def test_traces_unknown_env_404():
    resp = _trace_client().get("/v1/staging/traces")
    assert resp.status_code == 404


def test_traces_empty_list():
    resp = _trace_client([]).get("/v1/prod/traces")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# /v1/{env}/infra
# ---------------------------------------------------------------------------

class _FakeMetricsSource:
    def __init__(self, snapshot: InfraSnapshot):
        self._snapshot = snapshot

    def snapshot(self, env: str) -> InfraSnapshot:
        return self._snapshot


def _infra_client(snapshot: InfraSnapshot):
    return TestClient(create_app(
        log_source=_FakeSource([]),
        trace_source=_FakeTraceSource([]),
        metrics_source=_FakeMetricsSource(snapshot),
    ))


def test_infra_returns_snapshot():
    """/v1/qa/infra returns InfraSnapshot JSON from injected source."""
    snap = InfraSnapshot(
        nodes=[NodeStat(name="n1", cpu_pct=42.0, mem_pct=70.0)],
        pods=[PodStat(name="pod-a", namespace="enterprise-ai", cpu_cores=0.5, mem_bytes=1048576.0, restarts=0)],
        node_count=1,
        pod_count=1,
    )
    resp = _infra_client(snap).get("/v1/qa/infra")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_count"] == 1
    assert data["pod_count"] == 1
    assert data["nodes"][0]["name"] == "n1"
    assert data["nodes"][0]["cpu_pct"] == 42.0
    assert data["pods"][0]["name"] == "pod-a"
    assert data["pods"][0]["restarts"] == 0


def test_infra_unknown_env_404():
    """Unknown environment returns 404."""
    snap = InfraSnapshot(nodes=[], pods=[], node_count=0, pod_count=0)
    resp = _infra_client(snap).get("/v1/staging/infra")
    assert resp.status_code == 404


def test_infra_empty_snapshot():
    """Empty snapshot is valid and returns 200 with empty lists."""
    snap = InfraSnapshot(nodes=[], pods=[], node_count=0, pod_count=0)
    resp = _infra_client(snap).get("/v1/prod/infra")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["pods"] == []
    assert data["node_count"] == 0
    assert data["pod_count"] == 0


# ---------------------------------------------------------------------------
# /v1/{env}/workloads
# ---------------------------------------------------------------------------

class _FakeWorkloadSource:
    def __init__(self, wh: WorkloadHealth):
        self._wh = wh

    def health(self, _env: str) -> WorkloadHealth:
        return self._wh


def _workload_client(wh: WorkloadHealth):
    return TestClient(create_app(
        log_source=_FakeSource([]),
        trace_source=_FakeTraceSource([]),
        workload_source=_FakeWorkloadSource(wh),
    ))


def test_workloads_returns_workload_health():
    """/v1/qa/workloads returns WorkloadHealth JSON from injected source."""
    wh = WorkloadHealth(
        pod_issues=[PodIssue(namespace="enterprise-ai", pod="api-x", problem="CrashLoopBackOff", detail="api")],
        replica_shortfalls=[ReplicaShortfall(kind="Deployment", name="api-deploy", namespace="enterprise-ai", desired=3, available=1)],
        pvc_issues=[PvcIssue(namespace="enterprise-ai", name="data-pvc", phase="Pending")],
    )
    resp = _workload_client(wh).get("/v1/qa/workloads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pod_issues"]) == 1
    assert data["pod_issues"][0]["problem"] == "CrashLoopBackOff"
    assert data["pod_issues"][0]["detail"] == "api"
    assert len(data["replica_shortfalls"]) == 1
    assert data["replica_shortfalls"][0]["kind"] == "Deployment"
    assert data["replica_shortfalls"][0]["desired"] == 3
    assert len(data["pvc_issues"]) == 1
    assert data["pvc_issues"][0]["phase"] == "Pending"


def test_workloads_unknown_env_404():
    """Unknown environment returns 404 for /workloads."""
    wh = WorkloadHealth(pod_issues=[], replica_shortfalls=[], pvc_issues=[])
    resp = _workload_client(wh).get("/v1/staging/workloads")
    assert resp.status_code == 404


def test_workloads_empty_result():
    """Empty WorkloadHealth (all green) returns 200 with empty lists."""
    wh = WorkloadHealth(pod_issues=[], replica_shortfalls=[], pvc_issues=[])
    resp = _workload_client(wh).get("/v1/prod/workloads")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pod_issues"] == []
    assert data["replica_shortfalls"] == []
    assert data["pvc_issues"] == []


# ---------------------------------------------------------------------------
# /v1/{env}/events
# ---------------------------------------------------------------------------

class _FakeEventsSource:
    def __init__(self, events: list[K8sEvent], captured: dict | None = None):
        self._events = events
        self._captured = captured if captured is not None else {}

    def recent_warnings(self, env: str, limit: int = 50, since_minutes: int = 60) -> list[K8sEvent]:
        self._captured["env"] = env
        self._captured["limit"] = limit
        self._captured["since_minutes"] = since_minutes
        return self._events[:limit]


def _events_client(events: list[K8sEvent] | None = None, captured: dict | None = None):
    return TestClient(create_app(
        log_source=_FakeSource([]),
        trace_source=_FakeTraceSource([]),
        events_source=_FakeEventsSource(events or [], captured),
    ))


def test_events_returns_list():
    """/v1/qa/events returns list[K8sEvent] from injected source."""
    evs = [
        K8sEvent(ts="2026-06-19T10:00:00Z", type="Warning", reason="BackOff",
                 kind="Pod", name="api-abc", namespace="enterprise-ai",
                 message="restarting failed container", count=3),
    ]
    resp = _events_client(evs).get("/v1/qa/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["reason"] == "BackOff"
    assert data[0]["count"] == 3
    assert data[0]["kind"] == "Pod"


def test_events_unknown_env_404():
    """Unknown environment returns 404 for /events."""
    resp = _events_client().get("/v1/staging/events")
    assert resp.status_code == 404


def test_events_empty_list():
    """Empty event list is a valid 200 response."""
    resp = _events_client([]).get("/v1/prod/events")
    assert resp.status_code == 200
    assert resp.json() == []


def test_events_limit_forwarded():
    """limit query param is forwarded to the source."""
    captured: dict = {}
    _events_client(captured=captured).get("/v1/qa/events?limit=13")
    assert captured.get("limit") == 13


def test_events_limit_bounded_min():
    """limit=0 is rejected (ge=1)."""
    resp = _events_client().get("/v1/qa/events?limit=0")
    assert resp.status_code == 422


def test_events_limit_bounded_max():
    """limit>200 is rejected (le=200)."""
    resp = _events_client().get("/v1/qa/events?limit=201")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /v1/{env}/rag-nodes
# ---------------------------------------------------------------------------

class _FakeRagSource:
    def __init__(self, stats: list[RagNodeStat], captured: dict | None = None):
        self._stats = stats
        self._captured = captured if captured is not None else {}

    def rag_node_stats(self, env: str, since_minutes: int = 60) -> list[RagNodeStat]:
        self._captured["env"] = env
        self._captured["since_minutes"] = since_minutes
        return self._stats


def _rag_client(stats: list[RagNodeStat] | None = None, captured: dict | None = None):
    return TestClient(create_app(
        log_source=_FakeSource([]),
        trace_source=_FakeTraceSource([]),
        rag_source=_FakeRagSource(stats or [], captured),
    ))


def test_rag_nodes_returns_list():
    """/v1/qa/rag-nodes returns list[RagNodeStat] from injected source."""
    stats = [RagNodeStat(node="retriever", calls=5, p50_ms=120.0, p95_ms=300.0, errors=1, total_tokens=500)]
    resp = _rag_client(stats).get("/v1/qa/rag-nodes")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["node"] == "retriever"
    assert data[0]["calls"] == 5
    assert data[0]["p50_ms"] == 120.0
    assert data[0]["errors"] == 1


def test_rag_nodes_unknown_env_404():
    """Unknown environment returns 404 for /rag-nodes."""
    resp = _rag_client().get("/v1/unknown_env/rag-nodes")
    assert resp.status_code == 404


def test_rag_nodes_since_minutes_forwarded():
    """since_minutes query param is forwarded to the source."""
    captured: dict = {}
    _rag_client(captured=captured).get("/v1/qa/rag-nodes?since_minutes=30")
    assert captured.get("since_minutes") == 30


def test_rag_nodes_since_minutes_out_of_range():
    """since_minutes=0 → 422; since_minutes=1441 → 422."""
    client = _rag_client()
    assert client.get("/v1/qa/rag-nodes?since_minutes=0").status_code == 422
    assert client.get("/v1/qa/rag-nodes?since_minutes=1441").status_code == 422


# ---------------------------------------------------------------------------
# /v1/{env}/argocd
# ---------------------------------------------------------------------------

class _FakeArgocdSource:
    def __init__(self, apps):
        self._apps = apps

    def apps(self, env):
        return self._apps


def _argocd_client(apps=None):
    from obs_backend.models import ArgoApp  # noqa: PLC0415
    return TestClient(create_app(
        log_source=_FakeSource([]),
        argocd_source=_FakeArgocdSource(apps if apps is not None else [
            ArgoApp(name="langfuse-prod", sync_status="OutOfSync", health_status="Healthy"),
        ]),
    ))


def test_argocd_returns_apps():
    resp = _argocd_client().get("/v1/prod/argocd")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "langfuse-prod"
    assert body[0]["sync_status"] == "OutOfSync"
    assert body[0]["health_status"] == "Healthy"


def test_argocd_unknown_env_404():
    assert _argocd_client([]).get("/v1/staging/argocd").status_code == 404
