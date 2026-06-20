"""Tests TDD para obs_backend.latency (task 1.A-2c).

Orden: RED → GREEN → REFACTOR.
Todos deben fallar antes de que exista obs_backend/latency.py.
"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from obs_backend.api import create_app
from obs_backend.models import LogEvent, Trace
from obs_backend.latency import build_latency_summary, percentile, summarize_latency


# ---------------------------------------------------------------------------
# Helpers — fuentes fake (misma convención que test_api.py)
# ---------------------------------------------------------------------------

class _FakeLogSource:
    def __init__(self, events: list[LogEvent]):
        self._events = events

    def recent(self, env: str, min_severity: str = "DEFAULT", limit: int = 100) -> list[LogEvent]:
        return self._events[:limit]


class _FakeTraceSource:
    def __init__(self, traces: list[Trace]):
        self._traces = traces

    def recent_traces(self, env: str, limit: int = 20) -> list[Trace]:
        return self._traces[:limit]


# ---------------------------------------------------------------------------
# percentile() — casos exactos del contrato
# ---------------------------------------------------------------------------

def test_percentile_p50_four_elements():
    assert percentile([10, 20, 30, 40], 50) == 20


def test_percentile_p95_four_elements():
    assert percentile([10, 20, 30, 40], 95) == 40


def test_percentile_single_element():
    assert percentile([42], 50) == 42


def test_percentile_empty_returns_none():
    assert percentile([], 50) is None


def test_percentile_zero_preserved():
    """0.0 es un valor válido; no debe caer en la rama de None."""
    assert percentile([0.0, 0.0, 5.0], 50) == 0.0


def test_percentile_p95_longer_list():
    # 10 elementos; rank = ceil(0.95 * 10) = 10 → último elemento
    values = list(range(1, 11))  # [1,2,...,10]
    assert percentile(values, 95) == 10


def test_percentile_p50_longer_list():
    # 6 elementos; rank = ceil(0.50 * 6) = 3 → sorted[2] = 30
    assert percentile([50, 10, 30, 20, 60, 40], 50) == 30


def test_percentile_p95_interior_not_max():
    # n=20: rank = ceil(0.95*20) = 19 -> 19th value (19), NOT the max (20)
    assert percentile(list(range(1, 21)), 95) == 19


def test_percentile_p50_interior():
    # n=20: rank = ceil(0.50*20) = 10 -> 10th value (10)
    assert percentile(list(range(1, 21)), 50) == 10


# ---------------------------------------------------------------------------
# summarize_latency()
# ---------------------------------------------------------------------------

def test_summarize_latency_empty():
    stat = summarize_latency("chat_agente", [])
    assert stat.label == "chat_agente"
    assert stat.sample_count == 0
    assert stat.p50_ms is None
    assert stat.p95_ms is None
    assert stat.max_ms is None


def test_summarize_latency_normal():
    stat = summarize_latency("chat_request", [100.0, 200.0, 300.0, 400.0])
    assert stat.sample_count == 4
    assert stat.p50_ms == 200.0
    assert stat.p95_ms == 400.0
    assert stat.max_ms == 400.0


def test_summarize_latency_none_entries_filtered():
    """Nones mezclados en la lista se filtran; sample_count refleja solo valores reales."""
    stat = summarize_latency("chat_agente", [100.0, None, 200.0, None, 300.0])  # type: ignore[list-item]
    assert stat.sample_count == 3
    assert stat.p50_ms == 200.0
    assert stat.max_ms == 300.0


def test_summarize_latency_zero_not_dropped():
    """0.0 es un valor válido; no se trata como falsy."""
    stat = summarize_latency("chat_request", [0.0, 100.0, 200.0])
    assert stat.sample_count == 3
    assert stat.p50_ms == 100.0


def test_summarize_latency_p95_below_max():
    stat = summarize_latency("x", [float(v) for v in range(1, 21)])
    assert stat.sample_count == 20
    assert stat.p95_ms == 19.0   # interior, not the max
    assert stat.max_ms == 20.0
    assert stat.p50_ms == 10.0


# ---------------------------------------------------------------------------
# build_latency_summary() — orquestación con fuentes fake
# ---------------------------------------------------------------------------

def test_build_latency_summary_chat_agente():
    """Traces con latency_ms conocidos → stat chat_agente correcta."""
    traces = [
        Trace(id="t1", latency_ms=100.0),
        Trace(id="t2", latency_ms=200.0),
        Trace(id="t3", latency_ms=300.0),
        Trace(id="t4", latency_ms=400.0),
    ]
    log_source = _FakeLogSource([])
    trace_source = _FakeTraceSource(traces)

    summary = build_latency_summary("qa", log_source, trace_source)
    agente = next(s for s in summary.stats if s.label == "chat_agente")
    assert agente.sample_count == 4
    assert agente.p50_ms == 200.0
    assert agente.p95_ms == 400.0


def test_build_latency_summary_chat_request_filters_path():
    """Solo logs con 'chat' en path y duration_ms se incluyen en chat_request."""
    logs = [
        LogEvent(ts="t", severity="INFO", pod="api", path="/v1/chat/completions", duration_ms=150.0),
        LogEvent(ts="t", severity="INFO", pod="api", path="/v1/health", duration_ms=5.0),  # no chat
        LogEvent(ts="t", severity="INFO", pod="api", path="/v1/chat/completions", duration_ms=250.0),
        LogEvent(ts="t", severity="INFO", pod="api", path=None, duration_ms=99.0),  # path None, ignorar
    ]
    log_source = _FakeLogSource(logs)
    trace_source = _FakeTraceSource([])

    summary = build_latency_summary("qa", log_source, trace_source)
    req = next(s for s in summary.stats if s.label == "chat_request")
    assert req.sample_count == 2
    assert req.p50_ms == 150.0
    assert req.p95_ms == 250.0


def test_build_latency_summary_zero_duration_counted():
    """duration_ms == 0.0 en un log de chat NO se descarta (0.0 no es None)."""
    logs = [
        LogEvent(ts="t", severity="INFO", pod="api", path="/chat", duration_ms=0.0),
        LogEvent(ts="t", severity="INFO", pod="api", path="/chat", duration_ms=100.0),
    ]
    log_source = _FakeLogSource(logs)
    trace_source = _FakeTraceSource([])

    summary = build_latency_summary("qa", log_source, trace_source)
    req = next(s for s in summary.stats if s.label == "chat_request")
    assert req.sample_count == 2


def test_build_latency_summary_path_none_ignored():
    """Log con path=None no entra en chat_request aunque tenga duration_ms."""
    logs = [
        LogEvent(ts="t", severity="INFO", pod="api", path=None, duration_ms=500.0),
    ]
    log_source = _FakeLogSource(logs)
    trace_source = _FakeTraceSource([])

    summary = build_latency_summary("qa", log_source, trace_source)
    req = next(s for s in summary.stats if s.label == "chat_request")
    assert req.sample_count == 0


def test_build_latency_summary_stats_order():
    """summary.stats siempre tiene dos entradas: chat_agente y chat_request."""
    summary = build_latency_summary("qa", _FakeLogSource([]), _FakeTraceSource([]))
    labels = [s.label for s in summary.stats]
    assert labels == ["chat_agente", "chat_request"]


# ---------------------------------------------------------------------------
# API endpoint  GET /v1/{env}/latency
# ---------------------------------------------------------------------------

def _latency_client(logs=None, traces=None):
    return TestClient(create_app(
        log_source=_FakeLogSource(logs or []),
        trace_source=_FakeTraceSource(traces or []),
    ))


def test_api_latency_returns_summary():
    """GET /v1/qa/latency devuelve LatencySummary con las dos stats."""
    traces = [Trace(id="t1", latency_ms=200.0), Trace(id="t2", latency_ms=400.0)]
    logs = [LogEvent(ts="t", severity="INFO", pod="api", path="/chat", duration_ms=100.0)]
    resp = _latency_client(logs=logs, traces=traces).get("/v1/qa/latency")
    assert resp.status_code == 200
    data = resp.json()
    assert "stats" in data
    labels = [s["label"] for s in data["stats"]]
    assert "chat_agente" in labels
    assert "chat_request" in labels


def test_api_latency_unknown_env_404():
    """Entorno desconocido devuelve 404."""
    resp = _latency_client().get("/v1/staging/latency")
    assert resp.status_code == 404


def test_api_latency_values_correct():
    """Los valores p50/p95 del endpoint coinciden con la lógica de latency.py."""
    traces = [
        Trace(id="t1", latency_ms=100.0),
        Trace(id="t2", latency_ms=200.0),
        Trace(id="t3", latency_ms=300.0),
        Trace(id="t4", latency_ms=400.0),
    ]
    resp = _latency_client(traces=traces).get("/v1/prod/latency")
    data = resp.json()
    agente = next(s for s in data["stats"] if s["label"] == "chat_agente")
    assert agente["sample_count"] == 4
    assert agente["p50_ms"] == 200.0
    assert agente["p95_ms"] == 400.0
