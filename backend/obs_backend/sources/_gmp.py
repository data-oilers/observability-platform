"""Shared GMP/Cloud Monitoring helpers.

These helpers were originally inline in monitoring.py; extracted here so
WorkloadSource (and future sources) can reuse them without duplication.

Contracts:
- latest_value: handles real proto-plus TypedValue (WhichOneof) and duck-typed
  SimpleNamespace fakes; guards against missing points/value; never drops 0.0.
- metric_label / resource_label: use collections.abc.Mapping (NOT dict) because
  real proto-plus label maps are MappingProxyType-like, not plain dicts.
- get_label: checks metric.labels first, then resource.labels (KSM object fields
  live on metric.labels in GMP; resource.labels holds scrape-target metadata).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from google.cloud import monitoring_v3


# ---------------------------------------------------------------------------
# Value extraction
# ---------------------------------------------------------------------------

def latest_value(series) -> float | None:
    """Return the numeric value of the most recent aligned point, or None.

    Handles:
    - real proto-plus TypedValue (uses WhichOneof to avoid the 0.0 trap)
    - duck-typed SimpleNamespace fakes (attr presence check)
    - series with no points → None
    """
    points = getattr(series, "points", None) or []
    if not points:
        return None
    first = points[0]
    v = getattr(first, "value", None)
    if v is None:
        return None

    pb = getattr(type(v), "pb", None)          # real proto-plus TypedValue
    if pb is not None:
        field = pb(v).WhichOneof("value")
        if field is None:
            return None
        raw = getattr(v, field)
        return float(raw) if isinstance(raw, (int, float)) else None

    # Duck-typed fake: probe by attribute presence (NOT truthiness — 0.0 is valid)
    for attr in ("double_value", "int64_value"):
        if hasattr(v, attr):
            raw = getattr(v, attr)
            return float(raw) if isinstance(raw, (int, float)) else None
    return None


# ---------------------------------------------------------------------------
# Label extraction
# ---------------------------------------------------------------------------

def metric_label(series, key: str) -> str | None:
    """Extract a label from series.metric.labels (Mapping-safe); None if missing."""
    labels = getattr(getattr(series, "metric", None), "labels", None)
    if not isinstance(labels, Mapping):
        return None
    return labels.get(key) or None


def resource_label(series, key: str) -> str | None:
    """Extract a label from series.resource.labels (Mapping-safe); None if missing."""
    labels = getattr(getattr(series, "resource", None), "labels", None)
    if not isinstance(labels, Mapping):
        return None
    return labels.get(key) or None


def get_label(series, key: str) -> str | None:
    """Return label checking metric.labels first, then resource.labels.

    KSM object labels (namespace, pod, deployment, …) live on metric.labels in GMP.
    resource.labels holds scrape-target metadata and must not override object labels.
    """
    return metric_label(series, key) or resource_label(series, key)


# ---------------------------------------------------------------------------
# Request builders
# ---------------------------------------------------------------------------

def build_request(
    project: str,
    metric_type: str,
    aligner,
    alignment_seconds: int = 300,
    extra_filter: str = "",
) -> dict:
    """Build a list_time_series request dict for one metric type."""
    now = datetime.now(timezone.utc)
    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": int(now.timestamp())},
        "start_time": {"seconds": int((now - timedelta(seconds=alignment_seconds)).timestamp())},
    })
    aggregation = monitoring_v3.Aggregation({
        "alignment_period": {"seconds": alignment_seconds},
        "per_series_aligner": aligner,
    })
    flt = f'metric.type = "{metric_type}"'
    if extra_filter:
        flt += f" AND {extra_filter}"
    return {
        "name": f"projects/{project}",
        "filter": flt,
        "interval": interval,
        "aggregation": aggregation,
        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
    }
