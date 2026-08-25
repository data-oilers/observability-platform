"""Tests for ArgocdSource (B-3 roadmap).

ArgoCD corre en el cluster `auto` y gestiona TODAS las apps de todos los entornos.
La source lee `argocd_app_info` del GMP de auto-0 y filtra por sufijo -{env}.
Fake duck-typed igual que test_workload.py: los labels del info-metric viven en metric.labels.
"""
from __future__ import annotations

from types import SimpleNamespace

from obs_backend.sources.argocd import ArgocdSource

_BASE = "prometheus.googleapis.com"
APP_INFO = f"{_BASE}/argocd_app_info/gauge"


class _FakeClient:
    """Routes list_time_series by metric.type in the filter string."""

    def __init__(self, series_by_metric: dict):
        self._by = series_by_metric

    def list_time_series(self, request=None, **kw):
        req = request or kw
        flt = req["filter"]
        mtype = flt.split('metric.type = "', 1)[1].split('"', 1)[0]
        return self._by.get(mtype, [])


def _make_source(series: list) -> ArgocdSource:
    client = _FakeClient({APP_INFO: series})
    return ArgocdSource(client_factory=lambda: client)


def _app_series(name: str, sync_status: str, health_status: str, value: float = 1.0) -> SimpleNamespace:
    v = SimpleNamespace(double_value=value)
    point = SimpleNamespace(value=v)
    return SimpleNamespace(
        metric=SimpleNamespace(labels={
            "name": name,
            "sync_status": sync_status,
            "health_status": health_status,
        }),
        resource=SimpleNamespace(labels={"project_id": "itmind-macro-auto-0"}),
        points=[point],
    )


def test_filters_by_env_suffix():
    src = _make_source([
        _app_series("airflow-prod", "OutOfSync", "Healthy"),
        _app_series("airflow-qa", "Synced", "Healthy"),
        _app_series("airflow-dev", "Synced", "Healthy"),
        _app_series("external-secrets-auto", "Synced", "Healthy"),
    ])
    assert {a.name for a in src.apps("prod")} == {"airflow-prod"}
    assert {a.name for a in src.apps("qa")} == {"airflow-qa"}


def test_reports_sync_and_health():
    src = _make_source([_app_series("langfuse-prod", "OutOfSync", "Healthy")])
    apps = src.apps("prod")
    assert len(apps) == 1
    assert apps[0].name == "langfuse-prod"
    assert apps[0].sync_status == "OutOfSync"
    assert apps[0].health_status == "Healthy"


def test_problems_sorted_first():
    src = _make_source([
        _app_series("a-prod", "Synced", "Healthy"),
        _app_series("z-prod", "OutOfSync", "Healthy"),
    ])
    apps = src.apps("prod")
    assert apps[0].name == "z-prod"  # problema primero, pese al orden alfabético


def test_dedup_prefers_problem_status():
    # duplicado transitorio dentro de la ventana de alineación: misma app, dos label-sets
    src = _make_source([
        _app_series("kyverno-prod", "Synced", "Healthy"),
        _app_series("kyverno-prod", "Synced", "Degraded"),
    ])
    apps = src.apps("prod")
    assert len(apps) == 1
    assert apps[0].health_status == "Degraded"


def test_empty_when_no_series():
    assert _make_source([]).apps("prod") == []


def test_ignores_series_with_zero_value():
    # value < 1 = serie stale/ausente; se saltea
    src = _make_source([_app_series("airflow-prod", "OutOfSync", "Healthy", value=0.0)])
    assert src.apps("prod") == []


def test_dev_env_does_not_match_prod_app():
    # guardarraíl: "-dev" no debe matchear "...-dev-0" ni confundir sufijos
    src = _make_source([_app_series("enterprise-ai-prod", "OutOfSync", "Healthy")])
    assert src.apps("dev") == []
