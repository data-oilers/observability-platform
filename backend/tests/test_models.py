from obs_backend.models import HealthSummary, LogEvent


def test_logevent_defaults():
    ev = LogEvent(ts="2026-06-12T10:31:12Z", severity="WARNING", pod="api-7c9f",
                  message="RAGAS OpenAIError")
    assert ev.path is None and ev.status is None
    assert ev.severity == "WARNING"


def test_health_summary_shape():
    h = HealthSummary(status="ok", reasons=[], errors=0, gemini_429=0)
    assert h.status == "ok"
    assert h.reasons == []
