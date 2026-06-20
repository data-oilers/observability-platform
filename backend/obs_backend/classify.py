import json
import re

from obs_backend.models import LogEvent

_UVICORN_RE = re.compile(r'"(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+) HTTP/[0-9.]+" (\d{3})')
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_STRUCTLOG_LVL_RE = re.compile(r"\[\s*(debug|info|warning|error|critical)\s*\]")
_KV_RE = re.compile(r"\b(\w+)=(\S+)")

_LEVEL_MAP = {"debug": "INFO", "info": "INFO", "warning": "WARNING",
              "error": "ERROR", "critical": "CRITICAL"}

# Ruido conocido (relevado en QA 2026-06-12): visible como WARNING, nunca pinta
# el semáforo de rojo. Ver monitoreo-banco-2026-06-12/notas.md.
_KNOWN_NOISE = ("No active span in current context", "OpenAIError",
                "api_key client option")


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_line(raw: str, pod: str, ts: str = "") -> LogEvent:
    """Convierte una línea de log cruda en un LogEvent normalizado. Pura."""
    s = _ANSI_RE.sub("", str(raw)).strip()
    # Recorta "enterprise-ai-" (prefijo de namespace). Cambio intencional vs v1 que
    # recortaba "enterprise-ai-api-" (solo un pod); este es correcto para multi-namespace.
    ev = LogEvent(ts=ts, pod=str(pod).replace("enterprise-ai-", ""), message=s[:300])

    inner = None
    if s.startswith("{"):
        try:
            inner = json.loads(s)
        except (ValueError, TypeError):
            inner = None

    if isinstance(inner, dict):
        ev.severity = str(inner.get("severity") or inner.get("level") or "INFO").upper()
        ev.message = str(inner.get("event") or inner.get("message") or s)[:300]
        path = inner.get("path")
        ev.path = path if isinstance(path, str) else None
        ev.status = _to_int(inner.get("status", inner.get("status_code")))
        ev.duration_ms = _to_float(inner.get("duration_ms"))
        rid, uid = inner.get("request_id"), inner.get("user_id")
        ev.request_id = str(rid) if rid is not None else None
        ev.user_id = str(uid) if uid is not None else None
    else:
        lvl = _STRUCTLOG_LVL_RE.search(s)
        if lvl:
            ev.severity = _LEVEL_MAP[lvl.group(1)]
        else:
            # Heurística deliberada para líneas no estructuradas de fuente conocida.
            # Fuente de falsos positivos: cualquier log que mencione "ERROR" en mensaje
            # informativo. Aceptamos el trade-off a favor de cobertura.
            # Nota: promover CRITICAL a su propio nivel (en vez de colapsarlo a ERROR)
            # es un cambio intencional respecto del v1.
            up = s.upper()
            if "CRITICAL" in up:
                ev.severity = "CRITICAL"
            elif "ERROR" in up or "TRACEBACK" in up:
                ev.severity = "ERROR"
            elif "WARN" in up:
                ev.severity = "WARNING"
        kv = dict(_KV_RE.findall(s))
        ev.path = kv.get("path", ev.path)
        # Fix A: usar presencia de clave, no truthiness, para preservar status=0 o duration_ms=0.
        if "status_code" in kv:
            ev.status = _to_int(kv["status_code"])
        if "duration_ms" in kv:
            ev.duration_ms = _to_float(kv["duration_ms"])
        ev.request_id = kv.get("request_id", ev.request_id)
        ev.user_id = kv.get("user_id", ev.user_id)
        m = _UVICORN_RE.search(s)
        if m and ev.path is None:
            ev.path, ev.status = m.group(2), int(m.group(3))

    # Ruido conocido degradado a WARNING (después del parseo de severidad).
    if any(n in s for n in _KNOWN_NOISE):
        if ev.severity in ("ERROR", "CRITICAL"):
            ev.severity = "WARNING"

    # 5xx siempre cuenta como error aunque el logger no lo marque.
    if ev.status is not None and ev.status >= 500:
        if ev.severity == "INFO":
            ev.severity = "ERROR"
    return ev
