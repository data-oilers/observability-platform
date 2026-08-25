"""ArgocdSource — estado GitOps (sync/health por app) desde `argocd_app_info` en GMP.

ArgoCD corre en el cluster `auto` (proyecto itmind-macro-auto-0) y gestiona TODAS las apps de
todos los entornos. Por eso esta source lee SIEMPRE el GMP de auto-0 (no `project_for(env)`) y
filtra las apps por sufijo `-{env}`.

`argocd_app_info` es un gauge con valor 1 y los labels `name`, `sync_status`, `health_status`.
Read-only: solo list_time_series.
"""
from __future__ import annotations

import logging
from typing import Callable

from google.cloud import monitoring_v3

from obs_backend.config import AUTO_PROJECT
from obs_backend.models import ArgoApp
from obs_backend.sources._gmp import build_request, get_label, latest_value

_log = logging.getLogger(__name__)

_BASE = "prometheus.googleapis.com"
_APP_INFO = f"{_BASE}/argocd_app_info/gauge"


def _default_client_factory() -> object:
    return monitoring_v3.MetricServiceClient()


def _is_ok(sync_status: str, health_status: str) -> bool:
    """Una app está OK sólo si está Synced y Healthy; el resto se muestra y sube en el orden."""
    return sync_status == "Synced" and health_status == "Healthy"


class ArgocdSource:
    """Conector read-only a `argocd_app_info` via GMP del proyecto auto-0."""

    def __init__(self, client_factory: Callable[[], object] = _default_client_factory):
        self._client_factory = client_factory

    def apps(self, env: str) -> list[ArgoApp]:
        client = self._client_factory()
        suffix = f"-{env}"

        # ALIGN_MAX: mismo criterio de stickiness que WorkloadSource — un cambio de estado
        # reciente sigue visible dentro de la ventana; se dedup abajo prefiriendo el peor.
        try:
            series = list(client.list_time_series(request=build_request(
                AUTO_PROJECT, _APP_INFO, monitoring_v3.Aggregation.Aligner.ALIGN_MAX,
            )))
        except Exception:
            _log.warning("argocd: fallo leyendo %s", _APP_INFO, exc_info=True)
            return []

        # name -> ArgoApp, dedup prefiriendo la serie "peor" (no-ok) ante duplicados transitorios.
        best: dict[str, ArgoApp] = {}
        for s in series:
            val = latest_value(s)
            if val is None or val < 1:
                continue
            name = get_label(s, "name")
            if not name or not name.endswith(suffix):
                continue
            sync = get_label(s, "sync_status") or "Unknown"
            health = get_label(s, "health_status") or "Unknown"
            prev = best.get(name)
            if prev is None or (_is_ok(prev.sync_status, prev.health_status)
                                and not _is_ok(sync, health)):
                best[name] = ArgoApp(name=name, sync_status=sync, health_status=health)

        # Problemas primero, después alfabético.
        return sorted(
            best.values(),
            key=lambda a: (_is_ok(a.sync_status, a.health_status), a.name),
        )
