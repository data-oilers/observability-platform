import re

from obs_backend.models import HealthSummary, LogEvent

_RE_429 = re.compile(r"\b429\b")


def _is_gemini_429(ev: LogEvent) -> bool:
    low = ev.message.lower()
    return "gemini" in low and (bool(_RE_429.search(ev.message)) or "rate limit" in low)


def summarize(events: list[LogEvent]) -> HealthSummary:
    errors = sum(1 for e in events
                 if e.severity in ("ERROR", "CRITICAL") or (e.status or 0) >= 500)
    g429 = sum(1 for e in events if _is_gemini_429(e))

    reasons: list[str] = []
    status = "ok"
    if errors >= 1 or g429 >= 1:
        status = "warn"
        if errors:
            reasons.append(f"{errors} errores en la ventana")
        if g429:
            reasons.append(f"{g429} rate-limit Gemini")
    if errors >= 5:
        status = "crit"
        reasons.append("pico de errores (>=5)")
    return HealthSummary(status=status, reasons=reasons, errors=errors, gemini_429=g429)
