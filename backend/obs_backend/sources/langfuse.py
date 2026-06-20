from typing import Callable

import httpx

from obs_backend.config import ENVIRONMENTS
from obs_backend.models import Trace
from obs_backend.sources._langfuse import build_auth, default_client_factory as _default_client_factory


class LangfuseSource:
    """Conector read-only a una instancia self-hosted de Langfuse.

    Defensivo ante el sistema observado: cualquier shape inesperado en la
    respuesta degrada a vacío / campo None en vez de propagar una excepción.
    """

    def __init__(
        self,
        client_factory: Callable[[str, tuple[str, str] | None], httpx.Client] = _default_client_factory,
    ):
        self._client_factory = client_factory

    def recent_traces(self, env: str, limit: int = 20) -> list[Trace]:
        cfg = ENVIRONMENTS[env]
        base_url: str = cfg["langfuse_url"]

        # Secretos cargados desde k8s/archivos suelen traer un '\n' final:
        # recortarlo evita un 401 confuso. Vacío -> sin auth (local/dev).
        auth = build_auth()

        # Cliente como context manager: cierra el pool de conexiones por llamada.
        with self._client_factory(base_url, auth) as client:
            resp = client.get(
                "/api/public/traces",
                params={"limit": limit, "orderBy": "timestamp.desc"},
            )
            resp.raise_for_status()
            payload = resp.json()

        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        out: list[Trace] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            # Langfuse devuelve latency en segundos -> ms. isinstance (no truthiness)
            # preserva latency==0 y evita el trap de '1.2'*1000 (repetición de str).
            latency_raw = item.get("latency")
            latency_ms = latency_raw * 1000 if isinstance(latency_raw, (int, float)) else None

            tokens_raw = item.get("totalTokens")
            total_tokens = int(tokens_raw) if isinstance(tokens_raw, (int, float)) else None

            faithfulness: float | None = None
            scores = item.get("scores")
            if isinstance(scores, list):
                for score in scores:
                    if isinstance(score, dict) and score.get("name") == "faithfulness":
                        val = score.get("value")
                        faithfulness = float(val) if isinstance(val, (int, float)) else None
                        break

            uid = item.get("userId")
            out.append(Trace(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                ts=str(item.get("timestamp") or ""),
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                faithfulness=faithfulness,
                user_id=str(uid) if uid is not None else None,
            ))
        return out
