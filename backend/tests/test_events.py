"""Tests para EventsSource — K8s Warning events desde Cloud Logging."""
import pytest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from obs_backend.api import create_app
from obs_backend.sources.events import EventsSource


class _FakeEntry:
    def __init__(self, payload, ts=None):
        self.payload = payload
        if ts is not None:
            self.timestamp = SimpleNamespace(isoformat=lambda: ts)
        else:
            self.timestamp = None


class _FakeClient:
    def __init__(self, entries):
        self._entries = entries
        self.last_filter = None
        self.last_order_by = None
        self.last_max_results = None

    def list_entries(self, filter_=None, order_by=None, max_results=None):
        self.last_filter = filter_
        self.last_order_by = order_by
        self.last_max_results = max_results
        return self._entries[:max_results] if max_results else self._entries


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

def test_full_warning_event_parses_all_fields():
    payload = {
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off restarting failed container",
        "count": 7,
        "involvedObject": {
            "kind": "Pod",
            "name": "api-abc123",
            "namespace": "enterprise-ai",
        },
    }
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa", limit=10)

    assert len(events) == 1
    e = events[0]
    assert e.ts == "2026-06-19T10:00:00Z"
    assert e.type == "Warning"
    assert e.reason == "BackOff"
    assert e.message == "Back-off restarting failed container"
    assert e.count == 7
    assert e.kind == "Pod"
    assert e.name == "api-abc123"
    assert e.namespace == "enterprise-ai"


def test_ts_empty_when_no_timestamp():
    payload = {"type": "Warning", "reason": "OOMKilling"}
    entry = _FakeEntry(payload, ts=None)
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    assert events[0].ts == ""


# ---------------------------------------------------------------------------
# Filter / query parameters
# ---------------------------------------------------------------------------

def test_filter_targets_events_log_and_warning_type():
    fake = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake)
    src.recent_warnings("qa", limit=25)
    assert 'logName="projects/itmind-macro-ai-qa-0/logs/events"' in fake.last_filter
    assert 'jsonPayload.type="Warning"' in fake.last_filter


def test_order_is_descending_and_max_results_forwarded():
    from google.cloud import logging_v2

    fake = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake)
    src.recent_warnings("qa", limit=37)
    assert fake.last_order_by == logging_v2.DESCENDING
    assert fake.last_max_results == 37


def test_filter_uses_prod_project_for_prod_env():
    fake = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake)
    src.recent_warnings("prod", limit=5)
    assert 'logName="projects/itmind-macro-ai-prod-0/logs/events"' in fake.last_filter


# ---------------------------------------------------------------------------
# Defensive parsing
# ---------------------------------------------------------------------------

def test_payload_not_a_dict_skips_entry():
    entries = [
        _FakeEntry("este-no-es-un-dict", ts="2026-06-19T10:00:00Z"),
        _FakeEntry({"type": "Warning", "reason": "Unhealthy"}, ts="2026-06-19T10:01:00Z"),
    ]
    src = EventsSource(client_factory=lambda project: _FakeClient(entries))
    events = src.recent_warnings("qa")
    assert len(events) == 1
    assert events[0].reason == "Unhealthy"


def test_payload_none_skips_entry():
    entries = [
        _FakeEntry(None, ts="2026-06-19T10:00:00Z"),
        _FakeEntry({"type": "Warning", "reason": "FailedMount"}, ts="2026-06-19T10:01:00Z"),
    ]
    src = EventsSource(client_factory=lambda project: _FakeClient(entries))
    events = src.recent_warnings("qa")
    assert len(events) == 1
    assert events[0].reason == "FailedMount"


def test_involved_object_missing_leaves_defaults():
    payload = {"type": "Warning", "reason": "FailedScheduling"}
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    e = events[0]
    assert e.kind == ""
    assert e.name == ""
    assert e.namespace == ""


def test_involved_object_not_dict_leaves_defaults():
    payload = {"type": "Warning", "reason": "FailedScheduling", "involvedObject": "not-a-dict"}
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    e = events[0]
    assert e.kind == ""
    assert e.name == ""
    assert e.namespace == ""


def test_count_as_string_becomes_none():
    payload = {"type": "Warning", "reason": "BackOff", "count": "tres"}
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    assert events[0].count is None


