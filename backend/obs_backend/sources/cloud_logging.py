import json
from typing import Callable

from google.cloud import logging_v2

from obs_backend.classify import classify_line
from obs_backend.config import ENVIRONMENTS, project_for
from obs_backend.models import LogEvent


def _default_client_factory(project: str):
    # La SA obs-backend (Workload Identity) provee las credenciales en cluster;
    # en local, ADC con impersonación. Solo lectura (logging.viewer).
    return logging_v2.Client(project=project)


class CloudLoggingSource:
    def __init__(self, client_factory: Callable[[str], object] = _default_client_factory):
        self._client_factory = client_factory

    def recent(self, env: str, min_severity: str = "DEFAULT", limit: int = 100) -> list[LogEvent]:
        project = project_for(env)
        namespace = ENVIRONMENTS[env]["app_namespace"]
        filter_parts = [
            'resource.type="k8s_container"',
            f'resource.labels.namespace_name="{namespace}"',
        ]
        if min_severity and min_severity != "DEFAULT":
            filter_parts.append(f"severity>={min_severity}")
        filter_ = "\n".join(filter_parts)

        client = self._client_factory(project)
        entries = client.list_entries(
            filter_=filter_,
            order_by=logging_v2.DESCENDING,
            max_results=limit,
        )
        out: list[LogEvent] = []
        for e in entries:
            raw = _entry_text(e.payload)
            res = getattr(e, "resource", None)
            pod = (res.labels or {}).get("pod_name", "") if res else ""
            ts = e.timestamp.isoformat() if getattr(e, "timestamp", None) else ""
            out.append(classify_line(raw, pod, ts))
        return out


def _entry_text(payload) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return json.dumps(payload)
    return str(payload)

