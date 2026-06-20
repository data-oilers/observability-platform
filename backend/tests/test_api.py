from fastapi.testclient import TestClient

from obs_backend.api import create_app
from obs_backend.models import LogEvent


class _FakeSource:
    def __init__(self, events):
        self._events = events

    def recent(self, env, min_severity="DEFAULT", limit=100):
        return self._events[:limit]


def _client(events=None):
    return TestClient(create_app(log_source=_FakeSource(events or [])))


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