def test_count_zero_preserved():
    payload = {"type": "Warning", "reason": "BackOff", "count": 0}
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    assert events[0].count == 0


def test_count_as_float_converts_to_int():
    payload = {"type": "Warning", "reason": "OOMKilling", "count": 3.0}
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    assert events[0].count == 3


# ---------------------------------------------------------------------------
# Multiple events / ordering
# ---------------------------------------------------------------------------

def test_multiple_events_preserve_order():
    entries = [
        _FakeEntry({"type": "Warning", "reason": "BackOff"}, ts="2026-06-19T10:05:00Z"),
        _FakeEntry({"type": "Warning", "reason": "OOMKilling"}, ts="2026-06-19T10:04:00Z"),
        _FakeEntry({"type": "Warning", "reason": "FailedMount"}, ts="2026-06-19T10:03:00Z"),
    ]
    src = EventsSource(client_factory=lambda project: _FakeClient(entries))
    events = src.recent_warnings("qa", limit=3)
    assert [e.reason for e in events] == ["BackOff", "OOMKilling", "FailedMount"]


def test_mixed_bad_and_good_entries_only_returns_good():
    entries = [
        _FakeEntry("malformed", ts="2026-06-19T10:05:00Z"),
        _FakeEntry({"type": "Warning", "reason": "Unhealthy"}, ts="2026-06-19T10:04:00Z"),
        _FakeEntry(None, ts="2026-06-19T10:03:00Z"),
        _FakeEntry({"type": "Warning", "reason": "BackOff"}, ts="2026-06-19T10:02:00Z"),
    ]
    src = EventsSource(client_factory=lambda project: _FakeClient(entries))
    events = src.recent_warnings("qa", limit=10)
    assert len(events) == 2
    assert events[0].reason == "Unhealthy"
    assert events[1].reason == "BackOff"


# ---------------------------------------------------------------------------
# T-1: propagate-on-failure contract
# ---------------------------------------------------------------------------

def test_list_entries_failure_propagates():
    """Un fallo de transporte/permisos durante la iteración debe propagarse (no degradar a [])."""
    class _RaisingClient:
        def list_entries(self, **kwargs):
            def _gen():
                raise PermissionError("denied")
                yield  # pragma: no cover
            return _gen()

    src = EventsSource(client_factory=lambda project: _RaisingClient())
    with pytest.raises(PermissionError):
        src.recent_warnings("qa")


def test_events_endpoint_returns_500_on_source_failure():
    """El endpoint /v1/{env}/events devuelve 500 cuando la fuente lanza excepción."""
    class _BoomSource:
        def recent_warnings(self, env, limit=50):
            raise PermissionError("denied")

    client = TestClient(create_app(events_source=_BoomSource()), raise_server_exceptions=False)
    assert client.get("/v1/qa/events").status_code == 500


# ---------------------------------------------------------------------------
# T-2: str() coercion + M-1 timestamp guard
# ---------------------------------------------------------------------------

def test_non_string_reason_and_message_coerced_to_str():
    """reason y message no-string se convierten a str sin perder la entrada."""
    payload = {"type": "Warning", "reason": 137, "message": 999}
    entry = _FakeEntry(payload, ts="2026-06-19T10:00:00Z")
    src = EventsSource(client_factory=lambda project: _FakeClient([entry]))
    events = src.recent_warnings("qa")
    assert len(events) == 1
    assert events[0].reason == "137"
    assert events[0].message == "999"


def test_malformed_timestamp_isoformat_raises_degrades_to_empty_string():
    """Un timestamp cuyo .isoformat() lanza degrada esa entrada a ts='' sin perder el batch."""
    bad_ts = SimpleNamespace(isoformat=lambda: (_ for _ in ()).throw(ValueError("bad ts")))
    bad_entry = _FakeEntry.__new__(_FakeEntry)
    bad_entry.payload = {"type": "Warning", "reason": "BackOff"}
    bad_entry.timestamp = bad_ts

    good_entry = _FakeEntry({"type": "Warning", "reason": "OOMKilling"}, ts="2026-06-19T10:01:00Z")

    src = EventsSource(client_factory=lambda project: _FakeClient([bad_entry, good_entry]))
    events = src.recent_warnings("qa", limit=10)

    assert len(events) == 2
    assert events[0].ts == ""
    assert events[0].reason == "BackOff"
    assert events[1].ts == "2026-06-19T10:01:00Z"
    assert events[1].reason == "OOMKilling"


