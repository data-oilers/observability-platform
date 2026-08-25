"""RagPipelineSource — agrega stats de nodos RAG desde Langfuse observations."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from obs_backend.config import ENVIRONMENTS
from obs_backend.latency import percentile
from obs_backend.models import RagNodeStat
from obs_backend.sources._langfuse import build_auth, default_client_factory, warn_unreachable

_MAX_PAGES = 5
# /api/public/observations con limit=100 sobrecarga el Langfuse de qa (tarda ~13s
# y devuelve 503). limit=10 responde en ~3s; 25 da margen y entra holgado.
_PAGE_SIZE = 25

# Langfuse expresses observation latency in SECONDS (same as traces, see langfuse.py).
# Prefer startTime/endTime delta; fall back to latency field * 1000.
# NOTE: confirm the unit against one live observation at deploy
# (Langfuse is Citrix-only; not verifiable from CI).
_LATENCY_FIELD_IS_SECONDS = True

_log = logging.getLogger(__name__)


class RagPipelineSource:
    """Conector read-only a Langfuse observations para topología RAG.

    Defensivo: errores de parseo por observación se descartan (degrade row,
    not batch). Errores de transporte HTTP → WARNING + resultado parcial (no propagan).
    """

    def __init__(
        self,
        client_factory: Callable[[str, tuple[str, str] | None], httpx.Client] = default_client_factory,
    ):
        self._client_factory = client_factory

    def rag_node_stats(self, env: str, since_minutes: int = 60) -> list[RagNodeStat]:
        """Retorna stats por nodo RAG del entorno env. Resultados capados en _MAX_PAGES * _PAGE_SIZE observaciones."""
        cfg = ENVIRONMENTS[env]
        base_url: str = cfg["langfuse_url"]
        auth = build_auth()

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        cutoff_iso = cutoff.isoformat()

        # groups: name -> list of (latency_ms | None, is_error, tokens | None)
        groups: dict[str, list[tuple[float | None, bool, int | None]]] = defaultdict(list)

        try:
            with self._client_factory(base_url, auth) as client:
                for page_num in range(1, _MAX_PAGES + 1):
                    resp = client.get(
                        "/api/public/observations",
                        params={
                            "fromStartTime": cutoff_iso,
                            "limit": _PAGE_SIZE,
                            "page": page_num,
                        },
                    )
                    resp.raise_for_status()
                    payload = resp.json()

                    if not isinstance(payload, dict):
                        break
                    data = payload.get("data")
                    if not isinstance(data, list) or len(data) == 0:
                        break

                    if page_num == _MAX_PAGES and len(data) == _PAGE_SIZE:
                        _log.warning(
                            "rag_node_stats: pagination cap (%d obs) hit for env=%s; stats may be truncated",
                            _MAX_PAGES * _PAGE_SIZE,
                            env,
                        )

                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        try:
                            _parse_obs(item, groups)
                        except Exception:
                            # Degrade per-observation, not per-batch
                            continue
        except httpx.TransportError as exc:
            warn_unreachable(_log, "rag/langfuse-observations", base_url, exc)
            return _aggregate(groups)

        return _aggregate(groups)


def _parse_obs(
    item: dict,
    groups: dict[str, list[tuple[float | None, bool, int | None]]],
) -> None:
    """Extrae campos relevantes de un observation y los acumula en groups."""
    name = item.get("name") or ""
    if not name:
        return

    # Prefer the unambiguous startTime/endTime delta (real ms); fall back to the
    # latency field treated as SECONDS → ms (_LATENCY_FIELD_IS_SECONDS=True, mirrors
    # langfuse.py traces treatment). NOTE: confirm unit at deploy (Citrix-only; not CI-verifiable).
    latency_ms: float | None = None
    start_str, end_str = item.get("startTime"), item.get("endTime")
    if start_str and end_str:
        try:
            start_dt = datetime.fromisoformat(str(start_str).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
            latency_ms = (end_dt - start_dt).total_seconds() * 1000
        except (ValueError, TypeError):
            latency_ms = None
    if latency_ms is None:
        latency_raw = item.get("latency")
        if isinstance(latency_raw, (int, float)):
            latency_ms = float(latency_raw) * 1000  # seconds → ms

    is_error = item.get("level") == "ERROR"

    tokens: int | None = None
    usage = item.get("usage")
    if isinstance(usage, dict):
        tt = usage.get("totalTokens")
        if isinstance(tt, (int, float)):
            tokens = int(tt)
        else:
            tot = usage.get("total")
            tokens = int(tot) if isinstance(tot, (int, float)) else None

    groups[name].append((latency_ms, is_error, tokens))


def _aggregate(
    groups: dict[str, list[tuple[float | None, bool, int | None]]],
) -> list[RagNodeStat]:
    result: list[RagNodeStat] = []

    for name, entries in groups.items():
        calls = len(entries)
        latencies = [lat for lat, _, _ in entries if lat is not None]
        errors = sum(1 for _, is_err, _ in entries if is_err)

        token_vals = [tok for _, _, tok in entries if tok is not None]
        total_tokens = sum(token_vals) if token_vals else None

        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)

        result.append(RagNodeStat(
            node=name,
            calls=calls,
            p50_ms=p50,
            p95_ms=p95,
            errors=errors,
            total_tokens=total_tokens,
        ))

    result.sort(key=lambda s: s.calls, reverse=True)
    return result
