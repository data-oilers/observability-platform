"""Cálculo de latencia de chat (p50/p95) — módulo puro sin dependencias externas.

Las fuentes (log_source, trace_source) se inyectan; este módulo no las importa
en runtime (solo bajo TYPE_CHECKING), lo que lo mantiene unit-testable con fakes.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from obs_backend.sources.base import LogSource, TraceSource

from obs_backend.models import LatencyStat, LatencySummary

# Ventanas de muestreo para build_latency_summary
_TRACE_WINDOW = 200   # trazas recientes a muestrear para p50/p95 del agente (cada traza = un turno de chat)
_LOG_WINDOW = 500     # logs recientes; ventana mayor porque el filtro de path-chat descarta la mayoría de las filas


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile.

    Ordena ascendente, calcula rank = ceil(q/100 * n), clampea a [1, n].
    Devuelve None solo si la lista está vacía.
    q se espera en [0, 100]; fuera de rango se clampea (q<=0 → mínimo, q>=100 → máximo). Lista vacía → None.
    """
    if not values:
        return None
    sorted_xs = sorted(values)
    n = len(sorted_xs)
    rank = math.ceil(q / 100 * n)
    rank = max(1, min(rank, n))
    return sorted_xs[rank - 1]


def summarize_latency(label: str, values_ms: list[float]) -> LatencyStat:
    """Calcula p50, p95 y max para una lista de duraciones en ms.

    Filtra entradas None defensivamente (permite que el caller pase listas
    sin pre-filtrar). sample_count refleja solo valores reales.
    """
    filtered = [v for v in values_ms if v is not None]
    sample_count = len(filtered)
    return LatencyStat(
        label=label,
        sample_count=sample_count,
        p50_ms=percentile(filtered, 50),
        p95_ms=percentile(filtered, 95),
        max_ms=max(filtered) if filtered else None,
    )


def build_latency_summary(env: str, log_source: "LogSource", trace_source: "TraceSource") -> LatencySummary:
    """Orquesta la extracción de latencias desde las fuentes inyectadas.

    chat_agente: latency_ms de trazas Langfuse (respuesta del agente).
    chat_request: duration_ms de logs de app cuyo path contiene "chat".
    """
    # --- chat_agente: traza Langfuse ---
    traces = trace_source.recent_traces(env, limit=_TRACE_WINDOW)
    agente_values = [t.latency_ms for t in traces if t.latency_ms is not None]
    chat_agente_stat = summarize_latency("chat_agente", agente_values)

    # --- chat_request: logs de app en el path de chat ---
    events = log_source.recent(env, min_severity="DEFAULT", limit=_LOG_WINDOW)
    request_values = [
        e.duration_ms
        for e in events
        # substring deliberada: el path real del backend es /api/v1/chat; en un dashboard
        # interno aceptamos un match laxo (no segmentado). NO cambiar a igualdad estricta sin
        # conocer el set real de paths.
        if e.duration_ms is not None and e.path and "chat" in e.path
    ]
    chat_request_stat = summarize_latency("chat_request", request_values)

    return LatencySummary(stats=[chat_agente_stat, chat_request_stat])
