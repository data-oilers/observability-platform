from obs_backend.health import summarize
from obs_backend.models import LogEvent


def _ev(sev, msg="", status=None):
    return LogEvent(ts="t", severity=sev, pod="api", message=msg, status=status)


def test_ok_when_no_errors():
    h = summarize([_ev("INFO"), _ev("WARNING")])
    assert h.status == "ok"
    assert h.errors == 0


def test_warn_on_error():
    h = summarize([_ev("ERROR")])
    assert h.status == "warn"
    assert h.errors == 1


def test_crit_on_many_errors():
    h = summarize([_ev("ERROR")] * 5)
    assert h.status == "crit"


def test_gemini_429_counted():
    h = summarize([_ev("WARNING", msg="Gemini rate limited (429)")])
    assert h.gemini_429 == 1
    assert h.status == "warn"


# Fix C: ≥5 errores Y un Gemini-429 → crit, reasons incluye ambos.
def test_crit_and_gemini_429_reasons():
    events = [_ev("ERROR")] * 5 + [_ev("WARNING", msg="Gemini 429 rate limit exceeded")]
    h = summarize(events)
    assert h.status == "crit"
    assert h.errors == 5
    assert h.gemini_429 == 1
    # reasons debe mencionar ambos: errores y gemini
    assert any("error" in r.lower() for r in h.reasons)
    assert any("gemini" in r.lower() or "rate" in r.lower() for r in h.reasons)
