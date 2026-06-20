from datetime import datetime, timedelta, timezone
from typing import Callable

from google.cloud import logging_v2

from obs_backend.config import project_for
from obs_backend.models import K8sEvent


def _default_client_factory(project: str):
    # La SA obs-backend (Workload Identity) provee las credenciales en cluster;
    # en local, ADC con impersonación. Solo lectura (logging.viewer).
    return logging_v2.Client(project=project)


class EventsSource:
    """Fuente read-only de K8s Warning events exportados a Cloud Logging.

    Resiliencia por capa:
    - Payload no-dict o timestamp malformado: la *entrada* se descarta/degrada
      silenciosamente (errores de datos por entrada).
    - Fallos de transporte, permisos o quota (errores de RPC): se PROPAGAN
      intencionalmente → 500 en FastAPI → la UI marca el panel en error via
      Promise.allSettled, evitando un falso "no hay warnings".
    """

    def __init__(self, client_factory: Callable[[str], object] = _default_client_factory):
        self._client_factory = client_factory

    def recent_warnings(self, env: str, limit: int = 50, since_minutes: int = 60) -> list[K8sEvent]:
        project = project_for(env)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        cutoff_rfc3339 = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_ = (
            f'logName="projects/{project}/logs/events"'
            ' AND jsonPayload.type="Warning"'
            f' AND timestamp>="{cutoff_rfc3339}"'
        )

        client = self._client_factory(project)
        entries = client.list_entries(
            filter_=filter_,
            order_by=logging_v2.DESCENDING,
            max_results=limit,
        )

        out: list[K8sEvent] = []
        # La iteración dispara el RPC. NO la envolvemos en try/except: si la lectura
        # falla (permisos/quota/red) debe PROPAGAR (→ 500 → la UI marca el panel en
        # error), nunca degradar a [] (que se leería como "no hay warnings" = falso OK).
        # (A diferencia de WorkloadSource, que es multi-métrica y degrada a parcial+errors;
        #  esto es una sola query all-or-nothing.)
        for entry in entries:
            payload = entry.payload
            if not isinstance(payload, dict):
                continue

            ts_obj = getattr(entry, "timestamp", None)
            try:
                ts = ts_obj.isoformat() if ts_obj else ""
            except Exception:
                ts = ""

            type_ = str(payload.get("type") or "")
            reason = str(payload.get("reason") or "")
            message = str(payload.get("message") or "")

            involved = payload.get("involvedObject")
            if isinstance(involved, dict):
                kind = str(involved.get("kind") or "")
                name = str(involved.get("name") or "")
                namespace = str(involved.get("namespace") or "")
            else:
                kind = name = namespace = ""

            count_raw = payload.get("count")
            count: int | None = int(count_raw) if isinstance(count_raw, (int, float)) else None

            out.append(K8sEvent(
                ts=ts,
                type=type_,
                reason=reason,
                kind=kind,
                name=name,
                namespace=namespace,
                message=message,
                count=count,
            ))
        return out
