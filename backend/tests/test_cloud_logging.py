from types import SimpleNamespace

from obs_backend.sources.cloud_logging import CloudLoggingSource


class _FakeEntry:
    def __init__(self, payload, severity, ts, pod):
        self.payload = payload
        self.severity = severity
        self.timestamp = SimpleNamespace(isoformat=lambda: ts)
        self.resource = SimpleNamespace(labels={"pod_name": pod})


class _FakeClient:
    def __init__(self, entries):
        self._entries = entries
        self.last_filter = None

    def list_entries(self, filter_=None, order_by=None, max_results=None):
        self.last_filter = filter_
        return self._entries[:max_results]


def test_recent_maps_entries_to_logevents():
    entries = [
        _FakeEntry('{"severity":"warning","event":"RAGAS OpenAIError"}', "WARNING",
                   "2026-06-12T10:31:12Z", "enterprise-ai-api-7c9f"),
        _FakeEntry('{"severity":"info","path":"/api/v1/chat","status":200}', "INFO",
                   "2026-06-12T10:31:48Z", "enterprise-ai-api-7c9f"),
    ]
    src = CloudLoggingSource(client_factory=lambda project: _FakeClient(entries))
    out = src.recent("qa", limit=2)
    assert len(out) == 2
    assert out[0].severity == "WARNING"
    assert out[1].path == "/api/v1/chat"


def test_recent_filter_targets_project_namespace():
    fake = _FakeClient([])
    src = CloudLoggingSource(client_factory=lambda project: fake)
    src.recent("qa", min_severity="WARNING", limit=10)
    assert 'resource.labels.namespace_name="enterprise-ai"' in fake.last_filter
    assert "severity>=WARNING" in fake.last_filter