# ---------------------------------------------------------------------------
# T-3: freshness window (since_minutes)
# ---------------------------------------------------------------------------

def test_filter_includes_timestamp_lower_bound_by_default():
    """Con since_minutes default (60), el filtro incluye un cláusula timestamp>=."""
    fake = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake)
    src.recent_warnings("qa", limit=25)
    assert 'logName="projects/itmind-macro-ai-qa-0/logs/events"' in fake.last_filter
    assert 'jsonPayload.type="Warning"' in fake.last_filter
    assert 'timestamp>="' in fake.last_filter


def test_filter_timestamp_bound_is_valid_rfc3339():
    """El cutoff embebido en el filtro es un string RFC3339 válido (YYYY-MM-DDTHH:MM:SSZ)."""
    import re
    fake = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake)
    src.recent_warnings("qa")
    # Extrae el valor entre comillas después de timestamp>=
    match = re.search(r'timestamp>="(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"', fake.last_filter)
    assert match is not None, f"No se encontró RFC3339 en el filtro: {fake.last_filter}"


def test_custom_since_minutes_still_includes_timestamp_bound():
    """since_minutes=15 también emite cláusula timestamp>= en el filtro."""
    fake = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake)
    src.recent_warnings("qa", since_minutes=15)
    assert 'timestamp>="' in fake.last_filter
    assert 'jsonPayload.type="Warning"' in fake.last_filter


def test_since_minutes_custom_value_produces_later_cutoff_than_default():
    """since_minutes=15 produce un cutoff más reciente (mayor) que since_minutes=60."""
    import re
    fake_15 = _FakeClient([])
    fake_60 = _FakeClient([])
    src = EventsSource(client_factory=lambda project: fake_15)
    src.recent_warnings("qa", since_minutes=15)
    src2 = EventsSource(client_factory=lambda project: fake_60)
    src2.recent_warnings("qa", since_minutes=60)

    m15 = re.search(r'timestamp>="(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"', fake_15.last_filter)
    m60 = re.search(r'timestamp>="(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"', fake_60.last_filter)
    assert m15 and m60
    # 15 minutos atrás es más reciente que 60 minutos atrás
    assert m15.group(1) > m60.group(1)


# ---------------------------------------------------------------------------
# T-4: API query param since_minutes
# ---------------------------------------------------------------------------

class _CapturingSource:
    """Fuente fake que captura los kwargs de recent_warnings."""
    def __init__(self):
        self.calls: list[dict] = []

    def recent_warnings(self, env: str, limit: int = 50, since_minutes: int = 60) -> list:
        self.calls.append({"env": env, "limit": limit, "since_minutes": since_minutes})
        return []


def test_events_endpoint_forwards_since_minutes_to_source():
    """GET /v1/qa/events?since_minutes=30 pasa since_minutes=30 a recent_warnings."""
    capturing = _CapturingSource()
    client = TestClient(create_app(events_source=capturing))
    resp = client.get("/v1/qa/events?since_minutes=30")
    assert resp.status_code == 200
    assert len(capturing.calls) == 1
    assert capturing.calls[0]["since_minutes"] == 30


def test_events_endpoint_since_minutes_zero_is_unprocessable():
    """since_minutes=0 está fuera del rango permitido (ge=1) → 422."""
    capturing = _CapturingSource()
    client = TestClient(create_app(events_source=capturing))
    resp = client.get("/v1/qa/events?since_minutes=0")
    assert resp.status_code == 422


def test_events_endpoint_since_minutes_over_max_is_unprocessable():
    """since_minutes=1441 supera el máximo (le=1440) → 422."""
    capturing = _CapturingSource()
    client = TestClient(create_app(events_source=capturing))
    resp = client.get("/v1/qa/events?since_minutes=1441")
    assert resp.status_code == 422


def test_events_endpoint_since_minutes_default_is_60():
    """Sin pasar since_minutes, el endpoint usa el default 60."""
    capturing = _CapturingSource()
    client = TestClient(create_app(events_source=capturing))
    resp = client.get("/v1/qa/events")
    assert resp.status_code == 200
    assert capturing.calls[0]["since_minutes"] == 60
