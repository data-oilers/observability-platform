from fastapi.testclient import TestClient

from obs_backend.api import create_app
from obs_backend.models import InfraSnapshot, LogEvent, NodeStat, PodStat, Trace


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
