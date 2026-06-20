"""Tests for optional static-file serving via OBS_FRONTEND_DIR."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from obs_backend.api import create_app
from obs_backend.models import InfraSnapshot, LogEvent, Trace


# ---------------------------------------------------------------------------
# Minimal stubs — keep tests self-contained, no GCP calls
# ---------------------------------------------------------------------------

class _FakeLog:
    def recent(self, env, min_severity="DEFAULT", limit=100):
        return []


class _FakeTrace:
    def recent_traces(self, env, limit=20):
        return []


class _FakeMetrics:
    def snapshot(self, env: str) -> InfraSnapshot:
        return InfraSnapshot(nodes=[], pods=[], node_count=0, pod_count=0)


def _make_client(**env_overrides):
    """Create a TestClient; environment is set before create_app is called."""
    app = create_app(
        log_source=_FakeLog(),
        trace_source=_FakeTrace(),
        metrics_source=_FakeMetrics(),
    )
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_static_serves_index_when_env_set(monkeypatch, tmp_path):
    """With OBS_FRONTEND_DIR pointing at a dir with index.html, GET / returns 200 HTML."""
    index = tmp_path / "index.html"
    index.write_text("<html><body>obs dashboard</body></html>")
    monkeypatch.setenv("OBS_FRONTEND_DIR", str(tmp_path))

    app = create_app(log_source=_FakeLog(), trace_source=_FakeTrace(), metrics_source=_FakeMetrics())
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "obs dashboard" in resp.text


def test_healthz_still_works_with_frontend_mounted(monkeypatch, tmp_path):
    """GET /healthz returns {status: ok} even when the static mount is active."""
    (tmp_path / "index.html").write_text("<html><body>x</body></html>")
    monkeypatch.setenv("OBS_FRONTEND_DIR", str(tmp_path))

    app = create_app(log_source=_FakeLog(), trace_source=_FakeTrace(), metrics_source=_FakeMetrics())
    client = TestClient(app)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_api_route_works_with_frontend_mounted(monkeypatch, tmp_path):
    """API routes are reachable (not swallowed by the static mount)."""
    (tmp_path / "index.html").write_text("<html><body>x</body></html>")
    monkeypatch.setenv("OBS_FRONTEND_DIR", str(tmp_path))

    app = create_app(log_source=_FakeLog(), trace_source=_FakeTrace(), metrics_source=_FakeMetrics())
    client = TestClient(app)

    resp = client.get("/v1/qa/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_root_404_without_frontend_env(monkeypatch):
    """Without OBS_FRONTEND_DIR, GET / returns 404 (no static mount)."""
    monkeypatch.delenv("OBS_FRONTEND_DIR", raising=False)

    app = create_app(log_source=_FakeLog(), trace_source=_FakeTrace(), metrics_source=_FakeMetrics())
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/")
    assert resp.status_code == 404


def test_static_skipped_when_dir_does_not_exist(monkeypatch, tmp_path):
    """OBS_FRONTEND_DIR set to a non-existent path: mount skipped, / still 404."""
    monkeypatch.setenv("OBS_FRONTEND_DIR", str(tmp_path / "nonexistent"))

    app = create_app(log_source=_FakeLog(), trace_source=_FakeTrace(), metrics_source=_FakeMetrics())
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/")
    assert resp.status_code == 404


def test_security_headers_present(monkeypatch):
    """GET /healthz response carries the expected security headers."""
    monkeypatch.delenv("OBS_FRONTEND_DIR", raising=False)

    app = create_app(log_source=_FakeLog(), trace_source=_FakeTrace(), metrics_source=_FakeMetrics())
    client = TestClient(app)

    resp = client.get("/healthz")
    assert resp.status_code == 200

    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp

    assert resp.headers.get("x-content-type-options") == "nosniff"
